import aiosqlite
import pytest

from data.db_init import dbInit


@pytest.mark.asyncio
async def test_db_init_deletes_memory_and_legacy_quota_locks(tmp_path):
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
    assert rows == [("left",)]
