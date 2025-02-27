import os
import logging
import psycopg2
from psycopg2 import sql

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# -------------------------- ЛОГИ --------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --------------------- ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ---------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN")         # Токен бота от BotFather
DB_CONN_STR = os.environ.get("DB_CONN_STR")     # Строка подключения к PostgreSQL
# Пример: "postgresql://user:password@host:5432/dbname"

if not BOT_TOKEN:
    raise ValueError("Не задана переменная окружения BOT_TOKEN")
if not DB_CONN_STR:
    raise ValueError("Не задана переменная окружения DB_CONN_STR")

# ------------------------ ПОДКЛЮЧЕНИЕ К БД ------------------------
conn = psycopg2.connect(DB_CONN_STR)
conn.autocommit = True
cursor = conn.cursor()

# Создадим таблицу для хранения пользователей (если не существует)
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id BIGINT PRIMARY KEY,
    username TEXT,
    age INT,
    gender TEXT,
    looking_for TEXT
);
""")

# -------------------- СОСТОЯНИЯ ДЛЯ РЕГИСТРАЦИИ --------------------
REG_AGE, REG_GENDER, REG_LOOKING_FOR = range(3)

# Для хранения состояний в памяти (просто для примера)
user_state = {}

# ------------------ ФУНКЦИИ ДЛЯ РАБОТЫ С БД ------------------

def get_user_profile(user_id: int):
    """Возвращает запись о пользователе или None."""
    cursor.execute(
        "SELECT user_id, username, age, gender, looking_for FROM users WHERE user_id = %s",
        (user_id,)
    )
    return cursor.fetchone()

def create_or_update_user(user_id: int, username: str):
    """Создаёт пользователя, если его нет в БД. При необходимости можно обновлять username."""
    profile = get_user_profile(user_id)
    if profile is None:
        cursor.execute(
            "INSERT INTO users (user_id, username) VALUES (%s, %s)",
            (user_id, username)
        )
    else:
        # Если нужно обновлять username при каждом новом /start
        cursor.execute(
            "UPDATE users SET username = %s WHERE user_id = %s",
            (username, user_id)
        )

def update_user_field(user_id: int, field: str, value):
    """Обновляет одно поле в таблице users."""
    query = sql.SQL("UPDATE users SET {field} = %s WHERE user_id = %s").format(
        field=sql.Identifier(field)
    )
    cursor.execute(query, (value, user_id))

# -------------------------- ХЕНДЛЕРЫ --------------------------

# 1. /start
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    username = user.username or "NoUsername"

    create_or_update_user(user_id, username)

    await update.message.reply_text(
        "Привет! Я тестовый бот для Railway.\n\n"
        "Используй /register, чтобы заполнить анкету, или /search, чтобы найти собеседника.\n"
        "Но здесь пока нет полной логики поиска. Можно дополнять код под свои нужды!"
    )
    user_state[user_id] = None

# 2. /register (ConversationHandler)
async def register_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text("Введите ваш возраст (число).")
    user_state[user_id] = REG_AGE
    return REG_AGE

async def reg_age(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if not text.isdigit():
        await update.message.reply_text("Возраст должен быть числом. Попробуйте снова.")
        return REG_AGE

    age = int(text)
    if age < 10 or age > 120:
        await update.message.reply_text("Введите реальный возраст (10–120).")
        return REG_AGE

    update_user_field(user_id, "age", age)
    await update.message.reply_text("Введите ваш пол (М/Ж).")
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

    await update.message.reply_text("Анкета заполнена! Можете использовать /search.")
    user_state[user_id] = None
    return ConversationHandler.END

# 3. /search (просто пример — без настоящего чата)
async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_profile = get_user_profile(user_id)
    if not user_profile:
        await update.message.reply_text("Сначала заполните анкету: /register")
        return

    # В реальном боте здесь будет логика поиска собеседника
    await update.message.reply_text(
        "Поиск собеседника пока не реализован.\n"
        "Но в продвинутом боте здесь можно добавить очередь или matching."
    )

# 4. Обработка простых сообщений (если нужно)
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Это простой бот, в нём нет анонимного чата. Допишите логику сами!")

# --------------------- MAIN (запуск) ---------------------

from telegram.ext import (
    ConversationHandler
)

def main():
    # Создаём приложение
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # ConversationHandler для регистрации
    register_conv = ConversationHandler(
        entry_points=[CommandHandler("register", register_command)],
        states={
            REG_AGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_age)],
            REG_GENDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_gender)],
            REG_LOOKING_FOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_looking_for)],
        },
        fallbacks=[]
    )
    app.add_handler(register_conv)

    # Прочие команды
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("search", search_command))

    # Хендлер для любых обычных сообщений
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Бот запущен...")
    app.run_polling()

if __name__ == "__main__":
    main()
