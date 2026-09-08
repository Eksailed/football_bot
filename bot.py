import logging
import os
import re
import csv
import io
from datetime import datetime, timedelta
import pytz
import psycopg2
from psycopg2.extras import RealDictCursor
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

# --- ПОДКЛЮЧЕНИЕ К POSTGRESQL ---
DATABASE_URL = os.environ.get("DATABASE_UR")
if not DATABASE_URL:
    raise ValueError("DATABASE_UR не задан! Подключите PostgreSQL.")

TIMEZONE = pytz.timezone("Europe/Moscow")
SHORT_DAYS = {
    "Mon": "Пн", "Tue": "Вт", "Wed": "Ср",
    "Thu": "Чт", "Fri": "Пт", "Sat": "Сб", "Sun": "Вс"
}

# --- СЛОВАРЬ ПЕРЕВОДА НАЗВАНИЙ КОМАНД ---
TEAM_TRANSLATIONS = {
    "AEK Athens": "АЕК Афины",
    "PAE AEK": "АЕК Афины",
    "LASK": "ЛАСК",
    "LASK Linz": "ЛАСК",
    "Club Brugge": "Брюгге",
    "Club Brugge KV": "Брюгге",
    "Aston Villa": "Астон Вилла",
    "Aston Villa FC": "Астон Вилла",
    "Borussia Dortmund": "Боруссия Дортмунд",
    "B. Dortmund": "Боруссия Дортмунд",
    "Villarreal": "Вильярреал",
    "Villarreal CF": "Вильярреал",
    "Lille": "Лилль",
    "Lille OSC": "Лилль",
    "Real Betis": "Реал Бетис",
    "Real Betis Balompié": "Реал Бетис",
    "Porto": "Порту",
    "FC Porto": "Порту",
    "Manchester City": "Манчестер Сити",
    "Manchester City FC": "Манчестер Сити",
    "Man City": "Манчестер Сити",
    "Real Madrid": "Реал Мадрид",
    "Real Madrid CF": "Реал Мадрид",
    "Inter": "Интер",
    "FC Internazionale Milano": "Интер",
    "Barcelona": "Барселона",
    "FC Barcelona": "Барселона",
    "Feyenoord": "Фейеноорд",
    "Feyenoord Rotterdam": "Фейеноорд",
    "Stuttgart": "Штутгарт",
    "VfB Stuttgart": "Штутгарт",
    "Viking": "Викинг",
    "Viking FK": "Викинг",
    "Liverpool": "Ливерпуль",
    "Liverpool FC": "Ливерпуль",
    "Atletico Madrid": "Атлетико Мадрид",
    "Atleti": "Атлетико",
    "Club Atlético de Madrid": "Атлетико Мадрид",
    "Napoli": "Наполи",
    "SSC Napoli": "Наполи",
    "Arsenal": "Арсенал",
    "Arsenal FC": "Арсенал",
    "Paris Saint-Germain": "Пари Сен-Жермен",
    "Paris": "Пари Сен-Жермен",
    "Paris Saint-Germain FC": "Пари Сен-Жермен",
    "Slovan Bratislava": "Слован Братислава",
    "S. Bratislava": "Слован Братислава",
    "ŠK Slovan Bratislava": "Слован Братислава",
    "Sporting CP": "Спортинг Лиссабон",
    "Sporting": "Спортинг Лиссабон",
    "Sporting Clube de Portugal": "Спортинг Лиссабон",
    "Galatasaray": "Галатасарай",
    "Galatasaray SK": "Галатасарай",
    "Fenerbahçe": "Фенербахче",
    "Fenerbahce": "Фенербахче",
    "Fenerbahçe SK": "Фенербахче",
    "Roma": "Рома",
    "AS Roma": "Рома",
    "PSV": "ПСВ",
    "Shakhtar Donetsk": "Шахтёр",
    "Shakhtar": "Шахтёр",
    "FK Shakhtar Donetsk": "Шахтёр",
    "Bayern München": "Бавария",
    "Bayern Munich": "Бавария",
    "FC Bayern München": "Бавария",
    "Bodø/Glimt": "Будё-Глимт",
    "Bodo/Glimt": "Будё-Глимт",
    "FK Bodø/Glimt": "Будё-Глимт",
    "Como": "Комо",
    "Como 1907": "Комо",
    "RB Leipzig": "Лейпциг",
    "Leipzig": "Лейпциг",
    "Manchester United": "Манчестер Юнайтед",
    "Man Utd": "Манчестер Юнайтед",
    "Manchester United FC": "Манчестер Юнайтед",
    "Sabah": "Сабах",
    "Sabah FK": "Сабах",
    "Slavia Praha": "Славия Прага",
    "Slavia Prague": "Славия Прага",
    "SK Slavia Praha": "Славия Прага",
    "Lens": "Ланс",
    "Racing Club de Lens": "Ланс",
}

