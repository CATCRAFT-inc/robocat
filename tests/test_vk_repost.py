"""Тесты ручного гейта перепоста Discord → ВК (issue #32)."""

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import disnake
import pytest

from bot import vk
from bot.discord_config import Channels, Users
from bot.handlers import vk_repost
from bot.handlers.vk_repost import VKRepost


def _cog():
    return VKRepost(MagicMock())


def _message(
    *,
    channel_id=Channels.announcements,
    message_id=555,
    text="Новость дня",
    components=None,
    attachments=None,
):
    message = MagicMock()
    message.id = message_id
    message.channel.id = channel_id
    message.content = text
    message.components = components or []
    message.attachments = attachments or []
    for attachment in message.attachments:
        if not isinstance(attachment.size, int):
            attachment.size = 1
    message.author.display_name = "Рыжий кот"
    message.author.bot = False
    message.webhook_id = None
    message.type = disnake.MessageType.default
    message.jump_url = "https://discord.test/channels/1/2/555"
    message.edited_at = None
    message.guild = MagicMock()
    message.guild.get_member.return_value = None
    message.guild.get_channel.return_value = None
    message.guild.get_role.return_value = None
    return message


def _interaction(custom_id, *, author_id=Users.szarkan):
    inter = MagicMock()
    inter.component.custom_id = custom_id
    inter.author.id = author_id
    inter.response.send_message = AsyncMock()
    inter.response.edit_message = AsyncMock()
    inter.edit_original_response = AsyncMock()
    inter.guild_id = 999
    inter.message.created_at = datetime(2026, 7, 26, tzinfo=timezone.utc)
    inter.message.edited_at = None
    inter.channel.send = AsyncMock()
    return inter


def _texts(components):
    result = []
    stack = list(components)
    while stack:
        component = stack.pop(0)
        content = getattr(component, "content", None)
        if isinstance(content, str):
            result.append(content)
        stack[:0] = list(getattr(component, "children", None) or [])
    return result


def _buttons(components):
    return [
        child
        for component in components
        if isinstance(component, disnake.ui.ActionRow)
        for child in component.children
    ]


def _patch_vk(monkeypatch, *, post_id=77, comment_error=None):
    upload = AsyncMock(return_value="photo-1_2")
    post = AsyncMock(return_value=post_id)
    comment = AsyncMock(side_effect=comment_error)
    download = AsyncMock(return_value=(b"gallery", "image/jpeg"))
    monkeypatch.setattr(vk, "enabled", lambda: True)
    monkeypatch.setattr(vk, "upload_attachment", upload)
    monkeypatch.setattr(vk, "post_to_group", post)
    monkeypatch.setattr(vk, "comment", comment)
    monkeypatch.setattr(vk, "download", download)
    monkeypatch.setattr(vk, "post_url", lambda value: f"https://vk.test/{value}")
    return upload, post, comment, download


def test_cog_invalid_group_with_token_logs_disabled_warning(
    monkeypatch,
    caplog,
):
    # Ошибка конфигурации должна быть видна один раз при загрузке кога.
    monkeypatch.setenv("VK_USER_TOKEN", "token")
    monkeypatch.setenv("VK_GROUP_ID", "-123")

    with caplog.at_level(logging.WARNING, logger="robocat.vkrepost"):
        _cog()

    assert "VK_GROUP_ID некорректен" in caplog.text


async def test_gate_newspaper_message_sends_header_ping_and_buttons(monkeypatch):
    # Газетный пост обязан получить шапку автора и гейт только для Серёжи.
    monkeypatch.setattr(vk, "enabled", lambda: True)
    cog = _cog()
    secret = MagicMock()
    secret.send = AsyncMock()
    cog.bot.get_channel.return_value = secret

    await cog.vkGate(_message(channel_id=Channels.newspaper))

    secret.send.assert_awaited_once()
    kwargs = secret.send.await_args.kwargs
    assert f"<@{Users.szarkan}>" in _texts(kwargs["components"])[0]
    assert "https://discord.test/channels/1/2/555" in _texts(
        kwargs["components"]
    )[0]
    assert "Пост от репортёра: Рыжий кот" in "\n".join(_texts(kwargs["components"]))
    assert [button.custom_id for button in _buttons(kwargs["components"])] == [
        f"VKPOST:{Channels.newspaper}:555",
        f"VKDROP:{Channels.newspaper}:555",
    ]
    assert kwargs["allowed_mentions"].users[0].id == Users.szarkan


