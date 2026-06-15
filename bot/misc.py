import disnake
from disnake.ext import commands


class Tests(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.artem123zzz_id = 1348192851573604412

    @commands.Cog.listener("on_message")
    async def restrictArtem123zzz(self, message: disnake.Message):
        if message.author.bot:
            return
        if message.author.id == self.artem123zzz_id:
            if 'https' in message.content or message.attachments:
                await message.delete()

def setup(bot: commands.Bot):
    bot.add_cog(Tests(bot))
