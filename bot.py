import os
import logging
import psycopg2
from psycopg2 import sql

from telegram import (
    Update,
    LabeledPrice,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    PreCheckoutQueryHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ChatAction

# ------------------ НАСТРОЙКИ ------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "PLACE_YOUR_TOKEN_HERE")
PAYMENT_PROVIDER_TOKEN = os.environ.get("PAYMENT_PROVIDER_TOKEN", "PAYMENT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", 123456789))
DB_CONN_STR = os.environ.get("DB_CONN_STR", "postgresql://user:pass@host:5432/dbname")
BANNED_WORDS = {"badword1", "badword2"}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ------------------ СОСТОЯНИЯ ------------------
REG_GENDER, REG_AGE, REG_REGION, REG_LOOKING_FOR = range(4)
STATE_IN_CHAT = "in_chat"
STATE_WAITING_PARTNER = "waiting_partner"

# ------------------ ПАМЯТЬ В ПИТОНЕ ------------------
user_state = {}
user_partner = {}
waiting_queue = []

# ------------------ ФУНКЦИИ РАБОТЫ С БД ------------------

def get_user_profile(user_id: int):
    """Открываем connect-контекст, берём профайл."""
    with psycopg2.connect(DB_CONN_STR) as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT user_id, username, gender, age, region, looking_for, premium, banned
                FROM users
                WHERE user_id = %s
            """, (user_id,))
            row = cursor.fetchone()
    return row

def create_user(user_id: int, username: str):
    """Создаём запись, если её нет."""
    prof = get_user_profile(user_id)
    if prof is None:
        with psycopg2.connect(DB_CONN_STR) as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO users (user_id, username)
                    VALUES (%s, %s)
                """, (user_id, username))

def update_user_field(user_id: int, field: str, value):
    """Обновляем одно поле в таблице."""
    with psycopg2.connect(DB_CONN_STR) as conn:
        with conn.cursor() as cursor:
            query = sql.SQL("UPDATE users SET {field} = %s WHERE user_id = %s").format(
                field=sql.Identifier(field)
            )
            cursor.execute(query, (value, user_id))

def is_banned(user_id: int) -> bool:
    """Проверяем, забанен ли пользователь."""
    prof = get_user_profile(user_id)
    if prof:
        return bool(prof[7])
    return False