async def test_gate_announcements_message_omits_reporter_header(monkeypatch):
    # Объявление должно ехать текст-в-текст без газетной шапки.
    monkeypatch.setattr(vk, "enabled", lambda: True)
    cog = _cog()
    secret = MagicMock()
    secret.send = AsyncMock()
    cog.bot.get_channel.return_value = secret

    await cog.vkGate(_message())

    assert "Пост от репортёра" not in "\n".join(
        _texts(secret.send.await_args.kwargs["components"])
    )


async def test_gate_bot_newspaper_message_omits_reporter_header(monkeypatch):
    # Голый /news-бот не должен подписываться именем Робокотика как репортёра.
    monkeypatch.setattr(vk, "enabled", lambda: True)
    message = _message(channel_id=Channels.newspaper)
    message.author.bot = True
    cog = _cog()
    secret = MagicMock()
    secret.send = AsyncMock()
    cog.bot.get_channel.return_value = secret

    await cog.vkGate(message)

    assert "Пост от репортёра" not in "\n".join(
        _texts(secret.send.await_args.kwargs["components"])
    )


async def test_gate_webhook_newspaper_message_includes_reporter_header(monkeypatch):
    # Имя вебхука в газете — это имя репортёра, его шапка должна сохраниться.
    monkeypatch.setattr(vk, "enabled", lambda: True)
    message = _message(channel_id=Channels.newspaper)
    message.author.bot = True
    message.webhook_id = 123
    cog = _cog()
    secret = MagicMock()
    secret.send = AsyncMock()
    cog.bot.get_channel.return_value = secret

    await cog.vkGate(message)

    assert "Пост от репортёра: Рыжий кот" in "\n".join(
        _texts(secret.send.await_args.kwargs["components"])
    )


async def test_gate_non_news_channel_does_not_send(monkeypatch):
    # Соседние каналы не должны создавать ручные гейты.
    monkeypatch.setattr(vk, "enabled", lambda: True)
    cog = _cog()
    secret = MagicMock()
    secret.send = AsyncMock()
    cog.bot.get_channel.return_value = secret

    await cog.vkGate(_message(channel_id=Channels.secret))

    secret.send.assert_not_awaited()


async def test_gate_system_message_does_not_send(monkeypatch):
    # Системные закрепы не должны создавать пустые ВК-гейты.
    monkeypatch.setattr(vk, "enabled", lambda: True)
    message = _message()
    message.type = disnake.MessageType.pins_add
    cog = _cog()
    secret = MagicMock()
    secret.send = AsyncMock()
    cog.bot.get_channel.return_value = secret

    await cog.vkGate(message)

    secret.send.assert_not_awaited()


async def test_gate_disabled_vk_does_not_send(monkeypatch):
    # Без обоих VK env фича обязана оставаться полностью инертной.
    monkeypatch.setattr(vk, "enabled", lambda: False)
    cog = _cog()
    secret = MagicMock()
    secret.send = AsyncMock()
    cog.bot.get_channel.return_value = secret

    await cog.vkGate(_message())

    secret.send.assert_not_awaited()


async def test_gate_v2_news_uses_component_text_and_drops_role_ping(monkeypatch):
    # Роль-пинг /news удаляется до резолва упоминаний и не попадает в превью.
    monkeypatch.setattr(vk, "enabled", lambda: True)
    container = disnake.ui.Container(
        disnake.ui.TextDisplay("# Заголовок"),
        disnake.ui.TextDisplay("Текст новости"),
        disnake.ui.TextDisplay("<@&123456789012345678>"),
    )
    cog = _cog()
    secret = MagicMock()
    secret.send = AsyncMock()
    cog.bot.get_channel.return_value = secret

    await cog.vkGate(_message(components=[container], text=""))

    preview = "\n".join(_texts(secret.send.await_args.kwargs["components"]))
    assert "Заголовок" in preview
    assert "Текст новости" in preview
    assert "<@&" not in preview


