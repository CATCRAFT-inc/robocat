import aiosqlite
import pytest

from data.db_init import dbInit


@pytest.mark.asyncio
async def test_db_init_deletes_legacy_quota_locks_but_keeps_memory(tmp_path):
    db_path = tmp_path / "robocat.sqlite"
    await dbInit(db_path)
    async with aiosqlite.connect(db_path) as db:
        await db.executemany(
            """
            INSERT INTO flags(entity_type, entity_id, flag, value)
            VALUES (?, ?, ?, ?)
            """,
            [
                ("member", 1, "fact:123", "private"),
                ("member", 1, "fact:456", "temporary"),
                ("member", 1, "ai_locked", "legacy quota timer"),
                ("member", 1, "left", "keep me"),
            ],
        )
        await db.commit()

    await dbInit(db_path)
    await dbInit(db_path)

    async with aiosqlite.connect(db_path) as db:
        rows = await (await db.execute("SELECT flag FROM flags ORDER BY flag")).fetchall()
    # факты памяти переживают инициализацию, легаси-таймер квоты — нет
    assert rows == [("fact:123",), ("fact:456",), ("left",)]


async def test_db_init_adds_scheduled_news_queue_idempotently(tmp_path):
    db_path = tmp_path / "robocat.sqlite"

    await dbInit(db_path)
    await dbInit(db_path)

    async with aiosqlite.connect(db_path) as db:
        columns = await (await db.execute("PRAGMA table_info(scheduled_news)")).fetchall()
        indexes = await (await db.execute("PRAGMA index_list(scheduled_news)")).fetchall()
    assert {row[1] for row in columns} >= {
        "id", "channel_id", "author_id", "title", "body",
        "ping_role_id", "publish_at", "claimed_at", "created_at",
    }
    assert "idx_scheduled_news_due" in {row[1] for row in indexes}
