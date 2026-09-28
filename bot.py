import logging
import os
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

import html
import re
import csv
import io
import time
from datetime import datetime, timedelta
import pytz
import psycopg2
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, MenuButtonCommands
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill, Border, Side
from io import BytesIO
from unl_fixtures_2027 import REAL_UNL_FIXTURES

# --- НАСТРОЙКИ ---
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("BOT_TOKEN") or os.environ.get("TOKEN")
if not TOKEN:
    raise ValueError("Токен не найден! Проверьте переменную окружения TELEGRAM_BOT_TOKEN или TOKEN")

MAIN_ADMIN_ID = int(os.environ.get("MAIN_ADMIN_ID") or os.environ.get("ADMIN_ID") or 5601944469)
FOOTBALL_API_KEY = os.environ.get("FOOTBALL_API_KEY")
if not FOOTBALL_API_KEY:
    print("Предупреждение: FOOTBALL_API_KEY не задан.")

DATABASE_URL = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_UR")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL не задан! Подключите PostgreSQL в Railway.")

TIMEZONE = pytz.timezone("Europe/Moscow")
SHORT_DAYS = {
    "Mon": "Пн", "Tue": "Вт", "Wed": "Ср",
    "Thu": "Чт", "Fri": "Пт", "Sat": "Сб", "Sun": "Вс"
}

# --- ЛИГИ ---
LEAGUES = {
    "UCL": {"name": "Лига чемпионов", "flag": "🏆", "code": "CL"},
    "UNL": {"name": "Лига наций", "flag": "🌍", "code": "UNL"},
    "PL": {"name": "АПЛ (Англия)", "flag": "🏴󠁧󠁢󠁥󠁮󠁧󠁿", "code": "PL"},
    "PD": {"name": "Ла Лига (Испания)", "flag": "🇪🇸", "code": "PD"},
    "FL1": {"name": "Лига 1 (Франция)", "flag": "🇫🇷", "code": "FL1"},
    "BL1": {"name": "Бундеслига (Германия)", "flag": "🇩🇪", "code": "BL1"},
    "SA": {"name": "Серия A (Италия)", "flag": "🇮🇹", "code": "SA"},
}

# --- СЛОВАРЬ ПЕРЕВОДА НАЗВАНИЙ КОМАНД НА РУССКИЙ ---
TEAM_TRANSLATIONS = {
    # Англия (АПЛ)
    "Arsenal FC": "Арсенал", "Arsenal": "Арсенал",
    "Aston Villa FC": "Астон Вилла", "Aston Villa": "Астон Вилла",
    "AFC Bournemouth": "Борнмут", "Bournemouth": "Борнмут",
    "Brentford FC": "Брентфорд", "Brentford": "Брентфорд",
    "Brighton & Hove Albion FC": "Брайтон", "Brighton & Hove Albion": "Брайтон", "Brighton": "Брайтон",
    "Chelsea FC": "Челси", "Chelsea": "Челси",
    "Crystal Palace FC": "Кристал Пэлас", "Crystal Palace": "Кристал Пэлас",
    "Everton FC": "Эвертон", "Everton": "Эвертон",
    "Fulham FC": "Фулхэм", "Fulham": "Фулхэм",
    "Ipswich Town FC": "Ипсвич Таун", "Ipswich Town": "Ипсвич Таун", "Ipswich": "Ипсвич",
    "Leicester City FC": "Лестер Сити", "Leicester City": "Лестер Сити", "Leicester": "Лестер",
    "Liverpool FC": "Ливерпуль", "Liverpool": "Ливерпуль",
    "Manchester City FC": "Манчестер Сити", "Manchester City": "Манчестер Сити", "Man City": "Манчестер Сити",
    "Manchester United FC": "Манчестер Юнайтед", "Manchester United": "Манчестер Юнайтед", "Man United": "Манчестер Юнайтед",
    "Newcastle United FC": "Ньюкасл", "Newcastle United": "Ньюкасл", "Newcastle": "Ньюкасл",
    "Nottingham Forest FC": "Ноттингем Форест", "Nottingham Forest": "Ноттингем Форест",
    "Southampton FC": "Саутгемптон", "Southampton": "Саутгемптон",
    "Tottenham Hotspur FC": "Тоттенхэм", "Tottenham Hotspur": "Тоттенхэм", "Tottenham": "Тоттенхэм",
    "West Ham United FC": "Вест Хэм", "West Ham United": "Вест Хэм", "West Ham": "Вест Хэм",
    "Wolverhampton Wanderers FC": "Вулверхэмптон", "Wolverhampton Wanderers": "Вулверхэмптон", "Wolves": "Вулверхэмптон",

    # Испания (Ла Лига)
    "Athletic Club": "Атлетик Бильбао", "Athletic": "Атлетик Бильбао",
    "Club Atlético de Madrid": "Атлетико Мадрид", "Atlético Madrid": "Атлетико Мадрид", "Atletico Madrid": "Атлетико Мадрид",
    "FC Barcelona": "Барселона", "Barcelona": "Барселона",
    "Real Betis Balompié": "Реал Бетис", "Real Betis": "Реал Бетис", "Betis": "Реал Бетис",
    "RC Celta de Vigo": "Сельта", "Celta de Vigo": "Сельта", "Celta Vigo": "Сельта",
    "Deportivo Alavés": "Алавес", "Alavés": "Алавес", "Alaves": "Алавес",
    "RCD Espanyol de Barcelona": "Эспаньол", "RCD Espanyol": "Эспаньол", "Espanyol": "Эспаньол",
    "Getafe CF": "Хетафе", "Getafe": "Хетафе",
    "Girona FC": "Жирона", "Girona": "Жирона",
    "UD Las Palmas": "Лас-Пальмас", "Las Palmas": "Лас-Пальмас",
    "CD Leganés": "Леганес", "Leganés": "Леганес", "Leganes": "Леганес",
    "RCD Mallorca": "Мальорка", "Mallorca": "Мальорка",
    "CA Osasuna": "Осасуна", "Osasuna": "Осасуна",
    "Rayo Vallecano de Madrid": "Райо Вальекано", "Rayo Vallecano": "Райо Вальекано",
    "Real Madrid CF": "Реал Мадрид", "Real Madrid": "Реал Мадрид",
    "Real Sociedad de Fútbol": "Реал Сосьедад", "Real Sociedad": "Реал Сосьедад",
    "Sevilla FC": "Севилья", "Sevilla": "Севилья",
    "Valencia CF": "Валенсия", "Valencia": "Валенсия",
    "Real Valladolid CF": "Вальядолид", "Real Valladolid": "Вальядолид", "Valladolid": "Вальядолид",
    "Villarreal CF": "Вильярреал", "Villarreal": "Вильярреал",

    # Германия (Бундеслига)
    "FC Augsburg": "Аугсбург", "Augsburg": "Аугсбург",
    "Bayer 04 Leverkusen": "Байер", "Bayer Leverkusen": "Байер", "Leverkusen": "Байер",
    "FC Bayern München": "Бавария", "Bayern Munich": "Бавария", "Bayern": "Бавария",
    "VfL Bochum 1848": "Бохум", "VfL Bochum": "Бохум", "Bochum": "Бохум",
    "Borussia Dortmund": "Боруссия Д", "Dortmund": "Боруссия Д", "BVB": "Боруссия Д",
    "Borussia Mönchengladbach": "Боруссия М", "Borussia Monchengladbach": "Боруссия М",
    "Eintracht Frankfurt": "Айнтрахт Ф", "Frankfurt": "Айнтрахт Ф",
    "SC Freiburg": "Фрайбург", "Freiburg": "Фрайбург",
    "1. FC Heidenheim 1846": "Хайденхайм", "1. FC Heidenheim": "Хайденхайм", "Heidenheim": "Хайденхайм",
    "TSG 1899 Hoffenheim": "Хоффенхайм", "Hoffenheim": "Хоффенхайм",
    "Holstein Kiel": "Хольштайн Киль", "Kiel": "Хольштайн Киль",
    "1. FSV Mainz 05": "Майнц", "Mainz 05": "Майнц", "Mainz": "Майнц",
    "RB Leipzig": "РБ Лейпциг", "Leipzig": "РБ Лейпциг",
    "FC St. Pauli 1910": "Санкт-Паули", "FC St. Pauli": "Санкт-Паули", "St. Pauli": "Санкт-Паули",
    "VfB Stuttgart": "Штутгарт", "Stuttgart": "Штутгарт",
    "1. FC Union Berlin": "Унион Берлин", "Union Berlin": "Унион Берлин",
    "SV Werder Bremen": "Вердер", "Werder Bremen": "Вердер", "Bremen": "Вердер",
    "VfL Wolfsburg": "Вольфсбург", "Wolfsburg": "Вольфсбург",

    # Италия (Серия А)
    "Atalanta BC": "Аталанта", "Atalanta": "Аталанта",
    "Bologna FC 1909": "Болонья", "Bologna": "Болонья",
    "Cagliari Calcio": "Кальяри", "Cagliari": "Кальяри",
    "Como 1907": "Комо", "Como": "Комо",
    "Empoli FC": "Эмполи", "Empoli": "Эмполи",
    "ACF Fiorentina": "Фиорентина", "Fiorentina": "Фиорентина",
    "Genoa CFC": "Дженоа", "Genoa": "Дженоа",
    "Hellas Verona FC": "Эллас Верона", "Hellas Verona": "Эллас Верона", "Verona": "Эллас Верона",
    "FC Internazionale Milano": "Интер", "Inter Milan": "Интер", "Inter": "Интер",
    "Juventus FC": "Ювентус", "Juventus": "Ювентус",
    "SS Lazio": "Лацио", "Lazio": "Лацио",
    "US Lecce": "Лечче", "Lecce": "Лечче",
    "AC Milan": "Милан", "Milan": "Милан",
    "AC Monza": "Монца", "Monza": "Монца",
    "SSC Napoli": "Наполи", "Napoli": "Наполи",
    "Parma Calcio 1913": "Парма", "Parma": "Парма",
    "AS Roma": "Рома", "Roma": "Рома",
    "Torino FC": "Торино", "Torino": "Торино",
    "Udinese Calcio": "Удинезе", "Udinese": "Удинезе",
    "Venezia FC": "Венеция", "Venezia": "Венеция",

    # Франция (Лига 1)
    "Angers SCO": "Анже", "Angers": "Анже",
    "AJ Auxerre": "Осер", "Auxerre": "Осер",
    "Stade Brestois 29": "Брест", "Brest": "Брест",
    "Le Havre AC": "Гавр", "Le Havre": "Гавр",
    "Racing Club de Lens": "Ланс", "RC Lens": "Ланс", "Lens": "Ланс",
    "Lille OSC": "Лилль", "Lille": "Лилль",
    "Olympique Lyonnais": "Лион", "Lyon": "Лион",
    "Olympique de Marseille": "Марсель", "Marseille": "Марсель",
    "AS Monaco FC": "Монако", "AS Monaco": "Монако", "Monaco": "Монако",
    "Montpellier HSC": "Монпелье", "Montpellier": "Монпелье",
    "FC Nantes": "Нант", "Nantes": "Нант",
    "OGC Nice": "Ницца", "Nice": "Ницца",
    "Paris Saint-Germain FC": "ПСЖ", "Paris Saint-Germain": "ПСЖ", "PSG": "ПСЖ",
    "Stade de Reims": "Реймс", "Reims": "Реймс",
    "Stade Rennais FC 1901": "Ренн", "Stade Rennais": "Ренн", "Rennes": "Ренн",
    "AS Saint-Étienne": "Сент-Этьен", "Saint-Étienne": "Сент-Этьен", "Saint-Etienne": "Сент-Этьен",
    "RC Strasbourg Alsace": "Страсбур", "Strasbourg": "Страсбур",
    "Toulouse FC": "Тулуза", "Toulouse": "Тулуза",

    # Другие клубы ЛЧ
    "Sporting Clube de Portugal": "Спортинг", "Sporting CP": "Спортинг", "Sporting": "Спортинг",
    "SL Benfica": "Бенфика", "Benfica": "Бенфика",
    "Feyenoord Rotterdam": "Фейеноорд", "Feyenoord": "Фейеноорд",
    "PSV": "ПСВ", "PSV Eindhoven": "ПСВ",
    "Celtic FC": "Селтик", "Celtic": "Селтик",
    "GNK Dinamo Zagreb": "Динамо Загреб", "Dinamo Zagreb": "Динамо Загреб",
    "FK Crvena Zvezda": "Црвена Звезда", "Red Star Belgrade": "Црвена Звезда",
    "FC Shakhtar Donetsk": "Шахтёр", "Shakhtar Donetsk": "Шахтёр",
    "AC Sparta Praha": "Спарта Прага", "Sparta Prague": "Спарта Прага", "Sparta Praha": "Спарта Прага",
    "SK Sturm Graz": "Штурм", "Sturm Graz": "Штурм",
    "BSC Young Boys": "Янг Бойз", "Young Boys": "Янг Бойз",
    "Club Brugge KV": "Брюгге", "Club Brugge": "Брюгге",
    "ŠK Slovan Bratislava": "Слован Братислава", "Slovan Bratislava": "Слован Братислава",
    "FC Red Bull Salzburg": "Зальцбург", "Red Bull Salzburg": "Зальцбург", "Salzburg": "Зальцбург",

    # Сборные (Лига наций)
    "Albania": "Албания",
    "Andorra": "Андорра",
    "Armenia": "Армения",
    "Austria": "Австрия",
    "Azerbaijan": "Азербайджан",
    "Belarus": "Беларусь",
    "Belgium": "Бельгия",
    "Bosnia and Herzegovina": "Босния и Герцеговина", "Bosnia": "Босния и Герцеговина", "Bosnia & Herzegovina": "Босния и Герцеговина",
    "Bulgaria": "Болгария",
    "Croatia": "Хорватия",
    "Cyprus": "Кипр",
    "Czech Republic": "Чехия", "Czechia": "Чехия",
    "Denmark": "Дания",
    "England": "Англия",
    "Estonia": "Эстония",
    "Faroe Islands": "Фарерские острова",
    "Finland": "Финляндия",
    "France": "Франция",
    "Georgia": "Грузия",
    "Germany": "Германия",
    "Gibraltar": "Гибралтар",
    "Greece": "Греция",
    "Hungary": "Венгрия",
    "Iceland": "Исландия",
    "Ireland": "Ирландия", "Republic of Ireland": "Ирландия",
    "Israel": "Израиль",
    "Italy": "Италия",
    "Kazakhstan": "Казахстан",
    "Kosovo": "Косово",
    "Latvia": "Латвия",
    "Liechtenstein": "Лихтенштейн",
    "Lithuania": "Литва",
    "Luxembourg": "Люксембург",
    "Malta": "Мальта",
    "Moldova": "Молдова",
    "Montenegro": "Черногория",
    "Netherlands": "Нидерланды", "Holland": "Нидерланды",
    "North Macedonia": "Северная Македония", "Macedonia": "Северная Македония",
    "Northern Ireland": "Северная Ирландия",
    "Norway": "Норвегия",
    "Poland": "Польша",
    "Portugal": "Португалия",
    "Romania": "Румыния",
    "San Marino": "Сан-Марино",
    "Scotland": "Шотландия",
    "Serbia": "Сербия",
    "Slovakia": "Словакия",
    "Slovenia": "Словения",
    "Spain": "Испания",
    "Sweden": "Швеция",
    "Switzerland": "Швейцария",
    "Turkey": "Турция",
    "Ukraine": "Украина",
    "Wales": "Уэльс",
}