async def test_gate_v2_news_resolves_mentions_after_dropping_role_line(monkeypatch):
    # Сначала удаляется служебная строка роли, затем разворачиваются упоминания в тексте.
    monkeypatch.setattr(vk, "enabled", lambda: True)
    container = disnake.ui.Container(
        disnake.ui.TextDisplay(
            "<@111111111111111111> в <#222222222222222222> зовёт "
            "<@&333333333333333333>\n<@&333333333333333333>"
        )
    )
    message = _message(components=[container], text="")
    message.guild.get_member.return_value = MagicMock(display_name="Кот")
    channel = MagicMock()
    channel.name = "объявления"
    role = MagicMock()
    role.name = "читатель"
    message.guild.get_channel.return_value = channel
    message.guild.get_role.return_value = role
    cog = _cog()
    secret = MagicMock()
    secret.send = AsyncMock()
    cog.bot.get_channel.return_value = secret

    await cog.vkGate(message)

    preview = "\n".join(_texts(secret.send.await_args.kwargs["components"]))
    assert "@Кот в #объявления зовёт @читатель" in preview
    assert preview.count("@читатель") == 1


def test_message_text_legacy_button_falls_back_to_content():
    # Компоненты без TextDisplay не должны скрывать content старого поста.
    row = disnake.ui.ActionRow(
        disnake.ui.Button(label="Кнопка", custom_id="LEGACY")
    )

    assert _cog()._message_text(
        _message(text="Старый пост", components=[row])
    ) == "Старый пост"


def test_message_text_reporter_header_keeps_display_name_markup():
    # Ник репортёра добавляется после MD-чистки и не меняется как тело поста.
    message = _message(channel_id=Channels.newspaper)
    message.author.display_name = "[Кот](https://example.com)"

    text = _cog()._message_text(message)

    assert text.startswith(
        "Пост от репортёра: [Кот](https://example.com)\n\n"
    )


def test_resolve_mentions_missing_role_keeps_placeholder():
    # Неизвестная роль в середине предложения не должна молча исчезать.
    guild = MagicMock()
    guild.get_role.return_value = None

    assert _cog()._resolve_mentions("Зовём <@&123> сюда", guild) == (
        "Зовём @роль сюда"
    )


async def test_gate_human_role_ping_line_is_removed(monkeypatch):
    # Сырой content сохраняет роль-пинг до Markdown-чистки, чтобы строка исчезла.
    monkeypatch.setattr(vk, "enabled", lambda: True)
    cog = _cog()
    secret = MagicMock()
    secret.send = AsyncMock()
    cog.bot.get_channel.return_value = secret

    await cog.vkGate(_message(text="Текст\n<@&123456789012345678>"))

    preview = "\n".join(_texts(secret.send.await_args.kwargs["components"]))
    assert "Текст" in preview
    assert "<@&" not in preview
    assert "@юзер" not in preview


async def test_gate_twelve_attachments_reports_two_over_limit(monkeypatch):
    # Превью должно заранее показать, что лишние вложения не попадут в ВК.
    monkeypatch.setattr(vk, "enabled", lambda: True)
    attachments = [MagicMock(filename=f"{index}.png") for index in range(12)]
    cog = _cog()
    secret = MagicMock()
    secret.send = AsyncMock()
    cog.bot.get_channel.return_value = secret

    await cog.vkGate(_message(attachments=attachments))

    preview = "\n".join(_texts(secret.send.await_args.kwargs["components"]))
    assert "(+2 не уедут: лимит ВК 10)" in preview


async def test_gate_long_attachment_names_fit_details_budget(monkeypatch):
    # Длинные имена файлов не должны переполнить общий лимит V2-сообщения.
    monkeypatch.setattr(vk, "enabled", lambda: True)
    attachments = [
        MagicMock(filename=f"{index}-{'x' * 100}.png")
        for index in range(12)
    ]
    cog = _cog()
    secret = MagicMock()
    secret.send = AsyncMock()
    cog.bot.get_channel.return_value = secret

    await cog.vkGate(_message(attachments=attachments))

    details = next(
        text
        for text in _texts(secret.send.await_args.kwargs["components"])
        if text.startswith("-# Вложения:")
    )
    assert len(details) <= 300
    assert "(+2 не уедут: лимит ВК 10)" in details


async def test_gate_long_preview_truncates_and_sends(monkeypatch):
    # Текст длиннее бюджета должен обрезаться до 3000, а не гасить весь гейт.
    monkeypatch.setattr(vk, "enabled", lambda: True)
    cog = _cog()
    secret = MagicMock()
    secret.send = AsyncMock()
    cog.bot.get_channel.return_value = secret

    await cog.vkGate(_message(text="x" * 4000))

    previews = [
        text
        for text in _texts(secret.send.await_args.kwargs["components"])
        if text.startswith("x")
    ]
    assert len(previews[0]) == 3000
    assert previews[0].endswith("…")