def add_report(reporter_id: int, target_id: int, reason: str):
    """Сохраняем жалобу."""
    with psycopg2.connect(DB_CONN_STR) as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO reports (reporter_id, target_id, reason)
                VALUES (%s, %s, %s)
            """, (reporter_id, target_id, reason))

# ------------------ ХЕНДЛЕРЫ РЕГИСТРАЦИИ ------------------

async def register_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    username = user.username or "NoUsername"

    create_user(user_id, username)

    if is_banned(user_id):
        await update.message.reply_text("Вы заблокированы.")
        return ConversationHandler.END

    await update.message.reply_text("Укажите свой пол (М/Ж).")
    user_state[user_id] = REG_GENDER
    return REG_GENDER

async def reg_gender(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip().lower()

    if text not in ["м", "ж"]:
        await update.message.reply_text("Введите 'М' или 'Ж'.")
        return REG_GENDER

    gender = "М" if text == "м" else "Ж"
    update_user_field(user_id, "gender", gender)
    await update.message.reply_text("Введите ваш возраст (число).")
    user_state[user_id] = REG_AGE
    return REG_AGE

async def reg_age(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if not text.isdigit():
        await update.message.reply_text("Возраст должен быть числом.")
        return REG_AGE

    age = int(text)
    if age < 14 or age > 120:
        await update.message.reply_text("Допустимый возраст 14–120.")
        return REG_AGE

    update_user_field(user_id, "age", age)
    await update.message.reply_text("Введите ваш регион (город/область).")
    user_state[user_id] = REG_REGION
    return REG_REGION

async def reg_region(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    region = update.message.text.strip()
    if len(region) < 2:
        await update.message.reply_text("Введите более корректное название региона.")
        return REG_REGION

    update_user_field(user_id, "region", region)
    await update.message.reply_text("Кого ищете? (М/Ж/любые)")
    user_state[user_id] = REG_LOOKING_FOR
    return REG_LOOKING_FOR

async def reg_looking_for(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip().lower()

    if text not in ["м", "ж", "любые"]:
        await update.message.reply_text("Введите 'М', 'Ж' или 'любые'.")
        return REG_LOOKING_FOR

    lf = "М" if text == "м" else ("Ж" if text == "ж" else "любые")
    update_user_field(user_id, "looking_for", lf)
    await update.message.reply_text("Анкета сохранена! Введите /search для поиска.")
    user_state[user_id] = None
    return ConversationHandler.END

# ------------------ ОБЩИЕ КОМАНДЫ ------------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "NoUsername"
    create_user(user_id, username)

    if is_banned(user_id):
        await update.message.reply_text("Вы заблокированы.")
        return

    await update.message.reply_text(
        "Добро пожаловать!\n"
        "/register — заполнить/обновить анкету\n"
        "/search — найти собеседника\n"
        "/stop — остановить чат\n"
        "/report — пожаловаться\n"
        "/premium — платная подписка\n"
    )
    user_state[user_id] = None

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_banned(user_id):
        await update.message.reply_text("Вы заблокированы.")
        return

    state = user_state.get(user_id)
    if state == STATE_IN_CHAT:
        partner_id = user_partner.get(user_id)
        if partner_id:
            await context.bot.send_message(
                partner_id,
                "Собеседник покинул чат. /search для нового."
            )
            user_state[partner_id] = None
            user_partner.pop(partner_id, None)

        user_partner.pop(user_id, None)
        user_state[user_id] = None
        await update.message.reply_text("Вы вышли из чата.")
    elif state == STATE_WAITING_PARTNER:
        if user_id in waiting_queue:
            waiting_queue.remove(user_id)
        user_state[user_id] = None
        await update.message.reply_text("Поиск отменён.")
    else:
        await update.message.reply_text("Вы не в чате и не в очереди.")

# ------------------ ПОИСК / ЧАТ ------------------

def match_users(user_id):
    user_profile = get_user_profile(user_id)
    if not user_profile:
        return None
    _, _, user_gender, _, user_region, user_looking_for, user_premium, user_banned = user_profile

    for candidate_id in waiting_queue:
        if candidate_id == user_id:
            continue
        cprof = get_user_profile(candidate_id)
        if not cprof:
            continue
        _, _, c_gender, _, c_region, c_looking_for, c_premium, c_banned = cprof

        if c_banned:
            continue

        cond1 = (user_looking_for == "любые") or (user_looking_for == c_gender)
        cond2 = (c_looking_for == "любые") or (c_looking_for == user_gender)
        if cond1 and cond2:
            return candidate_id

    return None

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_banned(user_id):
        await update.message.reply_text("Вы заблокированы.")
        return

    prof = get_user_profile(user_id)
    if not prof or not prof[2] or not prof[3] or not prof[4] or not prof[5]:
        await update.message.reply_text("Сначала /register.")
        return

    state = user_state.get(user_id)
    if state == STATE_IN_CHAT:
        await update.message.reply_text("Вы уже в чате. /stop для выхода.")
        return
    if state == STATE_WAITING_PARTNER:
        await update.message.reply_text("Вы уже ждёте собеседника...")
        return

    partner_id = match_users(user_id)
    if partner_id:
        waiting_queue.remove(partner_id)
        user_state[user_id] = STATE_IN_CHAT
        user_state[partner_id] = STATE_IN_CHAT
        user_partner[user_id] = partner_id
        user_partner[partner_id] = user_id

        await update.message.reply_text("Собеседник найден! Общайтесь анонимно.")
        await context.bot.send_message(partner_id, "Собеседник найден! Общайтесь анонимно.")
    else:
        waiting_queue.append(user_id)
        user_state[user_id] = STATE_WAITING_PARTNER
        await update.message.reply_text("Пока нет подходящего собеседника, вы в очереди.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    if is_banned(user_id):
        await update.message.reply_text("Вы заблокированы.")
        return

    if user_state.get(user_id) == STATE_IN_CHAT:
        partner_id = user_partner.get(user_id)
        if not partner_id:
            await update.message.reply_text("Ошибка: нет собеседника. /stop")
            return

        # Фильтр слов
        if any(bad_word in text.lower() for bad_word in BANNED_WORDS):
            await update.message.reply_text("Запрещённое слово!")
            return

        # Префикс Премиум
        prof = get_user_profile(user_id)
        prefix = "[Премиум] " if prof and prof[6] else ""

        await context.bot.send_chat_action(partner_id, ChatAction.TYPING)
        await context.bot.send_message(partner_id, f"{prefix}{text}")
    else:
        await update.message.reply_text("Сейчас вы не в чате. /search")

# ------------------ РЕПОРТ ------------------

async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_state.get(user_id) != STATE_IN_CHAT:
        await update.message.reply_text("Вы не в чате, чтобы жаловаться.")
        return

    partner_id = user_partner.get(user_id)
    if not partner_id:
        await update.message.reply_text("Нет собеседника.")
        return

    add_report(user_id, partner_id, "Жалоба на собеседника")
    await update.message.reply_text("Жалоба отправлена.")

    await context.bot.send_message(
        ADMIN_ID,
        f"Репорт от {user_id} на {partner_id}"
    )

# ------------------ ПРЕМИУМ ------------------

async def premium_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Премиум даёт [Премиум]-метку в чате. /buy, чтобы оплатить."
    )

async def buy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prices = [LabeledPrice("Премиум (1 месяц)", 100_00)]
    await update.message.reply_invoice(
        title="Премиум-подписка",
        description="1 месяц расширенных возможностей",
        payload="premium_payload",
        provider_token=PAYMENT_PROVIDER_TOKEN,
        currency="RUB",
        prices=prices,
    )

async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    await query.answer(ok=True)

async def successful_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    update_user_field(user_id, "premium", True)
    await update.message.reply_text("Оплата прошла успешно! Теперь у вас Премиум.")

# ------------------ АДМИН ------------------

async def admin_ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    args = update.message.text.split()
    if len(args) < 2:
        await update.message.reply_text("/adminban <user_id>")
        return
    target = int(args[1])
    update_user_field(target, "banned", True)
    await update.message.reply_text(f"Пользователь {target} забанен.")

async def admin_unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    args = update.message.text.split()
    if len(args) < 2:
        await update.message.reply_text("/adminunban <user_id>")
        return
    target = int(args[1])
    update_user_field(target, "banned", False)
    await update.message.reply_text(f"Пользователь {target} разбанен.")

# ------------------ MAIN ------------------

from telegram.ext import ConversationHandler

def main():
    # ВАЖНО: drop_pending_updates=True, чтобы при перезапуске
    # не было двойных конфликтах getUpdates
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Создание таблиц, если нужны, можно здесь (или заранее). 
    # Для примера минимальный DDL:
    with psycopg2.connect(DB_CONN_STR) as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                gender TEXT,
                age INT,
                region TEXT,
                looking_for TEXT,
                premium BOOLEAN DEFAULT FALSE,
                banned BOOLEAN DEFAULT FALSE
            );
            """)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                id SERIAL PRIMARY KEY,
                reporter_id BIGINT,
                target_id BIGINT,
                reason TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            );
            """)

    register_conv = ConversationHandler(
        entry_points=[CommandHandler("register", register_command)],
        states={
            REG_GENDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_gender)],
            REG_AGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_age)],
            REG_REGION: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_region)],
            REG_LOOKING_FOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_looking_for)],
        },
        fallbacks=[]
    )
    app.add_handler(register_conv)

    # Общие
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("stop", stop_command))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CommandHandler("report", report_command))

    # Премиум
    app.add_handler(CommandHandler("premium", premium_command))
    app.add_handler(CommandHandler("buy", buy_command))
    app.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))

    # Админ
    app.add_handler(CommandHandler("adminban", admin_ban_user))
    app.add_handler(CommandHandler("adminunban", admin_unban_user))

    # Сообщения
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Бот запущен...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