# --- ФЛАГИ КОМАНД ---
TEAM_FLAGS = {
    "АЕК Афины": "🇬🇷",
    "ЛАСК": "🇦🇹",
    "Брюгге": "🇧🇪",
    "Астон Вилла": "🏴󠁧󠁢󠁥󠁮󠁧󠁿",
    "Манчестер Сити": "🏴󠁧󠁢󠁥󠁮󠁧󠁿",
    "Ливерпуль": "🏴󠁧󠁢󠁥󠁮󠁧󠁿",
    "Арсенал": "🏴󠁧󠁢󠁥󠁮󠁧󠁿",
    "Манчестер Юнайтед": "🏴󠁧󠁢󠁥󠁮󠁧󠁿",
    "Боруссия Дортмунд": "🇩🇪",
    "Штутгарт": "🇩🇪",
    "Бавария": "🇩🇪",
    "Лейпциг": "🇩🇪",
    "Вильярреал": "🇪🇸",
    "Реал Бетис": "🇪🇸",
    "Реал Мадрид": "🇪🇸",
    "Атлетико Мадрид": "🇪🇸",
    "Барселона": "🇪🇸",
    "Лилль": "🇫🇷",
    "Пари Сен-Жермен": "🇫🇷",
    "Ланс": "🇫🇷",
    "Порту": "🇵🇹",
    "Спортинг Лиссабон": "🇵🇹",
    "Интер": "🇮🇹",
    "Наполи": "🇮🇹",
    "Рома": "🇮🇹",
    "Комо": "🇮🇹",
    "Фейеноорд": "🇳🇱",
    "ПСВ": "🇳🇱",
    "Викинг": "🇳🇴",
    "Будё-Глимт": "🇳🇴",
    "Слован Братислава": "🇸🇰",
    "Галатасарай": "🇹🇷",
    "Фенербахче": "🇹🇷",
    "Шахтёр": "🇺🇦",
    "Сабах": "🇦🇿",
    "Славия Прага": "🇨🇿",
}

def translate_team(name: str) -> str:
    return TEAM_TRANSLATIONS.get(name, name)

def get_team_flag(name: str) -> str:
    return TEAM_FLAGS.get(name, "")

