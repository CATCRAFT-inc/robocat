import logging

import disnake
from disnake.ext import commands

from bot.discord_config import Channels, ForumTags, Users
from bot.server_status import CLOSED_REPLY, SERVER_CLOSED
from bot.storage import Buttons

logger = logging.getLogger("robocat.misc")


class Tests(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener("on_message")
    async def restrictArtem123zzz(self, message: disnake.Message):
        if message.guild is None:
            return
        if message.author.bot:
            return
        if message.author.id == Users.artem123zzz:
            # lower() + 'http': фильтр обходился HTTPS://, http:// и прочим регистром
            if 'http' in message.content.lower() or message.attachments:
                try:
                    await message.delete()
                except (disnake.Forbidden, disnake.NotFound) as e:
                    # NotFound (уже удалено) безобиден, а вот Forbidden — деградация модерации
                    if isinstance(e, disnake.Forbidden):
                        logger.warning("Не удалось удалить сообщение %s ограниченного пользователя в канале %s", message.id, message.channel.id)

    @commands.Cog.listener("on_button_click")
    async def closedVacancyButtons(self, inter: disnake.MessageInteraction):
        """Старые посты вакансий остались в истории — их кнопки теперь молчат.

        Ког вакансий выключен вместе с сервером, поэтому отвечаем здесь, а не
        оставляем человеку «interaction failed». Когда сервер вернут и ког
        включат, флаг `SERVER_CLOSED` снимут, и этот перехватчик отключится.
        """
        if not SERVER_CLOSED:
            return
        custom_id = getattr(inter.component, "custom_id", "") or ""
        if custom_id == Buttons.GET_A_JOB.id or custom_id.startswith("VACANCY_"):
            await inter.response.send_message(CLOSED_REPLY, ephemeral=True)

    @commands.command("mess")
    @commands.is_owner()
    @commands.guild_only()
    async def sendMessages(self, inter: commands.Context):
        party_search = inter.guild.get_channel(Channels.requests)
        if party_search is None:
            logger.error("Канал запросов %s не найден — тред «Запросы» не создан", Channels.requests)
            return
        tag = party_search.get_tag(ForumTags.party_search)
        await party_search.create_thread(name="Запросы!", applied_tags=[tag] if tag else [], components=disnake.ui.Container(
            disnake.ui.TextDisplay("# Запросы!"),
            disnake.ui.Separator(),
            disnake.ui.TextDisplay("""Тут ты можешь запросить получение ролей, достижений и прочего!

Сервер закрыт, но архивные роли и медали остались. Например, тут можно запросить роль @С 2 сезона, @С 3 сезона и прочее.

## А получить награду то как?
Всё просто - напиши пост сюда и приложи доказательство того, что она была получена на сервере, и всё :3""")
        ))
        


def setup(bot: commands.Bot):
    bot.add_cog(Tests(bot))
