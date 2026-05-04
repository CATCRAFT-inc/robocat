import aiosqlite
import asyncio

async def dbInit():
    async with aiosqlite.connect("db.sqlite") as db:
        # Создать таблицу
        await db.execute("""
            CREATE TABLE IF NOT EXISTS left_players (
                id INTEGER PRIMARY KEY,
                left_time INTEGER,
                roles TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_channel_id (
                user_id INTEGER PRIMARY KEY,
                channel_id INTEGER
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS stats (
                user_id INTEGER PRIMARY KEY,
                bugs_submitted INTEGER,
                bugs_fixed INTEGER,
                ideas_submitted INTEGER,
                ideas_added INTEGER
            )
        """)
        await db.execute("CREATE TABLE IF NOT EXISTS flags "
        "(entity_type TEXT NOT NULL, " \
        "entity_id INTEGER NOT NULL, " \
        "flag TEXT NOT NULL, " \
        "value TEXT, " \
        "expires_at INTEGER, " \
        "PRIMARY KEY (entity_type, entity_id, flag))")
        await db.commit()

async def dbCommit():
    async with aiosqlite.connect("db.sqlite") as db:
        await db.execute("CREATE TABLE IF NOT EXISTS flags "
        "(entity_type TEXT NOT NULL, " \
        "entity_id INTEGER NOT NULL, " \
        "flag TEXT NOT NULL, value TEXT, " \
        "expires_at INTEGER, " \
        "PRIMARY KEY (entity_type, entity_id, flag))")
        await db.commit()

async def test():
    async with aiosqlite.connect("db.sqlite") as db:
        tables = await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = await tables.fetchall()
        print(tables)

if __name__ == '__main__':
    #asyncio.run(test())
    asyncio.run(dbInit())
    #asyncio.run(dbCommit())
    