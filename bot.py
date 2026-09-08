import logging
import os
import re
import csv
import io
from datetime import datetime, timedelta
import pytz
import psycopg2
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
import openpyxl
from openpyxl.styles import Alignment, Font
from io import BytesIO

# --- НАСТРОЙКИ ---
TOKEN = os.environ.get("TOKEN")
if not TOKEN:
    raise ValueError("Токен не найден! Проверьте переменную окружения TELEGRAM_BOT_TOKEN")

MAIN_ADMIN_ID = 5601944469  # замените на ваш Telegram ID
FOOTBALL_API_KEY = os.environ.get("FOOTBALL_API_KEY")
if not FOOTBALL_API_KEY:
    print("Предупреждение: FOOTBALL_API_KEY не задан.")

DATABASE_URL = os.environ.get("DATABASE_UR")
if not DATABASE_URL:
    raise ValueError("DATABASE_UR не задан! Подключите PostgreSQL.")

TIMEZONE = pytz.timezone("Europe/Moscow")
SHORT_DAYS = {
    "Mon": "Пн", "Tue": "Вт", "Wed": "Ср",
    "Thu": "Чт", "Fri": "Пт", "Sat": "Сб", "Sun": "Вс"
}

# --- ЛИГИ ---
LEAGUES = {
    "UCL": {"name": "Лига чемпионов", "flag": "🏆", "code": "CL"},
    "PL": {"name": "АПЛ (Англия)", "flag": "🏴󠁧󠁢󠁥󠁮󠁧󠁿", "code": "PL"},
    "PD": {"name": "Ла Лига (Испания)", "flag": "🇪🇸", "code": "PD"},
    "FL1": {"name": "Лига 1 (Франция)", "flag": "🇫🇷", "code": "FL1"},
    "BL1": {"name": "Бундеслига (Германия)", "flag": "🇩🇪", "code": "BL1"},
    "SA": {"name": "Серия A (Италия)", "flag": "🇮🇹", "code": "SA"},
}

# --- СЛОВАРЬ ПЕРЕВОДА НАЗВАНИЙ КОМАНД (можно дополнить) ---
TEAM_TRANSLATIONS = {
    # Дополните своими переводами, если нужно
}

def translate_team(name: str) -> str:
    return TEAM_TRANSLATIONS.get(name, name)

# --- РАБОТА С БАЗОЙ ДАННЫХ (PostgreSQL) ---
def get_db_connection():
    return psycopg2.connect(DATABASE_URL, sslmode='require')

def normalize_score(score_str):
    if not score_str or score_str == "-":
        return score_str
    s = re.sub(r'\s+', '', score_str)
    parts = s.split(':')
    if len(parts) >= 2:
        try:
            home = int(parts[0])
            away = int(parts[1])
            return f"{home}:{away}"
        except ValueError:
            return score_str
    return score_str

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    # ... создание таблиц users, leagues и т.д. ...

    cur.execute('''
        CREATE TABLE IF NOT EXISTS matches (
            match_id INTEGER PRIMARY KEY,
            home TEXT,
            away TEXT,
            day TEXT,
            result TEXT,
            start_time TEXT,
            api_id TEXT UNIQUE,
            current_result TEXT,
            league_id TEXT REFERENCES leagues(league_id)
        )
    ''')

    # Миграция для существующих таблиц
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='matches' AND column_name='league_id'")
    if not cur.fetchone():
        cur.execute("ALTER TABLE matches ADD COLUMN league_id TEXT REFERENCES leagues(league_id)")
        conn.commit()
        print("✅ Добавлена колонка league_id")

    # Остальные таблицы predictions, scores, admins...
    # ...
    conn.commit()
    cur.close()
    conn.close()
    print("База данных инициализирована.")

def is_admin(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM admins WHERE user_id=%s", (user_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row is not None

def get_user(user_id, username, first_name):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO users (user_id, username, first_name) VALUES (%s,%s,%s) ON CONFLICT (user_id) DO NOTHING",
                (user_id, username, first_name))
    cur.execute("INSERT INTO scores (user_id, score) VALUES (%s,0) ON CONFLICT (user_id) DO NOTHING", (user_id,))
    conn.commit()
    cur.close()
    conn.close()

