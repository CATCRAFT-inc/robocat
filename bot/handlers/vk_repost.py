"""Ручной гейт перепоста новостей Discord → ВК (issue #32)."""

import asyncio
import logging
import mimetypes
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
_PREVIEW_LIMIT = 3000
_DETAILS_LIMIT = 300
_ERROR_LIMIT = 300

# message.components отдаёт read-side классы, тесты собирают те же узлы ui-классами.
_GALLERY_TYPES = (disnake.ui.MediaGallery, disnake.MediaGallery)


class VKRepost(commands.Cog):
    """Создаёт гейт и по кнопке переносит выбранный пост в ВК."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # In-flight живёт только до рестарта, но закрывает два быстрых клика.
        self._in_flight: set[str] = set()

    @staticmethod
    def _gallery_urls(message) -> list[str]:
        urls = []
        seen = set()
        stack = list(message.components or [])
        while stack:
            component = stack.pop(0)
            if isinstance(component, _GALLERY_TYPES):
                for item in component.items:
                    media = getattr(item, "media", None)
                    url = getattr(media, "url", None)
                    if (
                        url
                        and not getattr(media, "attachment_id", None)
                        and url not in seen
                    ):
                        seen.add(url)
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
        body = component_text(message.components) or (message.content or "")
        text = self._resolve_mentions(
            vk.discord_md_to_plain(body, seed=message.id),
            message.guild,
        )
        if message.channel.id == Channels.newspaper and not message.author.bot:
            return f"Пост от репортёра: {message.author.display_name}\n\n{text}"
        return text

    def _gate_components(self, message) -> list:
        text = self._message_text(message)
        if len(text) > _PREVIEW_LIMIT:
            text = text[: _PREVIEW_LIMIT - 1].rstrip() + "…"
        body = [disnake.ui.TextDisplay(text or "(без текста)")]

        gallery_count = len(self._gallery_urls(message))
        total = len(message.attachments) + gallery_count
        if total:
            names = [
                attachment.filename
                for attachment in message.attachments[:_MAX_ATTACHMENTS]
            ]
            gallery_slots = _MAX_ATTACHMENTS - len(names)
            if gallery_count and gallery_slots:
                names.append(
                    "картинок из MediaGallery: "
                    f"{min(gallery_count, gallery_slots)}"
                )
            overflow = max(0, total - _MAX_ATTACHMENTS)
            suffix = (
                f" (+{overflow} не уедут: лимит ВК {_MAX_ATTACHMENTS})"
                if overflow
                else ""
            )
            prefix = "-# Вложения: "
            details = ", ".join(names)
            available = _DETAILS_LIMIT - len(prefix) - len(suffix)
            if len(details) > available:
                details = details[: max(0, available - 1)].rstrip() + "…"
            body.append(disnake.ui.TextDisplay(f"{prefix}{details}{suffix}"))

        channel_id = message.channel.id
        message_id = message.id
        return [
            disnake.ui.TextDisplay(
                f"<@{Users.szarkan}> Постить вот это в ВК? {message.jump_url}"
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
            if message.type not in (
                disnake.MessageType.default,
                disnake.MessageType.reply,
            ):
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
            text = f"{error.code}: {error.msg}"
        else:
            text = str(error) or type(error).__name__
        if len(text) > _ERROR_LIMIT:
            return text[: _ERROR_LIMIT - 1].rstrip() + "…"
        return text

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

        async def upload(data, filename, content_type):
            if uploaded:
                await asyncio.sleep(0.35)
            uploaded.append(
                await vk.upload_attachment(data, filename, content_type)
            )

        for attachment in message.attachments:
            if len(uploaded) == _MAX_ATTACHMENTS:
                break
            data = await attachment.read()
            await upload(
                data,
                attachment.filename,
                attachment.content_type or "application/octet-stream",
            )
        for url in gallery_urls:
            if len(uploaded) == _MAX_ATTACHMENTS:
                break
            try:
                data, content_type = await vk.download(url)
            except vk.VKError as error:
                if error.msg.startswith("внешний URL пропущен:"):
                    logger.warning(
                        "Внешнее вложение сообщения %s пропущено: %s",
                        message.id,
                        error.msg,
                    )
                    continue
                raise
            filename = unquote(urlsplit(url).path.rsplit("/", 1)[-1]) or "image.jpg"
            if "." not in filename:
                extension = mimetypes.guess_extension(
                    content_type.split(";", 1)[0]
                )
                if extension:
                    filename += extension
            await upload(data, filename, content_type)
        return uploaded

    async def _restore_failure(
        self,
        inter,
        channel_id: int,
        message_id: int,
        error: Exception,
    ):
        components = [
            disnake.ui.TextDisplay(
                f"❌ Не получилось: {self._error_text(error)}. "
                "Возможно, пост уже ушёл — проверь стену."
            ),
            self._buttons(channel_id, message_id),
        ]
        await inter.edit_original_response(components=components)

    async def _post(self, inter, channel_id: int, message_id: int):
        try:
            channel = self.bot.get_channel(channel_id)
            if channel is None:
                channel = await self.bot.fetch_channel(channel_id)
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
            await self._restore_failure(inter, channel_id, message_id, error)
            return

        try:
            text = self._message_text(message)
            attachments = await self._upload_attachments(message)
            if not text.strip() and not attachments:
                await inter.edit_original_response(
                    components=[
                        disnake.ui.TextDisplay("❌ Пусто — нечего постить.")
                    ]
                )
                return
            post_id = await vk.post_to_group(
                text, attachments, guid=str(message_id)
            )
            url = vk.post_url(post_id)
        except Exception as error:
            logger.exception("Не удалось отправить сообщение %s в ВК", message.id)
            await self._restore_failure(inter, channel_id, message_id, error)
            return

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

        if not vk.enabled():
            await inter.response.edit_message(
                components=[
                    disnake.ui.TextDisplay(
                        "❌ ВК выключен (нет VK_USER_TOKEN/VK_GROUP_ID)."
                    )
                ]
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
            channel_id = message_id = None
            try:
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
                        components=[
                            disnake.ui.TextDisplay(
                                "❌ Некорректная кнопка ВК."
                            )
                        ]
                    )
                    return
                channel_id = int(raw_channel_id)
                message_id = int(raw_message_id)
                await self._post(inter, channel_id, message_id)
            except Exception as error:
                logger.exception("Не удалось обработать кнопку ВК %s", cid)
                try:
                    components = [
                        disnake.ui.TextDisplay(
                            f"❌ Не получилось: {self._error_text(error)}"
                        )
                    ]
                    if channel_id is not None and message_id is not None:
                        components.append(self._buttons(channel_id, message_id))
                    await inter.edit_original_response(components=components)
                except Exception:
                    pass
        finally:
            self._in_flight.discard(cid)


def setup(bot: commands.Bot):
    bot.add_cog(VKRepost(bot))
