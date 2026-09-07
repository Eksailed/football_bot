import logging
import sqlite3
import os
import re
import requests
from datetime import datetime, timedelta
import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

# --- НАСТРОЙКИ ---
TOKEN = os.environ.get("TOKEN")
if not TOKEN:
    raise ValueError("Токен не найден! Проверьте переменную окружения TELEGRAM_BOT_TOKEN")
MAIN_ADMIN_ID = 5601944469  # ваш Telegram ID
FOOTBALL_API_KEY = os.environ.get("FOOTBALL_API_KEY")
if not FOOTBALL_API_KEY:
    print("Предупреждение: FOOTBALL_API_KEY не задан.")

TIMEZONE = pytz.timezone("Europe/Moscow")
SHORT_DAYS = {
    "Mon": "Пн", "Tue": "Вт", "Wed": "Ср",
    "Thu": "Чт", "Fri": "Пт", "Sat": "Сб", "Sun": "Вс"
}

# --- СЛОВАРЬ ПЕРЕВОДА ---
TEAM_TRANSLATIONS = {
    "AEK Athens": "АЕК Афины",
    "LASK": "ЛАСК",
    "Club Brugge": "Брюгге",
    "Aston Villa": "Астон Вилла",
    "Borussia Dortmund": "Боруссия Дортмунд",
    "B. Dortmund": "Боруссия Дортмунд",
    "Villarreal": "Вильярреал",
    "Lille": "Лилль",
    "Real Betis": "Реал Бетис",
    "Porto": "Порту",
    "Manchester City": "Манчестер Сити",
    "Man City": "Манчестер Сити",
    "Real Madrid": "Реал Мадрид",
    "Inter": "Интер",
    "Barcelona": "Барселона",
    "Feyenoord": "Фейеноорд",
    "Stuttgart": "Штутгарт",
    "Viking": "Викинг",
    "Liverpool": "Ливерпуль",
    "Atletico Madrid": "Атлетико Мадрид",
    "Atleti": "Атлетико",
    "Napoli": "Наполи",
    "Arsenal": "Арсенал",
    "Paris Saint-Germain": "Пари Сен-Жермен",
    "Paris": "Пари Сен-Жермен",
    "Slovan Bratislava": "Слован Братислава",
    "S. Bratislava": "Слован Братислава",
    "Sporting CP": "Спортинг Лиссабон",
    "Sporting": "Спортинг Лиссабон",
    "Galatasaray": "Галатасарай",
    "Fenerbahçe": "Фенербахче",
    "Fenerbahce": "Фенербахче",
    "Roma": "Рома",
    "PSV": "ПСВ",
    "Shakhtar Donetsk": "Шахтёр",
    "Shakhtar": "Шахтёр",
    "Bayern München": "Бавария",
    "Bayern Munich": "Бавария",
    "Bodø/Glimt": "Будё-Глимт",
    "Bodo/Glimt": "Будё-Глимт",
    "Como": "Комо",
    "RB Leipzig": "Лейпциг",
    "Leipzig": "Лейпциг",
    "Manchester United": "Манчестер Юнайтед",
    "Man Utd": "Манчестер Юнайтед",
    "Sabah": "Сабах",
    "Slavia Praha": "Славия Прага",
    "Slavia Prague": "Славия Прага",
    "Lens": "Ланс",
}

def translate_team(name: str) -> str:
    return TEAM_TRANSLATIONS.get(name, name)

# --- НАЧАЛЬНЫЕ МАТЧИ ---
INITIAL_MATCHES = []

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

# --- ФУНКЦИИ АДМИНИСТРИРОВАНИЯ ---
def init_admins():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS admins
                 (user_id INTEGER PRIMARY KEY)''')
    c.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (MAIN_ADMIN_ID,))
    conn.commit()
    conn.close()

def is_admin(user_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT 1 FROM admins WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row is not None

def add_admin(user_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()
    return True

def remove_admin(user_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("DELETE FROM admins WHERE user_id=?", (user_id,))
    affected = c.rowcount
    conn.commit()
    conn.close()
    return affected > 0

def get_admins_list():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''SELECT a.user_id, u.username, u.first_name
                 FROM admins a
                 LEFT JOIN users u ON a.user_id = u.user_id''')
    rows = c.fetchall()
    conn.close()
    return rows

