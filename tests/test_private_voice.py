"""«Создать приват»: создание при заходе в триггер, удаление опустевшего."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import disnake
import pytest

from bot.discord_config import Channels
import bot.handlers.private_voice as pv


@pytest.fixture
def cog(monkeypatch):
    fake_flags = SimpleNamespace(
        setFlag=AsyncMock(),
        getFlag=AsyncMock(return_value=None),
        removeFlag=AsyncMock(),
        getAllWithFlag=AsyncMock(return_value=None),
    )
    monkeypatch.setattr(pv, "flags", fake_flags)
    c = pv.PrivateVoice(MagicMock())
    return c, fake_flags


def _voice_channel(cid, members=()):
    ch = MagicMock(spec=disnake.VoiceChannel)
    ch.id = cid
    ch.members = list(members)
    ch.delete = AsyncMock()
    ch.set_permissions = AsyncMock()
    return ch


def _member():
    m = MagicMock(spec=disnake.Member)
    m.id = 42
    m.bot = False
    m.display_name = "серёжа"
    m.move_to = AsyncMock()
    return m


def _state(channel):
    return SimpleNamespace(channel=channel)


@pytest.mark.asyncio
async def test_join_trigger_creates_moves_and_flags(cog):
    c, flags = cog
    member = _member()
    trigger = _voice_channel(Channels.create_private_voice)
    trigger.category = MagicMock()
    created = _voice_channel(555)
    member.guild = MagicMock()
    member.guild.create_voice_channel = AsyncMock(return_value=created)

    await c.onVoiceUpdate(member, _state(None), _state(trigger))

    member.guild.create_voice_channel.assert_awaited_once()
    assert member.guild.create_voice_channel.call_args.kwargs["name"] == "серёжа"
    created.set_permissions.assert_awaited_once()
    member.move_to.assert_awaited_once()
    assert member.move_to.call_args.args[0] is created
    flags.setFlag.assert_awaited_once_with(created, pv.OWNER_FLAG, 42)


@pytest.mark.asyncio
async def test_leave_empty_owned_channel_is_deleted(cog):
    c, flags = cog
    member = _member()
    owned = _voice_channel(555, members=[])
    flags.getFlag = AsyncMock(return_value=object())  # наш приват

    await c.onVoiceUpdate(member, _state(owned), _state(None))

    owned.delete.assert_awaited_once()
    flags.removeFlag.assert_awaited_once()


@pytest.mark.asyncio
async def test_leave_unowned_channel_is_not_deleted(cog):
    c, flags = cog
    member = _member()
    other = _voice_channel(777, members=[])
    flags.getFlag = AsyncMock(return_value=None)  # не наш

    await c.onVoiceUpdate(member, _state(other), _state(None))

    other.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_nonempty_owned_channel_survives(cog):
    c, flags = cog
    member = _member()
    owned = _voice_channel(555, members=[MagicMock()])
    flags.getFlag = AsyncMock(return_value=object())

    await c.onVoiceUpdate(member, _state(owned), _state(None))

    owned.delete.assert_not_awaited()
    flags.getFlag.assert_not_awaited()  # непустой отсекаем до запроса флага


@pytest.mark.asyncio
async def test_move_failure_deletes_orphan_without_flag(cog):
    c, flags = cog
    member = _member()
    trigger = _voice_channel(Channels.create_private_voice)
    trigger.category = MagicMock()
    created = _voice_channel(555)
    member.move_to = AsyncMock(side_effect=disnake.HTTPException(MagicMock(), "boom"))
    member.guild = MagicMock()
    member.guild.create_voice_channel = AsyncMock(return_value=created)

    await c.onVoiceUpdate(member, _state(None), _state(trigger))

    created.delete.assert_awaited_once()
    flags.setFlag.assert_not_awaited()


@pytest.mark.asyncio
async def test_bot_join_ignored(cog):
    c, flags = cog
    member = _member()
    member.bot = True
    trigger = _voice_channel(Channels.create_private_voice)
    member.guild = MagicMock()
    member.guild.create_voice_channel = AsyncMock()

    await c.onVoiceUpdate(member, _state(None), _state(trigger))

    member.guild.create_voice_channel.assert_not_awaited()
