import aiosqlite
import asyncio
from pathlib import Path

DB_PATH = Path(__file__).parent / "db.sqlite"


async def dbInit():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS flags (
                entity_type TEXT NOT NULL,
                entity_id   INTEGER NOT NULL,
                flag        TEXT NOT NULL,
                value       TEXT,
                expires_at  INTEGER,
                PRIMARY KEY (entity_type, entity_id, flag)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS stats (
                user_id         INTEGER PRIMARY KEY,
                bugs_submitted  INTEGER DEFAULT 0,
                bugs_fixed      INTEGER DEFAULT 0,
                ideas_submitted INTEGER DEFAULT 0,
                ideas_added     INTEGER DEFAULT 0
            )
        """)
        await db.commit()
    print(f"DB initialised at {DB_PATH}")


if __name__ == '__main__':
    asyncio.run(dbInit())
