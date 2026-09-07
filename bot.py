import logging
import sqlite3
import os
import re
import requests
from datetime import datetime, timedelta
import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# --- НАСТРОЙКИ ---
TOKEN = os.environ.get("TOKEN")
if not TOKEN:
    raise ValueError("Токен не найден! Проверьте переменную окружения TELEGRAM_BOT_TOKEN")
ADMIN_USER_ID = 5601944469  # замените на ваш Telegram ID
FOOTBALL_API_KEY = os.environ.get("FOOTBALL_API_KEY")
if not FOOTBALL_API_KEY:
    print("Предупреждение: FOOTBALL_API_KEY не задан. Команды /fetchmatches и /fetchresults не будут работать.")

TIMEZONE = pytz.timezone("Europe/Moscow")
SHORT_DAYS = {
    "Mon": "Пн", "Tue": "Вт", "Wed": "Ср",
    "Thu": "Чт", "Fri": "Пт", "Sat": "Сб", "Sun": "Вс"
}

DB_NAME = "predictions.db"

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def parse_score(score_str):
    if not score_str:
        return None
    cleaned = re.sub(r'\s+', '', score_str)
    cleaned = re.sub(r'[:-]', ':', cleaned)
    parts = cleaned.split(':')
    if len(parts) != 2:
        return None
    try:
        home = int(parts[0])
        away = int(parts[1])
        return home, away
    except ValueError:
        return None

def get_outcome(home, away):
    if home > away:
        return '1'
    elif home == away:
        return 'X'
    else:
        return '2'

def is_match_open(start_time_str):
    try:
        start_dt = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M")
        start_dt = TIMEZONE.localize(start_dt)
    except Exception:
        return False
    now = datetime.now(TIMEZONE)
    deadline = start_dt - timedelta(minutes=10)
    return now < deadline

def is_match_finished(start_time_str):
    try:
        start_dt = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M")
        start_dt = TIMEZONE.localize(start_dt)
    except Exception:
        return False
    now = datetime.now(TIMEZONE)
    finish_dt = start_dt + timedelta(hours=2)
    return now > finish_dt

# --- БАЗА ДАННЫХ ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT)''')
    # Добавляем поле api_id для уникальной идентификации матчей из API
    c.execute('''CREATE TABLE IF NOT EXISTS matches
                 (match_id INTEGER PRIMARY KEY, home TEXT, away TEXT, day TEXT, result TEXT, start_time TEXT, api_id TEXT UNIQUE)''')
    c.execute('''CREATE TABLE IF NOT EXISTS predictions
                 (user_id INTEGER, match_id INTEGER, prediction TEXT,
                  PRIMARY KEY (user_id, match_id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS scores
                 (user_id INTEGER PRIMARY KEY, score INTEGER DEFAULT 0)''')
    # Проверяем, есть ли колонка api_id (для старых баз) и добавляем, если нет
    c.execute("PRAGMA table_info(matches)")
    columns = [col[1] for col in c.fetchall()]
    if "api_id" not in columns:
        c.execute("ALTER TABLE matches ADD COLUMN api_id TEXT UNIQUE")
    conn.commit()
    conn.close()

def get_next_match_id():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT MAX(match_id) FROM matches")
    row = c.fetchone()
    conn.close()
    return (row[0] or 0) + 1

def add_match_from_api(api_id, home, away, start_time, day=None):
    """Добавляет матч из API, если его ещё нет по api_id."""
    if not day:
        dt = datetime.strptime(start_time, "%Y-%m-%d %H:%M")
        day_eng = dt.strftime("%a")
    else:
        day_eng = day
    match_id = get_next_match_id()
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    try:
        c.execute("INSERT INTO matches (match_id, home, away, day, start_time, api_id) VALUES (?,?,?,?,?,?)",
                  (match_id, home, away, day_eng, start_time, str(api_id)))
        conn.commit()
        conn.close()
        return match_id
    except sqlite3.IntegrityError:  # дубликат по api_id
        conn.close()
        return None

