from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import disnake
import pytest

from bot.ai.handler import AIMessageHandler


def make_member():
    member = Mock(spec=disnake.Member)
    member.id = 100
    member.roles = []
    return member


class _QuotaFlags:
    def __init__(self):
        self.count = 0
        self.values = {}

    async def getFlag(self, _user, name):
        return self.values.get(name)

    async def incrementFlag(self, _user, name, _delta, *, create_expires_at=None):
        assert name == "airequests"
        assert create_expires_at == "8ч"
        self.count += 1
        return self.count

    async def setFlag(self, _user, name, value, expires_at=None):
        self.values[name] = SimpleNamespace(value=value, expires_at=expires_at)
        return True


@pytest.mark.asyncio
async def test_quota_accepts_35_and_rejects_36_without_second_timer(monkeypatch):
    test_flags = _QuotaFlags()
    monkeypatch.setattr("bot.ai.handler.flags", test_flags)
    handler = object.__new__(AIMessageHandler)
    handler.user_request_limit = 35
    member = make_member()

    results = [await handler._consumeRequest(member) for _ in range(36)]

    assert results == [True] * 35 + [False]
    assert "ai_locked" not in test_flags.values
    assert test_flags.count == 36


class _LockFlags:
    def __init__(self):
        self.values = {}

    @staticmethod
    def _key(entity, name):
        entity_id = -1 if entity == "abstract" else entity.id
        return entity_id, name

    async def hasFlag(self, entity, name):
        return self._key(entity, name) in self.values

    async def setFlag(self, entity, name, value=None, expires_at=None):
        self.values[self._key(entity, name)] = value
        return True

    async def removeFlag(self, entity, name, reason=None):
        self.values.pop(self._key(entity, name), None)


@pytest.mark.asyncio
@pytest.mark.parametrize("method_name", ["_handleMention", "_handleThreadMessage"])
async def test_chat_locks_silently_stop_both_conversation_paths(method_name):
    handler = object.__new__(AIMessageHandler)
    handler._chat_blocked = AsyncMock(return_value=True)
    handler._consumeRequest = AsyncMock()
    message = SimpleNamespace(author=make_member(), reply=AsyncMock())

    await getattr(handler, method_name)(message)

    message.reply.assert_not_awaited()
    handler._consumeRequest.assert_not_awaited()


@pytest.mark.asyncio
async def test_global_chat_lock_is_persistent_toggle(monkeypatch):
    test_flags = _LockFlags()
    monkeypatch.setattr("bot.ai.handler.flags", test_flags)
    first = object.__new__(AIMessageHandler)
    second = object.__new__(AIMessageHandler)
    inter = SimpleNamespace(send=AsyncMock())

    await AIMessageHandler.aiLock.callback(first, inter)
    assert await test_flags.hasFlag("abstract", "ai_chat_global_lock")

    await AIMessageHandler.aiLock.callback(second, inter)
    assert not await test_flags.hasFlag("abstract", "ai_chat_global_lock")


@pytest.mark.asyncio
async def test_user_lock_slash_and_reply_commands_target_selected_user(monkeypatch):
    test_flags = _LockFlags()
    monkeypatch.setattr("bot.ai.handler.flags", test_flags)
    handler = object.__new__(AIMessageHandler)
    slash_target = make_member()
    reply_target = make_member()
    reply_target.id = 200
    inter = SimpleNamespace(send=AsyncMock())
    ctx = SimpleNamespace(
        send=AsyncMock(),
        message=SimpleNamespace(
            reference=SimpleNamespace(resolved=SimpleNamespace(author=reply_target))
        ),
    )

    await AIMessageHandler.aiUserLock.callback(handler, inter, slash_target)
    await AIMessageHandler.aiUserLockReply.callback(handler, ctx)

    assert await test_flags.hasFlag(slash_target, "ai_chat_user_lock")
    assert await test_flags.hasFlag(reply_target, "ai_chat_user_lock")


@pytest.mark.asyncio
async def test_mention_uses_current_plus_seven_reply_ancestors():
    handler = object.__new__(AIMessageHandler)
    handler._chat_blocked = AsyncMock(return_value=False)
    handler._consumeRequest = AsyncMock(return_value=True)
    handler._streamAnswer = AsyncMock()
    handler.ai_engine = SimpleNamespace(buildConverstaion=AsyncMock(return_value=[]))

    chain = []
    for message_id in range(10):
        reference = (
            SimpleNamespace(resolved=chain[-1], message_id=chain[-1].id)
            if chain
            else None
        )
        chain.append(
            SimpleNamespace(
                id=message_id,
                author=make_member(),
                reference=reference,
                channel=SimpleNamespace(),
            )
        )

    await handler._handleMention(chain[-1])

    messages = handler.ai_engine.buildConverstaion.await_args.args[0]
    assert [message.id for message in messages] == list(range(2, 10))
