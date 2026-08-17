"""«Создать приват»: заход в канал-триггер создаёт личный голосовой канал.

Discord не умеет join-to-create сам — всё держится на этом коге. Владелец
получает полный контроль над своим каналом, канал удаляется как только опустеет.
Владение хранится флагом на канале (а не в памяти), поэтому приваты, опустевшие
пока бот лежал на деплое, подчищаются sweep'ом на старте.
"""

import logging

import disnake
from disnake.ext import commands

from bot.discord_config import Channels
from bot.flag_system.flag_system import flags

logger = logging.getLogger("robocat.private_voice")

OWNER_FLAG = "private_voice_owner"

# Права владельца в своём приватном канале: имя, лимит, кик, доступ.
_OWNER_PERMS = disnake.PermissionOverwrite(
    manage_channels=True,
    manage_permissions=True,
    move_members=True,
    mute_members=True,
    deafen_members=True,
)


class PrivateVoice(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener("on_voice_state_update")
    async def onVoiceUpdate(
        self,
        member: disnake.Member,
        before: disnake.VoiceState,
        after: disnake.VoiceState,
    ):
        if member.bot:
            return

        after_id = after.channel.id if after.channel else None

        # Заход в канал-триггер → создать личный приват и перекинуть игрока.
        if after_id == Channels.create_private_voice:
            await self._create(member, after.channel)

        # Уход из канала (или переход в другой) → снести, если это опустевший наш приват.
        if before.channel is not None and before.channel.id != after_id:
            await self._cleanup(before.channel)

    async def _create(self, member: disnake.Member, trigger: disnake.VoiceChannel):
        try:
            # category без overwrites → канал синкается с правами категории,
            # поверх точечно выдаём владельцу управление его каналом.
            channel = await member.guild.create_voice_channel(
                name=member.display_name,
                category=trigger.category,
                reason=f"Приват для {member} ({member.id})",
            )
        except disnake.HTTPException:
            logger.exception("Не удалось создать приват для %s", member.id)
            return

        try:
            await channel.set_permissions(
                member, overwrite=_OWNER_PERMS, reason="Владелец приватного канала"
            )
            await member.move_to(channel, reason="Переброс в свой приват")
        except disnake.HTTPException:
            # Игрок уже вышел или прав не хватило — приват остался бы пустым, сносим.
            logger.warning("Не удалось выдать/перекинуть %s в приват %s", member.id, channel.id)
            try:
                await channel.delete(reason="Приват без владельца")
            except disnake.HTTPException:
                logger.exception("Не удалось удалить брошенный приват %s", channel.id)
            return

        await flags.setFlag(channel, OWNER_FLAG, member.id)
        logger.info("Создан приват %s для %s (%s)", channel.id, member, member.id)

    async def _cleanup(self, channel: disnake.abc.GuildChannel):
        if not isinstance(channel, disnake.VoiceChannel) or channel.members:
            return
        try:
            if await flags.getFlag(channel, OWNER_FLAG) is None:
                return  # не наш приват — не трогаем
        except Exception:
            logger.exception("Не удалось проверить флаг привата %s", channel.id)
            return
        try:
            await channel.delete(reason="Приват опустел")
        except disnake.NotFound:
            pass
        except disnake.HTTPException:
            logger.exception("Не удалось удалить опустевший приват %s", channel.id)
            return
        await flags.removeFlag(channel, OWNER_FLAG, reason="Приват опустел")
        logger.info("Удалён опустевший приват %s", channel.id)

    @commands.Cog.listener("on_ready")
    async def sweepOrphans(self):
        """Приваты, опустевшие пока бот лежал: on_voice_state_update по ним уже
        не придёт, поэтому чистим их один раз на старте."""
        try:
            rows = await flags.getAllWithFlag(OWNER_FLAG)
        except Exception:
            logger.exception("Не удалось получить список приватов для sweep")
            return
        for entity_type, channel_id, _exp in rows or []:
            if entity_type != "voicechannel":
                continue
            channel = self.bot.get_channel(channel_id)
            # channel is None → канал уже удалён; ждущий флаг безвреден, GC не делаем.
            # ponytail: мёртвые строки флагов не чистим — Discord ID не переиспользует, редко.
            if channel is not None:
                await self._cleanup(channel)


def setup(bot: commands.Bot):
    bot.add_cog(PrivateVoice(bot))
