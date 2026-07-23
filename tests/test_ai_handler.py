from types import SimpleNamespace
from unittest.mock import Mock

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
