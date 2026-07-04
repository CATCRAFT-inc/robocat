from dataclasses import dataclass
import time
from typing import Any, Optional
import aiosqlite
import pathlib

import disnake

from bot.utils import parse_duration
import logging


@dataclass
class FlagRow:
    entity_type: str
    entity_id: int
    flag: str
    value: Optional[Any] = None
    expires_at: Optional[int] = None

    @classmethod
    def from_row(cls, row, entity_type: str, entity_id: int, flag: str):
        value, expires_at = row
        return cls(entity_type, entity_id, flag, value, expires_at)

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and self.expires_at < int(time.time())


class Flags:
    def __init__(self):
        p = pathlib.Path(__file__).parent.parent.parent
        self.dbpath = p / "data" / "db.sqlite"
        self.logger = logging.getLogger("robocat.flags")

    def _defineEntityType(self, entity) -> str | None:
        if isinstance(entity, disnake.Thread):
            return "thread"
        elif isinstance(entity, disnake.TextChannel):
            return "textchannel"
        elif isinstance(entity, disnake.VoiceChannel):
            return "voicechannel"
        elif isinstance(entity, (disnake.Member, disnake.User)):
            return "member"
        elif isinstance(entity, disnake.CategoryChannel):
            return "category"
        elif isinstance(entity, disnake.ForumChannel):
            return "forumchannel"
        elif entity == "abstract":
            return "abstract"
        return None

    def _resolveEntity(self, entity) -> tuple[str, int] | tuple[None, None]:
        entity_type = self._defineEntityType(entity)
        if entity_type is None:
            self.logger.warning("Entity type not recognised: %r", entity)
            return None, None
        entity_id = -1 if entity_type == "abstract" else entity.id
        return entity_type, entity_id

    async def setFlag(self, entity, flag: str, value=None, expires_at: int | str | None = None) -> bool:
        entity_type, entity_id = self._resolveEntity(entity)
        if entity_type is None:
            return False

        if isinstance(expires_at, str):
            seconds = parse_duration(expires_at)
            if seconds is None:
                self.logger.warning("Invalid expires_at %r for flag %s", expires_at, flag)
                return False
            expires_at = int(time.time()) + seconds

        is_increment = (isinstance(value, str) and len(value) > 1
                        and value[0] in ('+', '-') and value[1:].isdigit())

        # +N / -N: атомарный read-modify-write в одной транзакции, expires_at
        # сохраняется (COALESCE), если новый явно не передан.
        if is_increment:
            async with aiosqlite.connect(self.dbpath, isolation_level=None) as db:
                await db.execute("BEGIN IMMEDIATE")
                try:
                    cursor = await db.execute(
                        "SELECT value FROM flags WHERE entity_type=? AND entity_id=? AND flag=?",
                        (entity_type, entity_id, flag),
                    )
                    row = await cursor.fetchone()
                    if row is not None and row[0] is not None:
                        try:
                            base = int(row[0])
                        except (ValueError, TypeError):
                            self.logger.warning("Flag %s is not numeric, cannot apply %s", flag, value)
                            await db.execute("ROLLBACK")
                            return False
                        new_value = base + int(value)
                    else:
                        new_value = int(value)
                    await db.execute(
                        """
                        INSERT INTO flags (entity_type, entity_id, flag, value, expires_at)
                        VALUES (:entity_type, :entity_id, :flag, :value, :expires_at)
                        ON CONFLICT(entity_type, entity_id, flag) DO UPDATE SET
                            value = :value,
                            expires_at = COALESCE(:expires_at, flags.expires_at)
                        """,
                        {
                            "entity_type": entity_type,
                            "entity_id": entity_id,
                            "flag": flag,
                            "value": str(new_value),
                            "expires_at": expires_at,
                        },
                    )
                    await db.execute("COMMIT")
                except BaseException:
                    await db.execute("ROLLBACK")
                    raise
            self.logger.info("[FLAG SET] %s on (%s, %s) = %s", flag, entity_type, entity_id, new_value)
            return True

        async with aiosqlite.connect(self.dbpath) as db:
            await db.execute(
                """
                INSERT INTO flags (entity_type, entity_id, flag, value, expires_at)
                VALUES (:entity_type, :entity_id, :flag, :value, :expires_at)
                ON CONFLICT(entity_type, entity_id, flag) DO UPDATE SET
                    value = :value,
                    expires_at = :expires_at
                """,
                {
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "flag": flag,
                    "value": str(value) if value is not None else None,
                    "expires_at": expires_at,
                },
            )
            await db.commit()
        self.logger.info("[FLAG SET] %s on (%s, %s) = %s", flag, entity_type, entity_id, value)
        return True

    async def getFlag(self, entity, flag: str) -> "FlagRow | None":
        entity_type, entity_id = self._resolveEntity(entity)
        if entity_type is None:
            return None
        async with aiosqlite.connect(self.dbpath) as db:
            cursor = await db.execute(
                "SELECT value, expires_at FROM flags WHERE entity_type=? AND entity_id=? AND flag=?",
                (entity_type, entity_id, flag),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        flag_row = FlagRow.from_row(row, entity_type, entity_id, flag)
        if flag_row.is_expired:
            await self.removeFlag(entity, flag, "expired")
            return None
        return flag_row

    async def hasFlag(self, entity, flag: str) -> bool:
        result = await self.getFlag(entity, flag)
        return result is not None

    async def getAllFlags(self, entity) -> list[tuple] | None:
        entity_type, entity_id = self._resolveEntity(entity)
        if entity_type is None:
            return None
        async with aiosqlite.connect(self.dbpath) as db:
            cursor = await db.execute(
                "SELECT flag, value, expires_at FROM flags WHERE entity_type=? AND entity_id=?",
                (entity_type, entity_id),
            )
            rows = await cursor.fetchall()
        if not rows:
            return None
        now = int(time.time())
        live = []
        for flag_name, value, exp in rows:
            if exp is not None and exp < now:
                await self._removeFlagRaw(entity_type, entity_id, flag_name)
            else:
                live.append((flag_name, value, exp))
        return live or None

    async def getAllWithFlag(self, flag: str) -> list[tuple] | None:
        async with aiosqlite.connect(self.dbpath) as db:
            cursor = await db.execute(
                "SELECT entity_type, entity_id, expires_at FROM flags WHERE flag=?",
                (flag,),
            )
            rows = await cursor.fetchall()
        if not rows:
            return None
        now = int(time.time())
        live = []
        for entity_type, entity_id, exp in rows:
            if exp is not None and exp < now:
                await self._removeFlagRaw(entity_type, entity_id, flag)
            else:
                live.append((entity_type, entity_id, exp))
        return live or None

    async def removeFlag(self, entity, flag: str, reason: str = None):
        entity_type, entity_id = self._resolveEntity(entity)
        if entity_type is None:
            return None
        self.logger.info("[FLAG REMOVE] %s from (%s, %s), reason: %s",
                         flag, entity_type, entity_id, reason or "unspecified")
        await self._removeFlagRaw(entity_type, entity_id, flag)

    async def _removeFlagRaw(self, entity_type: str, entity_id: int, flag: str):
        async with aiosqlite.connect(self.dbpath) as db:
            await db.execute(
                "DELETE FROM flags WHERE entity_type=? AND entity_id=? AND flag=?",
                (entity_type, entity_id, flag),
            )
            await db.commit()


flags = Flags()