def test_news_reaction_channels_exact_pair_guards_vk_scope():
    # Расширение набора молча включит ВК-репост — прими решение осознанно.
    assert Channels.news_reaction_channels == (
        Channels.announcements,
        Channels.newspaper,
    )


async def test_buttons_foreign_user_denies_without_vk_calls(monkeypatch):
    # Чужой пользователь не должен суметь ни опубликовать, ни отклонить пост.
    upload, post, comment, download = _patch_vk(monkeypatch)
    cog = _cog()
    inter = _interaction(
        f"VKPOST:{Channels.announcements}:555", author_id=Users.szarkan + 1
    )

    await cog.vkButtons(inter)

    inter.response.send_message.assert_awaited_once_with(
        "Кнопка только для Серёжи :3", ephemeral=True
    )
    upload.assert_not_awaited()
    post.assert_not_awaited()
    comment.assert_not_awaited()
    download.assert_not_awaited()


async def test_vkpost_disabled_vk_reports_without_vk_calls(monkeypatch):
    # Старые кнопки после выключения env не должны запускать публикацию.
    upload, post, comment, download = _patch_vk(monkeypatch)
    monkeypatch.setattr(vk, "enabled", lambda: False)
    cog = _cog()
    inter = _interaction(f"VKPOST:{Channels.announcements}:555")

    await cog.vkButtons(inter)

    components = inter.response.edit_message.await_args.kwargs["components"]
    assert "ВК выключен" in "\n".join(_texts(components))
    assert not _buttons(components)
    upload.assert_not_awaited()
    post.assert_not_awaited()
    comment.assert_not_awaited()
    download.assert_not_awaited()


async def test_vkpost_unicode_digit_custom_id_reports_error(monkeypatch):
    # isdigit принимает надстрочные цифры, но int — нет: интеракция не должна зависнуть.
    _patch_vk(monkeypatch)
    cog = _cog()
    inter = _interaction(f"VKPOST:{Channels.announcements}:²")

    await cog.vkButtons(inter)

    final = "\n".join(
        _texts(inter.response.edit_message.await_args.kwargs["components"])
    )
    assert "Некорректная кнопка" in final


async def test_vkpost_attachment_posts_comments_and_shows_link(monkeypatch):
    # Успешный клик читает файл, публикует его и заменяет гейт ссылкой на ВК.
    attachment = MagicMock()
    attachment.filename = "cat.png"
    attachment.content_type = "image/png"
    attachment.read = AsyncMock(return_value=b"image")
    message = _message(attachments=[attachment])
    channel = MagicMock()
    channel.fetch_message = AsyncMock(return_value=message)
    cog = _cog()
    cog.bot.get_channel.return_value = channel
    upload, post, comment, _ = _patch_vk(monkeypatch)
    inter = _interaction(f"VKPOST:{Channels.announcements}:555")

    await cog.vkButtons(inter)

    inter.response.edit_message.assert_awaited_once()
    assert "https://discord.com/channels/999" in "\n".join(
        _texts(inter.response.edit_message.await_args.kwargs["components"])
    )
    upload.assert_awaited_once_with(b"image", "cat.png", "image/png")
    post.assert_awaited_once_with(
        "Новость дня", ["photo-1_2"], guid="555"
    )
    comment.assert_awaited_once_with(
        77,
        "http://discord.gg/catcraftmc\nhttp://t.me/catcraftmc_tg\n\n❤",
        guid="c555",
    )
    final = "\n".join(_texts(inter.edit_original_response.await_args.kwargs["components"]))
    assert "✅ Запощено: https://vk.test/77" in final


async def test_vkpost_empty_message_reports_without_post(monkeypatch):
    # Пустой оригинал без вложений нельзя отправлять в wall.post.
    message = _message(text="")
    channel = MagicMock()
    channel.fetch_message = AsyncMock(return_value=message)
    cog = _cog()
    cog.bot.get_channel.return_value = channel
    upload, post, comment, download = _patch_vk(monkeypatch)
    inter = _interaction(f"VKPOST:{Channels.announcements}:555")

    await cog.vkButtons(inter)

    final = "\n".join(
        _texts(inter.edit_original_response.await_args.kwargs["components"])
    )
    assert "Пусто" in final
    assert not _buttons(
        inter.edit_original_response.await_args.kwargs["components"]
    )
    upload.assert_not_awaited()
    post.assert_not_awaited()
    comment.assert_not_awaited()
    download.assert_not_awaited()


