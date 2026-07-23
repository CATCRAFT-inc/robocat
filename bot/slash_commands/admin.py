import logging

import disnake
from disnake.ext import commands

from bot.flag_system.flag_system import flags
from bot.discord_config import (
    Channels,
    ConfigError,
    Roles,
    Users,
    has_config_roles,
    reload_config,
)
from bot.storage import Buttons, ColorStorage, Embeds
from bot.utils import create_embed

logger = logging.getLogger("robocat.admin")

# Имена стабильны для регистрации choices, а сам объект строится при вызове:
# контейнеры с Discord ID тогда видят последний /config-reload.
_EMBED_NAMES = [name for name in vars(Embeds) if not name.startswith("_")]


class AdminCommands(commands.Cog):
    """Админские команды и прочее гавно"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.slash_command(
        name="config-reload",
        description="Перечитать data/discord.local.yml без рестарта бота",
    )
    @has_config_roles("admin", "st_admin")
    async def configReload(self, inter: disnake.ApplicationCommandInteraction):
        await inter.response.defer(ephemeral=True)
        try:
            reload_config()
        except ConfigError as exc:
            logger.warning("config-reload отклонён: %s", exc)
            await inter.edit_original_response(f"Конфиг не применён: {exc}")
            return
        self.bot.owner_id = Users.szarkan
        await inter.edit_original_response("Discord-конфиг перезагружен.")



    @commands.slash_command(name='send_embed', description='Отправить Embed из списка.')
    @has_config_roles("admin", "st_admin")
    async def embedCommand(self, inter: disnake.ApplicationCommandInteraction,
                        embed_name: str = commands.Param(choices=_EMBED_NAMES),
                        message: str = None,
                        silent: bool = True):
        # TODO: реализовать silent. Хотя мб не нужно.
        embed = getattr(Embeds, embed_name, None)
        if embed is None:
            await inter.send("Такого эмбеда/контейнера нет!", ephemeral=True)
            return
        if callable(embed):
            embed = embed()
        if isinstance(embed, disnake.ui.Container):
            await inter.channel.send(components=[embed])
            await inter.send(f"Container {embed_name} отправлен.", ephemeral=True)
        elif isinstance(embed, disnake.Embed):
            await inter.channel.send(content=message, embed=embed)
            await inter.send(f"Embed {embed_name} отправлен.", ephemeral=True)
        else:
            await inter.send("Этот объект нельзя отправить как эмбед/контейнер.", ephemeral=True)

    @commands.slash_command(name='delete_until', description='Удалить все сообщения до определенного сообщения')
    @has_config_roles("admin", "st_admin")
    async def deleteUntil(self, inter: disnake.ApplicationCommandInteraction,
                        message_id: str):
        try:
            message = await inter.channel.fetch_message(int(message_id))
        except (ValueError, disnake.NotFound, disnake.HTTPException):
            logger.warning("delete_until: не удалось получить сообщение %s в канале %s", message_id, inter.channel.id)
            await inter.send("Такого сообщения не нашёл.", ephemeral=True)
            return
        await inter.response.defer(ephemeral=True)
        try:
            deleted = await inter.channel.purge(limit=None, after=message.created_at)
        except disnake.HTTPException as e:
            logger.exception("delete_until: не удалось удалить сообщения в канале %s", inter.channel.id)
            await inter.edit_original_response(f"Не удалось удалить сообщения: {e}")
            return
        logger.info("delete_until: %s удалил %s сообщений в канале %s", inter.author.id, len(deleted), inter.channel.id)
        await inter.edit_original_response(f"Успешно удалено сообщений: {len(deleted)}")

    @commands.slash_command(name='test_some_shit', description='Команда тестирования всякого... говна.')
    @has_config_roles("admin", "st_admin")
    async def testCommandAdminOnlyWarningVeryStrictDontTouch(self, inter: disnake.ApplicationCommandInteraction):
        #await Flags().setFlag(inter.author, "expire_test", "privet PIDORASI", "5сек")
        # has_flag = await Flags().hasFlag(inter.author, "expire_test")
        # print(has_flag)
        logger.info("test_some_shit: member_count=%s, в кэше=%s", inter.guild.member_count, len(inter.guild.members))





def setup(bot: commands.Bot):
    bot.add_cog(AdminCommands(bot))
    logger.info("Ког AdminCommands загружен")
