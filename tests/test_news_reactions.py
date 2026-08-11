"""Реакции и тред под новостями (bot.bot.on_message).

issue #39: пост, сделанный через вебхук, приходит с `author.bot == True`, и
старое условие отсекало его вместе с обычными ботами — новость оставалась без
«пака» реакций и без треда обсуждения. Отличает вебхук от бота `webhook_id`.
"""

from unittest.mock import AsyncMock, MagicMock

import disnake
import pytest

import bot.bot as bot_module
from bot.bot import on_message
from bot.discord_config import Channels


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    # В листенере sleep(1) между реакциями — в тесте ждать нечего.
    monkeypatch.setattr(bot_module.asyncio, "sleep", AsyncMock())


def _post(*, channel_id=Channels.announcements, is_bot=False, webhook_id=None):
    message = MagicMock()
    message.id = 900
    message.channel = MagicMock()
    message.channel.id = channel_id
    message.author.bot = is_bot
    message.webhook_id = webhook_id
    message.type = disnake.MessageType.default
    message.add_reaction = AsyncMock()
    message.create_thread = AsyncMock()
    return message


async def test_webhook_post_gets_reactions_and_thread():
    message = _post(is_bot=True, webhook_id=12345)

    await on_message(message)

    assert message.add_reaction.await_count == 3
    message.create_thread.assert_awaited_once()


async def test_human_post_still_gets_reactions_and_thread():
    message = _post()

    await on_message(message)

    assert message.add_reaction.await_count == 3
    message.create_thread.assert_awaited_once()


async def test_plain_bot_post_is_still_ignored():
    # Свои посты бот декорирует сам (news_editor) — второй заход дал бы дубль.
    message = _post(is_bot=True, webhook_id=None)

    await on_message(message)

    message.add_reaction.assert_not_awaited()
    message.create_thread.assert_not_awaited()


async def test_post_outside_news_channels_is_ignored():
    message = _post(channel_id=1234567890123456789, is_bot=True, webhook_id=12345)

    await on_message(message)

    message.add_reaction.assert_not_awaited()


async def test_failed_reaction_does_not_cancel_the_thread():
    """Реакции и тред — независимые украшения.

    Между реакциями ждём по секунде: за это окно пост успевают удалить, а прав
    на реакции может не быть вовсе. Раньше сбой любой из трёх обрывал листенер
    ДО create_thread, и новость оставалась без обсуждения.
    """
    message = _post()
    message.add_reaction = AsyncMock(
        side_effect=[None, disnake.HTTPException(MagicMock(), "нет прав"), None]
    )

    await on_message(message)

    # Упавшая реакция не уносит ни ОСТАВШИЕСЯ, ни тред.
    assert message.add_reaction.await_count == 3
    message.create_thread.assert_awaited_once()


async def test_transport_error_does_not_swallow_the_rest_of_the_decoration():
    """🛑 Тот же класс, что и `HTTPException` внутри цикла, — через другую дверь.

    Исчерпав внутренние ретраи, HTTP-клиент disnake пробрасывает транспортный
    `OSError`/таймаут, а он не наследник `HTTPException`. Такой сбой обрывал и
    оставшиеся реакции, и создание треда — при том что новость к этому моменту
    УЖЕ опубликована: украшение не имеет права её испортить (нашёл Codex).
    """
    message = _post()
    message.add_reaction = AsyncMock(
        side_effect=[OSError("сеть легла"), TimeoutError(), None]
    )

    await on_message(message)

    assert message.add_reaction.await_count == 3
    message.create_thread.assert_awaited_once()


async def test_transport_error_on_thread_creation_is_not_fatal():
    message = _post()
    message.create_thread = AsyncMock(side_effect=OSError("сеть легла"))

    await on_message(message)  # не бросает

    assert message.add_reaction.await_count == 3
