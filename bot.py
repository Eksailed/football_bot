import logging
import sqlite3
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# --- НАСТРОЙКИ ---
TOKEN = os.environ.get("8744688918:AAF6Q1L52Jo_03ewPBmOUP9Fo589ohAALbY") # Замените на реальный токен
if not TOKEN:
    raise ValueError("Токен не найден! Проверьте переменную окружения TELEGRAM_BOT_TOKEN")
ADMIN_USER_ID = 5601944469         # Замените на ваш Telegram ID

# --- СПИСОК МАТЧЕЙ (18) ---
# Добавлены поля home_logo и away_logo (пока пустые строки)
MATCHES = [
    {"id": 1, "home": "AEK Athens", "away": "LASK", "day": "Tue", "home_logo": "", "away_logo": ""},
    {"id": 2, "home": "Club Brugge", "away": "Aston Villa", "day": "Tue", "home_logo": "", "away_logo": ""},
    {"id": 3, "home": "B. Dortmund", "away": "Villarreal", "day": "Tue", "home_logo": "", "away_logo": ""},
    {"id": 4, "home": "Lille", "away": "Real Betis", "day": "Tue", "home_logo": "", "away_logo": ""},
    {"id": 5, "home": "Porto", "away": "Man City", "day": "Tue", "home_logo": "", "away_logo": ""},
    {"id": 6, "home": "Real Madrid", "away": "Inter", "day": "Tue", "home_logo": "", "away_logo": ""},
    {"id": 7, "home": "Barcelona", "away": "Feyenoord", "day": "Wed", "home_logo": "", "away_logo": ""},
    {"id": 8, "home": "Stuttgart", "away": "Viking", "day": "Wed", "home_logo": "", "away_logo": ""},
    {"id": 9, "home": "Liverpool", "away": "Atleti", "day": "Wed", "home_logo": "", "away_logo": ""},
    {"id": 10, "home": "Napoli", "away": "Arsenal", "day": "Wed", "home_logo": "", "away_logo": ""},
    {"id": 11, "home": "Paris", "away": "S. Bratislava", "day": "Wed", "home_logo": "", "away_logo": ""},
    {"id": 12, "home": "Sporting CP", "away": "Galatasaray", "day": "Wed", "home_logo": "", "away_logo": ""},
    {"id": 13, "home": "Fenerbahçe", "away": "Roma", "day": "Thu", "home_logo": "", "away_logo": ""},
    {"id": 14, "home": "PSV", "away": "Shakhtar", "day": "Thu", "home_logo": "", "away_logo": ""},
    {"id": 15, "home": "Bayern München", "away": "Bodø/Glimt", "day": "Thu", "home_logo": "", "away_logo": ""},
    {"id": 16, "home": "Como", "away": "Leipzig", "day": "Thu", "home_logo": "", "away_logo": ""},
    {"id": 17, "home": "Man Utd", "away": "Sabah", "day": "Thu", "home_logo": "", "away_logo": ""},
    {"id": 18, "home": "Slavia Praha", "away": "Lens", "day": "Thu", "home_logo": "", "away_logo": ""},
]

