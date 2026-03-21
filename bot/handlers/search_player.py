from dataclasses import dataclass

import aiohttp
import aiosqlite
import disnake
from disnake.ext import commands

from bot.storage import Roles
from pathlib import Path


class PlayerInfoFinder(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.path = Path(__file__).parent.parent.parent / "data" / "seasons"
    @dataclass
    class PlayerInfo:
        nickname: str | None = None
        offline_uuid: str | None = None
        online_uuid: str | None = None
        discord_id: str | None = None
        found_in: list | None = None

    async def searchCmi(self, nickname: str = None, uuid: str = None):
        all_results = []
        for i in ["3","5","6","7"]:
            async with aiosqlite.connect(self.path / i / "cmi.db") as db:
                if nickname:
                    cursor = await db.execute(
                        """
                        SELECT player_uuid FROM users WHERE username = ?
                        """, (nickname,)
                    )
                    result = await cursor.fetchall()
                    print(result[0][0])
                    if result:
                        result = [uuid[0] for uuid in result]
                    all_results.extend(result)
                elif uuid:
                    cursor = await db.execute(
                        """
                        SELECT username FROM users WHERE player_uuid = ?
                        """, (uuid,)
                    )
                    result = await cursor.fetchall()
                    print(result[0][0])
                    if result:
                        result = [uuid[0] for uuid in result]
                    all_results.extend(result)
        return list(set(all_results)) or None


    async def checkUUIDs(self, uuids: list[str]):
        uuid_dict = {}
        for u in uuids:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"https://playerdb.co/api/player/minecraft/{u}") as response:
                    if response.status == 200:
                        data = await response.json()
                        if data["code"] == "player.found":
                            uuid_dict[u] = data["data"]["username"]
                        
    
    @commands.slash_command(name='get_player_data', description='Найти всю возможную информацию по нику/пользователю ДС')
    @commands.has_any_role(Roles.admin, Roles.st_admin)
    async def getAllPlayersData(self, inter: disnake.ApplicationCommandInteraction, nickname: str = None):
        result = await self.searchCmi(nickname=nickname)
        if result:
            await self.checkUUIDs(result)




def setup(bot: commands.Bot):
    bot.add_cog(PlayerInfoFinder(bot))