"""Заявки на новость: создание треда и решение репортёров.

Проверяем ровно то, что ломается молча: приватный тред без поимённо добавленных
репортёров невидим для них; кнопку решения может нажать не-репортёр; тред
удаляется до того, как архив реально сохранён (переписка теряется навсегда).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.discord_config import Channels, Roles
from bot.handlers.tickets import news_request as nr


def _member(user_id: int, *role_ids: int):
    member = MagicMock()
    member.id = user_id
    member.mention = f"<@{user_id}>"
    member.roles = [MagicMock(id=rid) for rid in role_ids]
    member.send = AsyncMock()
    return member


def _thread(thread_id: int = 777, name: str = "Заявка"):
    thread = MagicMock()
    thread.id = thread_id
    thread.name = name
    thread.parent_id = Channels.news_request
    thread.send = AsyncMock()
    thread.add_user = AsyncMock()
    thread.delete = AsyncMock()
    return thread


def _modal_inter(author, *, values: dict, guild_channel=None, guild_members=None):
    inter = MagicMock()
    inter.author = author
    inter.guild_id = 1
    inter.text_values = values
    inter.response.defer = AsyncMock()
    inter.edit_original_response = AsyncMock()
    inter.guild.get_channel.return_value = guild_channel
    inter.guild.get_member.side_effect = lambda uid: (guild_members or {}).get(uid)
    return inter


@pytest.fixture
def cog():
    handler = nr.NewsRequestHandler(MagicMock())
    return handler


async def test_request_creates_private_thread_and_invites_reporters(cog, monkeypatch):
    author = _member(10)
    reporter = _member(20, Roles.reporter)
    thread = _thread()
    channel = MagicMock()
    channel.create_thread = AsyncMock(return_value=thread)
    role = MagicMock()
    role.members = [reporter]
    thread.guild.get_role.return_value = role

    monkeypatch.setattr(nr.flags, "setFlag", AsyncMock())
    inter = _modal_inter(author, values={"nick": "Кот", "news": "Открылся новый город"}, guild_channel=channel)

    await cog._createRequest(inter)

    assert channel.create_thread.await_args.kwargs["type"] == nr.disnake.ChannelType.private_thread
    # Репортёр обязан быть добавлен поимённо: пинг роли приватный тред не раскрывает
    added = {call.args[0].id for call in thread.add_user.await_args_list}
    assert added == {author.id, reporter.id}
    thread.send.assert_awaited_once()
    author.send.assert_awaited_once()


async def test_request_without_reporters_does_not_report_success(cog, monkeypatch):
    """Роль репортёров пуста/не найдена → тред есть, но заявку никто не увидит.
    Ответ «отправлено репортёрам» тут был бы ложным успехом."""
    author = _member(10)
    thread = _thread()
    channel = MagicMock()
    channel.create_thread = AsyncMock(return_value=thread)
    thread.guild.get_role.return_value = None
    monkeypatch.setattr(nr.flags, "setFlag", AsyncMock())

    inter = _modal_inter(author, values={"nick": "Кот", "news": "текст"}, guild_channel=channel)
    await cog._createRequest(inter)

    answer = inter.edit_original_response.await_args.args[0]
    assert "репортёрам!" not in answer
    assert "админов" in answer
    # и в самом треде админов зовут явно
    assert any("<@&" in str(call.args[0]) for call in thread.send.await_args_list if call.args)


async def test_request_without_channel_answers_interaction(cog):
    author = _member(10)
    inter = _modal_inter(author, values={"nick": "Кот", "news": "текст"}, guild_channel=None)

    await cog._createRequest(inter)

    # Интеракция уже defer'нута — без ответа юзер остаётся с вечным «думает»
    inter.edit_original_response.assert_awaited_once()


def _button_inter(author, custom_id=None):
    inter = MagicMock()
    inter.component.custom_id = custom_id or nr._DONE_ID
    thread = _thread()
    thread.__class__ = nr.disnake.Thread  # isinstance-проверка в хендлере
    inter.channel = thread
    inter.author = author
    inter.response.send_message = AsyncMock()
    inter.response.send_modal = AsyncMock()
    return inter


async def test_decision_button_rejected_for_non_reporter(cog):
    inter = _button_inter(_member(10))  # без роли репортёра

    await cog.newsDecisionButtons(inter)

    inter.response.send_modal.assert_not_awaited()
    # Именно отказ по правам, а не по «это не тред заявки» — иначе тест зелен по ошибке
    assert "репортёры" in inter.response.send_message.await_args.args[0]


async def test_decision_button_opens_modal_for_reporter(cog):
    inter = _button_inter(_member(20, Roles.reporter))

    await cog.newsDecisionButtons(inter)

    inter.response.send_modal.assert_awaited_once()
    inter.response.send_message.assert_not_awaited()


async def test_close_publishes_to_three_places_and_deletes_thread(cog, monkeypatch):
    reporter = _member(20, Roles.reporter)
    author = _member(10)
    thread = _thread()
    reporters_chat = MagicMock()
    reporters_chat.send = AsyncMock()
    cog.bot.get_channel.return_value = reporters_chat

    archive = AsyncMock(return_value="выжимка")
    monkeypatch.setattr(nr, "archive_ticket", archive)
    monkeypatch.setattr(nr.flags, "getFlag", AsyncMock(return_value=MagicMock(value=str(author.id))))
    monkeypatch.setattr(nr.flags, "removeFlag", AsyncMock())

    inter = _modal_inter(reporter, values={"note": "готово"}, guild_members={author.id: author})
    await cog._closeRequest(inter, thread, "готово", published=True)

    archive.assert_awaited_once()          # 1. архив с транскриптом в лог-канал
    reporters_chat.send.assert_awaited_once()  # 2. чат репортёров
    author.send.assert_awaited_once()      # 3. ЛС автору
    thread.delete.assert_awaited_once()


async def test_close_keeps_thread_when_archive_failed(cog, monkeypatch):
    reporter = _member(20, Roles.reporter)
    thread = _thread()
    monkeypatch.setattr(nr, "archive_ticket", AsyncMock(return_value=None))
    monkeypatch.setattr(nr.flags, "removeFlag", AsyncMock())

    inter = _modal_inter(reporter, values={"note": None})
    await cog._closeRequest(inter, thread, None, published=False)

    # Архив не сохранён → удаление треда потеряло бы переписку навсегда
    thread.delete.assert_not_awaited()
    inter.edit_original_response.assert_awaited_once()


async def test_second_reporter_gets_busy_answer(cog, monkeypatch):
    reporter = _member(20, Roles.reporter)
    thread = _thread()
    cog._closing.add(thread.id)
    monkeypatch.setattr(nr, "archive_ticket", AsyncMock(return_value="выжимка"))

    inter = _modal_inter(reporter, values={"note": None})
    await cog._closeRequest(inter, thread, None, published=True)

    thread.delete.assert_not_awaited()
    inter.edit_original_response.assert_awaited_once()
