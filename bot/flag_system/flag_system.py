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
        if not self.dbpath.exists():
            self.logger.warning("Файл БД %s не найден — выполните data/db_init.py, "
                                "иначе флаги работать не будут", self.dbpath)

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

    def _resolveExpires(self, expires_at, flag: str) -> tuple[bool, int | None]:
        """'15мин' → unix-время; int/None — как есть. (False, None) при кривой строке."""
        if isinstance(expires_at, str):
            seconds = parse_duration(expires_at)
            if seconds is None:
                self.logger.warning("Invalid expires_at %r for flag %s", expires_at, flag)
                return False, None
            return True, int(time.time()) + seconds
        return True, expires_at

    async def setFlag(self, entity, flag: str, value=None, expires_at: int | str | None = None) -> bool:
        """Записать значение флага ЛИТЕРАЛЬНО. Для счётчиков — incrementFlag:
        раньше setFlag сам угадывал инкремент по виду значения («+цифры»), и
        легитимный «+79261234567» через /flag_user превращался в сложение."""
        entity_type, entity_id = self._resolveEntity(entity)
        if entity_type is None:
            return False
        ok, expires_at = self._resolveExpires(expires_at, flag)
        if not ok:
            return False
        try:
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
        except Exception:
            self.logger.exception("Не удалось записать флаг %s на (%s, %s)", flag, entity_type, entity_id)
            raise
        # %.60s: значения флагов бывают приватными (факты памяти) — в лог целиком не пишем
        self.logger.info("[FLAG SET] %s on (%s, %s) = %.60s", flag, entity_type, entity_id, value)
        return True

    async def incrementFlag(self, entity, flag: str, delta: int,
                            expires_at: int | str | None = None,
                            create_expires_at: int | str | None = None) -> int | None:
        """Атомарно прибавить delta к числовому счётчику (BEGIN IMMEDIATE).
        Возвращает НОВОЕ значение счётчика (None — не удалось): вызывающий может
        атомарно «списать и проверить лимит» без гонки check-then-act.

        Протухшая строка считается отсутствующей: счётчик стартует с delta и НЕ
        наследует мёртвый expires_at (иначе следующий getFlag снёс бы свежий
        счётчик как протухший). Живая строка сохраняет свой expires_at, если
        новый явно не передан; create_expires_at применяется ТОЛЬКО при старте
        счётчика заново (фиксированное окно, не скользящее)."""
        entity_type, entity_id = self._resolveEntity(entity)
        if entity_type is None:
            return None
        ok, expires_at = self._resolveExpires(expires_at, flag)
        if not ok:
            return None
        ok, create_expires_at = self._resolveExpires(create_expires_at, flag)
        if not ok:
            return None
        delta = int(delta)
        async with aiosqlite.connect(self.dbpath, isolation_level=None) as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                cursor = await db.execute(
                    "SELECT value, expires_at FROM flags WHERE entity_type=? AND entity_id=? AND flag=?",
                    (entity_type, entity_id, flag),
                )
                row = await cursor.fetchone()
                row_live = (row is not None and row[0] is not None
                            and (row[1] is None or row[1] >= int(time.time())))
                if row_live:
                    try:
                        base = int(row[0])
                    except (ValueError, TypeError):
                        self.logger.warning("Flag %s is not numeric, cannot increment by %s", flag, delta)
                        await db.execute("ROLLBACK")
                        return None
                    new_value = base + delta
                    # живая строка: без явного нового expires_at сохраняем старый
                    final_expires = expires_at if expires_at is not None else row[1]
                else:
                    new_value = delta
                    # мёртвая/отсутствующая строка = как INSERT: протухший
                    # expires_at не должен переживать перезапуск счётчика
                    final_expires = expires_at if expires_at is not None else create_expires_at
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
                        "value": str(new_value),
                        "expires_at": final_expires,
                    },
                )
                await db.execute("COMMIT")
            except BaseException:
                self.logger.exception("Ошибка транзакции инкремента флага %s на (%s, %s), откатываю",
                                      flag, entity_type, entity_id)
                await db.execute("ROLLBACK")
                raise
        self.logger.info("[FLAG SET] %s on (%s, %s) = %s", flag, entity_type, entity_id, new_value)
        return new_value

    async def getFlag(self, entity, flag: str) -> "FlagRow | None":
        entity_type, entity_id = self._resolveEntity(entity)
        if entity_type is None:
            return None
        try:
            async with aiosqlite.connect(self.dbpath) as db:
                cursor = await db.execute(
                    "SELECT value, expires_at FROM flags WHERE entity_type=? AND entity_id=? AND flag=?",
                    (entity_type, entity_id, flag),
                )
                row = await cursor.fetchone()
        except Exception:
            self.logger.exception("Не удалось прочитать флаг %s для (%s, %s)", flag, entity_type, entity_id)
            raise
        if row is None:
            return None
        flag_row = FlagRow.from_row(row, entity_type, entity_id, flag)
        if flag_row.is_expired:
            await self._removeExpiredRaw(entity_type, entity_id, flag)
            return None
        return flag_row

    async def hasFlag(self, entity, flag: str) -> bool:
        result = await self.getFlag(entity, flag)
        return result is not None

    async def getAllFlags(self, entity) -> list[tuple] | None:
        entity_type, entity_id = self._resolveEntity(entity)
        if entity_type is None:
            return None
        try:
            async with aiosqlite.connect(self.dbpath) as db:
                cursor = await db.execute(
                    "SELECT flag, value, expires_at FROM flags WHERE entity_type=? AND entity_id=?",
                    (entity_type, entity_id),
                )
                rows = await cursor.fetchall()
        except Exception:
            self.logger.exception("Не удалось получить флаги для (%s, %s)", entity_type, entity_id)
            raise
        if not rows:
            return None
        now = int(time.time())
        live = []
        for flag_name, value, exp in rows:
            if exp is not None and exp < now:
                await self._removeExpiredRaw(entity_type, entity_id, flag_name)
            else:
                live.append((flag_name, value, exp))
        return live or None

    async def getAllWithFlag(self, flag: str) -> list[tuple] | None:
        try:
            async with aiosqlite.connect(self.dbpath) as db:
                cursor = await db.execute(
                    "SELECT entity_type, entity_id, expires_at FROM flags WHERE flag=?",
                    (flag,),
                )
                rows = await cursor.fetchall()
        except Exception:
            self.logger.exception("Не удалось получить сущности с флагом %s", flag)
            raise
        if not rows:
            return None
        now = int(time.time())
        live = []
        for entity_type, entity_id, exp in rows:
            if exp is not None and exp < now:
                await self._removeExpiredRaw(entity_type, entity_id, flag)
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
        try:
            async with aiosqlite.connect(self.dbpath) as db:
                await db.execute(
                    "DELETE FROM flags WHERE entity_type=? AND entity_id=? AND flag=?",
                    (entity_type, entity_id, flag),
                )
                await db.commit()
        except Exception:
            self.logger.exception("Не удалось удалить флаг %s у (%s, %s)", flag, entity_type, entity_id)
            raise

    async def _removeExpiredRaw(self, entity_type: str, entity_id: int, flag: str):
        """Ленивое удаление протухшего флага. Условие `expires_at < now` в самом
        DELETE закрывает TOCTOU: между чтением протухшей строки и удалением другой
        таск мог поставить свежий флаг — его не сносим."""
        try:
            async with aiosqlite.connect(self.dbpath) as db:
                await db.execute(
                    "DELETE FROM flags WHERE entity_type=? AND entity_id=? AND flag=? "
                    "AND expires_at IS NOT NULL AND expires_at < ?",
                    (entity_type, entity_id, flag, int(time.time())),
                )
                await db.commit()
        except Exception:
            # НЕ пробрасываем: вызывающие уже трактуют строку как протухшую, сбой
            # ленивой уборки не должен превращать чтение флага в крэш ответа
            self.logger.exception("Не удалось удалить протухший флаг %s у (%s, %s)", flag, entity_type, entity_id)


flags = Flags()
