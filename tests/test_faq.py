from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.commands.faq import FAQ


@pytest.mark.asyncio
async def test_donate_uses_canonical_url():
    cog = FAQ(SimpleNamespace())
    cog.send_faq = AsyncMock()

    await FAQ.donate.callback(cog, SimpleNamespace())

    container = cog.send_faq.await_args.kwargs["embed"]
    text = container.children[0].content
    assert "https://donate.catcraft.ru" in text
    assert "donate.catcraftmc.ru" not in text
