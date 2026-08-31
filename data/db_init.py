import aiosqlite
import asyncio
from pathlib import Path

DB_PATH = Path(__file__).parent / "db.sqlite"


async def dbInit(db_path: Path = DB_PATH):
    async with aiosqlite.connect(db_path) as db:
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
        # Расход токенов по суткам: источник (mention/aichat/background) × модель.
        # Аддитивно — существующие данные не трогает, миграции не требует.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS token_usage (
                day               TEXT NOT NULL,
                source            TEXT NOT NULL,
                model             TEXT NOT NULL,
                requests          INTEGER NOT NULL DEFAULT 0,
                prompt_tokens     INTEGER NOT NULL DEFAULT 0,
                completion_tokens INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (day, source, model)
            )
        """)
        # Агрегированные продуктовые события без пользовательских текстов и ID.
        # Сутки считаются по Москве; kind/name образуют ограниченный словарь
        # событий, поэтому таблица остаётся компактной даже на длинной дистанции.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS event_metrics (
                day   TEXT NOT NULL,
                kind  TEXT NOT NULL,
                name  TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (day, kind, name)
            )
        """)
        # Легаси-зачистка таймеров квоты. Факты памяти (fact:%) отсюда убраны:
        # это данные юзеров, dbInit не должен их стирать (issue #6, возврат 25.07).
        await db.execute("DELETE FROM flags WHERE flag = 'ai_locked'")
        await db.commit()
    print(f"DB initialised at {db_path}")


if __name__ == '__main__':
    asyncio.run(dbInit())
