import disnake
from disnake.ext import commands

from bot.utils import create_container


class GeneralPrefixCommands(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name='пинг', description="пинг", aliases=['ping', 'gbyu', 'зштп'])
    async def pingPlayers(self, ctx: commands.Context):
        await ctx.reply(components=create_container("## Пинга пока что нет!", "Сезон-то не начался хех!"))


def setup(bot: commands.Bot):
    bot.add_cog(GeneralPrefixCommands(bot))