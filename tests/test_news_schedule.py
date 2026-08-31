"""SQLite-очередь отложенных новостей: перезапуск, lease и гонки процессов."""

import asyncio

from bot.news_schedule import ScheduledNewsStore
from data.db_init import dbInit


async def _store(tmp_path):
    db_path = tmp_path / "robocat.sqlite"
    await dbInit(db_path)
    return ScheduledNewsStore(db_path)


async def test_scheduled_news_survives_store_recreation(tmp_path):
    store = await _store(tmp_path)
    scheduled_id = await store.add(
        channel_id=11,
        author_id=22,
        title="Заголовок",
        body="Текст",
        ping_role_id=33,
        publish_at=1_000,
    )

    restored = await ScheduledNewsStore(store.db_path).claim_due(now=1_000)

    assert restored is not None
    assert restored.id == scheduled_id
    assert restored.title == "Заголовок"
    assert restored.ping_role_id == 33


async def test_scheduled_news_is_not_claimed_before_publish_time(tmp_path):
    store = await _store(tmp_path)
    await store.add(
        channel_id=11,
        author_id=22,
        title="Заголовок",
        body="Текст",
        ping_role_id=None,
        publish_at=1_001,
    )

    assert await store.claim_due(now=1_000) is None


async def test_claim_due_is_atomic_between_workers(tmp_path):
    store = await _store(tmp_path)
    await store.add(
        channel_id=11,
        author_id=22,
        title="Заголовок",
        body="Текст",
        ping_role_id=None,
        publish_at=1_000,
    )
    other_process = ScheduledNewsStore(store.db_path)

    claimed = await asyncio.gather(
        store.claim_due(now=1_000),
        other_process.claim_due(now=1_000),
    )

    assert sum(row is not None for row in claimed) == 1


async def test_expired_claim_is_retried_and_completed(tmp_path):
    store = await _store(tmp_path)
    scheduled_id = await store.add(
        channel_id=11,
        author_id=22,
        title="Заголовок",
        body="Текст",
        ping_role_id=None,
        publish_at=1_000,
    )
    first = await store.claim_due(now=1_000, lease_seconds=600)
    assert first is not None
    assert await store.claim_due(now=1_599, lease_seconds=600) is None

    retried = await store.claim_due(now=1_600, lease_seconds=600)
    assert retried is not None
    assert retried.id == scheduled_id
    await store.complete(scheduled_id)

    assert await store.claim_due(now=2_200, lease_seconds=600) is None
