from bot.bot import bot
import os
from dotenv import load_dotenv

import logging
from logging.handlers import RotatingFileHandler

load_dotenv()
token = os.getenv("DISCORD_TOKEN")

def setup_logger():
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
        "handlers.search_player",
        "handlers.catcraft_fm",
        "handlers.idiot_check",
        "handlers.rcon",          # RCON команды

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

setup_logger()
load_extension()
bot.run(token)
