from dataclasses import dataclass
from hashlib import md5
import uuid

import aiohttp
import aiosqlite
import disnake
from disnake.ext import commands

from bot.storage import Roles
from pathlib import Path


# Ввод: никнейм
# 1. Ищем лицензионный UUID ника. Найден = записываем его себе, не найден - ну и не надо.
# 1.1. Если лицензия: ищем все ники по этому UUID, ищем все UUID по этому нику
# 1.2. Если пиратка: ищем все UUID по этому нику, генерируем пиратский UUID
# 2. Кросс-референсов других ников под этими UUID
# 3. Получаем список всех ников и UUID, связанным с этим ником
# 4. Ищем по базам данных

class PlayerInfoFinder(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.path = Path(__file__).parent.parent.parent / "data" / "seasons"
    
    @dataclass
    class PlayerInfo:
        nicknames: list[str] | None = None
        found_in: list | None = None
        is_license: bool = False
        offline_uuid: str | None = None
        online_uuid: str | None = None
        discord_id: str | None = None
        logs: str | None = None

    async def searchCmi(self, uuids: list):
        all_results = []
        seasons = []
        for season in ["3","5","6","7"]:
            async with aiosqlite.connect(self.path / season / "cmi.db") as db:
                for uid in uuids:
                    cursor = await db.execute(
                        """
                        SELECT username FROM users WHERE player_uuid = ?
                        """, (uid,)
                    )
                    result = await cursor.fetchall()
                    print(result)
                    if result:
                        result = [nickname[0] for nickname in result]
                    all_results.extend(result)
                    seasons.extend(season)
        return list(set(all_results)) or "?"
    
    async def searchPlayerData(self, uuids: list):
        all_results = []
        seasons = []
        for season in ["4", "6", "7"]:
            return


    async def licenseInfo(self, nickname: str):
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://playerdb.co/api/player/minecraft/{nickname}") as response:
                if response.status == 200:
                    data = await response.json()
                    if data["code"] == "player.found":
                        return True, data["data"]["player"]["id"]
                    return False, 1
    
    async def getOfflineUUID(self, nickname: str):
        hash = md5(f'OfflinePlayer:{nickname}'.encode('utf-8')).digest()
        return str(uuid.UUID(bytes=hash))

    
    @commands.slash_command(name='get_player_data', description='Найти всю возможную информацию по нику/пользователю ДС')
    @commands.has_any_role(Roles.admin, Roles.st_admin)
    async def getAllPlayersData(self, inter: disnake.ApplicationCommandInteraction, nickname: str):
        player_info = self.PlayerInfo()
        player_info.logs = "Начало поиска:\n"
        player_info.is_license, player_info.online_uuid = await self.licenseInfo(nickname)
        player_info.offline_uuid = await self.getOfflineUUID(nickname)
        nicknames_from_cmi = await self.searchCmi([player_info.offline_uuid,player_info.online_uuid])
        if nicknames_from_cmi:
            player_info.nicknames = [nicknames_from_cmi]
            player_info.logs += "Найден в CMI"
        nicknames_from_playerdata = print('')
        print(player_info)

def setup(bot: commands.Bot):
    bot.add_cog(PlayerInfoFinder(bot))