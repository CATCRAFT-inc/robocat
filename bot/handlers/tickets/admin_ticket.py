import disnake
from disnake.ext import commands
from disnake import TextInputStyle, GroupOption, RadioGroup
from disnake.ui import TextDisplay, TextInput, Label, Container, Separator

from bot.flag_system.flag_system import flags
from bot.storage import ColorStorage, Roles, Users
from bot.utils import create_container


class AdminTicket(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    class AdminTicketModal(disnake.ui.Modal):
        def __init__(self):
            # The details of the modal, and its components
            components = [
                disnake.ui.TextInput(
                    label="Твой ник",
                    placeholder="Введи свой ник",
                    custom_id="Никнейм",
                    style=TextInputStyle.short,
                    max_length=50,
                ),
                disnake.ui.TextInput(
                    label="Что случилось?",
                    placeholder="В чём твоя жалоба?",
                    custom_id="Жалоба",
                    style=TextInputStyle.long
                )
            ]
            super().__init__(title='Репорт администрации', components=components)
    
        # The callback received when the user input is completed.
        async def callback(self, inter: disnake.ModalInteraction):
            await inter.response.defer(ephemeral=True)
            channel = inter.channel
            modal = inter.resolved_values
            nick = modal["Никнейм"]
            bug_description = modal["Жалоба"]
            bug_thread_name = " ".join(bug_description.split(" ")[:5])
            bug_thread = await channel.create_thread(
                name=bug_thread_name,
                type=disnake.ChannelType.private_thread,
                auto_archive_duration=10080,
                reason=f"Новый админ-тикет от {nick}"
            )
            bug_container = disnake.ui.Container(
                disnake.ui.TextDisplay(
                    content=f"# Админ-тикет от {nick} ({inter.author.mention})"
                ),
                disnake.ui.TextDisplay(
                    content="### Суть"
                ),
                disnake.ui.TextDisplay(
                    content=bug_description
                ),
                disnake.ui.TextDisplay(
                    content=f"-# ||<@&{Roles.admin}> <@&{Roles.moderator}> {inter.author.mention}||"
                ),
                accent_colour=disnake.Color.from_hex(ColorStorage.main),
            )
            await bug_thread.send(components=[bug_container])
            await inter.edit_original_response(f"Репорт админам создан! Перейди в него: <#{bug_thread.id}>")
            await flags.setFlag(bug_thread,"created_by",inter.author.id)
            await inter.author.send(
                components=create_container(
                    f"## Тред админ-тикета ''{bug_thread_name}'' создан!",
                    f"Сохраню тред здесь: https://discord.com/channels/{inter.guild_id}/{inter.channel_id}",
                    "Треды пропадают через некоторое время, но эта ссылка позволяет тебе в любой момент вернуться!"
                )
            )


def setup(bot: commands.Bot):
    bot.add_cog(AdminTicket(bot))