def add_match_manual(home, away, start_time):
    """Для ручного добавления (без api_id)."""
    dt = datetime.strptime(start_time, "%Y-%m-%d %H:%M")
    day_eng = dt.strftime("%a")
    match_id = get_next_match_id()
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT INTO matches (match_id, home, away, day, start_time) VALUES (?,?,?,?,?)",
              (match_id, home, away, day_eng, start_time))
    conn.commit()
    conn.close()
    return match_id

def get_user(user_id, username, first_name):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, username, first_name) VALUES (?,?,?)",
              (user_id, username, first_name))
    c.execute("INSERT OR IGNORE INTO scores (user_id, score) VALUES (?,?)", (user_id, 0))
    conn.commit()
    conn.close()

def save_prediction(user_id, match_id, prediction):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO predictions (user_id, match_id, prediction) VALUES (?,?,?)",
              (user_id, match_id, prediction))
    conn.commit()
    conn.close()

def get_user_predictions(user_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''SELECT m.match_id, m.home, m.away, p.prediction, m.result
                 FROM predictions p
                 JOIN matches m ON p.match_id = m.match_id
                 WHERE p.user_id = ?''', (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows

def get_match(match_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT match_id, home, away, day, result, start_time, api_id FROM matches WHERE match_id=?", (match_id,))
    row = c.fetchone()
    conn.close()
    return row

def set_result(match_id, result):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE matches SET result=? WHERE match_id=?", (result, match_id))
    conn.commit()
    conn.close()
    recalc_all_scores()

def reset_result(match_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE matches SET result=NULL WHERE match_id=?", (match_id,))
    conn.commit()
    conn.close()
    recalc_all_scores()

def recalc_all_scores():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT user_id FROM users")
    users = c.fetchall()
    for (user_id,) in users:
        c.execute('''SELECT p.prediction, m.result
                     FROM predictions p
                     JOIN matches m ON p.match_id = m.match_id
                     WHERE p.user_id = ? AND m.result IS NOT NULL''', (user_id,))
        rows = c.fetchall()
        total = 0
        for pred_str, res_str in rows:
            pred = parse_score(pred_str)
            res = parse_score(res_str)
            if pred is None or res is None:
                continue
            pred_h, pred_a = pred
            res_h, res_a = res

            if pred_h == res_h and pred_a == res_a:
                total += 6
            elif (pred_h - pred_a) == (res_h - res_a):
                total += 3
            elif get_outcome(pred_h, pred_a) == get_outcome(res_h, res_a):
                total += 2
        c.execute("INSERT OR REPLACE INTO scores (user_id, score) VALUES (?,?)", (user_id, total))
    conn.commit()
    conn.close()

def get_all_matches():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT match_id, home, away, day, result, start_time, api_id FROM matches ORDER BY match_id")
    rows = c.fetchall()
    conn.close()
    return rows

def get_active_matches():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT match_id, home, away, day, result, start_time, api_id FROM matches WHERE result IS NULL ORDER BY match_id")
    rows = c.fetchall()
    conn.close()
    return rows

def get_scores():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''SELECT s.user_id, s.score, u.first_name, u.username
                 FROM scores s
                 JOIN users u ON s.user_id = u.user_id
                 ORDER BY s.score DESC''')
    rows = c.fetchall()
    conn.close()
    return rows