async def test_vkpost_empty_newspaper_message_reports_without_post(monkeypatch):
    # Шапка репортёра не должна превращать стикер без текста в непустой пост.
    message = _message(channel_id=Channels.newspaper, text="")
    channel = MagicMock()
    channel.fetch_message = AsyncMock(return_value=message)
    cog = _cog()
    cog.bot.get_channel.return_value = channel
    _, post, _, _ = _patch_vk(monkeypatch)
    inter = _interaction(f"VKPOST:{Channels.newspaper}:555")

    await cog.vkButtons(inter)

    final = "\n".join(
        _texts(inter.edit_original_response.await_args.kwargs["components"])
    )
    assert "Пусто" in final
    post.assert_not_awaited()


async def test_vkpost_gallery_downloads_url_and_uploads_basename(monkeypatch):
    # MediaGallery — не Discord attachment: внешний URL сначала скачивается.
    gallery = disnake.ui.MediaGallery(
        disnake.MediaGalleryItem("https://cdn.example/news/cat.jpg?size=large")
    )
    message = _message(components=[disnake.ui.Container(gallery)], text="")
    channel = MagicMock()
    channel.fetch_message = AsyncMock(return_value=message)
    cog = _cog()
    cog.bot.get_channel.return_value = channel
    upload, _, _, download = _patch_vk(monkeypatch)
    inter = _interaction(f"VKPOST:{Channels.announcements}:555")

    await cog.vkButtons(inter)

    download.assert_awaited_once_with("https://cdn.example/news/cat.jpg?size=large")
    upload.assert_awaited_once_with(b"gallery", "cat.jpg", "image/jpeg")


async def test_vkpost_gallery_without_extension_adds_mime_extension(monkeypatch):
    # ВК определяет фото по расширению, поэтому URL без суффикса нужно дорастить.
    gallery = disnake.ui.MediaGallery(
        disnake.MediaGalleryItem("https://cdn.discordapp.com/news/cat")
    )
    message = _message(
        components=[disnake.ui.Container(gallery)],
        text="Новость",
    )
    channel = MagicMock()
    channel.fetch_message = AsyncMock(return_value=message)
    cog = _cog()
    cog.bot.get_channel.return_value = channel
    upload, _, _, download = _patch_vk(monkeypatch)
    download.return_value = (b"gallery", "image/png")
    inter = _interaction(f"VKPOST:{Channels.announcements}:555")

    await cog.vkButtons(inter)

    upload.assert_awaited_once_with(b"gallery", "cat.png", "image/png")


async def test_vkpost_external_gallery_url_skips_attachment_and_posts_text(
    monkeypatch,
):
    # Протухший CDN URL не должен отменять безопасный текстовый пост.
    gallery = disnake.ui.MediaGallery(
        disnake.MediaGalleryItem("https://cdn.discordapp.com/cat.png")
    )
    message = _message(
        components=[disnake.ui.Container(gallery)],
        text="Новость",
    )
    channel = MagicMock()
    channel.fetch_message = AsyncMock(return_value=message)
    cog = _cog()
    cog.bot.get_channel.return_value = channel
    upload, post, _, download = _patch_vk(monkeypatch)
    download.side_effect = vk.VKError(404, "HTTP 404")
    inter = _interaction(f"VKPOST:{Channels.announcements}:555")

    await cog.vkButtons(inter)

    upload.assert_not_awaited()
    post.assert_awaited_once_with("Новость", [], guid="555")
    final = "\n".join(
        _texts(inter.edit_original_response.await_args.kwargs["components"])
    )
    assert "(пропущено вложений: 1)" in final


