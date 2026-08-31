"""Персистентная очередь отложенных новостей.

В таблице хранится уже подтверждённый админом снимок новости. Время — Unix
timestamp, то есть UTC; преобразование пользовательского времени МСК остаётся
на границе команды `/news`.
"""

import time
from dataclasses import dataclass
from pathlib import Path

import aiosqlite


DB_PATH = Path(__file__).resolve().parent.parent / "data" / "db.sqlite"
_CLAIM_LEASE_SECONDS = 10 * 60


@dataclass(frozen=True)
class ScheduledNews:
    id: int
    channel_id: int
    author_id: int
    title: str
    body: str
    ping_role_id: int | None
    publish_at: int


class ScheduledNewsStore:
    """Маленький SQLite-репозиторий с атомарной выдачей наступивших записей."""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path

    async def add(
        self,
        *,
        channel_id: int,
        author_id: int,
        title: str,
        body: str,
        ping_role_id: int | None,
        publish_at: int,
    ) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO scheduled_news (
                    channel_id, author_id, title, body, ping_role_id,
                    publish_at, claimed_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?)
                """,
                (
                    channel_id,
                    author_id,
                    title,
                    body,
                    ping_role_id,
                    publish_at,
                    int(time.time()),
                ),
            )
            await db.commit()
            return int(cursor.lastrowid)

    async def claim_due(
        self,
        now: int | None = None,
        *,
        lease_seconds: int = _CLAIM_LEASE_SECONDS,
    ) -> ScheduledNews | None:
        """Атомарно забрать одну наступившую запись.

        BEGIN IMMEDIATE не даёт двум процессам выбрать одну строку. Просроченная
        аренда возвращает запись в работу после падения процесса посреди отправки.
        """
        now = int(time.time()) if now is None else now
        stale_before = now - lease_seconds
        async with aiosqlite.connect(self.db_path, isolation_level=None) as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                cursor = await db.execute(
                    """
                    SELECT id, channel_id, author_id, title, body,
                           ping_role_id, publish_at
                    FROM scheduled_news
                    WHERE publish_at <= ?
                      AND (claimed_at IS NULL OR claimed_at <= ?)
                    ORDER BY publish_at, id
                    LIMIT 1
                    """,
                    (now, stale_before),
                )
                row = await cursor.fetchone()
                if row is None:
                    await db.commit()
                    return None
                await db.execute(
                    "UPDATE scheduled_news SET claimed_at = ? WHERE id = ?",
                    (now, row[0]),
                )
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
        return ScheduledNews(*row)

    async def complete(self, scheduled_id: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM scheduled_news WHERE id = ?", (scheduled_id,))
            await db.commit()