# --- РАБОТА С БАЗОЙ ДАННЫХ (PostgreSQL) ---
def get_db_connection():
    return psycopg2.connect(DATABASE_URL, sslmode='require')

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            first_name TEXT
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS matches (
            match_id INTEGER PRIMARY KEY,
            home TEXT,
            away TEXT,
            day TEXT,
            result TEXT,
            start_time TEXT,
            api_id TEXT UNIQUE,
            current_result TEXT
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS predictions (
            user_id BIGINT,
            match_id INTEGER,
            prediction TEXT,
            PRIMARY KEY (user_id, match_id)
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS scores (
            user_id BIGINT PRIMARY KEY,
            score INTEGER DEFAULT 0
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS admins (
            user_id BIGINT PRIMARY KEY
        )
    ''')
    cur.execute("INSERT INTO admins (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (MAIN_ADMIN_ID,))
    conn.commit()
    cur.close()
    conn.close()
    print("База данных PostgreSQL инициализирована.")

def is_admin(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM admins WHERE user_id=%s", (user_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row is not None

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

def add_admin(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO admins (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (user_id,))
    conn.commit()
    cur.close()
    conn.close()
    return True

def remove_admin(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM admins WHERE user_id=%s", (user_id,))
    affected = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    return affected > 0

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
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO predictions (user_id, match_id, prediction) VALUES (%s,%s,%s) ON CONFLICT (user_id, match_id) DO UPDATE SET prediction=EXCLUDED.prediction",
                (user_id, match_id, prediction))
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
    cur.execute("SELECT match_id, home, away, day, result, start_time, api_id, current_result FROM matches WHERE match_id=%s", (match_id,))
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

def add_match_from_api(api_id, home, away, start_time, day=None):
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
            INSERT INTO matches (match_id, home, away, day, start_time, api_id)
            VALUES (%s,%s,%s,%s,%s,%s)
        ''', (match_id, home, away, day_eng, start_time, str(api_id)))
        conn.commit()
        cur.close()
        conn.close()
        return match_id
    except psycopg2.IntegrityError:
        conn.rollback()
        cur.close()
        conn.close()
        return None

def add_match_manual(home, away, start_time):
    dt = datetime.strptime(start_time, "%Y-%m-%d %H:%M")
    day_eng = dt.strftime("%a")
    match_id = get_next_match_id()
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO matches (match_id, home, away, day, start_time)
        VALUES (%s,%s,%s,%s,%s)
    ''', (match_id, home, away, day_eng, start_time))
    conn.commit()
    cur.close()
    conn.close()
    return match_id

def set_result(match_id, result):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE matches SET result=%s WHERE match_id=%s", (result, match_id))
    conn.commit()
    cur.close()
    conn.close()
    recalc_all_scores()

def set_current_result(match_id, current_result):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE matches SET current_result=%s WHERE match_id=%s", (current_result, match_id))
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

def get_all_matches():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT match_id, home, away, day, result, start_time, api_id, current_result FROM matches ORDER BY match_id")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

def get_active_matches():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT match_id, home, away, day, result, start_time, api_id, current_result FROM matches WHERE result IS NULL ORDER BY match_id")
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
def fetch_matches_from_api(days_ahead=7):
    if not FOOTBALL_API_KEY:
        return []
    now = datetime.now(TIMEZONE)
    date_from = now.strftime("%Y-%m-%d")
    date_to = (now + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    url = f"https://api.football-data.org/v4/competitions/CL/matches?dateFrom={date_from}&dateTo={date_to}"
    headers = {"X-Auth-Token": FOOTBALL_API_KEY}
    try:
        import requests
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 429:
            print("API error: слишком много запросов (429). Попробуйте позже.")
            return []
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
        import requests
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 429:
            print(f"API error 429 для матча {api_id}: слишком много запросов")
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

# --- ГЕНЕРАЦИЯ ОТЧЁТА ---
def generate_report():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT match_id, home, away, start_time, result, current_result FROM matches ORDER BY start_time")
    matches = cur.fetchall()
    if not matches:
        cur.close()
        conn.close()
        return "Нет матчей в базе.", None

    cur.execute("SELECT user_id, username, first_name FROM users")
    users = {row[0]: {"username": row[1], "first_name": row[2]} for row in cur.fetchall()}

    report_lines = []
    csv_lines = [["Матч", "Команды", "Статус", "Счёт", "Пользователь", "Прогноз", "Очки"]]

    for match_id, home, away, start_time, result, current_result in matches:
        if result is not None:
            status = "Завершён"
            score = result
        elif current_result is not None:
            status = "Идёт"
            score = current_result
        else:
            try:
                start_dt = datetime.strptime(start_time, "%Y-%m-%d %H:%M")
                start_dt = TIMEZONE.localize(start_dt)
                now = datetime.now(TIMEZONE)
                if now >= start_dt:
                    status = "Идёт (счёт неизвестен)"
                else:
                    status = "Не начат"
            except:
                status = "Не начат"
            score = "-"

        report_lines.append(f"Матч #{match_id}: {home} – {away} ({status}, счёт: {score})")
        cur.execute("SELECT user_id, prediction FROM predictions WHERE match_id=%s", (match_id,))
        preds = cur.fetchall()
        if preds:
            for user_id, pred in preds:
                user_info = users.get(user_id, {})
                username = user_info.get("username")
                first_name = user_info.get("first_name")
                name = f"@{username}" if username else (first_name if first_name else str(user_id))
                if result is not None:
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
                    report_lines.append(f"  {name}: {pred} → очки: {points}")
                    csv_lines.append([f"#{match_id}", f"{home} – {away}", status, score, name, pred, points])
                else:
                    report_lines.append(f"  {name}: {pred} (ожидание)")
                    csv_lines.append([f"#{match_id}", f"{home} – {away}", status, score, name, pred, "-"])
            report_lines.append("")
        else:
            report_lines.append("  Нет прогнозов.\n")

    cur.close()
    conn.close()

    text_report = "📊 *ОТЧЁТ ПО ВСЕМ МАТЧАМ*\n\n" + "\n".join(report_lines)
    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    writer.writerows(csv_lines)
    csv_data = output.getvalue()
    output.close()
    return text_report, csv_data

# --- ОБРАБОТЧИКИ (кнопки и команды) ---
# (здесь идут все async обработчики – они остаются без изменений)
# Для краткости я опускаю их в этом сообщении, но вы должны оставить свои рабочие обработчики.
# В финальном коде они есть.

# ... (весь остальной код обработчиков, такой же как был)

# --- ГЛАВНАЯ (синхронная) ---
def main():
    init_db()
    if FOOTBALL_API_KEY:
        try:
            added = update_matches_from_api()
            print(f"При старте добавлено {added} матчей.")
            updated = update_results_from_api()
            print(f"При старте обновлено {updated} финальных результатов.")
        except Exception as e:
            print(f"Ошибка при стартовом обновлении: {e}")
    else:
        print("API-ключ не задан, матчи не загружены. Используйте /fetchmatches после добавления ключа.")

    scheduler = BackgroundScheduler()
    scheduler.add_job(
        func=update_results_from_api,
        trigger=IntervalTrigger(minutes=10),
        id='auto_update_results',
        name='Обновление счетов и результатов',
        replace_existing=True
    )
    scheduler.start()
    print("Планировщик запущен (обновление каждые 10 минут, только во время матчей).")

    app = Application.builder().token(TOKEN).build()
    # Регистрируем все обработчики (добавьте их сюда)
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
