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


async def test_faq_reply_does_not_ping_the_quoted_author():
    """FAQ открыт гостевым серверам, где у нас нет модерации.

    reply по умолчанию пингует автора цитируемого сообщения — иначе любой
    делает бота пингалкой, отвечая `!да` на чужое старое сообщение.
    """
    from unittest.mock import AsyncMock, MagicMock

    from bot.commands.faq import FAQ

    from bot.discord_config import Guilds

    def _ctx(guild_id):
        ctx = MagicMock()
        ctx.guild = None if guild_id is None else MagicMock(id=guild_id)
        ctx.message.reference.message_id = 1
        return ctx

    cog = FAQ(MagicMock())
    ref_message = MagicMock()
    ref_message.reply = AsyncMock()

    guest = _ctx(99999999999999999)
    guest.channel.fetch_message = AsyncMock(return_value=ref_message)
    await cog.send_faq(guest, MagicMock())
    assert ref_message.reply.await_args.kwargs["mention_author"] is False

    # 🛑 А дома пинг обязан остаться: `!читайвики` в ответ новичку — адресное
    # сообщение, и без уведомления он его не увидит. Глухой запрет был
    # регрессией, которую внёс гостевой фикс (нашло ревью).
    home = _ctx(Guilds.main)
    home.channel.fetch_message = AsyncMock(return_value=ref_message)
    await cog.send_faq(home, MagicMock())
    assert ref_message.reply.await_args.kwargs["mention_author"] is True


async def test_reports_points_to_admin_ticket_without_old_ksb_wording():
    cog = FAQ(SimpleNamespace())
    cog.send_faq = AsyncMock()

    await FAQ.report_faq.callback(cog, SimpleNamespace())

    container = cog.send_faq.await_args.kwargs["embed"]
    text = "\n".join(
        child.content for child in container.children if hasattr(child, "content")
    )
    assert "админ-тикет" in text.lower()
    assert "ксб" not in text.lower()
    assert "опк" not in text.lower()