def save_prediction(user_id, match_id, prediction):
    normalized = normalize_score(prediction)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO predictions (user_id, match_id, prediction) VALUES (%s,%s,%s) ON CONFLICT (user_id, match_id) DO UPDATE SET prediction=EXCLUDED.prediction",
                (user_id, match_id, normalized))
    conn.commit()
    cur.close()
    conn.close()

def get_user_predictions(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        SELECT m.match_id, m.home, m.away, p.prediction, m.result
        FROM predictions p
        JOIN matches m ON p.match_id = m.match_id
        WHERE p.user_id = %s
    ''', (user_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

def get_match(match_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT match_id, home, away, day, result, start_time, api_id, current_result, league_id FROM matches WHERE match_id=%s", (match_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row

def get_next_match_id():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(MAX(match_id),0) FROM matches")
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] + 1

def add_match_from_api(api_id, home, away, start_time, league_id, day=None):
    if not day:
        dt = datetime.strptime(start_time, "%Y-%m-%d %H:%M")
        day_eng = dt.strftime("%a")
    else:
        day_eng = day
    match_id = get_next_match_id()
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute('''
            INSERT INTO matches (match_id, home, away, day, start_time, api_id, league_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
        ''', (match_id, home, away, day_eng, start_time, str(api_id), league_id))
        conn.commit()
        cur.close()
        conn.close()
        return match_id
    except psycopg2.IntegrityError:
        conn.rollback()
        cur.close()
        conn.close()
        return None

def add_match_manual(home, away, start_time, league_id):
    dt = datetime.strptime(start_time, "%Y-%m-%d %H:%M")
    day_eng = dt.strftime("%a")
    match_id = get_next_match_id()
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO matches (match_id, home, away, day, start_time, league_id)
        VALUES (%s,%s,%s,%s,%s,%s)
    ''', (match_id, home, away, day_eng, start_time, league_id))
    conn.commit()
    cur.close()
    conn.close()
    return match_id

def set_result(match_id, result):
    normalized = normalize_score(result)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE matches SET result=%s WHERE match_id=%s", (normalized, match_id))
    conn.commit()
    cur.close()
    conn.close()
    recalc_all_scores()

def set_current_result(match_id, current_result):
    normalized = normalize_score(current_result)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE matches SET current_result=%s WHERE match_id=%s", (normalized, match_id))
    conn.commit()
    cur.close()
    conn.close()
    
def reset_result(match_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE matches SET result=NULL WHERE match_id=%s", (match_id,))
    conn.commit()
    cur.close()
    conn.close()
    recalc_all_scores()

def recalc_all_scores():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users")
    users = cur.fetchall()
    for (user_id,) in users:
        cur.execute('''
            SELECT p.prediction, m.result
            FROM predictions p
            JOIN matches m ON p.match_id = m.match_id
            WHERE p.user_id = %s AND m.result IS NOT NULL
        ''', (user_id,))
        rows = cur.fetchall()
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
        cur.execute("INSERT INTO scores (user_id, score) VALUES (%s,%s) ON CONFLICT (user_id) DO UPDATE SET score=EXCLUDED.score",
                    (user_id, total))
    conn.commit()
    cur.close()
    conn.close()

def get_all_matches(league_id=None):
    conn = get_db_connection()
    cur = conn.cursor()
    if league_id:
        cur.execute("SELECT match_id, home, away, day, result, start_time, api_id, current_result, league_id FROM matches WHERE league_id=%s ORDER BY match_id", (league_id,))
    else:
        cur.execute("SELECT match_id, home, away, day, result, start_time, api_id, current_result, league_id FROM matches ORDER BY match_id")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

def get_active_matches(league_id=None):
    conn = get_db_connection()
    cur = conn.cursor()
    if league_id:
        cur.execute("SELECT match_id, home, away, day, result, start_time, api_id, current_result, league_id FROM matches WHERE result IS NULL AND league_id=%s ORDER BY match_id", (league_id,))
    else:
        cur.execute("SELECT match_id, home, away, day, result, start_time, api_id, current_result, league_id FROM matches WHERE result IS NULL ORDER BY match_id")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

def get_active_matches_with_user_prediction(user_id, league_id=None):
    conn = get_db_connection()
    cur = conn.cursor()
    if league_id:
        cur.execute('''
            SELECT m.match_id, m.home, m.away, m.day, m.start_time, m.api_id, m.current_result, p.prediction
            FROM matches m
            LEFT JOIN predictions p ON m.match_id = p.match_id AND p.user_id = %s
            WHERE m.result IS NULL AND m.league_id = %s
            ORDER BY m.match_id
        ''', (user_id, league_id))
    else:
        cur.execute('''
            SELECT m.match_id, m.home, m.away, m.day, m.start_time, m.api_id, m.current_result, p.prediction
            FROM matches m
            LEFT JOIN predictions p ON m.match_id = p.match_id AND p.user_id = %s
            WHERE m.result IS NULL
            ORDER BY m.match_id
        ''', (user_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

def get_scores():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        SELECT s.user_id, s.score, u.first_name, u.username
        FROM scores s
        JOIN users u ON s.user_id = u.user_id
        ORDER BY s.score DESC
    ''')
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

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

def is_match_started(start_time_str):
    try:
        start_dt = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M")
        start_dt = TIMEZONE.localize(start_dt)
    except Exception:
        return False
    now = datetime.now(TIMEZONE)
    return now >= start_dt

def is_match_finished(start_time_str):
    try:
        start_dt = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M")
        start_dt = TIMEZONE.localize(start_dt)
    except Exception:
        return False
    now = datetime.now(TIMEZONE)
    finish_dt = start_dt + timedelta(hours=2)
    return now > finish_dt

# --- ФУНКЦИИ ДЛЯ РАБОТЫ С API ---
def fetch_matches_from_api(league_code, days_ahead=7):
    if not FOOTBALL_API_KEY:
        return []
    now = datetime.now(TIMEZONE)
    date_from = now.strftime("%Y-%m-%d")
    date_to = (now + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    url = f"https://api.football-data.org/v4/competitions/{league_code}/matches?dateFrom={date_from}&dateTo={date_to}"
    headers = {"X-Auth-Token": FOOTBALL_API_KEY}
    try:
        import requests
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 429:
            print(f"API error 429 для лиги {league_code}")
            return []
        if response.status_code != 200:
            print(f"API error {response.status_code} для {league_code}")
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
        print(f"Ошибка при получении матчей для {league_code}: {e}")
        return []

def update_matches_from_api_for_league(league_id):
    league_info = LEAGUES.get(league_id)
    if not league_info:
        return 0
    matches = fetch_matches_from_api(league_info["code"], days_ahead=7)
    added = 0
    for m in matches:
        if add_match_from_api(m["api_id"], m["home"], m["away"], m["start_time"], league_id):
            added += 1
    return added

def update_matches_from_api():
    total = 0
    for lid in LEAGUES:
        total += update_matches_from_api_for_league(lid)
    return total

def fetch_match_details_by_api_id(api_id):
    if not FOOTBALL_API_KEY:
        return None
    url = f"https://api.football-data.org/v4/matches/{api_id}"
    headers = {"X-Auth-Token": FOOTBALL_API_KEY}
    try:
        import requests
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 429:
            print(f"API error 429 для матча {api_id}")
            return None
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
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT match_id, api_id, start_time FROM matches WHERE result IS NULL AND api_id IS NOT NULL")
    matches = cur.fetchall()
    cur.close()
    conn.close()

    now = datetime.now(TIMEZONE)
    started_matches = []
    for match_id, api_id, start_time_str in matches:
        try:
            start_dt = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M")
            start_dt = TIMEZONE.localize(start_dt)
        except Exception:
            continue
        if now >= start_dt:
            started_matches.append((match_id, api_id, start_time_str))

    if not started_matches:
        return 0

    updated = 0
    for match_id, api_id, start_time_str in started_matches:
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

# --- ГЕНЕРАЦИЯ ОТЧЁТА (с поддержкой лиг) ---
def generate_report(league_id=None):
    conn = get_db_connection()
    cur = conn.cursor()
    if league_id:
        cur.execute("SELECT match_id, home, away, result, start_time, current_result, league_id FROM matches WHERE league_id=%s ORDER BY match_id", (league_id,))
    else:
        cur.execute("SELECT match_id, home, away, result, start_time, current_result, league_id FROM matches ORDER BY match_id")
    matches = cur.fetchall()
    if not matches:
        cur.close()
        conn.close()
        return "Нет матчей в базе.", None

    cur.execute("SELECT DISTINCT p.user_id, u.username, u.first_name FROM predictions p JOIN users u ON p.user_id = u.user_id")
    users = cur.fetchall()
    if not users:
        cur.close()
        conn.close()
        return "Нет прогнозов от пользователей.", None

    users_data = {}
    for user_id, username, first_name in users:
        users_data[user_id] = {
            "username": username,
            "first_name": first_name,
            "predictions": {},
            "total_score": 0
        }

    for user_id in users_data:
        cur.execute("SELECT match_id, prediction FROM predictions WHERE user_id=%s", (user_id,))
        preds = cur.fetchall()
        for match_id, pred in preds:
            users_data[user_id]["predictions"][match_id] = pred

    # Считаем очки
    for match in matches:
        match_id, home, away, result, start_time, current_result, league = match
        if result is not None:
            for user_id, data in users_data.items():
                pred = data["predictions"].get(match_id)
                if pred:
                    points = 0
                    if pred == result:
                        points = 6
                    else:
                        pred_score = parse_score(pred)
                        res_score = parse_score(result)
                        if pred_score and res_score:
                            if (pred_score[0] - pred_score[1]) == (res_score[0] - res_score[1]):
                                points = 3
                            elif get_outcome(pred_score[0], pred_score[1]) == get_outcome(res_score[0], res_score[1]):
                                points = 2
                    data["total_score"] += points

    # Заголовки
    header = "Пользователь"
    match_labels = []
    for m in matches:
        match_id, home, away, result, start_time, current_result, league = m
        if result is not None:
            status = "✅"
            score_display = normalize_score(result)
            label = f"{home}–{away} ({score_display})"
        elif current_result is not None:
            status = "⏳"
            score_display = normalize_score(current_result)
            label = f"{home}–{away} ({score_display})"
        else:
            try:
                start_dt = datetime.strptime(start_time, "%Y-%m-%d %H:%M")
                start_dt = TIMEZONE.localize(start_dt)
                now = datetime.now(TIMEZONE)
                if now >= start_dt:
                    status = "⏳"
                else:
                    status = "⏱️"
            except:
                status = "⏱️"
            label = f"{home}–{away}"
        header += f" | {label}{status}"
        match_labels.append(label)

    header += " | Итого"
    lines = [header]
    sep = "-" * len(header)
    lines.append(sep)

    for user_id, data in users_data.items():
        name = f"@{data['username']}" if data['username'] else data['first_name']
        if not name:
            name = str(user_id)
        row = name
        for match_id, label in zip([m[0] for m in matches], match_labels):
            pred = data["predictions"].get(match_id, "-")
            pred_display = normalize_score(pred) if pred != "-" else "-"
            row += f" | {pred_display}"
        row += f" | {data['total_score']}"
        lines.append(row)

    if league_id:
        league_name = LEAGUES.get(league_id, {}).get("name", league_id)
        text_report = f"📊 *ТАБЛИЦА ПРОГНОЗОВ – {league_name}*\n\n" + "\n".join(lines)
    else:
        text_report = "📊 *ТАБЛИЦА ПРОГНОЗОВ (все матчи)*\n\n" + "\n".join(lines)

    # Excel
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Прогнозы"
    headers = ["Пользователь"] + match_labels + ["Итого"]
    ws.append(headers)

    for user_id, data in users_data.items():
        name = f"@{data['username']}" if data['username'] else data['first_name']
        if not name:
            name = str(user_id)
        row_data = [name]
        for match_id in [m[0] for m in matches]:
            pred = data["predictions"].get(match_id, "-")
            pred_display = normalize_score(pred) if pred != "-" else "-"
            row_data.append(pred_display)
        row_data.append(data["total_score"])
        ws.append(row_data)

    # Стили
    for col in ws.columns:
        max_length = 0
        for cell in col:
            try:
                if len(str(cell.value)) > max_length:
                    max_length = len(str(cell.value))
            except:
                pass
        adjusted_width = min(max_length + 2, 50)
        ws.column_dimensions[col[0].column_letter].width = adjusted_width

    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal='center')

    for row in ws.iter_rows():
        for cell in row:
            cell.alignment = Alignment(horizontal='center', vertical='center')

    excel_data = BytesIO()
    wb.save(excel_data)
    excel_data.seek(0)

    cur.close()
    conn.close()
    return text_report, excel_data

# --- ОБРАБОТЧИКИ КОМАНД ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, user.username, user.first_name)
    await show_main_menu(update, context, "Добро пожаловать! Выберите действие:")

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, text="Главное меню:"):
    keyboard = []
    # Лига чемпионов отдельно
    keyboard.append([InlineKeyboardButton("🏆 Лига чемпионов", callback_data="league_UCL")])
    # Остальные лиги – в подменю
    keyboard.append([InlineKeyboardButton("⚽ Прогнозы лиг", callback_data="leagues_submenu")])
    # Общие кнопки
    keyboard.append([InlineKeyboardButton("📊 Общий отчёт", callback_data="report_all")])
    keyboard.append([InlineKeyboardButton("🏅 Таблица лидеров", callback_data="leaderboard")])
    keyboard.append([InlineKeyboardButton("📝 Мои прогнозы", callback_data="mypredicts")])
    if is_admin(update.effective_user.id):
        keyboard.append([InlineKeyboardButton("⚙️ Админ-панель", callback_data="admin_panel")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)

async def show_leagues_submenu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    other_leagues = ["PL", "PD", "FL1", "BL1", "SA"]
    keyboard = []
    for lid in other_leagues:
        info = LEAGUES[lid]
        keyboard.append([InlineKeyboardButton(f"{info['flag']} {info['name']}", callback_data=f"league_{lid}")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="menu")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        await update.callback_query.edit_message_text("Выберите лигу для прогнозов:", reply_markup=reply_markup)
    else:
        await update.message.reply_text("Выберите лигу для прогнозов:", reply_markup=reply_markup)

async def show_league_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, league_id):
    context.user_data["current_league"] = league_id
    rows = get_active_matches(league_id)
    if not rows:
        text = f"📋 *{LEAGUES[league_id]['flag']} {LEAGUES[league_id]['name']}*\n\nНет активных матчей."
    else:
        text = f"📋 *{LEAGUES[league_id]['flag']} {LEAGUES[league_id]['name']}*\n\n"
        for m in rows:
            match_id, home, away, day, result, start_time, api_id, current_result, league = m
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
    keyboard = [
        [InlineKeyboardButton("✏️ Сделать прогноз", callback_data=f"predict_{league_id}")],
        [InlineKeyboardButton("📊 Отчёт по лиге", callback_data=f"report_league_{league_id}")],
        [InlineKeyboardButton("🔙 Назад", callback_data="menu" if league_id == "UCL" else "leagues_submenu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")

async def show_predict_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, league_id):
    user = update.effective_user
    rows = get_active_matches_with_user_prediction(user.id, league_id)
    print(f"🔍 Найдено матчей для лиги {league_id}: {len(rows)}")
    for row in rows:
        match_id, home, away, day, start_time, api_id, current_result, prediction = row
        print(f"  Матч {match_id}: {home} – {away}, start_time={start_time}, is_open={is_match_open(start_time)}")
    
    keyboard = []
    for row in rows:
        match_id, home, away, day, start_time, api_id, current_result, prediction = row
        # Для Лиги чемпионов показываем все матчи (без фильтра по времени)
        if league_id == "UCL":
            if start_time:  # просто проверяем, что время задано
                label = f"{match_id}. {home} – {away}"
                if prediction:
                    label += f" ✅ ({prediction})"
                keyboard.append([InlineKeyboardButton(label, callback_data=f"pred_{match_id}")])
        else:
            # Для остальных лиг – только открытые
            if start_time and is_match_open(start_time):
                label = f"{match_id}. {home} – {away}"
                if prediction:
                    label += f" ✅ ({prediction})"
                keyboard.append([InlineKeyboardButton(label, callback_data=f"pred_{match_id}")])
    
    if not keyboard:
        keyboard.append([InlineKeyboardButton("Нет доступных матчей", callback_data="noop")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data=f"league_{league_id}")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        await update.callback_query.edit_message_text("Выберите матч для прогноза:", reply_markup=reply_markup)
    else:
        await update.message.reply_text("Выберите матч для прогноза:", reply_markup=reply_markup)

# --- ОБРАБОТЧИК КНОПОК ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    print(f"🔔 Получен callback: {data}")  # Логирование в консоль

    await query.answer()

    if data == "menu":
        await show_main_menu(update, context)
    elif data == "leagues_submenu":
        await show_leagues_submenu(update, context)
    elif data.startswith("league_"):
        league_id = data.split("_")[1]
        await show_league_menu(update, context, league_id)
    elif data.startswith("predict_"):
        league_id = data.split("_")[1]
        await show_predict_menu(update, context, league_id)
    elif data.startswith("pred_"):
        match_id = int(data.split("_")[1])
        match = get_match(match_id)
        if not match:
            await query.edit_message_text("Матч не найден.")
            return
        match_id, home, away, day, result, start_time, api_id, current_result, league_id = match
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
    elif data.startswith("report_league_"):
        league_id = data.split("_")[2]
        text_report, excel_data = generate_report(league_id)
        if excel_data is None:
            await query.edit_message_text(text_report)
            return
        if len(text_report) > 4000:
            text_report = text_report[:3900] + "\n... (остальное в Excel файле)"
        await query.edit_message_text(text_report)
        try:
            await query.message.reply_document(
                document=excel_data,
                filename=f"report_{league_id}_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
                caption=f"📊 Отчёт по лиге {LEAGUES[league_id]['name']}"
            )
        except Exception as e:
            await query.edit_message_text(f"Ошибка отправки Excel: {e}")
    elif data == "report_all":
        text_report, excel_data = generate_report()
        if excel_data is None:
            await query.edit_message_text(text_report)
            return
        if len(text_report) > 4000:
            text_report = text_report[:3900] + "\n... (остальное в Excel файле)"
        await query.edit_message_text(text_report)
        try:
            await query.message.reply_document(
                document=excel_data,
                filename=f"report_all_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
                caption="📊 Общий отчёт по всем лигам"
            )
        except Exception as e:
            await query.edit_message_text(f"Ошибка отправки Excel: {e}")
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
    elif data == "admin_panel":
        if not is_admin(query.from_user.id):
            await query.edit_message_text("У вас нет прав.")
            return
        keyboard = [
            [InlineKeyboardButton("📥 Загрузить матчи всех лиг", callback_data="fetch_all")],
            [InlineKeyboardButton("🔄 Обновить результаты", callback_data="fetch_results")],
            [InlineKeyboardButton("🗑️ Сбросить всё", callback_data="reset_confirm")],
            [InlineKeyboardButton("🔙 Назад", callback_data="menu")]
        ]
        await query.edit_message_text("⚙️ *Админ-панель*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    elif data == "fetch_all":
        if not is_admin(query.from_user.id):
            return
        await query.edit_message_text("⏳ Загружаю матчи всех лиг...")
        total = update_matches_from_api()
        await query.edit_message_text(f"✅ Добавлено матчей: {total}.")
    elif data == "fetch_results":
        if not is_admin(query.from_user.id):
            return
        await query.edit_message_text("⏳ Обновляю результаты...")
        updated = update_results_from_api()
        await query.edit_message_text(f"✅ Обновлено результатов: {updated}.")
    elif data == "reset_confirm":
        if not is_admin(query.from_user.id):
            return
        keyboard = [
            [InlineKeyboardButton("✅ Да, удалить всё", callback_data="reset_execute")],
            [InlineKeyboardButton("❌ Отмена", callback_data="admin_panel")]
        ]
        await query.edit_message_text(
            "⚠️ *Вы уверены?*\n\n"
            "Эта команда удалит ВСЕ прогнозы и результаты матчей.\n"
            "Пользователи и список матчей сохранятся.\n\n"
            "Данные будут потеряны безвозвратно!",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    elif data == "reset_execute":
        if not is_admin(query.from_user.id):
            return
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM predictions")
        cur.execute("UPDATE matches SET result = NULL")
        conn.commit()
        cur.close()
        conn.close()
        recalc_all_scores()
        await query.edit_message_text("✅ Все прогнозы и результаты удалены. Очки сброшены до 0.")
    elif data == "noop":
        await query.edit_message_text("Нет доступных матчей.")
    else:
        await query.edit_message_text("⚠️ Неизвестная команда. Попробуйте снова.")

# --- ОБРАБОТЧИК ТЕКСТА ---
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
    match_id, home, away, day, result, start_time, api_id, current_result, league_id = match
    if result is not None:
        await update.message.reply_text("Этот матч уже завершён, прогнозы не принимаются.")
        context.user_data.pop("awaiting_score", None)
        return
    if not is_match_open(start_time):
        await update.message.reply_text("Приём прогнозов на этот матч уже закрыт (за 10 минут до начала).")
        context.user_data.pop("awaiting_score", None)
        return
    if not re.match(r'^\d+\s*[:;-]\s*\d+$', text) and not re.match(r'^\d+\s*[-]\s*\d+$', text):
        await update.message.reply_text("Неверный формат. Введите счёт в формате 2:1 или 2-1.")
        return
    score = re.sub(r'\s*[:-]\s*', ':', text)
    score = re.sub(r'\s*[-]\s*', ':', score)
    save_prediction(user.id, match_id, normalize_score(score))
    await update.message.reply_text(f"✅ Ваш прогноз на матч #{match_id} ({home} – {away}) сохранён: {score}")
    context.user_data.pop("awaiting_score", None)
    await show_predict_menu(update, context, league_id)

# --- АДМИН-КОМАНДЫ ---
async def addmatch_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("У вас нет прав.")
        return
    context.user_data["addmatch_step"] = 1
    await update.message.reply_text(
        "Введите название команды ХОЗЯЕВ:\n"
        "Для отмены введите /cancel"
    )

async def addmatch_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "addmatch_step" in context.user_data:
        context.user_data.pop("addmatch_step", None)
        context.user_data.pop("addmatch_home", None)
        context.user_data.pop("addmatch_away", None)
        context.user_data.pop("addmatch_league", None)
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
        await update.message.reply_text("Введите название команды ГОСТЕЙ:\nДля отмены /cancel")
    elif step == 2:
        context.user_data["addmatch_away"] = text
        context.user_data["addmatch_step"] = 3
        await update.message.reply_text(
            "Введите код лиги (PL, PD, FL1, BL1, SA, UCL):\n"
            "Для отмены /cancel"
        )
    elif step == 3:
        league_id = text.upper()
        if league_id not in LEAGUES:
            await update.message.reply_text("Неверный код. Доступны: PL, PD, FL1, BL1, SA, UCL.\nПопробуйте снова.")
            return
        context.user_data["addmatch_league"] = league_id
        context.user_data["addmatch_step"] = 4
        await update.message.reply_text(
            "Введите дату и время начала в формате:\n"
            "ГГГГ-ММ-ДД ЧЧ:ММ (например, 2026-09-15 21:00)"
        )
    elif step == 4:
        try:
            datetime.strptime(text, "%Y-%m-%d %H:%M")
        except ValueError:
            await update.message.reply_text("Неверный формат. Попробуйте снова.")
            return
        home = context.user_data["addmatch_home"]
        away = context.user_data["addmatch_away"]
        league_id = context.user_data["addmatch_league"]
        start_time = text
        match_id = add_match_manual(home, away, start_time, league_id)
        context.user_data.pop("addmatch_step", None)
        context.user_data.pop("addmatch_home", None)
        context.user_data.pop("addmatch_away", None)
        context.user_data.pop("addmatch_league", None)
        await update.message.reply_text(
            f"✅ Матч #{match_id} добавлен в лигу {LEAGUES[league_id]['name']}:\n"
            f"{home} – {away}\n"
            f"Начало: {start_time}"
        )

async def set_result_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("У вас нет прав.")
        return
    args = context.args
    if len(args) != 2:
        await update.message.reply_text("Использование: /setresult <id> <счёт>")
        return
    try:
        match_id = int(args[0])
        result = args[1]
        result = re.sub(r'\s*[:-]\s*', ':', result)
        result = re.sub(r'\s*[-]\s*', ':', result)
        if not re.match(r'^\d+:\d+$', result):
            raise ValueError
    except:
        await update.message.reply_text("Неверный формат. Используйте /setresult <id> 2:1")
        return
    match = get_match(match_id)
    if not match:
        await update.message.reply_text("Матч не найден.")
        return
    if match[4] is not None:
        await update.message.reply_text("Результат уже установлен.")
        return
    result = normalize_score(result)
    set_result(match_id, result)
    await update.message.reply_text(f"Результат матча #{match_id} установлен: {result}. Очки пересчитаны.")

async def reset_result_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("У вас нет прав.")
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

async def admins_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("У вас нет прав.")
        return
    admins = get_admins_list()
    if not admins:
        await update.message.reply_text("Список администраторов пуст.")
        return
    text = "👑 *Список администраторов:*\n\n"
    for i, (user_id, username, first_name) in enumerate(admins, 1):
        role = " (главный)" if user_id == MAIN_ADMIN_ID else ""
        name = f"@{username}" if username else (first_name if first_name else f"ID: {user_id}")
        text += f"{i}. {name}{role}\n"
    await update.message.reply_text(text, parse_mode="Markdown")

def get_admins_list():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        SELECT a.user_id, u.username, u.first_name
        FROM admins a
        LEFT JOIN users u ON a.user_id = u.user_id
    ''')
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

async def addadmin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("У вас нет прав.")
        return
    args = context.args
    if len(args) != 1:
        await update.message.reply_text("Использование: /addadmin <user_id>")
        return
    try:
        new_admin_id = int(args[0])
    except ValueError:
        await update.message.reply_text("Введите корректный числовой ID.")
        return
    if is_admin(new_admin_id):
        await update.message.reply_text("Этот пользователь уже является администратором.")
        return
    add_admin(new_admin_id)
    await update.message.reply_text(f"✅ Пользователь с ID {new_admin_id} добавлен.")

async def removeadmin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("У вас нет прав.")
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
    await update.message.reply_text(f"✅ Пользователь с ID {admin_id} удалён.")

async def fetch_matches_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("У вас нет прав.")
        return
    if not FOOTBALL_API_KEY:
        await update.message.reply_text("API-ключ не настроен.")
        return
    await update.message.reply_text("⏳ Загружаю матчи всех лиг...")
    total = update_matches_from_api()
    await update.message.reply_text(f"✅ Добавлено новых матчей: {total}.")

async def fetch_results_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("У вас нет прав.")
        return
    if not FOOTBALL_API_KEY:
        await update.message.reply_text("API-ключ не настроен.")
        return
    await update.message.reply_text("⏳ Обновляю результаты...")
    updated = update_results_from_api()
    await update.message.reply_text(f"✅ Обновлено результатов: {updated}.")

async def report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("У вас нет прав.")
        return
    await update.message.reply_text("⏳ Генерирую отчёт...")
    text_report, excel_data = generate_report()
    if excel_data is None:
        await update.message.reply_text(text_report)
        return
    if len(text_report) > 4000:
        text_report = text_report[:3900] + "\n... (остальное в Excel файле)"
    await update.message.reply_text(text_report, parse_mode="Markdown")
    try:
        await update.message.reply_document(
            document=excel_data,
            filename=f"report_all_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
            caption="📊 Общий отчёт по всем лигам"
        )
    except Exception as e:
        await update.message.reply_text(f"Ошибка отправки Excel: {e}")

async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Неизвестная команда. Используйте /start.")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_admin(user.id) and "addmatch_step" in context.user_data:
        await handle_addmatch_text(update, context)
        return
    if "awaiting_score" in context.user_data:
        await handle_score_input(update, context)
        return

# --- ГЛАВНАЯ ---
def main():
    init_db()
    if FOOTBALL_API_KEY:
        try:
            total = update_matches_from_api()
            print(f"При старте добавлено {total} матчей.")
            updated = update_results_from_api()
            print(f"При старте обновлено {updated} результатов.")
        except Exception as e:
            print(f"Ошибка при стартовом обновлении: {e}")
    else:
        print("API-ключ не задан.")

    scheduler = BackgroundScheduler()
    scheduler.add_job(
        func=update_results_from_api,
        trigger=IntervalTrigger(minutes=10),
        id='auto_update_results',
        name='Обновление результатов',
        replace_existing=True
    )
    scheduler.start()
    print("Планировщик запущен.")

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
    app.add_handler(CommandHandler("report", report_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.COMMAND, unknown))

    print("Бот запущен...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
