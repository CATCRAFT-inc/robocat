import logging

import disnake
from disnake.ext import commands

from bot.flag_system.flag_system import Flags
from bot.discord_config import Roles, has_config_roles
from bot.utils import duration_to_text, parse_duration

logger = logging.getLogger("robocat.punishments")


class PunishmentsHanlder(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.slash_command(name="mute", description='Очевидно, мут')
    @has_config_roles("admin", "st_admin", "moderator")
    async def slashMute(self, inter: 
                        disnake.ApplicationCommandInteraction,
                        mute_member: disnake.Member,
                        duration = commands.Param(name="mute_time",
                            choices=[
                            '5сек',
                            '15мин',
                            '1ч',
                            '3д',
                            '28д'
                        ]),
                        reason: str = "Без причины"):
        duration_time = parse_duration(duration)
        if duration_time is None:
            await inter.send("Неправильно указано время!\nПоддерживаемые значения: `1сек`, `1мин`,`1ч`,`1д`,`1н`")
            return
        try:
            await inter.guild.timeout(user=mute_member, reason=reason, duration=duration_time)
        except ValueError as e:
            await inter.send(f'{e}', ephemeral=True)
            return
        except disnake.Forbidden as e:
            await inter.send(f'Этого пользователя нельзя замутить!', ephemeral=True)
            return
        except Exception as e:
            logger.exception("Не удалось замутить %s через /mute", mute_member.id)
            await inter.send(f'Чето пошло не так: {e}', ephemeral=True)
            return
        logger.info("Мут (/mute): %s замьючен модератором %s на %s, причина: %s", mute_member.id, inter.author.id, duration, reason)
        await inter.send(components=disnake.ui.Container(
            disnake.ui.TextDisplay("## Мут!"),
            disnake.ui.Separator(),
            disnake.ui.TextDisplay(f"### {mute_member.mention} был замьючен!\n**Причина**: {reason}\n**Длительность**: {duration_to_text(duration)}")
        ))
        try:
            await mute_member.send(components=disnake.ui.Container(
                disnake.ui.TextDisplay("## Ты был замьючен в ДС Кошкокрафта!"),
                disnake.ui.Separator(),
                disnake.ui.TextDisplay(f"**Причина**: {reason}\n**Длительность**: {duration_to_text(duration)}"),
                disnake.ui.Separator(),
                disnake.ui.TextDisplay("-# Это ошибка? Напиши тикет в ''получить-помощь'' или свяжись с любым админом напрямую!")
            ))
        except disnake.Forbidden:
            logger.info("Не удалось отправить ЛС о муте пользователю %s — закрыты личные сообщения", mute_member.id)


    @staticmethod
    async def _dmModerator(ctx, text: str):
        """Ошибки !мут уходят модератору в ЛС; при закрытых ЛС — фолбэк в канал
        (иначе команда молча исчезала вместе с удалённым сообщением)."""
        try:
            await ctx.author.send(text)
        except disnake.HTTPException:
            try:
                await ctx.send(f"{ctx.author.mention} {text}", delete_after=15)
            except disnake.HTTPException:
                logger.warning("Не удалось доставить модератору %s ответ !мут", ctx.author.id)

    @commands.command(name='мут')
    @has_config_roles("admin", "st_admin", "moderator")
    async def prefixMute(self,
                        ctx: commands.Context,
                        duration: str = '3д',
                        *,
                        reason: str = 'Без причины'):
        # reason — keyword-only (после *): парсер disnake отдаёт такому аргументу
        # весь остаток сообщения. Без этого причина обрезалась до первого слова
        # (issue #3): `!мут 3д привет мир` клал в reason только «привет».
        if not ctx.message.reference:
            await self._dmModerator(ctx, "Замутить можно только ответом на сообщение!")
            await ctx.message.delete()
            return

        try:
            ref_message = await ctx.channel.fetch_message(ctx.message.reference.message_id)
        except disnake.NotFound:
            await self._dmModerator(ctx, "Не могу найти это сообщение.")
            await ctx.message.delete()
            return

        mute_member = ref_message.author
        if not isinstance(mute_member, disnake.Member):
            await self._dmModerator(ctx, "Замутить можно только участника сервера (не бота/вебхук).")
            await ctx.message.delete()
            return
        time = parse_duration(duration)
        if not time:
            await self._dmModerator(ctx, "Неправильный формат времени. Используй `1d`, `12h`, `10m`.")
            await ctx.message.delete()
            return

        try:
            await ctx.guild.timeout(user=mute_member, reason=reason, duration=time)
        except ValueError as e:
            await self._dmModerator(ctx, f'Ошибка: {e}')
            await ctx.message.delete()
            return
        except disnake.Forbidden as e:
            await self._dmModerator(ctx, 'Этого пользователя нельзя замутить!')
            await ctx.message.delete()
            return
        except Exception as e:
            logger.exception("Не удалось замутить %s через !мут", mute_member.id)
            await self._dmModerator(ctx, f'Чето пошло не так: {e}')
            await ctx.message.delete()
            return
        logger.info("Мут (!мут): %s замьючен модератором %s на %s, причина: %s", mute_member.id, ctx.author.id, duration, reason)
        await ctx.send(components=disnake.ui.Container(
            disnake.ui.TextDisplay("## Мут!"),
            disnake.ui.Separator(),
            disnake.ui.TextDisplay(f"### {mute_member.mention} был замьючен!\n**Причина**: {reason}\n**Длительность**: {duration_to_text(duration)}")
        ))
        try:
            await mute_member.send(components=disnake.ui.Container(
                disnake.ui.TextDisplay("## Ты был замьючен в ДС Кошкокрафта!"),
                disnake.ui.Separator(),
                disnake.ui.TextDisplay(f"**Причина**: {reason}\n**Длительность**: {duration_to_text(duration)}"),
                disnake.ui.Separator(),
                disnake.ui.TextDisplay("-# Это ошибка? Напиши тикет в ''получить-помощь'' или свяжись с любым админом напрямую!")
            ))
        except disnake.Forbidden:
            logger.info("Не удалось отправить ЛС о муте пользователю %s — закрыты личные сообщения", mute_member.id)

    # @commands.command(name='lololo')
    # @commands.has_any_role(Roles.admin, Roles.st_admin)
    # async def lololoCommand(self, 
    #                         ctx: commands.Context):
    #     flag = Flags()
    #     await flag.setFlag(ctx.author, ctx.author.id, "test_flag", "test_value", "10сек")
    #     test_flag = await Flags().getFlag(ctx.author,"test_flag")       
    #     await ctx.send(test_flag)
    

    # @commands.command(name='пермабан')
    # @commands.has_any_role(Roles.tech_admin_id["id"])
    # async def permaban(self, inter, reason: str = "Без причины"):
    #     ref_message = await inter.channel.fetch_message(inter.message.reference.message_id)
    #     if ref_message is None:
    #         await inter.send('Данная команда работает только ответом на сообщение!', delete_after=5)
    #         return

    #     member = ref_message.author
    #     await inter.guild.ban(user=member, reason=reason)
    #     await inter.channel.send(embed=create_embed(
    #         title=f'Перма-бан {member.display_name}',
    #         description=f'Причина: `{reason}`'))


def setup(bot: commands.Bot):
    bot.add_cog(PunishmentsHanlder(bot))
