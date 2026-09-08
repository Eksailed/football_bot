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
from openpyxl.styles import Alignment, Font, Border, Side
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

# --- ЛИГИ (добавлена Лига чемпионов) ---
LEAGUES = {
    "PL": {"name": "АПЛ (Англия)", "flag": "🏴󠁧󠁢󠁥󠁮󠁧󠁿", "code": "PL"},
    "PD": {"name": "Ла Лига (Испания)", "flag": "🇪🇸", "code": "PD"},
    "FL1": {"name": "Лига 1 (Франция)", "flag": "🇫🇷", "code": "FL1"},
    "BL1": {"name": "Бундеслига (Германия)", "flag": "🇩🇪", "code": "BL1"},
    "UCL": {"name": "Лига чемпионов", "flag": "🏆", "code": "CL"},
}

# --- СЛОВАРЬ ПЕРЕВОДА НАЗВАНИЙ КОМАНД (можно дополнить) ---
TEAM_TRANSLATIONS = {
    # ... (оставьте свой словарь, он не изменился)
}

# --- ФЛАГИ КОМАНД (для отображения, можно расширить) ---
TEAM_FLAGS = { }

def translate_team(name: str) -> str:
    return TEAM_TRANSLATIONS.get(name, name)

def get_team_flag(name: str) -> str:
    return ""

# --- РАБОТА С БАЗОЙ ДАННЫХ (PostgreSQL) ---
# (все функции остаются как в предыдущей версии, я не буду дублировать их здесь, чтобы не загромождать ответ)
# Но в финальном коде они все будут.

# ------------------- ОБРАБОТЧИКИ КОМАНД -------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, user.username, user.first_name)
    await show_main_menu(update, context, "Добро пожаловать! Выберите лигу:")

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, text="Главное меню:"):
    keyboard = []
    for lid, info in LEAGUES.items():
        keyboard.append([InlineKeyboardButton(f"{info['flag']} {info['name']}", callback_data=f"league_{lid}")])
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
        [InlineKeyboardButton("🔙 Назад", callback_data="menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")

async def show_predict_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, league_id):
    user = update.effective_user
    rows = get_active_matches_with_user_prediction(user.id, league_id)
    keyboard = []
    for row in rows:
        match_id, home, away, day, start_time, api_id, current_result, prediction = row
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
        column = col[0].column_letter
        for cell in col:
            try:
                if len(str(cell.value)) > max_length:
                    max_length = len(str(cell.value))
            except:
                pass
        adjusted_width = min(max_length + 2, 50)
        ws.column_dimensions[column].width = adjusted_width

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

# --- ОБРАБОТЧИК КНОПОК ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "menu":
        await show_main_menu(update, context)
    elif data.startswith("league_"):
        league_id = data.split("_")[1]
        await show_league_menu(update, context, league_id)
    elif data.startswith("predict_"):
        league_id = data.split("_")[1]
        await show_predict_menu(update, context, league_id)
    elif data.startswith("pred_"):
        # ... (оставляем как было)
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
        await query.edit_message_text(text_report, parse_mode="Markdown")
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
        await query.edit_message_text(text_report, parse_mode="Markdown")
        try:
            await query.message.reply_document(
                document=excel_data,
                filename=f"report_all_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
                caption="📊 Общий отчёт по всем лигам"
            )
        except Exception as e:
            await query.edit_message_text(f"Ошибка отправки Excel: {e}")
    elif data == "leaderboard":
        # ... (оставляем как было)
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
        # ... (оставляем как было)
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
        # ... (админ-панель без изменений)
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
        # ... (загрузка всех лиг)
        if not is_admin(query.from_user.id):
            return
        await query.edit_message_text("⏳ Загружаю матчи всех лиг...")
        total = update_matches_from_api()
        await query.edit_message_text(f"✅ Добавлено матчей: {total}.")
    elif data == "fetch_results":
        # ... (обновление результатов)
        if not is_admin(query.from_user.id):
            return
        await query.edit_message_text("⏳ Обновляю результаты...")
        updated = update_results_from_api()
        await query.edit_message_text(f"✅ Обновлено результатов: {updated}.")
    elif data == "reset_confirm":
        # ... (подтверждение сброса)
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

# --- ОСТАЛЬНЫЕ ФУНКЦИИ (импорты, база данных, API, админ-команды) ---
# ... (здесь должны быть все функции, которые мы использовали выше: get_db_connection, normalize_score, init_db, is_admin, get_user, save_prediction, get_match, set_result, recalc_all_scores, get_active_matches, get_active_matches_with_user_prediction, get_scores, parse_score, get_outcome, is_match_open, is_match_started, is_match_finished, fetch_matches_from_api, update_matches_from_api, update_matches_from_api_for_league, fetch_match_details_by_api_id, update_results_from_api, handle_score_input, admins_cmd, addadmin_cmd, removeadmin_cmd, addmatch_start, addmatch_cancel, handle_addmatch_text, fetch_matches_cmd, fetch_results_cmd, set_result_cmd, reset_result_cmd, report_cmd, reset_all_cmd, handle_text, unknown, main)

# ВАЖНО: добавьте все недостающие функции из предыдущей версии, т.к. я не привожу их здесь для краткости.
# Они уже есть в вашем коде, просто нужно заменить блок с лигами, generate_report и обработчики.

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
    app.add_handler(CommandHandler("resetall", reset_all_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.COMMAND, unknown))

    print("Бот запущен...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
