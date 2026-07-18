from dataclasses import dataclass
from hashlib import md5
from pathlib import Path
import asyncio
import logging
import uuid

import aiohttp
import aiosqlite
import disnake
from disnake.ext import commands

from bot.storage import ColorStorage, Roles
from bot.utils import create_container

logger = logging.getLogger("robocat.search_player")


# Ввод: никнейм
# 1. Ищем лицензионный UUID ника. Найден = записываем его себе, не найден - ну и не надо.
# 1.1. Если лицензия: ищем все ники по этому UUID
# 1.2. Если пиратка: генерируем пиратский (offline) UUID
# 2. Ищем ники по обоим UUID в базах CMI сезонов
# 3. Отдаём собранную инфу

class PlayerInfoFinder(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.path = Path(__file__).parent.parent.parent / "data" / "seasons"

    @dataclass
    class PlayerInfo:
        nicknames: list[str] | None = None
        is_license: bool = False
        offline_uuid: str | None = None
        online_uuid: str | None = None

    async def searchCmi(self, uuids: list):
        all_results = []
        for season in ["3", "5", "6", "7"]:
            db_path = self.path / season / "cmi.db"
            if not db_path.exists():
                continue
            async with aiosqlite.connect(db_path) as db:
                for uid in uuids:
                    if not uid:
                        continue
                    cursor = await db.execute(
                        "SELECT username FROM users WHERE player_uuid = ?", (uid,)
                    )
                    result = await cursor.fetchall()
                    if result:
                        all_results.extend(nickname[0] for nickname in result)
        return sorted(set(all_results))

    async def licenseInfo(self, nickname: str):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"https://playerdb.co/api/player/minecraft/{nickname}"
                ) as response:
                    if response.status != 200:
                        logger.warning("playerdb.co вернул статус %s при проверке лицензии ника %s", response.status, nickname)
                        return False, None
                    data = await response.json()
                    if data.get("code") == "player.found":
                        pid = data.get("data", {}).get("player", {}).get("id")
                        return (True, pid) if pid else (False, None)
                    return False, None
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning("playerdb.co недоступен, лицензию для ника %s считаем отсутствующей: %s", nickname, e)
            return False, None

    async def getOfflineUUID(self, nickname: str):
        # Как Java UUID.nameUUIDFromBytes: сырой md5 надо пометить версией 3 и
        # IETF-variant, иначе UUID не совпадёт с настоящим офлайн-UUID пиратки.
        b = bytearray(md5(f'OfflinePlayer:{nickname}'.encode('utf-8')).digest())
        b[6] = (b[6] & 0x0f) | 0x30  # версия 3
        b[8] = (b[8] & 0x3f) | 0x80  # variant
        return str(uuid.UUID(bytes=bytes(b)))

    @commands.slash_command(name='get_player_data', description='Найти всю возможную информацию по нику/пользователю ДС')
    @commands.has_any_role(Roles.admin, Roles.st_admin)
    async def getAllPlayersData(self, inter: disnake.ApplicationCommandInteraction, nickname: str):
        await inter.response.defer()

        info = self.PlayerInfo()
        info.is_license, info.online_uuid = await self.licenseInfo(nickname)
        info.offline_uuid = await self.getOfflineUUID(nickname)
        info.nicknames = await self.searchCmi([info.offline_uuid, info.online_uuid])

        nicks = ", ".join(f"`{n}`" for n in info.nicknames) if info.nicknames else "*не найден*"
        description = (
            f"**Лицензия:** {'да ✅' if info.is_license else 'нет ❌'}\n"
            f"**Online UUID:** `{info.online_uuid or '—'}`\n"
            f"**Offline UUID:** `{info.offline_uuid}`\n"
            f"**Ники из CMI:** {nicks}"
        )
        container = create_container(
            title=f"# 🔎 Информация по `{nickname}`",
            description=description,
            color=ColorStorage.main,
        )
        await inter.edit_original_response(components=[container])


def setup(bot: commands.Bot):
    bot.add_cog(PlayerInfoFinder(bot))
    logger.info("Ког PlayerInfoFinder загружен")
