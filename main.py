from bot.bot import bot
import os
from dotenv import load_dotenv

import logging
from logging.handlers import RotatingFileHandler

load_dotenv()
env = os.getenv("BOT_ENV", "prod")
token = os.getenv("DEV_DISCORD_TOKEN") if env == "dev" else os.getenv("DISCORD_TOKEN")

logger = logging.getLogger("robocat")

def setup_logger():
    os.makedirs("logs", exist_ok=True)
    logger = logging.getLogger("robocat")
    logger.setLevel(logging.DEBUG)

    format = logging.Formatter(
        "[%(asctime)s] [%(levelname)-8s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    file = RotatingFileHandler(
        filename="logs/bot.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="UTF-8"
    )
    file.setLevel(logging.DEBUG)
    file.setFormatter(format)

    logger.addHandler(file)

    # Диагностика disnake («Heartbeat blocked» = завис event loop, rate limit,
    # reconnect) шла только в stderr и терялась в journald из-за буферизации —
    # инцидент 25-26.07.2026 расследовали вслепую.
    disnake_logger = logging.getLogger("disnake")
    disnake_logger.setLevel(logging.WARNING)
    disnake_logger.addHandler(file)

def load_extension():
    extensions = [
        ### Slash commands
        "slash_commands.admin",

        # Prefix commands
        "commands.faq",
        "commands.general",

        ### Handlers
        "handlers.role_select",
        "handlers.punishments",
        "handlers.tickets.admin_ticket",
        "handlers.tickets.bugs",
        "handlers.tickets.engine",
        "handlers.tickets.news_request",  # заявки на новость от игроков
        "handlers.search_player",
        "handlers.catcraft_fm",
        "handlers.idiot_check",
        "handlers.rcon",          # RCON команды
        "handlers.honeypot",      # Ловушка для ботов
        "handlers.get_a_job",     # кнопка «Посмотреть доступные вакансии»
        "handlers.digest",        # /digest — выжимка новостей
        "handlers.news_editor",   # /news — редактор новостей
        "handlers.vk_repost",     # ручной гейт перепоста новостей в ВК

        ### AI
        "ai.handler",

        ### Other
        "utils",
        "storage",
        "misc",
        "flag_system.flag_commands",
    ]
    for i in extensions:
        bot.load_extension(f"bot.{i}")
        logger.info("Загружен ког %s", i)

setup_logger()
if not token:
    logger.critical("Токен Discord не найден в окружении (env=%s)", env)
logger.info("Запуск бота, env=%s", env)
load_extension()
bot.run(token)
