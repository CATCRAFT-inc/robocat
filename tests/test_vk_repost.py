"""Тесты ручного гейта перепоста Discord → ВК (issue #32)."""

from unittest.mock import AsyncMock, MagicMock

import disnake

from bot import vk
from bot.discord_config import Channels, Users
from bot.handlers.vk_repost import VKRepost


def _cog():
    inst = object.__new__(VKRepost)
    inst.bot = MagicMock()
    inst._in_flight = set()
    return inst


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
    message.clean_content = text
    message.components = components or []
    message.attachments = attachments or []
    message.author.display_name = "Рыжий кот"
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
    monkeypatch.setattr(vk, "upload_attachment", upload)
    monkeypatch.setattr(vk, "post_to_group", post)
    monkeypatch.setattr(vk, "comment", comment)
    monkeypatch.setattr(vk, "download", download)
    monkeypatch.setattr(vk, "post_url", lambda value: f"https://vk.test/{value}")
    return upload, post, comment, download


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


async def test_gate_non_news_channel_does_not_send(monkeypatch):
    # Соседние каналы не должны создавать ручные гейты.
    monkeypatch.setattr(vk, "enabled", lambda: True)
    cog = _cog()
    secret = MagicMock()
    secret.send = AsyncMock()
    cog.bot.get_channel.return_value = secret

    await cog.vkGate(_message(channel_id=Channels.secret))

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


async def test_vkpost_unicode_digit_custom_id_reports_error(monkeypatch):
    # isdigit принимает надстрочные цифры, но int — нет: интеракция не должна зависнуть.
    _patch_vk(monkeypatch)
    cog = _cog()
    inter = _interaction(f"VKPOST:{Channels.announcements}:²")

    await cog.vkButtons(inter)

    final = "\n".join(_texts(inter.edit_original_response.await_args.kwargs["components"]))
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
    upload.assert_awaited_once_with(b"image", "cat.png", "image/png")
    post.assert_awaited_once_with("Новость дня", ["photo-1_2"])
    comment.assert_awaited_once_with(
        77,
        "http://discord.gg/catcraftmc\nhttp://t.me/catcraftmc_tg\n\n❤",
    )
    final = "\n".join(_texts(inter.edit_original_response.await_args.kwargs["components"]))
    assert "✅ Запощено: https://vk.test/77" in final


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
    upload.assert_not_awaited()
    post.assert_not_awaited()
    comment.assert_not_awaited()
    download.assert_not_awaited()


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
    assert "❌" in "\n".join(_texts(components))
    assert len(_buttons(components)) == 2
    upload.assert_not_awaited()
    comment.assert_not_awaited()


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
