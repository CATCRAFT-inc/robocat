from bot.bot import bot
import os
from dotenv import load_dotenv

load_dotenv()
token = os.getenv("DISCORD_TOKEN")

def load_extension():
    extensions = [
        ### Slash commands
        "slash_commands.admin",

        # Prefix commands
        "commands.faq",
        "commands.general",

        ### Handlers
        "handlers.bugs",
        "handlers.role_select",
        "handlers.punishments",
        "handlers.get_help.admin_ticket"
        "handlers.get_help.engine"

        ### Other
        "utils",
        "storage",
        "misc",
        "flag_system.flag_commands",
    ]
    for i in extensions:
        bot.load_extension(f"bot.{i}")

load_extension()
bot.run(token)