# --- ОСТАЛЬНЫЕ ФУНКЦИИ БАЗЫ ДАННЫХ ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS matches
                 (match_id INTEGER PRIMARY KEY, home TEXT, away TEXT, day TEXT,
                  result TEXT, start_time TEXT, api_id TEXT UNIQUE,
                  current_result TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS predictions
                 (user_id INTEGER, match_id INTEGER, prediction TEXT,
                  PRIMARY KEY (user_id, match_id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS scores
                 (user_id INTEGER PRIMARY KEY, score INTEGER DEFAULT 0)''')
    c.execute("PRAGMA table_info(matches)")
    columns = [col[1] for col in c.fetchall()]
    if "api_id" not in columns:
        c.execute("ALTER TABLE matches ADD COLUMN api_id TEXT UNIQUE")
    if "current_result" not in columns:
        c.execute("ALTER TABLE matches ADD COLUMN current_result TEXT")
    c.execute("SELECT COUNT(*) FROM matches")
    count = c.fetchone()[0]
    if count == 0:
        for m in INITIAL_MATCHES:
            c.execute("INSERT INTO matches (match_id, home, away, day, start_time) VALUES (?,?,?,?,?)",
                      (m["id"], m["home"], m["away"], m["day"], m["start_time"]))
    conn.commit()
    conn.close()
    init_admins()

def get_next_match_id():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT MAX(match_id) FROM matches")
    row = c.fetchone()
    conn.close()
    return (row[0] or 0) + 1

def add_match_from_api(api_id, home, away, start_time, day=None):
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
    except sqlite3.IntegrityError:
        conn.close()
        return None

def add_match_manual(home, away, start_time):
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
    c.execute("SELECT match_id, home, away, day, result, start_time, api_id, current_result FROM matches WHERE match_id=?", (match_id,))
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

def set_current_result(match_id, current_result):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE matches SET current_result=? WHERE match_id=?", (current_result, match_id))
    conn.commit()
    conn.close()

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
    c.execute("SELECT match_id, home, away, day, result, start_time, api_id, current_result FROM matches ORDER BY match_id")
    rows = c.fetchall()
    conn.close()
    return rows

def get_active_matches():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT match_id, home, away, day, result, start_time, api_id, current_result FROM matches WHERE result IS NULL ORDER BY match_id")
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

# --- ФУНКЦИИ ДЛЯ РАБОТЫ С API ---
def fetch_matches_from_api(days_ahead=7):
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
            home_en = m.get("homeTeam", {}).get("name", "")
            away_en = m.get("awayTeam", {}).get("name", "")
            utc_date = m.get("utcDate")
            if not api_id or not home_en or not away_en or not utc_date:
                continue
            home_ru = translate_team(home_en)
            away_ru = translate_team(away_en)
            utc_dt = datetime.fromisoformat(utc_date.replace("Z", "+00:00"))
            local_dt = utc_dt.astimezone(TIMEZONE)
            start_time = local_dt.strftime("%Y-%m-%d %H:%M")
            result.append({
                "api_id": api_id,
                "home": home_ru,
                "away": away_ru,
                "start_time": start_time
            })
        return result
    except Exception as e:
        print(f"Ошибка при получении матчей: {e}")
        return []

def update_matches_from_api():
    matches = fetch_matches_from_api(days_ahead=7)
    added = 0
    for m in matches:
        if add_match_from_api(m["api_id"], m["home"], m["away"], m["start_time"]):
            added += 1
    return added

def fetch_match_details_by_api_id(api_id):
    if not FOOTBALL_API_KEY:
        return None
    url = f"https://api.football-data.org/v4/matches/{api_id}"
    headers = {"X-Auth-Token": FOOTBALL_API_KEY}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            return None
        data = response.json()
        status = data.get("status", "")
        score = data.get("score", {})
        half_time = score.get("halfTime")
        full_time = score.get("fullTime")
        result = {
            "status": status,
            "half_time": f"{half_time.get('home')}:{half_time.get('away')}" if half_time and half_time.get('home') is not None else None,
            "full_time": f"{full_time.get('home')}:{full_time.get('away')}" if full_time and full_time.get('home') is not None else None,
        }
        return result
    except Exception as e:
        print(f"Ошибка получения деталей матча по api_id {api_id}: {e}")
        return None

def update_results_from_api():
    """Обновляет current_result и финальные результаты для всех матчей с api_id."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT match_id, api_id, start_time FROM matches WHERE api_id IS NOT NULL")
    matches = c.fetchall()
    conn.close()
    updated = 0
    for match_id, api_id, start_time in matches:
        # Обновляем только если матч уже начался (или прошло время начала), чтобы не тратить запросы на будущие
        if not is_match_finished(start_time) and not is_match_open(start_time):
            # Если матч уже идёт или завершился, обновляем
            pass
        # Но мы будем обновлять все, у которых result NULL и api_id есть, чтобы получить текущий счёт
        details = fetch_match_details_by_api_id(api_id)
        if not details:
            continue
        current = details["half_time"] or details["full_time"]
        if current:
            set_current_result(match_id, current)
        if details["status"] == "FINISHED" and details["full_time"]:
            match = get_match(match_id)
            if match and match[4] is None:
                set_result(match_id, details["full_time"])
                updated += 1
                print(f"Автообновление: финальный результат матча #{match_id}: {details['full_time']}")
    return updated

logging.basicConfig(level=logging.INFO)

# --- ОБРАБОТЧИКИ (кнопки и команды) ---
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
                match_id, home, away, day, result, start_time, api_id, current_result = m
                status = "⏳"
                day_short = SHORT_DAYS.get(day, day)
                if start_time:
                    start_dt = datetime.strptime(start_time, "%Y-%m-%d %H:%M")
                    deadline_dt = start_dt - timedelta(minutes=10)
                    start_str = start_dt.strftime("%H:%M")
                    deadline_str = deadline_dt.strftime("%H:%M")
                    score_info = f" | Счёт: {current_result}" if current_result else ""
                    text += (
                        f"*{match_id}.* {home} – {away}\n"
                        f"   🗓 {day_short} {start_str} | ⏳ дедлайн {deadline_str}{score_info}\n"
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
            match_id, home, away, day, result, start_time, api_id, current_result = m
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
            match_id, home, away, day, result, start_time, api_id, current_result = m
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
        match_id, home, away, day, result, start_time, api_id, current_result = match
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
    match_id, home, away, day, result, start_time, api_id, current_result = match
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

# --- АДМИН-КОМАНДЫ ---
async def admins_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("У вас нет прав для этой команды.")
        return
    admins = get_admins_list()
    if not admins:
        await update.message.reply_text("Список администраторов пуст (ошибка).")
        return
    text = "👑 *Список администраторов:*\n\n"
    for i, (user_id, username, first_name) in enumerate(admins, 1):
        if user_id == MAIN_ADMIN_ID:
            role = " (главный)"
        else:
            role = ""
        if username:
            name = f"@{username}"
        elif first_name:
            name = first_name
        else:
            name = f"ID: {user_id}"
        text += f"{i}. {name}{role}\n"
    await update.message.reply_text(text, parse_mode="Markdown")

async def addadmin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("У вас нет прав для этой команды.")
        return
    args = context.args
    if len(args) != 1:
        await update.message.reply_text("Использование: /addadmin <user_id>")
        return
    try:
        new_admin_id = int(args[0])
    except ValueError:
        await update.message.reply_text("Введите корректный числовой ID пользователя.")
        return
    if is_admin(new_admin_id):
        await update.message.reply_text("Этот пользователь уже является администратором.")
        return
    add_admin(new_admin_id)
    await update.message.reply_text(f"✅ Пользователь с ID {new_admin_id} добавлен в список администраторов.")

async def removeadmin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("У вас нет прав для этой команды.")
        return
    args = context.args
    if len(args) != 1:
        await update.message.reply_text("Использование: /removeadmin <user_id>")
        return
    try:
        admin_id = int(args[0])
    except ValueError:
        await update.message.reply_text("Введите корректный числовой ID.")
        return
    if admin_id == MAIN_ADMIN_ID:
        await update.message.reply_text("Нельзя удалить главного администратора.")
        return
    if not is_admin(admin_id):
        await update.message.reply_text("Этот пользователь не является администратором.")
        return
    remove_admin(admin_id)
    await update.message.reply_text(f"✅ Пользователь с ID {admin_id} удалён из списка администраторов.")

async def addmatch_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
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
    if not is_admin(update.effective_user.id):
        return
    if "addmatch_step" not in context.user_data:
        return
    text = update.message.text.strip()
    step = context.user_data["addmatch_step"]
    if step == 1:
        context.user_data["addmatch_home"] = text
        context.user_data["addmatch_step"] = 2
        await update.message.reply_text(
            "Введите название команды ГОСТЕЙ:\n"
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
                "Неверный формат даты/времени. Попробуйте снова или введите /cancel."
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
            f"Начало: {start_time}"
        )

async def fetch_matches_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("У вас нет прав для этой команды.")
        return
    if not FOOTBALL_API_KEY:
        await update.message.reply_text("API-ключ не настроен.")
        return
    await update.message.reply_text("⏳ Загружаю матчи Лиги чемпионов...")
    try:
        added = update_matches_from_api()
        await update.message.reply_text(f"✅ Добавлено новых матчей: {added}.")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def fetch_results_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("У вас нет прав для этой команды.")
        return
    if not FOOTBALL_API_KEY:
        await update.message.reply_text("API-ключ не настроен.")
        return
    await update.message.reply_text("⏳ Обновляю счета (по таймам) и финальные результаты...")
    try:
        updated = update_results_from_api()
        await update.message.reply_text(f"✅ Обновлено финальных результатов: {updated}.\nТекущие счета обновлены для всех матчей.")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def set_result_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("У вас нет прав для этой команды.")
        return
    args = context.args
    if len(args) != 2:
        await update.message.reply_text("Использование: /setresult <id> <счёт> (например, /setresult 1 2:1)")
        return
    try:
        match_id = int(args[0])
        result = args[1]
        if not re.match(r'^\d+\s*[:;-]\s*\d+$', result) and not re.match(r'^\d+\s*[-]\s*\d+$', result):
            raise ValueError
        result = re.sub(r'\s*[:-]\s*', ':', result)
        result = re.sub(r'\s*[-]\s*', ':', result)
    except ValueError:
        await update.message.reply_text("Неверный формат. Используйте /setresult <id> <счёт>")
        return
    match = get_match(match_id)
    if not match:
        await update.message.reply_text("Матч не найден.")
        return
    if match[4] is not None:
        await update.message.reply_text("Результат уже установлен.")
        return
    set_result(match_id, result)
    await update.message.reply_text(f"Результат матча #{match_id} установлен: {result}. Очки пересчитаны.")

async def reset_result_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("У вас нет прав для этой команды.")
        return
    args = context.args
    if len(args) != 1:
        await update.message.reply_text("Использование: /resetresult <id>")
        return
    try:
        match_id = int(args[0])
    except ValueError:
        await update.message.reply_text("Введите число.")
        return
    match = get_match(match_id)
    if not match:
        await update.message.reply_text("Матч не найден.")
        return
    if match[4] is None:
        await update.message.reply_text("Результат не установлен.")
        return
    reset_result(match_id)
    await update.message.reply_text(f"Результат матча #{match_id} удалён. Очки пересчитаны.")

async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Неизвестная команда. Используйте /start для начала.")

# --- ГЛАВНАЯ ---
def main():
    init_db()
    # При старте обновляем матчи и результаты
    if FOOTBALL_API_KEY:
        try:
            added = update_matches_from_api()
            print(f"При старте добавлено {added} матчей.")
            updated = update_results_from_api()
            print(f"При старте обновлено {updated} финальных результатов.")
        except Exception as e:
            print(f"Ошибка при стартовом обновлении: {e}")

    # Настраиваем планировщик для автоматического обновления каждые 10 минут
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        func=update_results_from_api,
        trigger=IntervalTrigger(minutes=10),
        id='auto_update_results',
        name='Обновление счетов и результатов',
        replace_existing=True
    )
    scheduler.start()
    print("Планировщик запущен (обновление каждые 10 минут).")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setresult", set_result_cmd))
    app.add_handler(CommandHandler("resetresult", reset_result_cmd))
    app.add_handler(CommandHandler("addmatch", addmatch_start))
    app.add_handler(CommandHandler("cancel", addmatch_cancel))
    app.add_handler(CommandHandler("fetchmatches", fetch_matches_cmd))
    app.add_handler(CommandHandler("fetchresults", fetch_results_cmd))
    app.add_handler(CommandHandler("addadmin", addadmin_cmd))
    app.add_handler(CommandHandler("removeadmin", removeadmin_cmd))
    app.add_handler(CommandHandler("admins", admins_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_addmatch_text))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_score_input))
    app.add_handler(MessageHandler(filters.COMMAND, unknown))

    print("Бот запущен...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
