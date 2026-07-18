import logging

import disnake
from disnake.ext import commands

from bot.storage import Channels

logger = logging.getLogger("robocat.misc")


class Tests(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.artem123zzz_id = 1348192851573604412

    @commands.Cog.listener("on_message")
    async def restrictArtem123zzz(self, message: disnake.Message):
        if message.guild is None:
            return
        if message.author.bot:
            return
        if message.author.id == self.artem123zzz_id:
            # lower() + 'http': фильтр обходился HTTPS://, http:// и прочим регистром
            if 'http' in message.content.lower() or message.attachments:
                try:
                    await message.delete()
                except (disnake.Forbidden, disnake.NotFound) as e:
                    # NotFound (уже удалено) безобиден, а вот Forbidden — деградация модерации
                    if isinstance(e, disnake.Forbidden):
                        logger.warning("Не удалось удалить сообщение %s ограниченного пользователя в канале %s", message.id, message.channel.id)

    @commands.command("mess")
    @commands.is_owner()
    @commands.guild_only()
    async def sendMessages(self, inter: commands.Context):
        party_search = inter.guild.get_channel(Channels.requests)
        if party_search is None:
            logger.error("Канал запросов %s не найден — тред «Запросы» не создан", Channels.requests)
            return
        tag = party_search.get_tag(1218152835716354058)
        await party_search.create_thread(name="Запросы!", applied_tags=[tag] if tag else [], components=disnake.ui.Container(
            disnake.ui.TextDisplay("# Запросы!"),
            disnake.ui.Separator(),
            disnake.ui.TextDisplay("""Тут ты можешь запросить получение ролей, достижений и прочего!

Например, тут ты можешь запросить **CatPass** - это ветка достижений на Кошкокрафте с различными игровыми заданиями, за которые ты можешь получить награду! От АРов до **рублей** :3
Ты можешь найти эту вкладку под иконкой алмазов в обычных достижениях.

## А получить награду то как?
Всё просто - напиши пост сюда и приложи доказательство выполнения, и всё :3

Или тут ты можешь запросить роль @С 2 сезона @С 3 сезона , медали и прочее!""")
        ))
        


def setup(bot: commands.Bot):
    bot.add_cog(Tests(bot))
