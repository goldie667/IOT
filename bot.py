import os
import logging
import psycopg2
from psycopg2 import sql



from telegram import (
    Update,
    LabeledPrice,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    CallbackQueryHandler,
    PreCheckoutQueryHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ChatAction

# ------------------ НАСТРОЙКИ БОТА И БАЗЫ ------------------

# 1) Токен бота (получаем от BotFather)
BOT_TOKEN = os.environ.get("BOT_TOKEN") or "ВАШ_BOT_TOKEN_ОТ_BOTFATHER"

# 2) Токен платёжного провайдера (получаем от BotFather, раздел Payments)
PAYMENT_PROVIDER_TOKEN = os.environ.get("PAYMENT_PROVIDER_TOKEN") or "ВАШ_PAYMENT_PROVIDER_TOKEN"

# 3) ID администратора (кто может банить/разбанивать)
ADMIN_ID = int(os.environ.get("ADMIN_ID", 123456789))

# 4) Строка подключения к PostgreSQL
# Пример: "postgresql://user:pass@host:5432/dbname"
DB_CONN_STR = os.environ.get("DB_CONN_STR") or "postgresql://user:pass@host:5432/dbname"

# 5) Запрещённые слова (примитивный фильтр)
BANNED_WORDS = {"badword1", "badword2"}

# Логи
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ------------------ ПОДКЛЮЧЕНИЕ К БАЗЕ ------------------

conn = psycopg2.connect(DB_CONN_STR)
conn.autocommit = True
cursor = conn.cursor()

# Создаём таблицы (если не существуют)
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

# ------------------ ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ БД ------------------

def get_user_profile(user_id: int):
    """
    Возвращает кортеж (user_id, username, gender, age, region,
                       looking_for, premium, banned)
    или None, если пользователя нет.
    """
    cursor.execute("""
        SELECT user_id, username, gender, age, region, looking_for, premium, banned
        FROM users
        WHERE user_id = %s
    """, (user_id,))
    return cursor.fetchone()

def create_user(user_id: int, username: str):
    """
    Создаёт запись о пользователе, если её нет в БД.
    """
    if get_user_profile(user_id) is None:
        cursor.execute("""
            INSERT INTO users (user_id, username)
            VALUES (%s, %s)
        """, (user_id, username))

def update_user_field(user_id: int, field: str, value):
    """
    Обновляет поле (gender, age, region, looking_for, premium, banned).
    """
    query = sql.SQL("UPDATE users SET {field} = %s WHERE user_id = %s").format(
        field=sql.Identifier(field)
    )
    cursor.execute(query, (value, user_id))

def is_banned(user_id: int) -> bool:
    """
    Проверяет, забанен ли пользователь (поле banned).
    """
    prof = get_user_profile(user_id)
    if prof:
        return prof[7]  # banned
    return False

def add_report(reporter_id: int, target_id: int, reason: str):
    """
    Записывает репорт в таблицу reports.
    """
    cursor.execute("""
        INSERT INTO reports (reporter_id, target_id, reason)
        VALUES (%s, %s, %s)
    """, (reporter_id, target_id, reason))

# ------------------ СОСТОЯНИЯ ДЛЯ REGISTRATION ------------------

REG_GENDER, REG_AGE, REG_REGION, REG_LOOKING_FOR = range(4)

# ------------------ СОСТОЯНИЯ ДЛЯ ЧАТА ------------------

STATE_IN_CHAT = "in_chat"
STATE_WAITING_PARTNER = "waiting_partner"

# ------------------ ВНУТРЕННИЕ СЛОВАРИ ------------------

user_state = {}       # user_id -> состояние (регистрация, в чате, в очереди и т.д.)
user_partner = {}     # user_id -> partner_id (с кем в чате)
waiting_queue = []    # очередь на поиск собеседника

# ------------------ ХЕНДЛЕРЫ РЕГИСТРАЦИИ ------------------

async def register_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    username = user.username or "NoUsername"

    # Создаём запись в БД, если нет
    create_user(user_id, username)

    if is_banned(user_id):
        await update.message.reply_text("Вы заблокированы и не можете пользоваться ботом.")
        return ConversationHandler.END

    await update.message.reply_text("Укажите свой пол (М/Ж).")
    user_state[user_id] = REG_GENDER
    return REG_GENDER

async def reg_gender(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip().lower()

    if text not in ["м", "ж"]:
        await update.message.reply_text("Пожалуйста, введите 'М' или 'Ж'.")
        return REG_GENDER

    gender = "М" if text == "м" else "Ж"
    update_user_field(user_id, "gender", gender)

    await update.message.reply_text("Укажите ваш возраст (число).")
    user_state[user_id] = REG_AGE
    return REG_AGE

async def reg_age(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if not text.isdigit():
        await update.message.reply_text("Возраст должен быть числом. Попробуйте снова.")
        return REG_AGE

    age = int(text)
    if age < 14 or age > 120:
        await update.message.reply_text("Введите возраст в диапазоне 14–120.")
        return REG_AGE

    update_user_field(user_id, "age", age)
    await update.message.reply_text("Укажите ваш регион (город/область).")
    user_state[user_id] = REG_REGION
    return REG_REGION

async def reg_region(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    region = update.message.text.strip()
    if len(region) < 2:
        await update.message.reply_text("Слишком короткое название, введите корректный регион.")
        return REG_REGION

    update_user_field(user_id, "region", region)
    await update.message.reply_text("Кого ищете? (М/Ж/любые)")
    user_state[user_id] = REG_LOOKING_FOR
    return REG_LOOKING_FOR

async def reg_looking_for(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip().lower()

    if text not in ["м", "ж", "любые"]:
        await update.message.reply_text("Пожалуйста, введите 'М', 'Ж' или 'любые'.")
        return REG_LOOKING_FOR

    lf = "М" if text == "м" else ("Ж" if text == "ж" else "любые")
    update_user_field(user_id, "looking_for", lf)

    await update.message.reply_text(
        "Ваша анкета сохранена!\n"
        "Теперь используйте /search, чтобы найти собеседника."
    )
    user_state[user_id] = None
    return ConversationHandler.END

# ------------------ ОБЩИЕ КОМАНДЫ ------------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    username = user.username or "NoUsername"

    create_user(user_id, username)

    if is_banned(user_id):
        await update.message.reply_text("Вы заблокированы.")
        return

    await update.message.reply_text(
        "Добро пожаловать в анонимный чат!\n\n"
        "Команды:\n"
        "/register — заполнить/обновить анкету\n"
        "/search — найти собеседника\n"
        "/stop — выйти из чата или отменить поиск\n"
        "/report — пожаловаться на собеседника\n"
        "/premium — узнать о подписке Премиум\n"
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
                "Собеседник покинул чат. Используйте /search, чтобы найти нового."
            )
            user_state[partner_id] = None
            user_partner.pop(partner_id, None)

        user_partner.pop(user_id, None)
        user_state[user_id] = None
        await update.message.reply_text("Вы покинули чат.")
    elif state == STATE_WAITING_PARTNER:
        if user_id in waiting_queue:
            waiting_queue.remove(user_id)
        user_state[user_id] = None
        await update.message.reply_text("Вы отменили поиск собеседника.")
    else:
        await update.message.reply_text("Вы не в чате и не в очереди.")

# ------------------ ПОИСК / ЧАТ ------------------

def match_users(user_id: int):
    """
    Ищем в очереди кандидата, у кого взаимные предпочтения.
    Возвращаем partner_id или None.
    """
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

        # Взаимная проверка пола
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

    # Проверяем анкету
    prof = get_user_profile(user_id)
    if not prof or not prof[2] or not prof[3] or not prof[4] or not prof[5]:
        await update.message.reply_text("Сначала заполните анкету: /register.")
        return

    state = user_state.get(user_id)
    if state == STATE_IN_CHAT:
        await update.message.reply_text("Вы уже в чате! /stop, чтобы выйти.")
        return
    if state == STATE_WAITING_PARTNER:
        await update.message.reply_text("Вы уже ждёте собеседника...")
        return

    # Пытаемся найти подходящего партнёра
    partner_id = match_users(user_id)
    if partner_id is not None:
        waiting_queue.remove(partner_id)
        user_state[user_id] = STATE_IN_CHAT
        user_state[partner_id] = STATE_IN_CHAT
        user_partner[user_id] = partner_id
        user_partner[partner_id] = user_id

        await update.message.reply_text("Собеседник найден! Можете общаться.")
        await context.bot.send_message(partner_id, "Собеседник найден! Можете общаться.")
    else:
        # Добавляем в очередь
        waiting_queue.append(user_id)
        user_state[user_id] = STATE_WAITING_PARTNER
        await update.message.reply_text("В очереди пока нет подходящего собеседника. Ждём...")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_banned(user_id):
        await update.message.reply_text("Вы заблокированы.")
        return

    text = update.message.text

    if user_state.get(user_id) == STATE_IN_CHAT:
        partner_id = user_partner.get(user_id)
        if not partner_id:
            await update.message.reply_text("Ошибка: нет собеседника. /stop")
            return

        # Фильтр запрещённых слов
        if any(bad_word in text.lower() for bad_word in BANNED_WORDS):
            await update.message.reply_text("Сообщение содержит запрещённое слово и не будет отправлено.")
            return

        # Префикс "Премиум"
        prof = get_user_profile(user_id)
        prefix = "[Премиум] " if prof and prof[6] else ""

        # Отправляем сообщение партнёру
        await context.bot.send_chat_action(chat_id=partner_id, action=ChatAction.TYPING)
        await context.bot.send_message(chat_id=partner_id, text=f"{prefix}{text}")
    else:
        await update.message.reply_text("Вы не в чате. Используйте /search, чтобы найти собеседника.")

# ------------------ РЕПОРТЫ ------------------

async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_state.get(user_id) != STATE_IN_CHAT:
        await update.message.reply_text("Вы не в чате, чтобы жаловаться.")
        return

    partner_id = user_partner.get(user_id)
    if not partner_id:
        await update.message.reply_text("Ошибка: нет собеседника.")
        return

    add_report(user_id, partner_id, "Неадекватное поведение / жалоба")
    await update.message.reply_text("Жалоба отправлена администрации.")

    # Уведомим админа
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"Поступила жалоба! От {user_id} на {partner_id}."
    )

# ------------------ ПРЕМИУМ ------------------

async def premium_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Премиум-подписка даёт:\n"
        "- Метку [Премиум] в чате.\n"
        "- (Дополнительно можете добавить больше функций)\n\n"
        "Нажмите /buy, чтобы оформить подписку."
    )

async def buy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Пример: 100 руб.
    prices = [LabeledPrice(label="Премиум (1 месяц)", amount=100_00)]
    await update.message.reply_invoice(
        title="Премиум-подписка",
        description="1 месяц расширенных возможностей",
        payload="premium_payload",
        provider_token=PAYMENT_PROVIDER_TOKEN,
        currency="RUB",
        prices=prices,
        start_parameter="premium-subscription",
    )

async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    # Подтверждаем, что всё ок
    await query.answer(ok=True)

async def successful_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    update_user_field(user_id, "premium", True)
    await update.message.reply_text("Оплата прошла успешно! Премиум-статус активирован.")

# ------------------ АДМИН-ФУНКЦИИ ------------------

async def admin_ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /adminban <user_id>
    """
    if update.effective_user.id != ADMIN_ID:
        return
    args = update.message.text.split()
    if len(args) < 2:
        await update.message.reply_text("Использование: /adminban <user_id>")
        return
    target = int(args[1])
    update_user_field(target, "banned", True)
    await update.message.reply_text(f"Пользователь {target} забанен.")

async def admin_unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /adminunban <user_id>
    """
    if update.effective_user.id != ADMIN_ID:
        return
    args = update.message.text.split()
    if len(args) < 2:
        await update.message.reply_text("Использование: /adminunban <user_id>")
        return
    target = int(args[1])
    update_user_field(target, "banned", False)
    await update.message.reply_text(f"Пользователь {target} разбанен.")

# ------------------ MAIN (ЗАПУСК) ------------------

from telegram.ext import ConversationHandler

def main():
    # Создаём приложение
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # ConversationHandler для /register
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

    # Общие команды
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

    # Обработка обычных сообщений (чат)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Бот запущен...")
    app.run_polling()

if __name__ == "__main__":
    main()
