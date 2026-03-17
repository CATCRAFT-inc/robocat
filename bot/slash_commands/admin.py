import aiosqlite
import disnake
from disnake.ext import commands

from bot.flag_system.flag_system import Flags
from bot.storage import Buttons, ColorStorage, Embeds, Roles, Channels
from bot.utils import create_embed


class AdminCommands(commands.Cog):
    """Админские команды и прочее гавно"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    
        
    @commands.slash_command(name='send_embed', description='Отправить Embed из списка.')
    @commands.has_any_role(Roles.admin, Roles.st_admin)
    async def embedCommand(self, inter: disnake.ApplicationCommandInteraction,
                        embed_name: str,
                        message: str = None):
        embed = getattr(Embeds, embed_name, None)
        if embed:
            if isinstance(embed, disnake.ui.Container):
                await inter.send(components=[embed])
            if isinstance(embed, disnake.Embed):
                await inter.send(content=message, embed=embed)
        else:
            await inter.send("Такого эмбеда/контейнера нет!", ephemeral=True)

    @commands.slash_command(name='delete_until', description='Удалить все сообщения до определенного сообщения')
    @commands.has_any_role(Roles.admin, Roles.st_admin)
    async def deleteUntil(self, inter: disnake.ApplicationCommandInteraction,
                        message_id: str):
        try:
            message = await inter.channel.fetch_message(message_id)
        except:
            await inter.send("Такого сообщения не нашёл.", ephemeral=True)
        else:
            if message:
                await inter.response.defer(ephemeral=True)
                messages = await inter.channel.history(limit=50, after=message.created_at).flatten()
                messages = [msg for msg in messages if msg.id != inter.id]
                count = 0
                error_count = 0
                for mes in messages:
                    if mes.channel != inter.channel:
                        break
                    try:
                        await mes.delete()
                    except:
                        await inter.send(f"Не удалось удалить сообщение {mes.id}", ephemeral=True)
                        error_count += 1
                    else:
                        count += 1
                await inter.edit_original_response(f"Успешно удалено сообщений: {count}\nНе получилось удалить: {error_count}")




def setup(bot: commands.Bot):
    bot.add_cog(AdminCommands(bot))