import disnake
from disnake.ext import commands

from bot.flag_system.flag_system import Flags
from bot.storage import Roles


class Tests(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name='test')
    @commands.has_any_role(Roles.admin, Roles.st_admin)
    async def testCommand(self, 
                        ctx: commands.Context):
        flag = Flags()
        await flag.setFlag(ctx.author, ctx.author.id, "test_flag", "test_value", "10сек")
        test_flag = await Flags().getFlag(ctx.author,"test_flag")       
        await ctx.send(test_flag)

def setup(bot: commands.Bot):
    bot.add_cog(Tests(bot))