def test_gallery_urls_duplicate_and_attachment_backed_returns_unique(
    monkeypatch,
):
    # Уже прикреплённая gallery-картинка и повтор URL не должны грузиться дважды.
    class Gallery:
        def __init__(self, *items):
            self.items = items
            self.children = []

    duplicate = "https://cdn.discordapp.com/news/cat.png"
    gallery = Gallery(
        MagicMock(
            media=MagicMock(
                proxy_url=None,
                url=duplicate,
                attachment_id=None,
            )
        ),
        MagicMock(
            media=MagicMock(
                proxy_url=None,
                url=duplicate,
                attachment_id=None,
            )
        ),
        MagicMock(
            media=MagicMock(
                proxy_url=None,
                url="https://cdn.discordapp.com/news/attached.png",
                attachment_id=123,
            )
        ),
    )
    monkeypatch.setattr(vk_repost, "_GALLERY_TYPES", (Gallery,))

    assert _cog()._gallery_urls(_message(components=[gallery])) == [duplicate]


def test_gallery_urls_proxy_url_preferred_over_source_url(monkeypatch):
    # Внешняя картинка должна идти через разрешённый Discord media proxy.
    class Gallery:
        def __init__(self, item):
            self.items = [item]
            self.children = []

    media = MagicMock(
        proxy_url="https://media.discordapp.net/proxy/cat.png",
        url="https://example.com/cat.png",
        attachment_id=None,
    )
    monkeypatch.setattr(vk_repost, "_GALLERY_TYPES", (Gallery,))

    assert _cog()._gallery_urls(
        _message(components=[Gallery(MagicMock(media=media))])
    ) == ["https://media.discordapp.net/proxy/cat.png"]


async def test_vkpost_oversized_attachment_skips_before_read(monkeypatch):
    # Большой Discord-файл нельзя целиком читать в RAM до проверки лимита.
    attachment = MagicMock()
    attachment.filename = "huge.mp4"
    attachment.content_type = "video/mp4"
    attachment.size = vk.MAX_UPLOAD + 1
    attachment.read = AsyncMock(return_value=b"too large")
    message = _message(attachments=[attachment])
    channel = MagicMock()
    channel.fetch_message = AsyncMock(return_value=message)
    cog = _cog()
    cog.bot.get_channel.return_value = channel
    upload, post, _, _ = _patch_vk(monkeypatch)
    inter = _interaction(f"VKPOST:{Channels.announcements}:555")

    await cog.vkButtons(inter)

    attachment.read.assert_not_awaited()
    upload.assert_not_awaited()
    post.assert_awaited_once_with("Новость дня", [], guid="555")
    final = "\n".join(
        _texts(inter.edit_original_response.await_args.kwargs["components"])
    )
    assert "(пропущено вложений: 1)" in final


async def test_vkpost_missing_original_reports_without_vk_calls(monkeypatch):
    # Удалённый после создания гейта оригинал нельзя публиковать из старого превью.
    response = MagicMock(status=404, reason="Not Found")
    channel = MagicMock()
    channel.fetch_message = AsyncMock(
        side_effect=disnake.NotFound(response, "gone")
    )
    cog = _cog()
    cog.bot.get_channel.return_value = channel
    upload, post, comment, download = _patch_vk(monkeypatch)
    inter = _interaction(f"VKPOST:{Channels.announcements}:555")

    await cog.vkButtons(inter)

    final = "\n".join(_texts(inter.edit_original_response.await_args.kwargs["components"]))
    assert "не найден" in final
    assert "https://discord.com/channels/999" in final
    assert len(
        _buttons(inter.edit_original_response.await_args.kwargs["components"])
    ) == 2
    upload.assert_not_awaited()
    post.assert_not_awaited()
    comment.assert_not_awaited()
    download.assert_not_awaited()


async def test_vkpost_channel_cache_miss_fetches_channel(monkeypatch):
    # Реконнект с пустым кешем не должен выглядеть как удалённый оригинал.
    message = _message()
    channel = MagicMock()
    channel.fetch_message = AsyncMock(return_value=message)
    cog = _cog()
    cog.bot.get_channel.return_value = None
    cog.bot.fetch_channel = AsyncMock(return_value=channel)
    _, post, _, _ = _patch_vk(monkeypatch)
    inter = _interaction(f"VKPOST:{Channels.announcements}:555")

    await cog.vkButtons(inter)

    cog.bot.fetch_channel.assert_awaited_once_with(Channels.announcements)
    post.assert_awaited_once()


