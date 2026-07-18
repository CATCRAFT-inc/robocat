"""Тесты редактора новостей (issue #4): контейнер, парсинг, черновики, публикация.

Discord мокается целиком; контейнер собирается и разбирается на ui-классах
(в проде message.components отдаёт read-side классы с теми же атрибутами
children/content/items — парсер принимает оба).
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import disnake
import pytest

from bot.handlers.news_editor import (
    NewsEditor,
    NewsModal,
    _Draft,
    _news_container,
    _parse_container,
)
from bot.storage import Channels, Roles


def _draft(**kw):
    base = dict(author_id=1, channel_id=Channels.announcements,
                title="Обнова", text="Завезли контент")
    base.update(kw)
    return _Draft(**base)


def _cog():
    inst = object.__new__(NewsEditor)
    inst.bot = MagicMock()
    inst.logger = MagicMock()
    inst.drafts = {}
    import itertools
    inst._seq = itertools.count(1)
    return inst


# --- контейнер ---

def test_news_container_minimal_structure():
    container = _news_container(_draft())
    children = list(container.children)
    assert children[0].content == "# Обнова"
    assert isinstance(children[1], disnake.ui.Separator)
    assert children[2].content == "Завезли контент"
    assert len(children) == 3


def test_news_container_with_image_and_ping():
    container = _news_container(_draft(image_url="https://x/img.png", ping_role_id=Roles.events))
    children = list(container.children)
    galleries = [c for c in children if isinstance(c, disnake.ui.MediaGallery)]
    assert len(galleries) == 1
    assert children[-1].content == f"<@&{Roles.events}>"


def test_parse_container_roundtrip():
    src = _draft(image_url="https://x/img.png", ping_role_id=Roles.events,
                 text="Первая строка\nВторая строка")
    parsed = _parse_container(_news_container(src))
    assert parsed is not None
    assert parsed.title == src.title
    assert parsed.text == src.text
    assert parsed.image_url == src.image_url
    assert parsed.ping_role_id == src.ping_role_id


def test_parse_container_rejects_foreign():
    foreign = disnake.ui.Container(disnake.ui.TextDisplay("просто текст без заголовка"))
    assert _parse_container(foreign) is None


# --- ссылка/ID сообщения ---

def test_parse_message_ref_link_overrides_channel():
    mid, cid = NewsEditor._parse_message_ref(
        "https://discord.com/channels/1/222/333", 999)
    assert (mid, cid) == (333, 222)


def test_parse_message_ref_plain_id_uses_fallback():
    assert NewsEditor._parse_message_ref("12345", 999) == (12345, 999)


def test_parse_message_ref_garbage():
    mid, _ = NewsEditor._parse_message_ref("не ссылка", 999)
    assert mid is None


async def test_news_edit_rejects_non_news_channel():
    # кривая ссылка на не-новостной канал не должна дать перезаписать чужой контейнер
    cog = _cog()
    inter = MagicMock()
    inter.response.send_message = AsyncMock()
    await NewsEditor.newsEdit.callback(
        cog, inter, message="https://discord.com/channels/1/999/333", channel="Объявления"
    )
    inter.response.send_message.assert_awaited_once()
    assert "новостном канале" in inter.response.send_message.await_args.args[0]


# --- модалка ---

def test_modal_constructs_with_prefill():
    cog = _cog()
    cog.drafts[1] = _draft(ping_role_id=Roles.events)
    modal = NewsModal(cog, 1)
    assert modal.custom_id == "news_modal:1"


async def test_modal_callback_updates_draft_and_sends_preview():
    cog = _cog()
    cog.drafts[1] = _draft(title="", text="")
    modal = NewsModal(cog, 1)
    inter = MagicMock()
    inter.message = None
    inter.text_values = {"news_title": " Обнова ", "news_text": "Текст", "news_image": ""}
    inter.resolved_values = {"news_ping": [str(Roles.events)]}
    inter.response.send_message = AsyncMock()

    await modal.callback(inter)

    draft = cog.drafts[1]
    assert draft.title == "Обнова"
    assert draft.ping_role_id == Roles.events
    inter.response.send_message.assert_awaited_once()
    assert inter.response.send_message.await_args.kwargs.get("ephemeral") is True


# --- кнопки и публикация ---

def _button_inter(custom_id: str, *, admin: bool = True):
    inter = MagicMock()
    inter.component.custom_id = custom_id
    role = MagicMock()
    role.id = Roles.admin if admin else 12345
    inter.author.roles = [role]
    inter.response.edit_message = AsyncMock()
    inter.response.send_message = AsyncMock()
    inter.response.send_modal = AsyncMock()
    inter.edit_original_response = AsyncMock()
    return inter


async def test_buttons_ignore_foreign_custom_id():
    cog = _cog()
    inter = _button_inter("HONEYPOT_BAN:1")
    await cog.newsButtons(inter)
    inter.response.edit_message.assert_not_awaited()
    inter.response.send_message.assert_not_awaited()


async def test_buttons_reject_non_admin():
    cog = _cog()
    cog.drafts[1] = _draft()
    inter = _button_inter("NEWS_PUB:1", admin=False)
    await cog.newsButtons(inter)
    inter.response.send_message.assert_awaited_once()


async def test_lost_draft_reports_and_no_publish():
    cog = _cog()
    inter = _button_inter("NEWS_PUB:7")
    await cog.newsButtons(inter)
    inter.response.edit_message.assert_awaited_once()


async def test_cancel_deletes_draft():
    cog = _cog()
    cog.drafts[1] = _draft()
    inter = _button_inter("NEWS_CANCEL:1")
    await cog.newsButtons(inter)
    assert 1 not in cog.drafts


async def test_publish_sends_container_reactions_and_thread(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    cog = _cog()
    cog.drafts[1] = _draft(ping_role_id=Roles.events)
    sent_msg = MagicMock()
    sent_msg.jump_url = "https://discord.com/x"
    sent_msg.add_reaction = AsyncMock()
    sent_msg.create_thread = AsyncMock()
    channel = MagicMock()
    channel.send = AsyncMock(return_value=sent_msg)
    cog.bot.get_channel.return_value = channel
    inter = _button_inter("NEWS_PUB:1")

    await cog.newsButtons(inter)

    channel.send.assert_awaited_once()
    mentions = channel.send.await_args.kwargs["allowed_mentions"]
    assert mentions.roles and mentions.roles[0].id == Roles.events
    assert sent_msg.add_reaction.await_count == 3
    sent_msg.create_thread.assert_awaited_once()
    assert 1 not in cog.drafts


async def test_publish_to_non_reaction_channel_skips_reactions(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    cog = _cog()
    cog.drafts[1] = _draft(channel_id=Channels.informator)
    sent_msg = MagicMock()
    sent_msg.jump_url = "url"
    sent_msg.add_reaction = AsyncMock()
    sent_msg.create_thread = AsyncMock()
    channel = MagicMock()
    channel.send = AsyncMock(return_value=sent_msg)
    cog.bot.get_channel.return_value = channel
    inter = _button_inter("NEWS_PUB:1")

    await cog.newsButtons(inter)

    sent_msg.add_reaction.assert_not_awaited()
    sent_msg.create_thread.assert_not_awaited()


async def test_edit_flow_edits_message_without_reactions(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    cog = _cog()
    cog.drafts[1] = _draft(edit_message_id=555)
    existing = MagicMock()
    existing.jump_url = "url"
    existing.edit = AsyncMock()
    channel = MagicMock()
    channel.fetch_message = AsyncMock(return_value=existing)
    channel.send = AsyncMock()
    cog.bot.get_channel.return_value = channel
    inter = _button_inter("NEWS_PUB:1")

    await cog.newsButtons(inter)

    existing.edit.assert_awaited_once()
    channel.send.assert_not_awaited()
