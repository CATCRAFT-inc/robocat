"""Поведение канала-приманки и анти-рейда упоминаниями."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from bot.discord_config import Roles
from bot.handlers import honeypot as honeypot_module
from bot.handlers.honeypot import Honeypot
from bot.utils import component_text


def _mention_message(message_id: int, author, text: str):
    message = MagicMock()
    message.id = message_id
    message.author = author
    message.guild = MagicMock()
    message.channel.id = 10_000_000_000_000_001
    message.channel.mention = "<#10000000000000001>"
    message.content = text
    message.attachments = []
    message.mention_everyone = False
    message.mentions = [SimpleNamespace(id=900 + message_id)]
    message.role_mentions = []
    message.delete = AsyncMock()
    return message


async def test_three_mention_messages_trigger_mute_delete_and_evidence_log(monkeypatch):
    metric = MagicMock()
    monkeypatch.setattr(honeypot_module.telemetry, "record_later", metric)
    author = MagicMock()
    author.id = 42
    author.bot = False
    author.mention = "<@42>"
    author.timeout = AsyncMock()

    log_channel = MagicMock()
    log_channel.send = AsyncMock()
    bot = MagicMock()
    bot.get_channel.return_value = log_channel
    cog = Honeypot(bot)

    messages = [
        _mention_message(1, author, "*" * 1990 + " <@901>"),
        _mention_message(2, author, "эй <@902>"),
        _mention_message(3, author, "эй <@903>"),
    ]

    for message in messages:
        await cog.mentionRaidTrap(message)

    for message in messages:
        message.delete.assert_awaited_once()
    author.timeout.assert_awaited_once()
    assert author.timeout.await_args.kwargs["duration"].days == 28

    log_channel.send.assert_awaited_once()
    sent = log_channel.send.await_args.kwargs
    text = component_text(sent["components"])
    assert len(text) < 4000
    evidence_file = sent["file"]
    evidence_file.fp.seek(0)
    evidence_text = evidence_file.fp.read().decode("utf-8")
    assert all(message.content in evidence_text for message in messages)
    assert "Полные тексты — в приложенном файле" in text
    assert f"<@&{Roles.moderator}>" in text
    assert f"<@&{Roles.admin}>" in text
    assert f"<@&{Roles.st_admin}>" in text
    assert {role.id for role in sent["allowed_mentions"].roles} == {
        Roles.moderator,
        Roles.admin,
        Roles.st_admin,
    }
    metric.assert_called_once_with("security", "mention_raid")


async def test_mention_raid_keeps_full_evidence_in_local_log_when_discord_log_fails(monkeypatch):
    author = MagicMock(id=42, bot=False, mention="<@42>")
    author.timeout = AsyncMock()
    log_channel = MagicMock()
    log_channel.send = AsyncMock(
        side_effect=honeypot_module.disnake.HTTPException(MagicMock(status=500), "boom")
    )
    bot = MagicMock()
    bot.get_channel.return_value = log_channel
    cog = Honeypot(bot)
    saved = MagicMock()
    monkeypatch.setattr(honeypot_module.logger, "error", saved)

    messages = [
        _mention_message(1, author, "первое доказательство"),
        _mention_message(2, author, "второе доказательство"),
        _mention_message(3, author, "третье доказательство"),
    ]
    for message in messages:
        await cog.mentionRaidTrap(message)

    rendered_log_calls = repr(saved.call_args_list)
    assert all(message.content in rendered_log_calls for message in messages)


async def test_mentions_outside_window_do_not_trigger(monkeypatch):
    author = MagicMock(id=42, bot=False)
    author.timeout = AsyncMock()
    bot = MagicMock()
    cog = Honeypot(bot)
    monkeypatch.setattr(
        honeypot_module,
        "monotonic",
        MagicMock(side_effect=[0, 21, 42]),
        raising=False,
    )

    for index in range(3):
        await cog.mentionRaidTrap(_mention_message(index, author, f"<@{index + 1}>"))

    author.timeout.assert_not_awaited()
    bot.get_channel.assert_not_called()
