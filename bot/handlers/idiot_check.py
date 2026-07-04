import disnake
from disnake.ext import commands
import re


class IdiotCheck(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

        self._WORD_NUMS = {
            "один": 1, "одна": 1, "два": 2, "две": 2, "три": 3, "четыре": 4,
            "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9,
            "десять": 10, "одиннадцать": 11, "двенадцать": 12,
        }
        _num = r"\d{1,2}|" + "|".join(self._WORD_NUMS)
        self._AGE_RE = re.compile(
            rf"""\bмне\s+
            (?:
                (?:скоро\s+|почти\s+|уже\s+|сейчас\s+|где[\s-]?то\s+)?
                ({_num})\s+(?:лет|год(?:а|ик|ика|иков|ов)?)
                |
                лет\s+({_num})
            )
            \b
            (?!\s+назад)
            """,
            re.IGNORECASE | re.VERBOSE,
        )

    def detect_underage(self, text: str, threshold: int = 12) -> int | None:
        m = self._AGE_RE.search(text)
        if not m:
            return None
        token = (m.group(1) or m.group(2)).lower()
        age = int(token) if token.isdigit() else self._WORD_NUMS.get(token)
        return age if (age is not None and age <= threshold) else None

    @commands.Cog.listener("on_message")
    async def checkUnderage(self, message: disnake.Message):
        if message.author.bot:
            return
        if message.content.startswith("!"):
            return
        if self.detect_underage(message.content) is None:
            return

        ai_engine = getattr(self.bot, 'ai_engine', None)
        if ai_engine is None:
            return

        try:
            is_underage = await ai_engine.idiotCheck(message.content)
        except Exception:
            return  # ИИ недоступен — молча пропускаем, это не повод ронять листенер
        if not is_underage:
            return

        try:
            await message.delete()
        except disnake.NotFound:
            return
        except disnake.Forbidden:
            note = ("Привет 🐾 На всякий случай удали своё сообщение сам — "
                    "на серверах лучше не писать свой возраст, это для твоей безопасности.")
        else:
            note = ("Привет 🐾 Я удалил твоё сообщение — "
                    "на серверах лучше не писать свой возраст, это для твоей безопасности.")

        try:
            await message.author.send(note)
        except disnake.Forbidden:
            pass 


def setup(bot: commands.Bot):
    bot.add_cog(IdiotCheck(bot))