def translate_team(name: str) -> str:
    if not name:
        return ""
    if name in TEAM_TRANSLATIONS:
        return TEAM_TRANSLATIONS[name]
    clean = re.sub(r'\b(FC|CF|BSC|SSC|AC|AS|OGC|RC|US|HNK|GNK|VfL|VfB|SV|1\.|1899|1848|1909|1910|1907|1913|1901|05|04|SCO|OSC|HSC|KV|SK|ŠK)\b', '', name).strip()
    clean = re.sub(r'\s+', ' ', clean)
    return TEAM_TRANSLATIONS.get(clean, TEAM_TRANSLATIONS.get(name, name))

# --- РАБОТА С БАЗОЙ ДАННЫХ (PostgreSQL) ---
def get_db_connection():
    return psycopg2.connect(DATABASE_URL, sslmode='prefer')

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

    # Справочник лиг (нужен раньше matches из-за внешнего ключа)
    cur.execute('''
        CREATE TABLE IF NOT EXISTS leagues (
            league_id TEXT PRIMARY KEY,
            name TEXT
        )
    ''')
    for lid, info in LEAGUES.items():
        cur.execute(
            "INSERT INTO leagues (league_id, name) VALUES (%s,%s) "
            "ON CONFLICT (league_id) DO UPDATE SET name=EXCLUDED.name",
            (lid, info["name"])
        )

    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            first_name TEXT
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS admins (
            user_id BIGINT PRIMARY KEY
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS scores (
            user_id BIGINT PRIMARY KEY REFERENCES users(user_id),
            score INTEGER DEFAULT 0
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
            current_result TEXT,
            league_id TEXT REFERENCES leagues(league_id),
            matchday INTEGER
        )
    ''')

    # Миграция для существующих таблиц (на случай старой БД без этих колонок)
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='matches' AND column_name='league_id'")
    if not cur.fetchone():
        cur.execute("ALTER TABLE matches ADD COLUMN league_id TEXT REFERENCES leagues(league_id)")
        print("✅ Добавлена колонка league_id")

    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='matches' AND column_name='matchday'")
    if not cur.fetchone():
        cur.execute("ALTER TABLE matches ADD COLUMN matchday INTEGER")
        print("✅ Добавлена колонка matchday")

    # Для ранее добавленных матчей ЛЧ и Лиги наций с результатом проставляем тур 1
    cur.execute("UPDATE matches SET matchday = 1 WHERE league_id = 'UCL' AND matchday IS NULL AND result IS NOT NULL")
    cur.execute("UPDATE matches SET matchday = 1 WHERE league_id = 'UNL' AND matchday IS NULL AND result IS NOT NULL")

    # Автоматически обновляем существующие названия команд на русский язык
    cur.execute("SELECT match_id, home, away FROM matches")
    for mid, h, a in cur.fetchall():
        h_ru = translate_team(h)
        a_ru = translate_team(a)
        if h_ru != h or a_ru != a:
            cur.execute("UPDATE matches SET home=%s, away=%s WHERE match_id=%s", (h_ru, a_ru, mid))

    cur.execute('''
        CREATE TABLE IF NOT EXISTS predictions (
            user_id BIGINT REFERENCES users(user_id),
            match_id INTEGER REFERENCES matches(match_id),
            prediction TEXT,
            PRIMARY KEY (user_id, match_id)
        )
    ''')

    # Гарантируем, что главный админ всегда есть в базе и имеет права
    cur.execute(
        "INSERT INTO users (user_id, username, first_name) VALUES (%s,%s,%s) "
        "ON CONFLICT (user_id) DO NOTHING",
        (MAIN_ADMIN_ID, None, "Главный админ")
    )
    cur.execute(
        "INSERT INTO admins (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING",
        (MAIN_ADMIN_ID,)
    )
    cur.execute(
        "INSERT INTO scores (user_id, score) VALUES (%s,0) ON CONFLICT (user_id) DO NOTHING",
        (MAIN_ADMIN_ID,)
    )

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

def add_admin(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO admins (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (user_id,))
    conn.commit()
    cur.close()
    conn.close()

def remove_admin(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM admins WHERE user_id=%s", (user_id,))
    conn.commit()
    cur.close()
    conn.close()

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
        SELECT m.match_id, m.home, m.away, p.prediction, m.result, m.start_time, m.league_id, m.matchday, m.current_result
        FROM predictions p
        JOIN matches m ON p.match_id = m.match_id
        WHERE p.user_id = %s
        ORDER BY m.start_time DESC, m.match_id DESC
    ''', (user_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def get_match(match_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT match_id, home, away, day, result, start_time, api_id, current_result, league_id, matchday FROM matches WHERE match_id=%s", (match_id,))
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

def add_match_from_api(api_id, home, away, start_time, league_id, day=None, matchday=None, result=None, current_result=None):
    if not day:
        try:
            dt = datetime.strptime(start_time, "%Y-%m-%d %H:%M")
            day_eng = dt.strftime("%a")
        except Exception:
            day_eng = "Sat"
    else:
        day_eng = day
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT match_id, matchday, result, start_time, current_result, home, away FROM matches WHERE api_id=%s", (str(api_id),))
    existing = cur.fetchone()
    if existing:
        m_id, existing_matchday, existing_result, existing_start, existing_current, ex_home, ex_away = existing
        update_fields = []
        params = []
        if matchday and existing_matchday is None:
            update_fields.append("matchday=%s")
            params.append(matchday)
        if start_time and start_time != existing_start:
            update_fields.append("start_time=%s")
            params.append(start_time)
            update_fields.append("day=%s")
            params.append(day_eng)
        if home and home != ex_home:
            update_fields.append("home=%s")
            params.append(home)
        if away and away != ex_away:
            update_fields.append("away=%s")
            params.append(away)
        if current_result and existing_result is None:
            norm_curr = normalize_score(current_result)
            if norm_curr != existing_current:
                update_fields.append("current_result=%s")
                params.append(norm_curr)
        score_updated = False
        if result and existing_result is None:
            norm_res = normalize_score(result)
            update_fields.append("result=%s")
            params.append(norm_res)
            update_fields.append("current_result=NULL")
            score_updated = True
        if update_fields:
            params.append(m_id)
            cur.execute(f"UPDATE matches SET {', '.join(update_fields)} WHERE match_id=%s", tuple(params))
            conn.commit()
            if score_updated:
                recalc_all_scores()
        cur.close()
        conn.close()
        return None

    match_id = get_next_match_id()
    try:
        cur.execute('''
            INSERT INTO matches (match_id, home, away, day, start_time, api_id, league_id, matchday, result, current_result)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ''', (match_id, home, away, day_eng, start_time, str(api_id), league_id, matchday, normalize_score(result) if result else None, normalize_score(current_result) if current_result else None))
        conn.commit()
        cur.close()
        conn.close()
        if result:
            recalc_all_scores()
        return match_id
    except psycopg2.IntegrityError:
        conn.rollback()
        cur.close()
        conn.close()
        return None

def add_match_manual(home, away, start_time, league_id, matchday=None):
    dt = datetime.strptime(start_time, "%Y-%m-%d %H:%M")
    day_eng = dt.strftime("%a")
    match_id = get_next_match_id()
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO matches (match_id, home, away, day, start_time, league_id, matchday)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
    ''', (match_id, translate_team(home), translate_team(away), day_eng, start_time, league_id, matchday))
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

def calc_match_points(pred_str, res_str):
    if not pred_str or not res_str:
        return 0
    pred = parse_score(pred_str)
    res = parse_score(res_str)
    if not pred or not res:
        return 0
    if pred[0] == res[0] and pred[1] == res[1]:
        return 6
    elif (pred[0] - pred[1]) == (res[0] - res[1]):
        return 3
    elif get_outcome(pred[0], pred[1]) == get_outcome(res[0], res[1]):
        return 2
    return 0

def is_match_open(start_time_str):
    try:
        start_dt = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M")
        start_dt = TIMEZONE.localize(start_dt)
    except Exception:
        return False
    now = datetime.now(TIMEZONE)
    open_window = start_dt - timedelta(days=7)
    deadline = start_dt - timedelta(minutes=10)
    return open_window <= now < deadline

def is_match_locked(start_time_str):
    try:
        start_dt = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M")
        start_dt = TIMEZONE.localize(start_dt)
    except Exception:
        return False
    now = datetime.now(TIMEZONE)
    open_window = start_dt - timedelta(days=7)
    return now < open_window

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

def get_leaderboard_data(category='all'):
    """
    category:
      'all' - все турниры
      'UCL' - Лига чемпионов
      'UNL' - Лига наций
      'leagues' - остальные лиги
    """
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT user_id, first_name, username FROM users")
    users = {
        r[0]: {
            "first_name": r[1] or "",
            "username": r[2] or "",
            "score": 0,
            "exact": 0,
            "diff": 0,
            "outcome": 0,
            "total_preds": 0
        }
        for r in cur.fetchall()
    }

    if category == 'UCL':
        cur.execute("""
            SELECT p.user_id, p.prediction, m.result
            FROM predictions p
            JOIN matches m ON p.match_id = m.match_id
            WHERE m.league_id = 'UCL' AND m.result IS NOT NULL
        """)
    elif category == 'UNL':
        cur.execute("""
            SELECT p.user_id, p.prediction, m.result
            FROM predictions p
            JOIN matches m ON p.match_id = m.match_id
            WHERE m.league_id = 'UNL' AND m.result IS NOT NULL
        """)
    elif category == 'leagues':
        cur.execute("""
            SELECT p.user_id, p.prediction, m.result
            FROM predictions p
            JOIN matches m ON p.match_id = m.match_id
            WHERE m.league_id NOT IN ('UCL', 'UNL') AND m.result IS NOT NULL
        """)
    else:
        cur.execute("""
            SELECT p.user_id, p.prediction, m.result
            FROM predictions p
            JOIN matches m ON p.match_id = m.match_id
            WHERE m.result IS NOT NULL
        """)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    for uid, pred, res in rows:
        if uid not in users:
            continue
        users[uid]["total_preds"] += 1
        pts = calc_match_points(pred, res)
        users[uid]["score"] += pts
        if pts == 6:
            users[uid]["exact"] += 1
        elif pts == 3:
            users[uid]["diff"] += 1
        elif pts == 2:
            users[uid]["outcome"] += 1

    leaderboard = []
    for uid, data in users.items():
        leaderboard.append({
            "user_id": uid,
            "first_name": data["first_name"],
            "username": data["username"],
            "score": data["score"],
            "exact": data["exact"],
            "diff": data["diff"],
            "outcome": data["outcome"],
            "total_preds": data["total_preds"]
        })

    leaderboard.sort(key=lambda x: (x["score"], x["exact"], x["diff"]), reverse=True)
    return leaderboard

# --- ФУНКЦИИ ДЛЯ РАБОТЫ С API ---
def fetch_matches_from_api(league_code, days_ahead=14):
    if not FOOTBALL_API_KEY:
        return []
    now = datetime.now(TIMEZONE)
    date_from = (now - timedelta(days=2)).strftime("%Y-%m-%d")
    date_to = (now + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    if league_code in ("CL", "UNL"):
        # Для Лиги чемпионов и Лиги наций запрашиваем все матчи этапа лиги
        url = f"https://api.football-data.org/v4/competitions/{league_code}/matches"
    else:
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
            matchday = m.get("matchday")
            if not api_id or not home_en or not away_en or not utc_date:
                continue
            home_ru = translate_team(home_en)
            away_ru = translate_team(away_en)
            utc_dt = datetime.fromisoformat(utc_date.replace("Z", "+00:00"))
            local_dt = utc_dt.astimezone(TIMEZONE)
            start_time = local_dt.strftime("%Y-%m-%d %H:%M")
            day_eng = local_dt.strftime("%a")

            score_data = m.get("score", {})
            full_time = score_data.get("fullTime")
            half_time = score_data.get("halfTime")
            status = m.get("status")

            final_res = None
            current_res = None
            if status == "FINISHED" and full_time and full_time.get("home") is not None:
                final_res = f"{full_time.get('home')}:{full_time.get('away')}"
            elif status in ("IN_PLAY", "PAUSED"):
                if full_time and full_time.get("home") is not None:
                    current_res = f"{full_time.get('home')}:{full_time.get('away')}"
                elif half_time and half_time.get("home") is not None:
                    current_res = f"{half_time.get('home')}:{half_time.get('away')}"

            result.append({
                "api_id": api_id,
                "home": home_ru,
                "away": away_ru,
                "day": day_eng,
                "start_time": start_time,
                "matchday": matchday,
                "result": final_res,
                "current_result": current_res,
                "status": status
            })
        return result
    except Exception as e:
        print(f"Ошибка при получении матчей для {league_code}: {e}")
        return []

def fetch_unl_matches_from_uefa():
    """
    Запрос актуальных данных и live-счетов Лиги наций 2026/27 напрямую из официального API UEFA.
    """
    try:
        import requests
        url = "https://match.uefa.com/v5/matches?competitionId=2014&seasonYear=2027&offset=0&limit=100"
        headers = {"x-api-key": "ceeee1a5bb209502c6c438abd8f30aef179ce669bb9288f2d1cf2fa276de03f4"}
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.error("Ошибка при запросе к UEFA API: %s", e)
    return []

def sync_unl_matches():
    """
    Синхронизация официальных матчей Лиги наций УЕФА актуального сезона 2026/27.
    1. Очищает старые записи (моки и старые сезоны 2024 года).
    2. Загружает официальный календарь сезона 2026/27 (156 матчей).
    3. Синхронизирует свежие результаты из UEFA API.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM predictions WHERE match_id IN (SELECT match_id FROM matches WHERE league_id='UNL' AND (api_id LIKE 'unl_%' OR api_id NOT LIKE 'uefa_%'))")
        cur.execute("DELETE FROM matches WHERE league_id='UNL' AND (api_id LIKE 'unl_%' OR api_id NOT LIKE 'uefa_%')")
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Ошибка при очистке старых матчей UNL: {e}")
    finally:
        cur.close()
        conn.close()

    added = 0
    for fix in REAL_UNL_FIXTURES:
        res = add_match_from_api(
            api_id=fix["api_id"],
            home=fix["home"],
            away=fix["away"],
            start_time=fix["time"],
            league_id="UNL",
            matchday=fix.get("matchday"),
            result=fix.get("result"),
            current_result=fix.get("current_result")
        )
        if res:
            added += 1

    try:
        uefa_matches = fetch_unl_matches_from_uefa()
        for m in uefa_matches:
            api_id = "uefa_" + str(m.get("id"))
            status = m.get("status")
            score_data = m.get("score", {})
            total_score = score_data.get("total", {}) if score_data else {}
            h_score = total_score.get("home") if total_score else None
            a_score = total_score.get("away") if total_score else None

            final_res = None
            cur_res = None
            if status == "FINISHED" and h_score is not None:
                final_res = f"{h_score}:{a_score}"
            elif status in ("IN_PLAY", "PAUSED", "LIVE") and h_score is not None:
                cur_res = f"{h_score}:{a_score}"

            if final_res or cur_res:
                c = get_db_connection()
                cu = c.cursor()
                if final_res:
                    cu.execute("UPDATE matches SET result=%s, current_result=NULL WHERE api_id=%s AND result IS NULL", (final_res, api_id))
                elif cur_res:
                    cu.execute("UPDATE matches SET current_result=%s WHERE api_id=%s AND result IS NULL", (cur_res, api_id))
                c.commit()
                cu.close()
                c.close()
    except Exception as e:
        print(f"Ошибка при обновлении UNL через UEFA API: {e}")

    return added

def update_matches_from_api_for_league(league_id):
    league_info = LEAGUES.get(league_id)
    if not league_info:
        return 0
    matches = fetch_matches_from_api(league_info["code"], days_ahead=14)
    added = 0
    for m in matches:
        if add_match_from_api(
            m["api_id"],
            m["home"],
            m["away"],
            m["start_time"],
            league_id,
            day=m.get("day"),
            matchday=m.get("matchday"),
            result=m.get("result"),
            current_result=m.get("current_result")
        ):
            added += 1
    return added

def update_matches_from_api():
    total = 0
    for lid in LEAGUES:
        try:
            if lid == "UNL":
                added = sync_unl_matches()
            else:
                added = update_matches_from_api_for_league(lid)
            total += added
            time.sleep(1.2)
        except Exception as e:
            print(f"Ошибка автообновления лиги {lid}: {e}")
    print(f"🔄 Автозагрузка матчей: добавлено {total} новых матчей.")
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
    cur.execute("SELECT match_id, api_id, start_time, league_id FROM matches WHERE result IS NULL AND api_id IS NOT NULL")
    matches = cur.fetchall()
    cur.close()
    conn.close()

    now = datetime.now(TIMEZONE)
    started_matches = []
    for match_id, api_id, start_time_str, league_id in matches:
        try:
            start_dt = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M")
            start_dt = TIMEZONE.localize(start_dt)
        except Exception:
            continue
        if now >= start_dt:
            started_matches.append((match_id, api_id, start_dt, league_id))

    updated = 0

    # Проверяем live-обновления UNL через UEFA API
    try:
        uefa_matches = fetch_unl_matches_from_uefa()
        for m in uefa_matches:
            api_id = "uefa_" + str(m.get("id"))
            status = m.get("status")
            score_data = m.get("score", {})
            total_score = score_data.get("total", {}) if score_data else {}
            h_score = total_score.get("home") if total_score else None
            a_score = total_score.get("away") if total_score else None

            if status == "FINISHED" and h_score is not None:
                res_str = f"{h_score}:{a_score}"
                c = get_db_connection()
                cu = c.cursor()
                cu.execute("SELECT match_id, result FROM matches WHERE api_id=%s", (api_id,))
                r = cu.fetchone()
                if r and r[1] is None:
                    set_result(r[0], res_str)
                    set_current_result(r[0], None)
                    updated += 1
                    logger.info("UEFA API: финальный результат UNL #%s: %s", r[0], res_str)
                cu.close()
                c.close()
            elif status in ("IN_PLAY", "PAUSED", "LIVE") and h_score is not None:
                res_str = f"{h_score}:{a_score}"
                c = get_db_connection()
                cu = c.cursor()
                cu.execute("SELECT match_id, result, current_result FROM matches WHERE api_id=%s", (api_id,))
                r = cu.fetchone()
                if r and r[1] is None and r[2] != res_str:
                    set_current_result(r[0], res_str)
                cu.close()
                c.close()
    except Exception as e:
        logger.error("Ошибка при live-обновлении UNL: %s", e)

    if not started_matches:
        return updated

    official_unl_scores = {fix["api_id"]: fix["result"] for fix in REAL_UNL_FIXTURES if fix.get("result")}

    for match_id, api_id, start_dt, league_id in started_matches:
        api_id_str = str(api_id)
        if api_id_str.startswith("uefa_") or api_id_str.startswith("unl_"):
            # Проверяем завершение матча Лиги наций (через 115 минут после начала)
            if now >= start_dt + timedelta(minutes=115):
                score = official_unl_scores.get(api_id_str)
                if score:
                    set_result(match_id, score)
                    set_current_result(match_id, None)
                    updated += 1
                    print(f"Автообновление: финальный результат UNL #{match_id}: {score}")
            continue

        details = fetch_match_details_by_api_id(api_id)
        time.sleep(1.2)
        if not details:
            continue
        current = details["half_time"] or details["full_time"]
        if current and details["status"] != "FINISHED":
            set_current_result(match_id, current)
        if details["status"] == "FINISHED" and details["full_time"]:
            match = get_match(match_id)
            if match and match[4] is None:
                set_result(match_id, details["full_time"])
                set_current_result(match_id, None)
                updated += 1
                print(f"Автообновление: финальный результат матча #{match_id}: {details['full_time']}")

    print(f"🔄 Автообновление результатов: обновлено {updated} матчей.")
    return updated

# --- ГЕНЕРАЦИЯ ОТЧЁТА (с поддержкой лиг) ---
def generate_report(league_id=None):
    conn = get_db_connection()
    cur = conn.cursor()
    if league_id:
        cur.execute("SELECT match_id, home, away, result, start_time, current_result, league_id, matchday FROM matches WHERE league_id=%s ORDER BY start_time, match_id", (league_id,))
    else:
        cur.execute("SELECT match_id, home, away, result, start_time, current_result, league_id, matchday FROM matches ORDER BY start_time, match_id")
    matches = cur.fetchall()
    if not matches:
        cur.close()
        conn.close()
        return "В базе пока нет матчей для данного отчёта.", None

    cur.execute("SELECT DISTINCT p.user_id, u.username, u.first_name FROM predictions p JOIN users u ON p.user_id = u.user_id")
    users = cur.fetchall()
    if not users:
        cur.close()
        conn.close()
        return "В базе пока нет прогнозов от пользователей.", None

    users_data = {}
    for user_id, username, first_name in users:
        users_data[user_id] = {
            "username": username,
            "first_name": first_name,
            "predictions": {},
            "total_score": 0,
            "exact": 0,
            "diff": 0,
            "outcome": 0,
            "zero": 0
        }

    for user_id in users_data:
        cur.execute("SELECT match_id, prediction FROM predictions WHERE user_id=%s", (user_id,))
        for mid, pred in cur.fetchall():
            users_data[user_id]["predictions"][mid] = pred

    cur.close()
    conn.close()

    total_matches = len(matches)
    finished_count = 0
    upcoming_count = 0

    for match in matches:
        match_id, home, away, result, start_time, current_result, league, mday = match
        if result is not None:
            finished_count += 1
            for user_id, data in users_data.items():
                pred = data["predictions"].get(match_id)
                if pred:
                    pts = calc_match_points(pred, result)
                    data["total_score"] += pts
                    if pts == 6:
                        data["exact"] += 1
                    elif pts == 3:
                        data["diff"] += 1
                    elif pts == 2:
                        data["outcome"] += 1
                    else:
                        data["zero"] += 1
        else:
            upcoming_count += 1

    sorted_users = sorted(
        users_data.values(),
        key=lambda x: (x["total_score"], x["exact"], x["diff"]),
        reverse=True
    )

    league_info = LEAGUES.get(league_id, {"flag": "🌐", "name": "Все турниры"}) if league_id else {"flag": "🌐", "name": "Все турниры"}

    text_report = (
        f"📊 <b>ОТЧЁТ: {league_info['flag']} {league_info['name']}</b>\n\n"
        f"🗓 <b>Статистика турнира:</b>\n"
        f"• Всего матчей: <b>{total_matches}</b>\n"
        f"• Завершено: <b>{finished_count}</b> ✅\n"
        f"• Ожидают / в игре: <b>{upcoming_count}</b> ⏳\n"
        f"• Участников с прогнозами: <b>{len(users_data)}</b> 👥\n\n"
        f"🏆 <b>Топ участников:</b>\n"
    )

    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    for idx, u in enumerate(sorted_users[:5], 1):
        med = medals.get(idx, f"<b>{idx}.</b>")
        uname = html.escape(u["first_name"] if u["first_name"] else "Участник")
        at_user = f" (@{html.escape(u['username'])})" if u["username"] else ""
        text_report += f"{med} {uname}{at_user} — <b>{u['total_score']}</b> очк. (🎯 {u['exact']} | 📐 {u['diff']} | 🎲 {u['outcome']})\n"

    text_report += "\n📎 <i>Подробная матрица прогнозов по каждому матчу в Excel файле ниже 👇</i>"

    # Excel Generation
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Отчёт"
    ws.views.sheetView[0].showGridLines = True

    title_font = Font(name="Calibri", size=13, bold=True, color="1F4E79")
    header_fill = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
    header_font = Font(name="Calibri", size=10, bold=True, color="FFFFFF")

    stat_header_fill = PatternFill(start_color="2F5597", end_color="2F5597", fill_type="solid")

    exact_fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
    exact_font = Font(name="Calibri", size=10, color="006100", bold=True)

    diff_fill = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
    diff_font = Font(name="Calibri", size=10, color="9C6500", bold=True)

    outcome_fill = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
    outcome_font = Font(name="Calibri", size=10, color="7F6000")

    zero_fill = PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid")
    zero_font = Font(name="Calibri", size=10, color="7F7F7F")

    total_fill = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")
    total_font = Font(name="Calibri", size=11, bold=True, color="1F4E79")

    thin_border = Border(
        left=Side(style='thin', color='D9D9D9'),
        right=Side(style='thin', color='D9D9D9'),
        top=Side(style='thin', color='D9D9D9'),
        bottom=Side(style='thin', color='D9D9D9')
    )

    ws.cell(row=1, column=1, value=f"{league_info['name']} — Отчёт по прогнозам ({datetime.now(TIMEZONE).strftime('%d.%m.%Y %H:%M')})")
    ws.cell(row=1, column=1).font = title_font

    col_headers = ["Участник"]
    match_cols_info = []

    for m in matches:
        mid, h, a, res, st, cur_res, leg, mday = m
        res_display = f" ({normalize_score(res)})" if res else (f" [{normalize_score(cur_res)}]" if cur_res else "")
        tour_label = f"[Тур {mday}] " if mday else ""
        col_headers.append(f"{tour_label}{h} – {a}{res_display}")
        match_cols_info.append(m)

    col_headers.extend(["🎯 Точный (6)", "📐 Разница (3)", "🎲 Исход (2)", "🌟 ИТОГО ОЧКОВ"])

    for col_idx, text in enumerate(col_headers, 1):
        c = ws.cell(row=3, column=col_idx, value=text)
        c.fill = header_fill if col_idx <= len(match_cols_info) + 1 else stat_header_fill
        c.font = header_font
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = thin_border

    for row_idx, u in enumerate(sorted_users, 4):
        name = f"@{u['username']}" if u['username'] else u['first_name']
        if not name:
            name = "Участник"

        c = ws.cell(row=row_idx, column=1, value=name)
        c.font = Font(name="Calibri", size=10, bold=True)
        c.alignment = Alignment(horizontal="left", vertical="center")
        c.border = thin_border

        for m_idx, m in enumerate(match_cols_info, 2):
            mid, h, a, res, st, cur_res, leg, mday = m
            pred = u["predictions"].get(mid, "-")
            pred_display = normalize_score(pred) if pred != "-" else "-"
            cell = ws.cell(row=row_idx, column=m_idx, value=pred_display)
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = thin_border

            if res is not None and pred != "-":
                pts = calc_match_points(pred, res)
                if pts == 6:
                    cell.fill = exact_fill
                    cell.font = exact_font
                elif pts == 3:
                    cell.fill = diff_fill
                    cell.font = diff_font
                elif pts == 2:
                    cell.fill = outcome_fill
                    cell.font = outcome_font
                else:
                    cell.fill = zero_fill
                    cell.font = zero_font
            else:
                cell.font = Font(name="Calibri", size=10)

        c_exact = ws.cell(row=row_idx, column=len(match_cols_info) + 2, value=u["exact"])
        c_exact.alignment = Alignment(horizontal="center", vertical="center")
        c_exact.font = Font(name="Calibri", size=10, bold=True)
        c_exact.border = thin_border

        c_diff = ws.cell(row=row_idx, column=len(match_cols_info) + 3, value=u["diff"])
        c_diff.alignment = Alignment(horizontal="center", vertical="center")
        c_diff.font = Font(name="Calibri", size=10)
        c_diff.border = thin_border

        c_out = ws.cell(row=row_idx, column=len(match_cols_info) + 4, value=u["outcome"])
        c_out.alignment = Alignment(horizontal="center", vertical="center")
        c_out.font = Font(name="Calibri", size=10)
        c_out.border = thin_border

        c_tot = ws.cell(row=row_idx, column=len(match_cols_info) + 5, value=u["total_score"])
        c_tot.alignment = Alignment(horizontal="center", vertical="center")
        c_tot.font = total_font
        c_tot.fill = total_fill
        c_tot.border = thin_border

    ws.row_dimensions[1].height = 25
    ws.row_dimensions[3].height = 32
    for r in range(4, len(sorted_users) + 4):
        ws.row_dimensions[r].height = 20

    ws.freeze_panes = "B4"

    for col in ws.columns:
        col_letter = col[0].column_letter
        max_len = 0
        for cell in col:
            val = str(cell.value or "")
            if cell.row == 1:
                continue
            if len(val) > max_len:
                max_len = len(val)
        ws.column_dimensions[col_letter].width = max(max_len + 3, 12)

    excel_data = BytesIO()
    wb.save(excel_data)
    excel_data.seek(0)

    return text_report, excel_data

# --- ОБРАБОТЧИКИ КОМАНД ---
def get_competitions_status():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT league_id, COALESCE(matchday, 1), start_time FROM matches WHERE result IS NULL")
    rows = cur.fetchall()
    cur.close()
    conn.close()

    leagues_stat = {lid: {"open": 0, "locked": 0, "in_progress": 0, "total": 0} for lid in LEAGUES}
    ucl_tours_stat = {t: {"open": 0, "locked": 0, "in_progress": 0, "total": 0} for t in range(1, 9)}
    unl_tours_stat = {t: {"open": 0, "locked": 0, "in_progress": 0, "total": 0} for t in range(1, 7)}

    for lid, mday, st in rows:
        if lid in leagues_stat:
            leagues_stat[lid]["total"] += 1
            if st:
                if is_match_open(st):
                    leagues_stat[lid]["open"] += 1
                elif is_match_locked(st):
                    leagues_stat[lid]["locked"] += 1
                else:
                    leagues_stat[lid]["in_progress"] += 1

        if lid == "UCL" and mday in ucl_tours_stat:
            ucl_tours_stat[mday]["total"] += 1
            if st:
                if is_match_open(st):
                    ucl_tours_stat[mday]["open"] += 1
                elif is_match_locked(st):
                    ucl_tours_stat[mday]["locked"] += 1
                else:
                    ucl_tours_stat[mday]["in_progress"] += 1

        if lid == "UNL" and mday in unl_tours_stat:
            unl_tours_stat[mday]["total"] += 1
            if st:
                if is_match_open(st):
                    unl_tours_stat[mday]["open"] += 1
                elif is_match_locked(st):
                    unl_tours_stat[mday]["locked"] += 1
                else:
                    unl_tours_stat[mday]["in_progress"] += 1

    return leagues_stat, ucl_tours_stat, unl_tours_stat

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, user.username, user.first_name)
    await show_main_menu(update, context, "Добро пожаловать! Выберите действие:")

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, text="Главное меню:"):
    leagues_stat, _, _ = get_competitions_status()
    ucl_open = leagues_stat.get("UCL", {}).get("open", 0)
    unl_open = leagues_stat.get("UNL", {}).get("open", 0)
    other_open = sum(leagues_stat.get(lid, {}).get("open", 0) for lid in ["PL", "PD", "FL1", "BL1", "SA"])

    ucl_btn_label = f"🟢 🏆 Лига чемпионов ({ucl_open} откр.)" if ucl_open > 0 else "🏆 Лига чемпионов"
    unl_btn_label = f"🟢 🌍 Лига наций ({unl_open} откр.)" if unl_open > 0 else "🌍 Лига наций"
    leagues_btn_label = f"🟢 ⚽ Прогнозы лиг ({other_open} откр.)" if other_open > 0 else "⚽ Прогнозы лиг"

    keyboard = []
    # Лига чемпионов отдельно
    keyboard.append([InlineKeyboardButton(ucl_btn_label, callback_data="league_UCL")])
    # Лига наций отдельно
    keyboard.append([InlineKeyboardButton(unl_btn_label, callback_data="league_UNL")])
    # Остальные лиги – в подменю
    keyboard.append([InlineKeyboardButton(leagues_btn_label, callback_data="leagues_submenu")])
    # Общие кнопки
    keyboard.append([InlineKeyboardButton("📊 Отчёты", callback_data="reports_menu")])
    keyboard.append([InlineKeyboardButton("🏅 Таблица лидеров", callback_data="leaderboard")])
    keyboard.append([InlineKeyboardButton("📝 Мои прогнозы", callback_data="mypredicts")])
    if is_admin(update.effective_user.id):
        keyboard.append([InlineKeyboardButton("⚙️ Админ-панель", callback_data="admin_panel")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")


async def show_reports_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📊 <b>ОТЧЁТЫ ПО ТУРНИРАМ</b>\n\n"
        "Выберите турнир для получения подробного отчёта со сводной статистикой и Excel-файлом:\n"
    )
    keyboard = [
        [InlineKeyboardButton("🌐 Общий отчёт (все турниры)", callback_data="report_all")],
        [InlineKeyboardButton("🏆 Лига чемпионов", callback_data="report_league_UCL")],
        [InlineKeyboardButton("🌍 Лига наций", callback_data="report_league_UNL")],
        [
            InlineKeyboardButton("🏴󠁧󠁢󠁥󠁮󠁧󠁿 АПЛ", callback_data="report_league_PL"),
            InlineKeyboardButton("🇪🇸 Ла Лига", callback_data="report_league_PD")
        ],
        [
            InlineKeyboardButton("🇩🇪 Бундеслига", callback_data="report_league_BL1"),
            InlineKeyboardButton("🇮🇹 Серия A", callback_data="report_league_SA")
        ],
        [InlineKeyboardButton("🇫🇷 Лига 1", callback_data="report_league_FL1")],
        [InlineKeyboardButton("🔙 Главное меню", callback_data="menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")

async def show_leaderboard_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🏅 <b>ТАБЛИЦА ЛИДЕРОВ</b>\n\n"
        "Выберите рейтинг для просмотра:\n\n"
        "🏆 <b>Лига чемпионов</b> — топ участников по матчам ЛЧ\n"
        "🌍 <b>Лига наций</b> — топ участников по матчам Лиги наций\n"
        "⚽ <b>Остальные лиги</b> — топ по национальным чемпионатам (АПЛ, Ла Лига, Серия А, Бундеслига, Лига 1)\n"
        "🌟 <b>Общий зачёт</b> — сводный рейтинг по всем турнирам"
    )
    keyboard = [
        [InlineKeyboardButton("🏆 Топ Лиги чемпионов", callback_data="lead_UCL")],
        [InlineKeyboardButton("🌍 Топ Лиги наций", callback_data="lead_UNL")],
        [InlineKeyboardButton("⚽ Топ остальных лиг", callback_data="lead_leagues")],
        [InlineKeyboardButton("🌟 Общий зачёт (все турниры)", callback_data="lead_all")],
        [InlineKeyboardButton("🔙 Главное меню", callback_data="menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")

async def show_leaderboard_view(update: Update, context: ContextTypes.DEFAULT_TYPE, category: str):
    category_titles = {
        "UCL": ("🏆", "Лидеры Лиги чемпионов"),
        "UNL": ("🌍", "Лидеры Лиги наций"),
        "leagues": ("⚽", "Лидеры остальных лиг"),
        "all": ("🌟", "Общий зачёт (все турниры)")
    }
    icon, cat_name = category_titles.get(category, ("🏅", "Таблица лидеров"))
    leaders = get_leaderboard_data(category)

    current_user_id = update.effective_user.id
    user_rank = None
    user_entry = None

    for idx, entry in enumerate(leaders, 1):
        if entry["user_id"] == current_user_id:
            user_rank = idx
            user_entry = entry
            break

    if not leaders or all(e["score"] == 0 and e["total_preds"] == 0 for e in leaders):
        text = f"{icon} <b>{cat_name}</b>\n\nПока нет данных или завершённых матчей в этой категории."
    else:
        text = f"{icon} <b>{cat_name}</b>\n\n"
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        for i, entry in enumerate(leaders[:15], 1):
            rank_str = medals.get(i, f"<b>{i}.</b>")
            name = html.escape(entry["first_name"] if entry["first_name"] else f"Участник {entry['user_id']}")
            username_str = f" (@{html.escape(entry['username'])})" if entry["username"] else ""
            score = entry["score"]
            exact = entry["exact"]
            diff = entry["diff"]
            outcome = entry["outcome"]

            is_me = " 👈 (Вы)" if entry["user_id"] == current_user_id else ""
            text += f"{rank_str} {name}{username_str}{is_me} — <b>{score}</b> очк.\n"
            text += f"   └ 🎯 {exact} | 📐 {diff} | 🎲 {outcome}\n\n"

        if user_rank and user_rank > 15 and user_entry:
            text += "──────────────────────────\n"
            text += f"Ваше место: <b>#{user_rank}</b> — <b>{user_entry['score']}</b> очк. (🎯 {user_entry['exact']} | 📐 {user_entry['diff']} | 🎲 {user_entry['outcome']})\n\n"

        text += "💡 <i>🎯 точный счёт (+6) | 📐 разница (+3) | 🎲 исход (+2)</i>"

    keyboard = [
        [InlineKeyboardButton("🔄 Обновить", callback_data=f"lead_{category}")],
        [InlineKeyboardButton("🔙 К выбору топа", callback_data="leaderboard")],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")

async def show_my_predictions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    rows = get_user_predictions(user.id)
    if not rows:
        text = (
            "👤 <b>МОИ ПРОГНОЗЫ</b>\n\n"
            "У вас пока нет сделанных прогнозов.\n"
            "Перейдите в меню 🏆 Лиги чемпионов, 🌍 Лиги наций или ⚽ Прогнозы лиг, чтобы сделать прогноз!"
        )
        keyboard = [[InlineKeyboardButton("🔙 Главное меню", callback_data="menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        if update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="HTML")
        else:
            await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")
        return

    total_preds = len(rows)
    total_score = 0
    exact_count = 0
    diff_count = 0
    outcome_count = 0
    zero_count = 0

    active_matches = []
    finished_matches = []

    for r in rows:
        mid, home, away, pred, res, start_time, league_id, matchday, cur_res = r
        if res is not None:
            pts = calc_match_points(pred, res)
            total_score += pts
            if pts == 6:
                exact_count += 1
            elif pts == 3:
                diff_count += 1
            elif pts == 2:
                outcome_count += 1
            else:
                zero_count += 1
            finished_matches.append((r, pts))
        else:
            active_matches.append(r)

    active_matches.sort(key=lambda x: x[5] if x[5] else "")

    text = "👤 <b>МОИ ПРОГНОЗЫ</b>\n\n"
    text += "📊 <b>Ваша статистика:</b>\n"
    text += f"• Всего прогнозов: <b>{total_preds}</b>\n"
    text += f"• Набрано очков: <b>{total_score}</b> 🏆\n"
    text += f"• 🎯 Точный счёт (+6): <b>{exact_count}</b>\n"
    text += f"• 📐 Разница мячей (+3): <b>{diff_count}</b>\n"
    text += f"• 🎲 Исход матча (+2): <b>{outcome_count}</b>\n"
    if finished_matches:
        text += f"• ❌ Без очков (0): <b>{zero_count}</b>\n"
    text += "\n"

    if active_matches:
        text += f"⏳ <b>Предстоящие / активные матчи ({len(active_matches)}):</b>\n"
        for r in active_matches[:10]:
            mid, home, away, pred, res, start_time, league_id, matchday, cur_res = r
            league_flag = LEAGUES.get(league_id, {}).get("flag", "⚽")
            time_str = ""
            if start_time:
                try:
                    s_dt = datetime.strptime(start_time, "%Y-%m-%d %H:%M")
                    day_ru = SHORT_DAYS.get(s_dt.strftime("%a"), "")
                    time_str = f"🗓 {day_ru} {s_dt.strftime('%d.%m %H:%M')}"
                except:
                    time_str = f"🗓 {start_time}"
            score_live = f" (сейчас {cur_res})" if cur_res else ""
            text += f"{league_flag} <b>{html.escape(home)} – {html.escape(away)}</b>\n"
            text += f"   {time_str} | Прогноз: <b>{pred}</b>{score_live} ✏️\n"
        if len(active_matches) > 10:
            text += f"   <i>...и ещё {len(active_matches) - 10} матчей</i>\n"
        text += "\n"

    if finished_matches:
        text += f"✅ <b>Завершённые матчи ({len(finished_matches)}):</b>\n"
        for r, pts in finished_matches[:12]:
            mid, home, away, pred, res, start_time, league_id, matchday, cur_res = r
            league_flag = LEAGUES.get(league_id, {}).get("flag", "⚽")
            if pts == 6:
                tag = "🎯 <b>+6 очк.</b>"
            elif pts == 3:
                tag = "📐 <b>+3 очк.</b>"
            elif pts == 2:
                tag = "🎲 <b>+2 очк.</b>"
            else:
                tag = "❌ <b>0 очк.</b>"
            text += f"{league_flag} <b>{html.escape(home)} {res} {html.escape(away)}</b>\n"
            text += f"   Прогноз: <b>{pred}</b> — {tag}\n"
        if len(finished_matches) > 12:
            text += f"   <i>...показаны последние 12 из {len(finished_matches)} матчей</i>\n"

    keyboard = [[InlineKeyboardButton("🔙 Главное меню", callback_data="menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")

async def show_leagues_submenu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    leagues_stat, _, _ = get_competitions_status()
    other_leagues = ["PL", "PD", "FL1", "BL1", "SA"]
    keyboard = []
    for lid in other_leagues:
        info = LEAGUES[lid]
        stat = leagues_stat.get(lid, {"open": 0, "locked": 0, "in_progress": 0, "total": 0})
        if stat["open"] > 0:
            btn_text = f"🟢 {info['flag']} {info['name']} — Открыт ({stat['open']})"
        elif stat["locked"] > 0:
            btn_text = f"🔒 {info['flag']} {info['name']} (за 7 дней)"
        elif stat["total"] == 0:
            btn_text = f"⚪ {info['flag']} {info['name']} (нет матчей)"
        else:
            btn_text = f"⏳ {info['flag']} {info['name']} (матчи идут)"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"league_{lid}")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="menu")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = (
        "⚽ <b>ПРОГНОЗЫ НА НАЦИОНАЛЬНЫЕ ЛИГИ</b>\n\n"
        "Выберите лигу для прогнозов:\n\n"
        "🟢 — <b>приём прогнозов открыт</b> (можно голосовать)\n"
        "🔒 — откроется ровно за 7 дней до матчей\n"
        "⏳ — матчи идут\n"
        "⚪ — нет активных матчей"
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")

def get_ucl_tours():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        SELECT 
            COALESCE(matchday, 1) as mday,
            COUNT(*) as total,
            COUNT(CASE WHEN result IS NOT NULL THEN 1 END) as finished
        FROM matches
        WHERE league_id = 'UCL'
        GROUP BY mday
        ORDER BY mday
    ''')
    stat_rows = cur.fetchall()

    cur.execute('''
        SELECT COALESCE(matchday, 1), start_time
        FROM matches
        WHERE league_id = 'UCL' AND result IS NULL
    ''')
    active_rows = cur.fetchall()
    cur.close()
    conn.close()

    stats = {r[0]: {"total": r[1], "finished": r[2]} for r in stat_rows}

    open_counts = {t: 0 for t in range(1, 9)}
    locked_counts = {t: 0 for t in range(1, 9)}
    in_progress_counts = {t: 0 for t in range(1, 9)}

    for mday, st in active_rows:
        if mday in open_counts and st:
            if is_match_open(st):
                open_counts[mday] += 1
            elif is_match_locked(st):
                locked_counts[mday] += 1
            else:
                in_progress_counts[mday] += 1

    tours = {}
    for t in range(1, 9):
        info = stats.get(t, {"total": 0, "finished": 0})
        total = info["total"]
        finished = info["finished"]
        op = open_counts[t]
        lk = locked_counts[t]
        inp = in_progress_counts[t]

        if t == 1 or (total > 0 and finished == total):
            status = "finished"
        elif op > 0:
            status = "open"
        elif inp > 0:
            status = "in_progress"
        elif lk > 0:
            status = "locked"
        else:
            status = "empty"

        tours[t] = {
            "status": status,
            "open": op,
            "locked": lk,
            "total": total,
            "finished": finished
        }
    return tours

def get_ucl_round_matches(round_num, user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    if round_num == 1:
        cur.execute('''
            SELECT m.match_id, m.home, m.away, m.day, m.start_time, m.result, m.current_result, p.prediction
            FROM matches m
            LEFT JOIN predictions p ON m.match_id = p.match_id AND p.user_id = %s
            WHERE m.league_id = 'UCL' AND (m.matchday = %s OR m.matchday IS NULL)
            ORDER BY m.start_time, m.match_id
        ''', (user_id, round_num))
    else:
        cur.execute('''
            SELECT m.match_id, m.home, m.away, m.day, m.start_time, m.result, m.current_result, p.prediction
            FROM matches m
            LEFT JOIN predictions p ON m.match_id = p.match_id AND p.user_id = %s
            WHERE m.league_id = 'UCL' AND m.matchday = %s
            ORDER BY m.start_time, m.match_id
        ''', (user_id, round_num))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

async def show_ucl_rounds_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["current_league"] = "UCL"
    tours = get_ucl_tours()

    def btn_text(t):
        st = tours[t]["status"]
        op = tours[t]["open"]
        if t == 1 or st == "finished":
            return "1 (закончен) ✅" if t == 1 else f"{t} (закончен) ✅"
        elif st == "open":
            return f"🟢 {t} тур (открыт: {op})" if op > 0 else f"🟢 {t} тур"
        elif st == "in_progress":
            return f"⏳ {t} тур (идёт)"
        elif st == "locked":
            return f"🔒 {t} тур"
        else:
            return f"⚪ {t} тур"

    keyboard = [
        [InlineKeyboardButton(btn_text(1), callback_data="ucl_round_1")],
        [
            InlineKeyboardButton(btn_text(2), callback_data="ucl_round_2"),
            InlineKeyboardButton(btn_text(3), callback_data="ucl_round_3")
        ],
        [
            InlineKeyboardButton(btn_text(4), callback_data="ucl_round_4"),
            InlineKeyboardButton(btn_text(5), callback_data="ucl_round_5")
        ],
        [
            InlineKeyboardButton(btn_text(6), callback_data="ucl_round_6"),
            InlineKeyboardButton(btn_text(7), callback_data="ucl_round_7")
        ],
        [InlineKeyboardButton(btn_text(8), callback_data="ucl_round_8")],
        [InlineKeyboardButton("📊 Отчёт по Лиге чемпионов", callback_data="report_league_UCL")],
        [InlineKeyboardButton("🔙 Главное меню", callback_data="menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = (
        "🏆 <b>Лига чемпионов 2024/25</b>\n\n"
        "Выберите тур, чтобы посмотреть матчи или сделать прогноз:\n\n"
        "<b>Обозначения:</b>\n"
        "🟢 — <b>приём прогнозов открыт</b> (можно голосовать)\n"
        "🔒 — откроется ровно за 7 дней до матчей\n"
        "⏳ — матчи идут\n"
        "✅ — тур завершён"
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")

MATCHES_PER_PAGE = 5
MATCHES_PER_PAGE_VIEW = 6
NUM_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

def format_match_time(start_time_str):
    if not start_time_str:
        return "", "", ""
    try:
        dt = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M")
        day_ru = SHORT_DAYS.get(dt.strftime("%a"), dt.strftime("%a"))
        date_str = f"{day_ru}, {dt.strftime('%d.%m в %H:%M')}"
        deadline_str = (dt - timedelta(minutes=10)).strftime("%H:%M")
        open_str = (dt - timedelta(days=7)).strftime("%d.%m в %H:%M")
        return date_str, deadline_str, open_str
    except Exception:
        return start_time_str, "", ""

def format_match_card(m):
    """
    Красивая и читабельная карточка матча для общего просмотра тура/лиги.
    m: (match_id, home, away, day, start_time, result, current_result, prediction, *rest)
    """
    match_id, home, away, day, start_time, result, current_result, prediction = m[:8]
    home_esc = html.escape(home)
    away_esc = html.escape(away)
    
    date_str, deadline_str, open_str = format_match_time(start_time)
    
    # 1. Завершённый матч
    if result is not None:
        if prediction:
            pts = calc_match_points(prediction, result)
            if pts == 6:
                pred_badge = f"🎯 <b>{prediction}</b> (+6 очк.)"
            elif pts == 3:
                pred_badge = f"📐 <b>{prediction}</b> (+3 очк.)"
            elif pts == 2:
                pred_badge = f"🎲 <b>{prediction}</b> (+2 очк.)"
            else:
                pred_badge = f"❌ <b>{prediction}</b> (0 очк.)"
        else:
            pred_badge = "⚪ <i>Без прогноза</i>"
            
        time_info = f"│  🗓 {date_str}\n" if date_str else ""
        return (
            f"┌ ⚽ <b>{home_esc}  {result}  {away_esc}</b>  ✅\n"
            f"{time_info}"
            f"└  Прогноз: {pred_badge}"
        )

    # 2. Матч идёт прямо сейчас (LIVE)
    if current_result or (start_time and is_match_started(start_time)):
        score_display = f" {current_result} " if current_result else " vs "
        pred_badge = f"🎯 Ваш прогноз: <b>{prediction}</b> ✏️" if prediction else "⚪ <i>Прогноз не сделан</i>"
        time_info = f"│  🗓 {date_str} • ⏱ <i>Идёт матч</i>\n" if date_str else ""
        return (
            f"┌ ⚽ <b>{home_esc} {score_display} {away_esc}</b>  🔴 <b>LIVE</b>\n"
            f"{time_info}"
            f"└  {pred_badge}"
        )

    # 3. Приём прогнозов открыт (за 7 дней до матча, дедлайн за 10 мин)
    if start_time and is_match_open(start_time):
        pred_badge = f"🎯 Ваш прогноз: <b>{prediction}</b> ✅" if prediction else "⚠️ <b>Прогноз не сделан</b>"
        time_info = f"│  🗓 {date_str} • ⏳ <i>дедлайн {deadline_str}</i>\n" if date_str else ""
        return (
            f"┌ ⚽ <b>{home_esc} — {away_esc}</b>\n"
            f"{time_info}"
            f"└  {pred_badge}"
        )

    # 4. Приём прогнозов заблокирован (> 7 дней до матча)
    if start_time and is_match_locked(start_time):
        time_info = f"│  🗓 {date_str}\n" if date_str else ""
        return (
            f"┌ ⚽ <b>{home_esc} — {away_esc}</b>\n"
            f"{time_info}"
            f"└  🔒 <i>Откроется {open_str}</i>"
        )

    # 5. Дедлайн прошёл (< 10 минут до матча)
    pred_badge = f"🎯 Ваш прогноз: <b>{prediction}</b>" if prediction else "⚪ <i>Без прогноза</i>"
    time_info = f"│  🗓 {date_str}\n" if date_str else ""
    return (
        f"┌ ⚽ <b>{home_esc} — {away_esc}</b>\n"
        f"{time_info}"
        f"└  ⏱ <i>Приём закрыт</i> • {pred_badge}"
    )

async def show_ucl_round_view(update: Update, context: ContextTypes.DEFAULT_TYPE, round_num: int, page: int = None):
    user = update.effective_user
    context.user_data["current_ucl_round"] = round_num

    if page is None:
        page = context.user_data.get(f"view_page_ucl_{round_num}", 0)
    context.user_data[f"view_page_ucl_{round_num}"] = page

    rows = get_ucl_round_matches(round_num, user.id)

    status_label = " (закончен)" if round_num == 1 else ""
    if not rows:
        text = f"🏆 <b>Лига чемпионов — {round_num} тур</b>{status_label}\n\nМатчи для этого тура пока не добавлены в базу."
        keyboard = [[InlineKeyboardButton("🔙 К списку туров", callback_data="league_UCL")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        if update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="HTML")
        else:
            await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")
        return

    total_matches = len(rows)
    total_pages = (total_matches + MATCHES_PER_PAGE_VIEW - 1) // MATCHES_PER_PAGE_VIEW
    page = max(0, min(page, total_pages - 1))
    context.user_data[f"view_page_ucl_{round_num}"] = page

    start_idx = page * MATCHES_PER_PAGE_VIEW
    end_idx = min(start_idx + MATCHES_PER_PAGE_VIEW, total_matches)
    current_page_matches = rows[start_idx:end_idx]

    preds_count = sum(1 for m in rows if m[7])
    open_count = sum(1 for m in rows if m[5] is None and m[4] and is_match_open(m[4]))

    page_info = f"📄 <i>Страница {page + 1} из {total_pages} (матчи {start_idx + 1}–{end_idx} из {total_matches})</i>\n" if total_pages > 1 else ""
    text = (
        f"🏆 <b>ЛИГА ЧЕМПИОНОВ • {round_num} ТУР</b>{status_label}\n"
        f"{page_info}"
        f"📊 <b>Прогнозы:</b> {preds_count} из {total_matches} сделано • 🟢 <b>Открыто:</b> {open_count}\n"
        f"────────────────────────────\n\n"
    )

    for m in current_page_matches:
        text += format_match_card(m) + "\n\n"

    text += "────────────────────────────"

    keyboard = []
    if open_count > 0:
        keyboard.append([InlineKeyboardButton(f"🟢 ✏️ Сделать прогноз ({open_count})", callback_data=f"ucl_predict_{round_num}")])

    if total_pages > 1:
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("◀️ Назад", callback_data=f"ucl_viewpage_{round_num}_{page - 1}"))
        else:
            nav_row.append(InlineKeyboardButton("⛔", callback_data="noop"))

        nav_row.append(InlineKeyboardButton(f"{page + 1} / {total_pages}", callback_data="noop"))

        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton("Вперёд ▶️", callback_data=f"ucl_viewpage_{round_num}_{page + 1}"))
        else:
            nav_row.append(InlineKeyboardButton("⛔", callback_data="noop"))
        keyboard.append(nav_row)

    keyboard.append([InlineKeyboardButton("📊 Отчёт по Лиге чемпионов", callback_data="report_league_UCL")])
    keyboard.append([InlineKeyboardButton("🔙 К списку туров", callback_data="league_UCL")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")

async def show_ucl_predict_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, round_num: int, page: int = None):
    user = update.effective_user
    context.user_data["current_ucl_round"] = round_num

    if page is None:
        page = context.user_data.get(f"predict_page_ucl_{round_num}", 0)
    context.user_data[f"predict_page_ucl_{round_num}"] = page
    context.user_data["last_predict_type"] = ("ucl", round_num)

    rows = get_ucl_round_matches(round_num, user.id)
    open_matches = []
    locked_count = 0
    for row in rows:
        match_id, home, away, day, start_time, result, current_result, prediction = row
        if result is None and start_time:
            if is_match_open(start_time):
                open_matches.append(row)
            elif is_match_locked(start_time):
                locked_count += 1

    if not open_matches:
        keyboard = [[InlineKeyboardButton("🔙 Назад к туру", callback_data=f"ucl_round_{round_num}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        if locked_count > 0:
            text = f"🏆 <b>Лига чемпионов — {round_num} тур</b>\n\n🔒 Приём прогнозов на матчи этого тура откроется за <b>7 дней</b> до их начала."
        else:
            text = f"🏆 <b>Лига чемпионов — {round_num} тур</b>\n\nВ этом туре сейчас нет открытых матчей для прогноза."
        if update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="HTML")
        else:
            await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")
        return

    total_matches = len(open_matches)
    total_pages = (total_matches + MATCHES_PER_PAGE - 1) // MATCHES_PER_PAGE
    page = max(0, min(page, total_pages - 1))
    context.user_data[f"predict_page_ucl_{round_num}"] = page

    start_idx = page * MATCHES_PER_PAGE
    end_idx = min(start_idx + MATCHES_PER_PAGE, total_matches)
    current_page_matches = open_matches[start_idx:end_idx]

    text = (
        f"🏆 <b>ЛИГА ЧЕМПИОНОВ • {round_num} ТУР</b>\n"
        f"✍️ <b>Оформление прогнозов</b>\n"
        f"📄 <i>Страница {page + 1} из {total_pages} (матчи {start_idx + 1}–{end_idx} из {total_matches})</i>\n"
        f"────────────────────────────\n\n"
    )

    keyboard = []
    for idx, m in enumerate(current_page_matches, 1):
        num_icon = NUM_EMOJIS[idx - 1] if idx <= len(NUM_EMOJIS) else f"{idx}."
        match_id, home, away, day, start_time, result, current_result, prediction = m
        date_str, deadline_str, _ = format_match_time(start_time)
        time_line = f"🗓 {date_str} • ⏳ <i>дедлайн {deadline_str}</i>" if date_str else ""

        if prediction:
            pred_line = f"Ваш прогноз: <b>{prediction}</b> ✅"
            btn_label = f"{num_icon} {home} — {away} ({prediction}) ✅"
        else:
            pred_line = "Ваш прогноз: <i>не сделан</i> ⚪"
            btn_label = f"{num_icon} {home} — {away} ✏️"

        text += (
            f"{num_icon} <b>{html.escape(home)} — {html.escape(away)}</b>\n"
            f"   {time_line}\n"
            f"   {pred_line}\n\n"
        )
        keyboard.append([InlineKeyboardButton(btn_label, callback_data=f"pred_{match_id}")])

    text += "────────────────────────────\n"
    text += "👇 <b>Нажмите на кнопку матча, чтобы отправить счёт:</b>"

    if total_pages > 1:
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("◀️ Назад", callback_data=f"ucl_page_{round_num}_{page - 1}"))
        else:
            nav_row.append(InlineKeyboardButton("⛔", callback_data="noop"))

        nav_row.append(InlineKeyboardButton(f"{page + 1} / {total_pages}", callback_data="noop"))

        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton("Вперёд ▶️", callback_data=f"ucl_page_{round_num}_{page + 1}"))
        else:
            nav_row.append(InlineKeyboardButton("⛔", callback_data="noop"))
        keyboard.append(nav_row)

    keyboard.append([InlineKeyboardButton("🔙 Назад к туру", callback_data=f"ucl_round_{round_num}")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")

# --- ФУНКЦИИ ЛИГИ НАЦИЙ И ЛИГ (МАТЧИ И РАСПИСАНИЕ) ---
def get_matches_for_league_view(user_id, league_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        SELECT m.match_id, m.home, m.away, m.day, m.start_time, m.result, m.current_result, p.prediction, m.matchday
        FROM matches m
        LEFT JOIN predictions p ON m.match_id = p.match_id AND p.user_id = %s
        WHERE m.league_id = %s
        ORDER BY m.start_time ASC, m.match_id ASC
    ''', (user_id, league_id))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

async def show_unl_rounds_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Прямой переход к расписанию и результатам Лиги наций без лишних кнопок туров
    await show_league_menu(update, context, "UNL")

async def show_unl_round_view(update: Update, context: ContextTypes.DEFAULT_TYPE, round_num: int = None, page: int = None):
    await show_league_menu(update, context, "UNL", page=page)

async def show_unl_predict_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, round_num: int = None, page: int = None):
    await show_predict_menu(update, context, "UNL", page=page)

async def show_league_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, league_id: str, page: int = None):
    user = update.effective_user
    context.user_data["current_league"] = league_id

    rows = get_matches_for_league_view(user.id, league_id)
    league_info = LEAGUES.get(league_id, {"flag": "⚽", "name": league_id})

    # Определение кнопки "Назад" в зависимости от турнира
    if league_id in ("UCL", "UNL"):
        back_btn = InlineKeyboardButton("🏠 Главное меню", callback_data="menu")
    else:
        back_btn = InlineKeyboardButton("🔙 К списку лиг", callback_data="leagues_submenu")

    if not rows:
        text = f"{league_info['flag']} <b>{league_info['name']}</b>\n\nВ базе пока нет матчей для этого турнира."
        keyboard = [[back_btn]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        if update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="HTML")
        else:
            await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")
        return

    total_matches = len(rows)
    total_pages = (total_matches + MATCHES_PER_PAGE_VIEW - 1) // MATCHES_PER_PAGE_VIEW

    if page is None:
        if f"view_page_{league_id}" in context.user_data:
            page = context.user_data[f"view_page_{league_id}"]
        else:
            # Находим первый незавершённый матч, чтобы сразу открыть актуальную страницу
            first_active_idx = 0
            found_active = False
            for i, r in enumerate(rows):
                if r[5] is None:  # result is None
                    first_active_idx = i
                    found_active = True
                    break
            if not found_active:
                first_active_idx = max(0, total_matches - 1)
            page = first_active_idx // MATCHES_PER_PAGE_VIEW

    page = max(0, min(page, total_pages - 1))
    context.user_data[f"view_page_{league_id}"] = page

    start_idx = page * MATCHES_PER_PAGE_VIEW
    end_idx = min(start_idx + MATCHES_PER_PAGE_VIEW, total_matches)
    current_page_matches = rows[start_idx:end_idx]

    preds_count = sum(1 for m in rows if m[7])
    open_count = sum(1 for m in rows if m[5] is None and m[4] and is_match_open(m[4]))

    page_info = f"📄 <i>Страница {page + 1} из {total_pages} (матчи {start_idx + 1}–{end_idx} из {total_matches})</i>\n" if total_pages > 1 else ""
    text = (
        f"{league_info['flag']} <b>{league_info['name'].upper()} • РАСПИСАНИЕ И РЕЗУЛЬТАТЫ</b>\n"
        f"{page_info}"
        f"📊 <b>Прогнозы:</b> {preds_count} из {total_matches} сделано • 🟢 <b>Открыто:</b> {open_count}\n"
        f"────────────────────────────\n\n"
    )

    for m in current_page_matches:
        text += format_match_card(m) + "\n\n"

    text += "────────────────────────────"

    keyboard = []
    if open_count > 0:
        keyboard.append([InlineKeyboardButton(f"🟢 ✏️ Сделать прогноз ({open_count})", callback_data=f"predict_{league_id}")])

    if total_pages > 1:
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("◀️ Назад", callback_data=f"league_viewpage_{league_id}_{page - 1}"))
        else:
            nav_row.append(InlineKeyboardButton("⛔", callback_data="noop"))

        nav_row.append(InlineKeyboardButton(f"{page + 1} / {total_pages}", callback_data="noop"))

        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton("Вперёд ▶️", callback_data=f"league_viewpage_{league_id}_{page + 1}"))
        else:
            nav_row.append(InlineKeyboardButton("⛔", callback_data="noop"))
        keyboard.append(nav_row)

    keyboard.append([InlineKeyboardButton("📊 Отчёт по турниру", callback_data=f"report_league_{league_id}")])
    keyboard.append([back_btn])

    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")

async def show_predict_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, league_id: str, page: int = None):
    user = update.effective_user
    context.user_data["current_league"] = league_id

    if page is None:
        page = context.user_data.get(f"predict_page_{league_id}", 0)
    context.user_data[f"predict_page_{league_id}"] = page
    context.user_data["last_predict_type"] = ("league", league_id)

    rows = get_matches_for_league_view(user.id, league_id)
    open_matches = []
    locked_count = 0
    for row in rows:
        match_id, home, away, day, start_time, result, current_result, prediction, *rest = row
        if result is None and start_time:
            if is_match_open(start_time):
                open_matches.append(row)
            elif is_match_locked(start_time):
                locked_count += 1

    league_info = LEAGUES.get(league_id, {"flag": "⚽", "name": league_id})
    if not open_matches:
        keyboard = [[InlineKeyboardButton("🔙 Назад к матчам", callback_data=f"league_{league_id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        if locked_count > 0:
            text = f"{league_info['flag']} <b>{league_info['name']}</b>\n\n🔒 Приём прогнозов откроется ровно <b>за 7 дней</b> до начала матчей."
        else:
            text = f"{league_info['flag']} <b>{league_info['name']}</b>\n\nСейчас нет открытых матчей для прогноза."
        if update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="HTML")
        else:
            await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")
        return

    total_matches = len(open_matches)
    total_pages = (total_matches + MATCHES_PER_PAGE - 1) // MATCHES_PER_PAGE
    page = max(0, min(page, total_pages - 1))
    context.user_data[f"predict_page_{league_id}"] = page

    start_idx = page * MATCHES_PER_PAGE
    end_idx = min(start_idx + MATCHES_PER_PAGE, total_matches)
    current_page_matches = open_matches[start_idx:end_idx]

    text = (
        f"{league_info['flag']} <b>{league_info['name'].upper()}</b>\n"
        f"✍️ <b>Оформление прогнозов</b>\n"
        f"📄 <i>Страница {page + 1} из {total_pages} (матчи {start_idx + 1}–{end_idx} из {total_matches})</i>\n"
        f"────────────────────────────\n\n"
    )

    keyboard = []
    for idx, m in enumerate(current_page_matches, 1):
        num_icon = NUM_EMOJIS[idx - 1] if idx <= len(NUM_EMOJIS) else f"{idx}."
        match_id, home, away, day, start_time, result, current_result, prediction, *rest = m
        date_str, deadline_str, _ = format_match_time(start_time)
        time_line = f"🗓 {date_str} • ⏳ <i>дедлайн {deadline_str}</i>" if date_str else ""

        if prediction:
            pred_line = f"Ваш прогноз: <b>{prediction}</b> ✅"
            btn_label = f"{num_icon} {home} — {away} ({prediction}) ✅"
        else:
            pred_line = "Ваш прогноз: <i>не сделан</i> ⚪"
            btn_label = f"{num_icon} {home} — {away} ✏️"

        text += (
            f"{num_icon} <b>{html.escape(home)} — {html.escape(away)}</b>\n"
            f"   {time_line}\n"
            f"   {pred_line}\n\n"
        )
        keyboard.append([InlineKeyboardButton(btn_label, callback_data=f"pred_{match_id}")])

    text += "────────────────────────────\n"
    text += "👇 <b>Нажмите на кнопку матча, чтобы отправить счёт:</b>"

    if total_pages > 1:
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("◀️ Назад", callback_data=f"pred_page_{league_id}_{page - 1}"))
        else:
            nav_row.append(InlineKeyboardButton("⛔", callback_data="noop"))

        nav_row.append(InlineKeyboardButton(f"{page + 1} / {total_pages}", callback_data="noop"))

        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton("Вперёд ▶️", callback_data=f"pred_page_{league_id}_{page + 1}"))
        else:
            nav_row.append(InlineKeyboardButton("⛔", callback_data="noop"))
        keyboard.append(nav_row)

    keyboard.append([InlineKeyboardButton("🔙 Назад к матчам", callback_data=f"league_{league_id}")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")

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
    elif data == "league_UCL":
        await show_ucl_rounds_menu(update, context)
    elif data == "league_UNL":
        await show_league_menu(update, context, "UNL")
    elif data.startswith("ucl_round_"):
        round_num = int(data.split("_")[2])
        await show_ucl_round_view(update, context, round_num)
    elif data.startswith("unl_round_"):
        await show_league_menu(update, context, "UNL")
    elif data.startswith("ucl_predict_"):
        round_num = int(data.split("_")[2])
        await show_ucl_predict_menu(update, context, round_num)
    elif data.startswith("unl_predict_"):
        await show_predict_menu(update, context, "UNL")
    elif data.startswith("ucl_viewpage_"):
        parts = data.split("_")
        round_num = int(parts[2])
        page = int(parts[3])
        await show_ucl_round_view(update, context, round_num, page=page)
    elif data.startswith("unl_viewpage_"):
        parts = data.split("_")
        page = int(parts[3]) if len(parts) > 3 else int(parts[2])
        await show_league_menu(update, context, "UNL", page=page)
    elif data.startswith("league_viewpage_"):
        parts = data.split("_")
        league_id = parts[2]
        page = int(parts[3])
        await show_league_menu(update, context, league_id, page=page)
    elif data.startswith("ucl_page_"):
        parts = data.split("_")
        round_num = int(parts[2])
        page = int(parts[3])
        await show_ucl_predict_menu(update, context, round_num, page=page)
    elif data.startswith("unl_page_"):
        parts = data.split("_")
        page = int(parts[3]) if len(parts) > 3 else int(parts[2])
        await show_predict_menu(update, context, "UNL", page=page)
    elif data.startswith("pred_page_"):
        parts = data.split("_")
        league_id = parts[2]
        page = int(parts[3])
        await show_predict_menu(update, context, league_id, page=page)
    elif data.startswith("league_"):
        league_id = data.split("_")[1]
        if league_id == "UCL":
            await show_ucl_rounds_menu(update, context)
        else:
            await show_league_menu(update, context, league_id)
    elif data.startswith("predict_"):
        league_id = data.split("_")[1]
        if league_id == "UCL":
            await show_ucl_rounds_menu(update, context)
        else:
            await show_predict_menu(update, context, league_id)
    elif data.startswith("pred_"):
        match_id = int(data.split("_")[1])
        match = get_match(match_id)
        if not match:
            await query.answer("Матч не найден.", show_alert=True)
            return
        match_id, home, away, day, result, start_time, api_id, current_result, league_id, *rest = match
        if rest and rest[0]:
            if league_id == "UCL":
                context.user_data["current_ucl_round"] = rest[0]
            elif league_id == "UNL":
                context.user_data["current_unl_round"] = rest[0]
        if result is not None:
            await query.answer("Этот матч уже завершён, прогнозы не принимаются.", show_alert=True)
            return
        if is_match_locked(start_time):
            open_str = ""
            try:
                s_dt = datetime.strptime(start_time, "%Y-%m-%d %H:%M")
                open_dt = s_dt - timedelta(days=7)
                open_str = f" ({open_dt.strftime('%d.%m в %H:%M')})"
            except:
                pass
            await query.answer(f"🔒 Приём прогнозов откроется ровно за 7 дней до начала{open_str}!", show_alert=True)
            return
        if not is_match_open(start_time):
            await query.answer("⏱ Приём прогнозов на этот матч уже закрыт (за 10 минут до начала).", show_alert=True)
            return
        context.user_data["awaiting_score"] = match_id

        user_pred = None
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT prediction FROM predictions WHERE user_id=%s AND match_id=%s", (query.from_user.id, match_id))
        pred_row = cur.fetchone()
        cur.close()
        conn.close()
        if pred_row and pred_row[0]:
            user_pred = pred_row[0]

        pred_status = f"🎯 Ваш текущий прогноз: <b>{user_pred}</b> ✅" if user_pred else "⚪ <i>Прогноз ещё не сделан</i>"

        date_str, deadline_str, is_live = format_match_time(start_time)
        league_info = LEAGUES.get(league_id, {"name": league_id, "flag": "🏆"})
        l_flag = league_info.get("flag", "🏆")
        l_name = league_info.get("name", league_id)

        time_line = f"🗓 {date_str} • ⏳ <i>дедлайн {deadline_str}</i>" if date_str else ""

        prompt_msg = (
            f"{l_flag} <b>{l_name.upper()}</b>\n"
            f"✍️ <b>Оформление прогноза</b>\n"
            f"────────────────────────────\n\n"
            f"┌ ⚽ <b>{html.escape(home)} — {html.escape(away)}</b>\n"
            f"│  {time_line}\n"
            f"└  {pred_status}\n\n"
            f"💬 <b>Отправьте счёт сообщением в чат:</b>\n"
            f"<i>Пример: <code>2:1</code> или <code>0-0</code></i>\n\n"
            f"📊 <b>Начисление очков:</b>\n"
            f"• <b>+6</b> — точный счёт\n"
            f"• <b>+3</b> — разница мячей\n"
            f"• <b>+2</b> — исход (победа/ничья)"
        )
        keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data="cancel_pred")]]
        await query.edit_message_text(prompt_msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    elif data == "cancel_pred":
        context.user_data.pop("awaiting_score", None)
        last_pred = context.user_data.get("last_predict_type")
        if last_pred and last_pred[0] == "ucl":
            r_num = last_pred[1]
            page = context.user_data.get(f"ucl_predict_page_{r_num}", 0)
            await show_ucl_predict_menu(update, context, r_num, page=page)
        elif last_pred and last_pred[0] == "unl":
            page = context.user_data.get("predict_page_UNL", 0)
            await show_predict_menu(update, context, "UNL", page=page)
        elif last_pred and last_pred[0] == "league":
            lid = last_pred[1]
            page = context.user_data.get(f"predict_page_{lid}", 0)
            await show_predict_menu(update, context, lid, page=page)
        else:
            await show_main_menu(update, context)
    elif data == "reports_menu":
        await show_reports_menu(update, context)
    elif data.startswith("report_league_"):
        league_id = data.split("_")[2]
        text_report, excel_data = generate_report(league_id)
        keyboard = [
            [InlineKeyboardButton("🔙 К выбору отчёта", callback_data="reports_menu")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="menu")]
        ]
        if excel_data is None:
            await query.edit_message_text(text_report, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
            return
        await query.edit_message_text(text_report, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        lname = LEAGUES.get(league_id, {}).get("name", league_id)
        try:
            await query.message.reply_document(
                document=excel_data,
                filename=f"report_{league_id}_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
                caption=f"📊 Отчёт по турниру {lname}"
            )
        except Exception as e:
            logger.error("Ошибка отправки Excel: %s", e)
    elif data == "report_all":
        text_report, excel_data = generate_report()
        keyboard = [
            [InlineKeyboardButton("🔙 К выбору отчёта", callback_data="reports_menu")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="menu")]
        ]
        if excel_data is None:
            await query.edit_message_text(text_report, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
            return
        await query.edit_message_text(text_report, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        try:
            await query.message.reply_document(
                document=excel_data,
                filename=f"report_all_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
                caption="📊 Общий отчёт по всем лигам"
            )
        except Exception as e:
            logger.error("Ошибка отправки Excel: %s", e)
    elif data == "leaderboard":
        await show_leaderboard_menu(update, context)
    elif data.startswith("lead_"):
        cat = data.split("_")[1]
        await show_leaderboard_view(update, context, cat)
    elif data == "mypredicts":
        await show_my_predictions(update, context)
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
    match_id, home, away, day, result, start_time, api_id, current_result, league_id, *rest = match
    if result is not None:
        await update.message.reply_text("Этот матч уже завершён, прогнозы не принимаются.")
        context.user_data.pop("awaiting_score", None)
        return
    if is_match_locked(start_time):
        await update.message.reply_text("🔒 Приём прогнозов на этот матч откроется за 7 дней до его начала.")
        context.user_data.pop("awaiting_score", None)
        return
    if not is_match_open(start_time):
        await update.message.reply_text("⏱ Приём прогнозов на этот матч уже закрыт (за 10 минут до начала).")
        context.user_data.pop("awaiting_score", None)
        return
    if not re.match(r'^\d+\s*[:;-]\s*\d+$', text) and not re.match(r'^\d+\s*[-]\s*\d+$', text):
        await update.message.reply_text("Неверный формат. Введите счёт в формате 2:1 или 2-1.")
        return
    score = re.sub(r'\s*[:-]\s*', ':', text)
    score = re.sub(r'\s*[-]\s*', ':', score)
    save_prediction(user.id, match_id, normalize_score(score))
    await update.message.reply_text(f"✅ Прогноз на матч <b>{html.escape(home)} – {html.escape(away)}</b> сохранён: <b>{score}</b>", parse_mode="HTML")
    context.user_data.pop("awaiting_score", None)
    if league_id == "UCL":
        round_num = context.user_data.get("current_ucl_round", 1)
        p = context.user_data.get(f"ucl_predict_page_{round_num}", 0)
        await show_ucl_predict_menu(update, context, round_num, page=p)
    elif league_id == "UNL":
        p = context.user_data.get("predict_page_UNL", 0)
        await show_predict_menu(update, context, "UNL", page=p)
    else:
        p = context.user_data.get(f"predict_page_{league_id}", 0)
        await show_predict_menu(update, context, league_id, page=p)

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
        context.user_data.pop("addmatch_round", None)
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
            "Введите код лиги (PL, PD, FL1, BL1, SA, UCL, UNL):\n"
            "Для отмены /cancel"
        )
    elif step == 3:
        league_id = text.upper()
        if league_id not in LEAGUES:
            await update.message.reply_text("Неверный код. Доступны: PL, PD, FL1, BL1, SA, UCL, UNL.\nПопробуйте снова.")
            return
        context.user_data["addmatch_league"] = league_id
        if league_id == "UCL":
            context.user_data["addmatch_step"] = 35
            await update.message.reply_text(
                "Введите номер тура Лиги чемпионов (1-8):\n"
                "Для отмены /cancel"
            )
        else:
            context.user_data["addmatch_step"] = 4
            await update.message.reply_text(
                "Введите дату и время начала в формате:\n"
                "ГГГГ-ММ-ДД ЧЧ:ММ (например, 2026-09-15 21:00)"
            )
    elif step == 35:
        try:
            round_num = int(text)
            if not (1 <= round_num <= 8):
                raise ValueError
        except ValueError:
            await update.message.reply_text("Номер тура должен быть числом от 1 до 8. Попробуйте снова:")
            return
        context.user_data["addmatch_round"] = round_num
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
        matchday = context.user_data.get("addmatch_round")
        match_id = add_match_manual(home, away, start_time, league_id, matchday=matchday)
        context.user_data.pop("addmatch_step", None)
        context.user_data.pop("addmatch_home", None)
        context.user_data.pop("addmatch_away", None)
        context.user_data.pop("addmatch_league", None)
        context.user_data.pop("addmatch_round", None)
        tour_str = f" (Тур {matchday})" if matchday else ""
        await update.message.reply_text(
            f"✅ Матч #{match_id} добавлен в лигу {LEAGUES[league_id]['name']}{tour_str}:\n"
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
        await update.message.reply_text(text_report, parse_mode="HTML")
        return
    await update.message.reply_text(text_report, parse_mode="HTML")
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

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Ошибка при обработке update: %s", update, exc_info=context.error)
    try:
        if isinstance(update, Update):
            if update.callback_query:
                await update.callback_query.answer("Произошла ошибка, попробуйте снова.", show_alert=True)
                await update.callback_query.edit_message_text(
                    "⚠️ Произошла ошибка. Вернитесь в меню: /start"
                )
            elif update.message:
                await update.message.reply_text("⚠️ Произошла ошибка. Попробуйте ещё раз или отправьте /start.")
    except Exception:
        logger.exception("Не удалось уведомить пользователя об ошибке")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_admin(user.id) and "addmatch_step" in context.user_data:
        await handle_addmatch_text(update, context)
        return
    if "awaiting_score" in context.user_data:
        await handle_score_input(update, context)
        return

async def post_init(application: Application):
    commands = [
        BotCommand("start", "🏠 Главное меню"),
        BotCommand("menu", "⚽ Выбор лиги и матчей"),
        BotCommand("leaderboard", "🏆 Таблица лидеров"),
        BotCommand("mypredicts", "🎯 Мои прогнозы"),
        BotCommand("reports", "📊 Отчёты и статистика"),
    ]
    try:
        await application.bot.set_my_commands(commands)
        logger.info("Команды бота успешно установлены через set_my_commands.")
    except Exception as e:
        logger.error("Ошибка при установке команд бота: %s", e)

    try:
        await application.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
        logger.info("Кнопка меню команд (⌘) успешно активирована через set_chat_menu_button.")
    except Exception as e:
        logger.error("Ошибка при установке кнопки меню: %s", e)

# --- ГЛАВНАЯ ---
def main():
    init_db()
    try:
        total = update_matches_from_api()
        print(f"При старте добавлено {total} матчей.")
        updated = update_results_from_api()
        print(f"При старте обновлено {updated} результатов.")
    except Exception as e:
        print(f"Ошибка при стартовом обновлении: {e}")

    scheduler = BackgroundScheduler()
    # Автоматическое добавление новых матчей всех лиг каждые 60 минут
    scheduler.add_job(
        func=update_matches_from_api,
        trigger=IntervalTrigger(minutes=60),
        id='auto_update_matches',
        name='Автозагрузка новых матчей',
        replace_existing=True
    )
    # Автоматическое обновление результатов и текущих счетов каждые 5 минут
    scheduler.add_job(
        func=update_results_from_api,
        trigger=IntervalTrigger(minutes=5),
        id='auto_update_results',
        name='Обновление результатов',
        replace_existing=True
    )
    scheduler.start()
    print("Планировщик запущен (автозагрузка матчей: каждый 1 ч, автообновление счетов: каждые 5 мин).")

    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", start))
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
    app.add_handler(CommandHandler("reports", show_reports_menu))
    app.add_handler(CommandHandler("leaderboard", show_leaderboard_menu))
    app.add_handler(CommandHandler("mypredicts", show_my_predictions))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.COMMAND, unknown))
    app.add_error_handler(error_handler)

    print("Бот запущен...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
