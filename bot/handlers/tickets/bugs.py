import aiosqlite
import disnake
from disnake import TextInputStyle, TextInput, StringSelectMenu, SelectOption
from disnake.ext import commands
from bot.storage import Buttons, Channels, ColorStorage, FAQStorage, Roles, Users
from bot.utils import create_container, create_embed
from bot.flag_system.flag_system import Flags


class BugHandler(commands.Cog):
    """
    Хендлер репорта багов
    ---
    Создаёт треды на каждый баг и т.д.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener("on_button_click")
    async def bugThreadCreate(self, inter: disnake.MessageInteraction):
        if inter.component.custom_id == Buttons.BUG_REPORT.id:
            has_bug_cd = await self.bot.flags.hasFlag(inter.author,"create_bug_cooldown")
            if has_bug_cd:
                await inter.send("Ты отправлял(а) слишком много багов за короткое время! Отдохни и сообщи о них попозже =)", ephemeral=True)
            else:  
                created_bugs = await self.bot.flags.getFlag(inter.author,"created_bugs")
                if created_bugs and int(created_bugs[0]) > 3:
                    await inter.send("Воу-воу, котик! Мы очень ценим твою помощь, но твои действия смахивают на спам тикетами... Я вынужден дать тебе КД, попробуй попозже.")
                    await self.bot.flags.setFlag(inter.author,"create_bug_cooldown", "true","15мин")
                    return
                elif created_bugs:
                    await self.bot.flags.setFlag(inter.author, "created_bugs", int(created_bugs[0]) + 1, expires_at="15мин")
                else:
                    await self.bot.flags.setFlag(inter.author, "created_bugs", 1, expires_at="15мин")
                await inter.response.send_modal(modal=self.BugModal())

    class BugModal(disnake.ui.Modal):
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
                    label="В чём баг?",
                    placeholder="Опиши подробно в чём баг, как воспроизвести, когда обнаружил(а) и т.д.",
                    custom_id="Описание бага",
                    style=TextInputStyle.long
                ),
                disnake.ui.Label(text="(Опционально) Критичность бага",
                component=
                    disnake.ui.RadioGroup(
                        custom_id="Приоритет",
                        options=[
                            disnake.GroupOption(label="😸 Минимальный",
                                                description="Баг не влияющий на игру"),
                            disnake.GroupOption(label="😿 Средний",
                                                description="Баг влияющий на игру"),
                            disnake.GroupOption(label="🙀 Критический",
                                                description="Дюп, безопасность, критеский баг"),
                            disnake.GroupOption(label="👀 Это баг?",
                                                description="Если не уверен(а) что это вообще баг.")
                        ],
                        required=False)
                )
            ]
            super().__init__(title='Баг-репорт', components=components)
    
        # The callback received when the user input is completed.
        async def callback(self, inter: disnake.ModalInteraction):
            channel = inter.guild.get_channel(Channels.bugs)
            modal = inter.resolved_values
            nick = modal["Никнейм"]
            bug_description = modal["Описание бага"]
            priority = modal["Приоритет"]
            bug_thread_name = " ".join(bug_description.split(" ")[:5])
            bug_thread = await channel.create_thread(
                name=bug_thread_name,
                type=disnake.ChannelType.private_thread,
                auto_archive_duration=10080,
                reason=f"Новый баг-репорт от {nick}"
            )
            bug_container = disnake.ui.Container(
                disnake.ui.TextDisplay(
                    content=f"# Баг! \nОт {nick} ({inter.author.mention})"
                ),
                disnake.ui.TextDisplay(
                    content="## Описание бага"
                ),
                disnake.ui.TextDisplay(
                    content=bug_description
                ),
                disnake.ui.Separator(),
                disnake.ui.TextDisplay(
                    content=f"Критичность бага:  {priority or "Не указана"}"
                ),
                disnake.ui.TextDisplay(
                    content=f"-# || <@&{Roles.st_admin}> <@&{Roles.admin}> <@&{Roles.developer}>||"
                ),
                accent_colour=disnake.Color.from_hex(ColorStorage.main),
            )
            await bug_thread.send(components=[bug_container])
            await inter.send(f"Баг-репорт создан! Перейди в него: <#{bug_thread.id}>", ephemeral=True)
            await inter.author.send(
                components=create_container(
                    f"## Спасибо за репорт бага ''{bug_thread_name}''!",
                    f"Сохраню канал баг-репорта здесь: https://discord.com/channels/{inter.guild_id}/{inter.channel_id}",
                    "Треды пропадают через некоторое время, но эта ссылка позволяет тебе в любой момент вернуться!"
                )
            )
            await self.bot.flags.setFlag(bug_thread,"created_by",inter.author.id)
            if 'getsockopt' in bug_description or 'гетсокопт' in bug_description:
                await bug_thread.send(components=create_container(
                    "## Авто-ответ по частой проблеме: `getsockopt`",
                    FAQStorage.getsockopt
                ))


    @commands.slash_command(name='clearbugs', description='Удаляет все треды с багами')
    async def doneCommand(self, inter: disnake.ApplicationCommandInteraction):
        if inter.channel_id == Channels.temp_bugs and inter.author.id == 531208170098655233:
            threads = inter.channel.threads
            amount = len(threads)
            for trd in threads:
                await trd.delete()
            await inter.send(f"Удалено {amount} тредов!")

def setup(bot: commands.Bot):
    bot.add_cog(BugHandler(bot))