async def test_vkpost_edited_after_gate_rerenders_without_vk_calls(monkeypatch):
    # Аппрув должен относиться к тем байтам, которые реально публикуются.
    message = _message()
    channel = MagicMock()
    channel.fetch_message = AsyncMock(return_value=message)
    cog = _cog()
    cog.bot.get_channel.return_value = channel
    upload, post, comment, download = _patch_vk(monkeypatch)
    inter = _interaction(f"VKPOST:{Channels.announcements}:555")
    message.edited_at = inter.message.created_at + timedelta(seconds=1)

    await cog.vkButtons(inter)

    components = inter.edit_original_response.await_args.kwargs["components"]
    assert "⚠️ Пост изменили после пинга" in "\n".join(_texts(components))
    assert len(_buttons(components)) == 2
    assert "allowed_mentions" in inter.edit_original_response.await_args.kwargs
    upload.assert_not_awaited()
    post.assert_not_awaited()
    comment.assert_not_awaited()
    download.assert_not_awaited()


async def test_vkpost_second_click_after_rerender_posts(monkeypatch):
    # Время редактирования гейта должно разрешить повторный осознанный аппрув.
    message = _message()
    channel = MagicMock()
    channel.fetch_message = AsyncMock(return_value=message)
    cog = _cog()
    cog.bot.get_channel.return_value = channel
    _, post, _, _ = _patch_vk(monkeypatch)
    first = _interaction(f"VKPOST:{Channels.announcements}:555")
    message.edited_at = first.message.created_at + timedelta(seconds=1)

    await cog.vkButtons(first)

    second = _interaction(f"VKPOST:{Channels.announcements}:555")
    second.message.edited_at = message.edited_at + timedelta(seconds=1)
    await cog.vkButtons(second)

    post.assert_awaited_once_with("Новость дня", [], guid="555")


async def test_vkpost_post_error_restores_retry_buttons(monkeypatch):
    # Сбой до создания поста обязан вернуть кнопки, чтобы повторный клик был ретраем.
    message = _message()
    channel = MagicMock()
    channel.fetch_message = AsyncMock(return_value=message)
    cog = _cog()
    cog.bot.get_channel.return_value = channel
    upload, post, comment, _ = _patch_vk(monkeypatch)
    post.side_effect = vk.VKError(5, "denied")
    inter = _interaction(f"VKPOST:{Channels.announcements}:555")

    await cog.vkButtons(inter)

    components = inter.edit_original_response.await_args.kwargs["components"]
    final = "\n".join(_texts(components))
    assert "❌" in final
    assert "проверь стену" in final
    assert "Новость дня" not in final
    assert len(_buttons(components)) == 2
    assert "allowed_mentions" in inter.edit_original_response.await_args.kwargs
    upload.assert_not_awaited()
    comment.assert_not_awaited()


async def test_vkpost_unexpected_error_after_ack_restores_retry_buttons(
    monkeypatch,
):
    # Неожиданная ошибка после ⏳ не должна навсегда оставлять гейт без кнопок.
    _patch_vk(monkeypatch)
    cog = _cog()
    cog._post = AsyncMock(side_effect=RuntimeError("boom"))
    inter = _interaction(f"VKPOST:{Channels.announcements}:555")

    await cog.vkButtons(inter)

    components = inter.edit_original_response.await_args.kwargs["components"]
    assert "❌" in "\n".join(_texts(components))
    assert len(_buttons(components)) == 2


def test_error_text_long_error_returns_three_hundred_chars():
    # Длинный текст исключения не должен переполнить V2-ответ гейта.
    text = _cog()._error_text(RuntimeError("x" * 500))

    assert len(text) == 300
    assert text.endswith("…")


async def test_vkpost_comment_error_keeps_success_without_buttons(monkeypatch):
    # После wall.post ошибка комментария не должна возвращать опасную кнопку ретрая.
    message = _message()
    channel = MagicMock()
    channel.fetch_message = AsyncMock(return_value=message)
    cog = _cog()
    cog.bot.get_channel.return_value = channel
    _, post, comment, _ = _patch_vk(
        monkeypatch, comment_error=vk.VKError(222, "forbidden")
    )
    inter = _interaction(f"VKPOST:{Channels.announcements}:555")

    await cog.vkButtons(inter)

    components = inter.edit_original_response.await_args.kwargs["components"]
    final = "\n".join(_texts(components))
    assert "✅" in final
    assert "коммент не ушёл" in final
    assert not _buttons(components)
    post.assert_awaited_once()
    comment.assert_awaited_once()