# --- БАЗА ДАННЫХ ---
DB_NAME = "predictions.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS matches
                 (match_id INTEGER PRIMARY KEY, home TEXT, away TEXT, day TEXT, result TEXT,
                  home_logo TEXT, away_logo TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS predictions
                 (user_id INTEGER, match_id INTEGER, prediction TEXT,
                  PRIMARY KEY (user_id, match_id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS scores
                 (user_id INTEGER PRIMARY KEY, score INTEGER DEFAULT 0)''')
    # Заполняем матчи
    for m in MATCHES:
        c.execute("INSERT OR IGNORE INTO matches (match_id, home, away, day, home_logo, away_logo) VALUES (?,?,?,?,?,?)",
                  (m["id"], m["home"], m["away"], m["day"], m["home_logo"], m["away_logo"]))
    conn.commit()
    conn.close()

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
    c.execute("SELECT match_id, home, away, day, result, home_logo, away_logo FROM matches WHERE match_id=?", (match_id,))
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

def set_logo(match_id, team, url):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    if team == "home":
        c.execute("UPDATE matches SET home_logo=? WHERE match_id=?", (url, match_id))
    elif team == "away":
        c.execute("UPDATE matches SET away_logo=? WHERE match_id=?", (url, match_id))
    conn.commit()
    conn.close()

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
        preds = c.fetchall()
        score = sum(1 for pred, res in preds if pred == res)
        c.execute("INSERT OR REPLACE INTO scores (user_id, score) VALUES (?,?)", (user_id, score))
    conn.commit()
    conn.close()

def get_all_matches():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT match_id, home, away, day, result FROM matches ORDER BY match_id")
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

# --- ОБРАБОТЧИКИ ---
logging.basicConfig(level=logging.INFO)

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
        rows = get_all_matches()
        text = "📋 Список матчей:\n\n"
        for m in rows:
            match_id, home, away, day, result = m
            status = "✅" if result else "⏳"
            text += f"{match_id}. {home} – {away} ({day}) {status}\n"
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="menu")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "mypredicts":
        user = update.effective_user
        rows = get_user_predictions(user.id)
        if not rows:
            text = "У вас пока нет прогнозов."
        else:
            text = "📝 Ваши прогнозы:\n\n"
            for r in rows:
                match_id, home, away, pred, result = r
                status = "✅" if result else "⏳"
                text += f"#{match_id} {home} – {away}: {pred} {status}\n"
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="menu")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "results":
        rows = get_all_matches()
        text = "🏆 Результаты завершённых матчей:\n\n"
        found = False
        for m in rows:
            match_id, home, away, day, result = m
            if result:
                found = True
                text += f"{match_id}. {home} – {away}: {result}\n"
        if not found:
            text = "Пока нет завершённых матчей."
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="menu")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "leaderboard":
        scores = get_scores()
        if not scores:
            text = "Пока нет данных для таблицы лидеров."
        else:
            text = "🏅 Таблица лидеров:\n\n"
            for i, (user_id, score, first_name, username) in enumerate(scores[:10], 1):
                name = first_name if first_name else str(user_id)
                if username:
                    name += f" (@{username})"
                text += f"{i}. {name} – {score} очков\n"
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="menu")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "make_predict":
        rows = get_all_matches()
        keyboard = []
        for m in rows:
            match_id, home, away, day, result = m
            if result is None:
                keyboard.append([InlineKeyboardButton(f"{match_id}. {home} – {away}", callback_data=f"pred_{match_id}")])
        if not keyboard:
            await query.edit_message_text("Нет доступных матчей для прогноза (все завершены).")
            return
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="menu")])
        await query.edit_message_text("Выберите матч для прогноза:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("pred_"):
        match_id = int(data.split("_")[1])
        context.user_data["predict_match_id"] = match_id
        match = get_match(match_id)
        if not match:
            await query.edit_message_text("Матч не найден.")
            return
        if match[4] is not None:
            await query.edit_message_text("Этот матч уже завершён, прогнозы не принимаются.")
            return

        # Проверяем, есть ли логотип хозяев
        home_logo = match[5]  # home_logo
        if home_logo:
            # Отправляем фото логотипа, а затем кнопки выбора исхода
            caption = f"🏟️ Сделайте прогноз на матч #{match_id}:\n{match[1]} – {match[2]}"
            # Удаляем предыдущее сообщение с кнопками (редактируем его в текст с ожиданием)
            await query.edit_message_text("⏳ Загружаю логотип...")
            # Отправляем новое сообщение с фото
            await query.message.reply_photo(photo=home_logo, caption=caption)
            # Теперь показываем кнопки выбора исхода
            keyboard = [
                [InlineKeyboardButton("1 (Победа хозяев)", callback_data=f"outcome_1")],
                [InlineKeyboardButton("X (Ничья)", callback_data=f"outcome_X")],
                [InlineKeyboardButton("2 (Победа гостей)", callback_data=f"outcome_2")],
                [InlineKeyboardButton("🔙 Назад", callback_data="make_predict")],
            ]
            await query.message.reply_text("Выберите исход:", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            # Без логотипа – как раньше
            keyboard = [
                [InlineKeyboardButton("1 (Победа хозяев)", callback_data=f"outcome_1")],
                [InlineKeyboardButton("X (Ничья)", callback_data=f"outcome_X")],
                [InlineKeyboardButton("2 (Победа гостей)", callback_data=f"outcome_2")],
                [InlineKeyboardButton("🔙 Назад", callback_data="make_predict")],
            ]
            await query.edit_message_text(f"Вы выбрали матч #{match_id}: {match[1]} – {match[2]}\nВыберите исход:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("outcome_"):
        outcome = data.split("_")[1]
        match_id = context.user_data.get("predict_match_id")
        if match_id is None:
            await query.edit_message_text("Ошибка, попробуйте снова.")
            return
        match = get_match(match_id)
        if not match:
            await query.edit_message_text("Матч не найден.")
            return
        if match[4] is not None:
            await query.edit_message_text("Этот матч уже завершён, прогнозы не принимаются.")
            return
        user = update.effective_user
        save_prediction(user.id, match_id, outcome)
        await query.edit_message_text(f"✅ Ваш прогноз на матч #{match_id} ({match[1]} – {match[2]}) сохранён: {outcome}")
        await show_main_menu(update, context, "Прогноз сохранён! Что дальше?")

    elif data == "menu":
        await show_main_menu(update, context)

# --- АДМИН КОМАНДЫ ---
async def set_result_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("У вас нет прав для этой команды.")
        return
    args = context.args
    if len(args) != 2:
        await update.message.reply_text("Использование: /setresult <номер_матча> <1/X/2>")
        return
    try:
        match_id = int(args[0])
        result = args[1].upper()
        if result not in ("1", "X", "2"):
            raise ValueError
    except ValueError:
        await update.message.reply_text("Неверный формат. Используйте номер матча и исход (1, X или 2).")
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

async def set_logo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("У вас нет прав для этой команды.")
        return
    args = context.args
    if len(args) != 3:
        await update.message.reply_text("Использование: /setlogo <match_id> <home|away> <url_картинки>")
        return
    try:
        match_id = int(args[0])
        team = args[1].lower()
        if team not in ("home", "away"):
            raise ValueError
        url = args[2]
        # простая проверка, что url начинается с http
        if not url.startswith("http"):
            await update.message.reply_text("URL должен начинаться с http:// или https://")
            return
    except ValueError:
        await update.message.reply_text("Неверный формат. Пример: /setlogo 5 home https://example.com/logo.png")
        return
    match = get_match(match_id)
    if not match:
        await update.message.reply_text("Матч не найден.")
        return
    set_logo(match_id, team, url)
    await update.message.reply_text(f"Логотип для команды {'хозяев' if team=='home' else 'гостей'} матча #{match_id} обновлён.")

async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Неизвестная команда. Используйте /start для начала.")

# --- ГЛАВНАЯ ---
def main():
    init_db()
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setresult", set_result_cmd))
    app.add_handler(CommandHandler("setlogo", set_logo_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.COMMAND, unknown))

    print("Бот запущен...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
