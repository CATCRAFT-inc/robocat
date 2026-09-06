"""«На X лайков нажимаю кнопку»: порог растёт, когда набрали лайки."""

from unittest.mock import AsyncMock, MagicMock

import pytest

import bot.bot  # noqa: F401 — первым, против циклического импорта
from bot.handlers import like_button as lb


@pytest.mark.parametrize(
    "n, word",
    [(1, "лайк"), (2, "лайка"), (4, "лайка"), (5, "лайков"), (11, "лайков"),
     (14, "лайков"), (21, "лайк"), (22, "лайка"), (111, "лайков")],
)
def test_likes_word(n, word):
    assert lb.likes_word(n) == word


def _reaction(count: int, me: bool = True, emoji: str = lb.LIKE):
    r = MagicMock()
    r.emoji = emoji
    r.count = count
    r.me = me
    return r


def _cog(monkeypatch, *, threshold=2, reactions):
    fake_flags = MagicMock()
    fake_flags.getFlag = AsyncMock(return_value=MagicMock(value=f"10:20:{threshold}"))
    fake_flags.setFlag = AsyncMock()
    monkeypatch.setattr(lb, "flags", fake_flags)

    message = MagicMock()
    message.reactions = reactions
    message.edit = AsyncMock()
    channel = MagicMock()
    channel.fetch_message = AsyncMock(return_value=message)
    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=channel)
    return lb.LikeButton(bot), message, fake_flags


def _payload(message_id=20, emoji=lb.LIKE):
    p = MagicMock()
    p.message_id = message_id
    p.emoji = emoji
    return p


def _title(message):
    container = message.edit.await_args.kwargs["components"][0]
    return container.children[0].content


async def test_threshold_reached_bumps_title_and_flag(monkeypatch):
    # Бот сам поставил 👍 — его не считаем: 3 в реакции = 2 от людей.
    cog, message, fake_flags = _cog(monkeypatch, reactions=[_reaction(3)])

    await cog.on_like(_payload())

    assert _title(message) == "## На 3 лайка нажимаю кнопку"
    fake_flags.setFlag.assert_awaited_once_with("abstract", lb.FLAG, "10:20:3")


async def test_below_threshold_does_nothing(monkeypatch):
    cog, message, fake_flags = _cog(monkeypatch, reactions=[_reaction(2)])

    await cog.on_like(_payload())

    message.edit.assert_not_awaited()
    fake_flags.setFlag.assert_not_awaited()


async def test_jump_past_threshold_goes_to_count_plus_one(monkeypatch):
    # Пока бот лежал, набрали 5 — следующая цель 6, а не 3.
    cog, message, _ = _cog(monkeypatch, reactions=[_reaction(6)])

    await cog.on_like(_payload())

    assert _title(message) == "## На 6 лайков нажимаю кнопку"


async def test_other_message_or_emoji_is_ignored(monkeypatch):
    cog, message, _ = _cog(monkeypatch, reactions=[_reaction(9)])

    await cog.on_like(_payload(message_id=999))
    await cog.on_like(_payload(emoji="❤️"))

    cog.bot.get_channel.assert_not_called()
    message.edit.assert_not_awaited()


async def test_state_is_cached_after_first_read(monkeypatch):
    cog, _, fake_flags = _cog(monkeypatch, reactions=[_reaction(1)])

    await cog.on_like(_payload())
    await cog.on_like(_payload())

    fake_flags.getFlag.assert_awaited_once()