async def test_vkpost_comment_error_reports_skipped_attachments(monkeypatch):
    # Счётчик скипов не должен теряться в ветке ошибки комментария.
    attachment = MagicMock()
    attachment.filename = "huge.bin"
    attachment.content_type = "application/octet-stream"
    attachment.size = vk.MAX_UPLOAD + 1
    attachment.read = AsyncMock()
    message = _message(attachments=[attachment])
    channel = MagicMock()
    channel.fetch_message = AsyncMock(return_value=message)
    cog = _cog()
    cog.bot.get_channel.return_value = channel
    _patch_vk(monkeypatch, comment_error=vk.VKError(222, "forbidden"))
    inter = _interaction(f"VKPOST:{Channels.announcements}:555")

    await cog.vkButtons(inter)

    final = "\n".join(
        _texts(inter.edit_original_response.await_args.kwargs["components"])
    )
    assert "(пропущено вложений: 1)" in final
    assert "коммент не ушёл" in final


async def test_vkpost_success_edit_failure_sends_channel_notice_without_buttons(
    monkeypatch,
):
    # После wall.post сбой обновления гейта не должен возвращать опасный ретрай.
    message = _message()
    channel = MagicMock()
    channel.fetch_message = AsyncMock(return_value=message)
    cog = _cog()
    cog.bot.get_channel.return_value = channel
    _patch_vk(monkeypatch)
    inter = _interaction(f"VKPOST:{Channels.announcements}:555")
    inter.edit_original_response.side_effect = disnake.HTTPException(
        MagicMock(status=500), "boom"
    )

    await cog.vkButtons(inter)

    inter.channel.send.assert_awaited_once_with(
        "✅ Запощено: https://vk.test/77 (гейт не обновился)"
    )
    assert not _buttons(
        inter.response.edit_message.await_args.kwargs["components"]
    )


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (6, "6: лимит запросов ВК — подожди минуту"),
        (9, "9: флуд-контроль ВК — подожди"),
        (14, "14: ВК требует капчу (токен под подозрением)"),
    ],
)
def test_error_text_known_vk_code_returns_friendly_message(code, expected):
    # Частые ошибки ВК должны объяснять администратору следующий шаг.
    assert _cog()._error_text(vk.VKError(code, "raw")) == expected


async def test_vkpost_double_click_in_flight_denies_second_post(monkeypatch):
    # Два быстрых клика до снятия кнопок не должны публиковать пост в ВК дважды.
    upload, post, comment, download = _patch_vk(monkeypatch)
    cog = _cog()
    cid = f"VKPOST:{Channels.announcements}:555"
    cog._in_flight.add(cid)
    inter = _interaction(cid)

    await cog.vkButtons(inter)

    inter.response.send_message.assert_awaited_once()
    assert inter.response.send_message.await_args.kwargs.get("ephemeral") is True
    inter.response.edit_message.assert_not_awaited()
    post.assert_not_awaited()


async def test_vkdrop_replaces_gate_without_vk_calls(monkeypatch):
    # Отклонение должно только закрыть гейт и не обращаться к ВК.
    upload, post, comment, download = _patch_vk(monkeypatch)
    cog = _cog()
    inter = _interaction(f"VKDROP:{Channels.announcements}:555")

    await cog.vkButtons(inter)

    final = "\n".join(_texts(inter.response.edit_message.await_args.kwargs["components"]))
    assert "Отклонено" in final
    upload.assert_not_awaited()
    post.assert_not_awaited()
    comment.assert_not_awaited()
    download.assert_not_awaited()


async def test_vkpost_eleven_attachments_uploads_only_ten(monkeypatch):
    # Лимит стены ВК — десять вложений; одиннадцатое нельзя даже загружать.
    attachments = []
    for index in range(11):
        attachment = MagicMock()
        attachment.filename = f"{index}.png"
        attachment.content_type = "image/png"
        attachment.read = AsyncMock(return_value=str(index).encode())
        attachments.append(attachment)
    message = _message(attachments=attachments)
    channel = MagicMock()
    channel.fetch_message = AsyncMock(return_value=message)
    cog = _cog()
    cog.bot.get_channel.return_value = channel
    upload, post, _, _ = _patch_vk(monkeypatch)
    upload.side_effect = [f"photo-1_{index}" for index in range(10)]
    inter = _interaction(f"VKPOST:{Channels.announcements}:555")

    await cog.vkButtons(inter)

    assert upload.await_count == 10
    assert attachments[10].read.await_count == 0
    assert len(post.await_args.args[1]) == 10
