"""Ручной гейт перепоста новостей Discord → ВК (issue #32)."""

import logging
import re
from urllib.parse import unquote, urlsplit

import disnake
from disnake.ext import commands

from bot import vk
from bot.discord_config import Channels, Users
from bot.utils import component_text


logger = logging.getLogger("robocat.vkrepost")

_COMMENT_TEXT = "http://discord.gg/catcraftmc\nhttp://t.me/catcraftmc_tg\n\n❤"
_MAX_ATTACHMENTS = 10
_PREVIEW_LIMIT = 3500

# message.components отдаёт read-side классы, тесты собирают те же узлы ui-классами.
_GALLERY_TYPES = (disnake.ui.MediaGallery, disnake.MediaGallery)


class VKRepost(commands.Cog):
    """Создаёт гейт и по кнопке переносит выбранный пост в ВК."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # VKPOST в полёте: у news_editor от даблклика защищает синхронный drafts.pop,
        # здесь состояния нет — без guard'а два быстрых клика дали бы два поста в ВК
        self._in_flight: set[str] = set()

    @staticmethod
    def _gallery_urls(message) -> list[str]:
        urls = []
        stack = list(message.components or [])
        while stack:
            component = stack.pop(0)
            if isinstance(component, _GALLERY_TYPES):
                for item in component.items:
                    url = getattr(getattr(item, "media", None), "url", None)
                    if url:
                        urls.append(url)
            stack[:0] = list(getattr(component, "children", None) or [])
        return urls

    @staticmethod
    def _resolve_mentions(text: str, guild) -> str:
        def user_name(match):
            member = guild.get_member(int(match.group(1))) if guild else None
            return f"@{member.display_name}" if member else "@юзер"

        def channel_name(match):
            channel = guild.get_channel(int(match.group(1))) if guild else None
            return f"#{channel.name}" if channel else "#канал"

        def role_name(match):
            role = guild.get_role(int(match.group(1))) if guild else None
            return f"@{role.name}" if role else ""

        text = re.sub(r"<@!?(\d+)>", user_name, text)
        text = re.sub(r"<#(\d+)>", channel_name, text)
        return re.sub(r"<@&(\d+)>", role_name, text)

    def _message_text(self, message) -> str:
        resolve_mentions = bool(message.components)
        text = (
            component_text(message.components)
            if resolve_mentions
            else message.clean_content
        )
        if message.channel.id == Channels.newspaper:
            text = f"Пост от репортёра: {message.author.display_name}\n\n{text}"
        text = vk.discord_md_to_plain(text)
        return self._resolve_mentions(text, message.guild) if resolve_mentions else text

    def _gate_components(self, message) -> list:
        text = self._message_text(message)
        if len(text) > _PREVIEW_LIMIT:
            text = text[: _PREVIEW_LIMIT - 1].rstrip() + "…"
        body = [disnake.ui.TextDisplay(text or "(без текста)")]

        attachment_names = [attachment.filename for attachment in message.attachments]
        gallery_count = len(self._gallery_urls(message))
        if gallery_count:
            attachment_names.append(f"картинок из MediaGallery: {gallery_count}")
        if attachment_names:
            body.append(
                disnake.ui.TextDisplay(f"-# Вложения: {', '.join(attachment_names)}")
            )

        channel_id = message.channel.id
        message_id = message.id
        return [
            disnake.ui.TextDisplay(
                f"<@{Users.szarkan}> Постить вот это в ВК?"
            ),
            disnake.ui.Container(*body),
            self._buttons(channel_id, message_id),
        ]

    @staticmethod
    def _buttons(channel_id: int, message_id: int) -> disnake.ui.ActionRow:
        return disnake.ui.ActionRow(
            disnake.ui.Button(
                style=disnake.ButtonStyle.success,
                label="В ВК",
                custom_id=f"VKPOST:{channel_id}:{message_id}",
            ),
            disnake.ui.Button(
                style=disnake.ButtonStyle.danger,
                label="Не постить",
                custom_id=f"VKDROP:{channel_id}:{message_id}",
            ),
        )

    async def _secret_channel(self):
        channel = self.bot.get_channel(Channels.secret)
        if channel is not None:
            return channel
        try:
            return await self.bot.fetch_channel(Channels.secret)
        except (disnake.NotFound, disnake.Forbidden, disnake.HTTPException):
            logger.exception("Не удалось получить secret-канал %s", Channels.secret)
            return None

    @commands.Cog.listener("on_message")
    async def vkGate(self, message: disnake.Message):
        try:
            if not vk.enabled():
                return
            if message.channel.id not in Channels.news_reaction_channels:
                return
            channel = await self._secret_channel()
            if channel is None:
                return
            await channel.send(
                components=self._gate_components(message),
                allowed_mentions=disnake.AllowedMentions(
                    users=[disnake.Object(id=Users.szarkan)],
                    everyone=False,
                    roles=False,
                ),
            )
        except Exception:
            logger.exception("Не удалось создать ВК-гейт для сообщения %s", message.id)

    @staticmethod
    def _error_text(error: Exception) -> str:
        if isinstance(error, vk.VKError):
            return f"{error.code}: {error.msg}"
        return str(error) or type(error).__name__

    async def _upload_attachments(self, message) -> list[str]:
        gallery_urls = self._gallery_urls(message)
        total = len(message.attachments) + len(gallery_urls)
        if total > _MAX_ATTACHMENTS:
            logger.warning(
                "У сообщения %s вложений %s, в ВК уйдут первые %s",
                message.id,
                total,
                _MAX_ATTACHMENTS,
            )

        uploaded = []
        for attachment in message.attachments:
            if len(uploaded) == _MAX_ATTACHMENTS:
                break
            data = await attachment.read()
            uploaded.append(
                await vk.upload_attachment(
                    data,
                    attachment.filename,
                    attachment.content_type or "application/octet-stream",
                )
            )
        for url in gallery_urls:
            if len(uploaded) == _MAX_ATTACHMENTS:
                break
            data, content_type = await vk.download(url)
            filename = unquote(urlsplit(url).path.rsplit("/", 1)[-1]) or "image.jpg"
            uploaded.append(await vk.upload_attachment(data, filename, content_type))
        return uploaded

    async def _restore_failure(
        self,
        inter,
        channel,
        message,
        channel_id: int,
        message_id: int,
        error: Exception,
    ):
        try:
            message = await channel.fetch_message(message_id)
        except (disnake.NotFound, disnake.Forbidden, disnake.HTTPException):
            pass
        components = [
            disnake.ui.TextDisplay(f"❌ Не получилось: {self._error_text(error)}"),
            *self._gate_components(message),
        ]
        await inter.edit_original_response(components=components)

    async def _post(self, inter, channel_id: int, message_id: int):
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            await inter.edit_original_response(
                components=[
                    disnake.ui.TextDisplay("❌ Оригинал не найден (удалили?)")
                ]
            )
            return
        try:
            message = await channel.fetch_message(message_id)
        except (disnake.NotFound, disnake.Forbidden):
            await inter.edit_original_response(
                components=[
                    disnake.ui.TextDisplay("❌ Оригинал не найден (удалили?)")
                ]
            )
            return
        except Exception as error:
            logger.exception("Не удалось получить сообщение %s:%s", channel_id, message_id)
            await inter.edit_original_response(
                components=[
                    disnake.ui.TextDisplay(
                        f"❌ Не получилось: {self._error_text(error)}"
                    ),
                    self._buttons(channel_id, message_id),
                ]
            )
            return

        try:
            text = self._message_text(message)
            attachments = await self._upload_attachments(message)
            post_id = await vk.post_to_group(text, attachments)
        except Exception as error:
            logger.exception("Не удалось отправить сообщение %s в ВК", message.id)
            await self._restore_failure(
                inter, channel, message, channel_id, message_id, error
            )
            return

        url = vk.post_url(post_id)
        try:
            await vk.comment(post_id, _COMMENT_TEXT)
        except Exception as error:
            logger.exception("Пост %s вышел, но комментарий ВК не отправлен", post_id)
            await inter.edit_original_response(
                components=[
                    disnake.ui.TextDisplay(
                        f"✅ Запощено: {url}, но коммент не ушёл: "
                        f"{self._error_text(error)}"
                    )
                ]
            )
            return
        await inter.edit_original_response(
            components=[disnake.ui.TextDisplay(f"✅ Запощено: {url}")]
        )

    @commands.Cog.listener("on_button_click")
    async def vkButtons(self, inter: disnake.MessageInteraction):
        cid = inter.component.custom_id or ""
        if not (cid.startswith("VKPOST:") or cid.startswith("VKDROP:")):
            return
        if inter.author.id != Users.szarkan:
            await inter.response.send_message(
                "Кнопка только для Серёжи :3", ephemeral=True
            )
            return

        action, _, raw_ids = cid.partition(":")
        if action == "VKDROP":
            await inter.response.edit_message(
                components=[disnake.ui.TextDisplay("❌ Отклонено.")]
            )
            return

        if cid in self._in_flight:
            await inter.response.send_message("Уже пощу — секунду.", ephemeral=True)
            return
        self._in_flight.add(cid)
        try:
            await inter.response.edit_message(
                components=[disnake.ui.TextDisplay("⏳ Пощу в ВК...")]
            )
            raw_channel_id, separator, raw_message_id = raw_ids.partition(":")
            if (
                not separator
                or not raw_channel_id.isascii()
                or not raw_message_id.isascii()
                or not raw_channel_id.isdigit()
                or not raw_message_id.isdigit()
            ):
                logger.warning("Некорректный custom_id ВК: %s", cid)
                await inter.edit_original_response(
                    components=[disnake.ui.TextDisplay("❌ Некорректная кнопка ВК.")]
                )
                return
            await self._post(inter, int(raw_channel_id), int(raw_message_id))
        finally:
            self._in_flight.discard(cid)


def setup(bot: commands.Bot):
    bot.add_cog(VKRepost(bot))