# --- ФУНКЦИИ ДЛЯ РАБОТЫ С API (МАТЧИ И РЕЗУЛЬТАТЫ) ---
def fetch_matches_from_api(days_ahead=7):
    """
    Получает матчи Лиги чемпионов на ближайшие days_ahead дней.
    Возвращает список словарей с полями: api_id, home, away, start_time.
    """
    if not FOOTBALL_API_KEY:
        return []
    now = datetime.now(TIMEZONE)
    date_from = now.strftime("%Y-%m-%d")
    date_to = (now + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    url = f"https://api.football-data.org/v4/competitions/CL/matches?dateFrom={date_from}&dateTo={date_to}"
    headers = {"X-Auth-Token": FOOTBALL_API_KEY}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            print(f"API error (matches): {response.status_code}")
            return []
        data = response.json()
        matches = data.get("matches", [])
        result = []
        for m in matches:
            api_id = m.get("id")
            home = m.get("homeTeam", {}).get("name", "")
            away = m.get("awayTeam", {}).get("name", "")
            utc_date = m.get("utcDate")
            if not api_id or not home or not away or not utc_date:
                continue
            # Преобразуем UTC в локальное время (Москва)
            utc_dt = datetime.fromisoformat(utc_date.replace("Z", "+00:00"))
            local_dt = utc_dt.astimezone(TIMEZONE)
            start_time = local_dt.strftime("%Y-%m-%d %H:%M")
            result.append({
                "api_id": api_id,
                "home": home,
                "away": away,
                "start_time": start_time
            })
        return result
    except Exception as e:
        print(f"Ошибка при получении матчей: {e}")
        return []

def update_matches_from_api():
    """Загружает матчи из API и добавляет их в базу, если их там нет."""
    matches = fetch_matches_from_api(days_ahead=7)
    added = 0
    for m in matches:
        if add_match_from_api(m["api_id"], m["home"], m["away"], m["start_time"]):
            added += 1
    return added

def fetch_match_result_from_api(home_team, away_team, start_date):
    """Ищет результат матча (использует ту же логику, что и раньше)."""
    if not FOOTBALL_API_KEY:
        return None
    date_obj = datetime.strptime(start_date, "%Y-%m-%d %H:%M")
    date_str = date_obj.strftime("%Y-%m-%d")
    url = f"https://api.football-data.org/v4/matches?dateFrom={date_str}&dateTo={date_str}"
    headers = {"X-Auth-Token": FOOTBALL_API_KEY}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            print(f"API error (result): {response.status_code}")
            return None
        data = response.json()
        matches = data.get("matches", [])
        for match in matches:
            home = match.get("homeTeam", {}).get("name", "")
            away = match.get("awayTeam", {}).get("name", "")
            if home.lower() == home_team.lower() and away.lower() == away_team.lower():
                score = match.get("score", {}).get("fullTime")
                if score:
                    home_score = score.get("home", -1)
                    away_score = score.get("away", -1)
                    if home_score >= 0 and away_score >= 0:
                        return f"{home_score}:{away_score}"
                break
        return None
    except Exception as e:
        print(f"Ошибка при запросе результата: {e}")
        return None

def update_results_from_api():
    """Обновляет результаты для матчей, у которых result NULL и время начала прошло."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT match_id, home, away, start_time FROM matches WHERE result IS NULL")
    matches = c.fetchall()
    conn.close()
    updated = 0
    for match_id, home, away, start_time in matches:
        if not is_match_finished(start_time):
            continue
        result = fetch_match_result_from_api(home, away, start_time)
        if result:
            set_result(match_id, result)
            updated += 1
            print(f"Обновлён матч #{match_id}: {home} – {away} = {result}")
    return updated

logging.basicConfig(level=logging.INFO)

# --- ОБРАБОТЧИКИ ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, user.username, user.first_name)
    await show_main_menu(update, context, "Добро пожаловать! Выберите действие:")

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, text="Главное меню:"):
    keyboard = [
        [InlineKeyboardButton("📋 Список матчей", callback_data="matches")],
        [InlineKeyboardButton("📝 Мои прогнозы", callback_data="mypredicts")],
        [InlineKeyboardButton("🏆 Результаты", callback_data="results")],
        [InlineKeyboardButton("🏅 Таблица лидеров", callback_data="leaderboard")],
        [InlineKeyboardButton("✏️ Сделать прогноз", callback_data="make_predict")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "matches":
        rows = get_active_matches()
        if not rows:
            text = "📋 *Список активных матчей:*\n\nНет активных матчей. Все матчи завершены!"
        else:
            text = "📋 *Список активных матчей:*\n\n"
            for m in rows:
                match_id, home, away, day, result, start_time, api_id = m
                status = "⏳"
                day_short = SHORT_DAYS.get(day, day)
                if start_time:
                    start_dt = datetime.strptime(start_time, "%Y-%m-%d %H:%M")
                    deadline_dt = start_dt - timedelta(minutes=10)
                    start_str = start_dt.strftime("%H:%M")
                    deadline_str = deadline_dt.strftime("%H:%M")
                    text += (
                        f"*{match_id}.* {home} – {away}\n"
                        f"   🗓 {day_short} {start_str} | ⏳ дедлайн {deadline_str}\n"
                        f"   Статус: {status}\n\n"
                    )
                else:
                    text += f"*{match_id}.* {home} – {away} ({day_short}) {status}\n\n"
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="menu")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data == "mypredicts":
        user = update.effective_user
        rows = get_user_predictions(user.id)
        if not rows:
            text = "У вас пока нет прогнозов."
        else:
            text = "📝 *Ваши прогнозы:*\n\n"
            for r in rows:
                match_id, home, away, pred, result = r
                status = "✅" if result else "⏳"
                text += f"#{match_id} {home} – {away}: *{pred}* {status}\n"
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="menu")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data == "results":
        rows = get_all_matches()
        text = "🏆 *Результаты завершённых матчей:*\n\n"
        found = False
        for m in rows:
            match_id, home, away, day, result, start_time, api_id = m
            if result:
                found = True
                text += f"#{match_id} {home} – {away}: *{result}*\n"
        if not found:
            text = "Пока нет завершённых матчей."
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="menu")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data == "leaderboard":
        scores = get_scores()
        if not scores:
            text = "Пока нет данных для таблицы лидеров."
        else:
            text = "🏅 *Таблица лидеров:*\n\n"
            for i, (user_id, score, first_name, username) in enumerate(scores[:10], 1):
                name = first_name if first_name else str(user_id)
                if username:
                    name += f" (@{username})"
                text += f"{i}. {name} – *{score}* очков\n"
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="menu")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data == "make_predict":
        rows = get_active_matches()
        keyboard = []
        for m in rows:
            match_id, home, away, day, result, start_time, api_id = m
            if start_time and is_match_open(start_time):
                keyboard.append([InlineKeyboardButton(f"{match_id}. {home} – {away}", callback_data=f"pred_{match_id}")])
        if not keyboard:
            await query.edit_message_text("Нет доступных матчей для прогноза (все завершены или дедлайн прошёл).")
            return
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="menu")])
        await query.edit_message_text("Выберите матч для прогноза:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("pred_"):
        match_id = int(data.split("_")[1])
        match = get_match(match_id)
        if not match:
            await query.edit_message_text("Матч не найден.")
            return
        match_id, home, away, day, result, start_time, api_id = match
        if result is not None:
            await query.edit_message_text("Этот матч уже завершён, прогнозы не принимаются.")
            return
        if not is_match_open(start_time):
            await query.edit_message_text("Приём прогнозов на этот матч уже закрыт (за 10 минут до начала).")
            return

        context.user_data["awaiting_score"] = match_id
        await query.edit_message_text(
            f"Введите ваш прогноз для матча #{match_id} ({home} – {away}) в формате:\n"
            "Например: 2:1 или 2-1\n\n"
            "Очки начисляются так:\n"
            "• +6 за точный счёт\n"
            "• +3 за разницу голов\n"
            "• +2 за исход\n\n"
            "Вы можете изменить прогноз до дедлайна (за 10 минут до начала)."
        )

    elif data == "menu":
        await show_main_menu(update, context)

# --- ОБРАБОТЧИК ТЕКСТОВЫХ СООБЩЕНИЙ (ввод счёта) ---
async def handle_score_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip()

    match_id = context.user_data.get("awaiting_score")
    if not match_id:
        return

    match = get_match(match_id)
    if not match:
        await update.message.reply_text("Матч не найден.")
        context.user_data.pop("awaiting_score", None)
        return
    match_id, home, away, day, result, start_time, api_id = match
    if result is not None:
        await update.message.reply_text("Этот матч уже завершён, прогнозы не принимаются.")
        context.user_data.pop("awaiting_score", None)
        return
    if not is_match_open(start_time):
        await update.message.reply_text("Приём прогнозов на этот матч уже закрыт (за 10 минут до начала).")
        context.user_data.pop("awaiting_score", None)
        return

    if not re.match(r'^\d+\s*[:;-]\s*\d+$', text) and not re.match(r'^\d+\s*[-]\s*\d+$', text):
        await update.message.reply_text(
            "Неверный формат. Введите счёт в формате:\n"
            "2:1 или 2-1 (допускаются пробелы)."
        )
        return

    score = re.sub(r'\s*[:-]\s*', ':', text)
    score = re.sub(r'\s*[-]\s*', ':', score)

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT prediction FROM predictions WHERE user_id=? AND match_id=?", (user.id, match_id))
    existing = c.fetchone()
    conn.close()
    if existing:
        await update.message.reply_text("Вы уже делали прогноз на этот матч. Ваш прогноз будет обновлён.")
    save_prediction(user.id, match_id, score)
    await update.message.reply_text(f"✅ Ваш прогноз на матч #{match_id} ({home} – {away}) сохранён: {score}")

    context.user_data.pop("awaiting_score", None)
    await show_main_menu(update, context, "Прогноз сохранён! Что дальше?")

# --- АДМИН КОМАНДЫ ДЛЯ ДОБАВЛЕНИЯ МАТЧА (ручное) ---
async def addmatch_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("У вас нет прав для этой команды.")
        return
    context.user_data["addmatch_step"] = 1
    await update.message.reply_text(
        "Введите название команды ХОЗЯЕВ (например, 'Бавария'):\n"
        "Для отмены введите /cancel"
    )

async def addmatch_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "addmatch_step" in context.user_data:
        context.user_data.pop("addmatch_step", None)
        context.user_data.pop("addmatch_home", None)
        context.user_data.pop("addmatch_away", None)
        await update.message.reply_text("Добавление матча отменено.")
    else:
        await update.message.reply_text("Нет активного процесса добавления.")

async def handle_addmatch_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        return
    if "addmatch_step" not in context.user_data:
        return

    text = update.message.text.strip()
    step = context.user_data["addmatch_step"]

    if step == 1:
        context.user_data["addmatch_home"] = text
        context.user_data["addmatch_step"] = 2
        await update.message.reply_text(
            "Введите название команды ГОСТЕЙ (например, 'Боруссия Дортмунд'):\n"
            "Для отмены введите /cancel"
        )
    elif step == 2:
        context.user_data["addmatch_away"] = text
        context.user_data["addmatch_step"] = 3
        await update.message.reply_text(
            "Введите дату и время начала матча в формате:\n"
            "ГГГГ-ММ-ДД ЧЧ:ММ (например, 2026-09-15 21:00)\n"
            "Для отмены введите /cancel"
        )
    elif step == 3:
        try:
            datetime.strptime(text, "%Y-%m-%d %H:%M")
        except ValueError:
            await update.message.reply_text(
                "Неверный формат даты/времени. Используйте ГГГГ-ММ-ДД ЧЧ:ММ (например, 2026-09-15 21:00).\n"
                "Попробуйте снова или введите /cancel для отмены."
            )
            return

        home = context.user_data["addmatch_home"]
        away = context.user_data["addmatch_away"]
        start_time = text
        match_id = add_match_manual(home, away, start_time)
        context.user_data.pop("addmatch_step", None)
        context.user_data.pop("addmatch_home", None)
        context.user_data.pop("addmatch_away", None)

        await update.message.reply_text(
            f"✅ Матч #{match_id} успешно добавлен:\n"
            f"{home} – {away}\n"
            f"Начало: {start_time}\n"
            "Теперь пользователи могут делать прогнозы (дедлайн за 10 минут до начала)."
        )

# --- НОВЫЕ АДМИН-КОМАНДЫ ДЛЯ API ---
async def fetch_matches_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("У вас нет прав для этой команды.")
        return
    if not FOOTBALL_API_KEY:
        await update.message.reply_text("API-ключ не настроен. Добавьте переменную FOOTBALL_API_KEY.")
        return

    await update.message.reply_text("⏳ Загружаю матчи Лиги чемпионов из API...")
    try:
        added = update_matches_from_api()
        await update.message.reply_text(f"✅ Добавлено новых матчей: {added}.")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка при загрузке: {e}")

async def fetch_results_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("У вас нет прав для этой команды.")
        return
    if not FOOTBALL_API_KEY:
        await update.message.reply_text("API-ключ не настроен. Добавьте переменную FOOTBALL_API_KEY.")
        return

    await update.message.reply_text("⏳ Обновляю результаты из API...")
    try:
        updated = update_results_from_api()
        await update.message.reply_text(f"✅ Обновлено матчей: {updated}.")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка при обновлении: {e}")

# --- АДМИН КОМАНДЫ ДЛЯ РЕЗУЛЬТАТОВ (ручные) ---
async def set_result_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("У вас нет прав для этой команды.")
        return
    args = context.args
    if len(args) != 2:
        await update.message.reply_text("Использование: /setresult <номер_матча> <счёт>\nНапример: /setresult 1 2:1")
        return
    try:
        match_id = int(args[0])
        result = args[1]
        if not re.match(r'^\d+\s*[:;-]\s*\d+$', result) and not re.match(r'^\d+\s*[-]\s*\d+$', result):
            raise ValueError
        result = re.sub(r'\s*[:-]\s*', ':', result)
        result = re.sub(r'\s*[-]\s*', ':', result)
    except ValueError:
        await update.message.reply_text("Неверный формат. Используйте: /setresult <id> <счёт> (например, 2:1)")
        return

    match = get_match(match_id)
    if not match:
        await update.message.reply_text("Матч не найден.")
        return
    if match[4] is not None:
        await update.message.reply_text("Результат для этого матча уже установлен.")
        return

    set_result(match_id, result)
    await update.message.reply_text(f"Результат матча #{match_id} установлен: {result}. Очки пересчитаны.")

async def reset_result_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("У вас нет прав для этой команды.")
        return
    args = context.args
    if len(args) != 1:
        await update.message.reply_text("Использование: /resetresult <номер_матча>\nПример: /resetresult 1")
        return
    try:
        match_id = int(args[0])
    except ValueError:
        await update.message.reply_text("Неверный номер матча. Введите число.")
        return

    match = get_match(match_id)
    if not match:
        await update.message.reply_text("Матч не найден.")
        return
    if match[4] is None:
        await update.message.reply_text("Результат для этого матча и так не установлен.")
        return

    reset_result(match_id)
    await update.message.reply_text(f"Результат матча #{match_id} удалён. Очки пересчитаны.")

async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Неизвестная команда. Используйте /start для начала.")

# --- ГЛАВНАЯ ---
def main():
    init_db()

    # При старте бота: обновляем расписание и результаты (если есть ключ)
    if FOOTBALL_API_KEY:
        try:
            # Добавляем новые матчи из API (на неделю вперёд)
            added = update_matches_from_api()
            print(f"При старте добавлено {added} новых матчей из API.")
            # Обновляем результаты для завершённых матчей
            updated = update_results_from_api()
            print(f"При старте обновлено {updated} результатов.")
        except Exception as e:
            print(f"Ошибка при стартовом обновлении: {e}")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setresult", set_result_cmd))
    app.add_handler(CommandHandler("resetresult", reset_result_cmd))
    app.add_handler(CommandHandler("addmatch", addmatch_start))
    app.add_handler(CommandHandler("cancel", addmatch_cancel))
    app.add_handler(CommandHandler("fetchmatches", fetch_matches_cmd))   # новая команда
    app.add_handler(CommandHandler("fetchresults", fetch_results_cmd))  # была
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_addmatch_text))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_score_input))
    app.add_handler(MessageHandler(filters.COMMAND, unknown))

    print("Бот запущен...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
