from dataclasses import dataclass
import time
import datetime
from typing import Any, Optional
import aiosqlite
import pathlib 

import disnake

from bot.utils import parse_duration
import logging

# TODO: написать норальные докстринги

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
# о боже тут докстринги, эт о писал чатджибити
    def __init__(self):
        p = pathlib.Path(__file__).parent.parent.parent
        self.dbpath = p / "data" / "db.sqlite"
        self.logger = logging.getLogger("robocat.flags")

    def _defineEntityType(self, entity: str) -> str | None:
        """Определяет строковый тип entity по объекту disnake или строке.

        :param entity: Объект disnake (Thread, TextChannel и т.д.) или строка.
        :returns: Строка типа ("thread", "member" и т.д.) или None, если тип не распознан.
        
        Ещо в этой системе есть абстрактный тип данных, прост флаг, который ни над чем не закреплен. вот.
        """
        if isinstance(entity, disnake.Thread):
            return "thread"
        elif isinstance(entity, disnake.TextChannel):
            return "textchannel"
        elif isinstance(entity, disnake.VoiceChannel):
            return "voicechannel"
        elif isinstance(entity, disnake.Member):
            return "member"
        elif isinstance(entity, disnake.CategoryChannel):
            return "category"
        elif isinstance(entity, disnake.ForumChannel):
            return "forumchannel"
        elif entity == "abstract":
            return "abstract"
        else:
            return None
        
    def _resolveEntity(self, entity) -> tuple[str, int] | None:
        """Резолвит Discord энтити, возвращая строковый тип энтити и его айди.
        Всё в дискорде же имеет айди ёбана рот! Кроме абстракта, который я создал - там айди -1
        Но это тоже айди... Ну не суть.

        Args:
            entity (disnake.Class | "abstract"): Discord entity | "abstract"

        Returns:
            tuple[str, int] | None: Возвращает либо entity_type + entity_id либо шиш с маслом! 
        """
        entity_type = self._defineEntityType(entity)
        if entity_type is None:
            print(f"Entity Type ''{entity}'' не найден.")
            return None, None
        elif entity_type == "abstract":
            entity_id = -1
        else:
            entity_id = entity.id
        return entity_type, entity_id
    
    def _isExpired(self, exp_time: int) -> bool:
        now = time.time()
        if now > float(exp_time): # если щяс больше секунд чем когда надо эээ флаг чтоб ну это .... чтоб он истёк
            return True
        return False

    async def setFlag(self,
                    entity,
                    flag: str,
                    value = None,
                    expires_at: int = None):
        """Устанавливает флаг для entity. Если флаг уже существует — обновляет value и expires_at.

        :param entity_type: Объект disnake или строка-тип entity.
        :param entity_id: Discord ID entity.
        :param flag: Название флага.
        :param value: Значение флага (опционально).
        :param expires_at: Unix timestamp | Строка формата "1д", "1ч" и прочее
        """
        entity_type, entity_id = self._resolveEntity(entity)
        if entity_type is None:
            return None
        if expires_at and isinstance(expires_at, str): # Если expires_at передано как "28д" и прочее
            seconds = parse_duration(expires_at)
            now = int(time.time())
            expires_at = now + seconds
        
        # Операция сложения/вычитания флага с цифрами
        if isinstance(value, str) and value.startswith(('+', '-')):
            if value[1:].isdigit():
                current_flag = await self.getFlag(entity, flag)
                if current_flag:
                    try:
                        current_value = int(current_flag.value)
                        match value[0]:
                            case "+":
                                value = current_value + int(value[1:])
                            case "-":
                                value = current_value - int(value[1:])
                    except (ValueError, TypeError):
                        self.logger.warning("Флаг %s не является числом, +/- операция (%s) невозможна", flag, value)
                        return None
                else:
                    match value[0]:
                        case "+":
                            value = int(value[1:])
                        case "-":
                            value = -int(value[1:])

        if value is not None and expires_at:    
            async with aiosqlite.connect(self.dbpath) as db:
                await db.execute(
                    """
                    INSERT INTO flags (entity_type, entity_id, flag, value, expires_at) VALUES
                    (:entity_type, :entity_id, :flag, :value, :expires_at) ON CONFLICT(entity_type, entity_id, flag)
                    DO UPDATE SET value = :value, expires_at = :expires_at
                    """,
                    {
                        "entity_type": entity_type,
                        "entity_id": entity_id,
                        "flag": flag,
                        "value": value,
                        "expires_at": expires_at,
                    }
                )
                await db.commit()
            self.logger.info("[ОБНОВЛЕНИЕ] Флаг %s на энтити (%s, ID: %s) создан, expires_at: %s", flag, entity_type, entity_id, expires_at or "Нет")
        elif value is not None:
            async with aiosqlite.connect(self.dbpath) as db:
                await db.execute(
                    """
                    INSERT INTO flags (entity_type, entity_id, flag, value) VALUES
                    (:entity_type, :entity_id, :flag, :value) ON CONFLICT(entity_type, entity_id, flag)
                    DO UPDATE SET value = :value
                    """,
                    {
                        "entity_type": entity_type,
                        "entity_id": entity_id,
                        "flag": flag,
                        "value": value,
                    }
                )
                await db.commit()
            self.logger.info("[ОБНОВЛЕНИЕ] На флаге %s на энтити (%s, ID: %s) обновлён value: %s", flag, entity, entity_id, value)
        elif expires_at:
            async with aiosqlite.connect(self.dbpath) as db:
                await db.execute(
                    """
                    INSERT INTO flags (entity_type, entity_id, flag, expires_at) VALUES
                    (:entity_type, :entity_id, :flag, :expires_at) ON CONFLICT(entity_type, entity_id, flag)
                    DO UPDATE SET expires_at = :expires_at
                    """,
                    {
                        "entity_type": entity_type,
                        "entity_id": entity_id,
                        "flag": flag,
                        "expires_at": expires_at
                    }
                )
                await db.commit()
            self.logger.info("[ОБНОВЛЕНИЕ] На флаге %s на энтити (%s, ID: %s) обновлён expires_at: %s", flag, entity, entity_id, expires_at)
        else:
            async with aiosqlite.connect(self.dbpath) as db:
                await db.execute(
                    """
                    INSERT INTO flags (entity_type, entity_id, flag, expires_at) VALUES
                    (:entity_type, :entity_id, :flag)
                    """,
                    {
                        "entity_type": entity_type,
                        "entity_id": entity_id,
                        "flag": flag,
                    }
                )
                await db.commit()
            self.logger.info("[ОБНОВЛЕНИЕ] На флаге %s на энтити (%s, ID: %s) создан флаг без значений", flag, entity, entity_id)

        

    async def getFlag(self,
                    entity,
                    flag: str) -> FlagRow:
        """Возвращает значение и expires_at конкретного флага у entity.

        :param entity_type: Объект disnake | abstract
        .0
        :param entity_id: Discord ID entity.
        :param flag: Название флага.
        :returns: Кортеж (value, expires_at) или None, если флаг не найден.
        """
        entity_type, entity_id = self._resolveEntity(entity)
        if entity_type is None:
            return None
        async with aiosqlite.connect(self.dbpath) as db:
            cursor = await db.execute(
                """
                SELECT value, expires_at FROM flags WHERE entity_type = ? AND entity_id = ? AND flag = ?
                """, (entity_type, entity_id, flag)
            )
            result = await cursor.fetchone()
            row = FlagRow.from_row(result, entity_type, entity_id, flag) if result else None
            if row:
                if row.is_expired:
                    await self.removeFlag(entity,flag,"истёк")
                    return None
                else:
                    return row
            return None
        
    # async def getFlagRaw(self,
    #                 entity_type,
    #                 entity_id: int,
    #                 flag: str):
    #     if not isinstance(entity_type, str):
    #         entity_type = self._defineEntityType(entity_type)
    #     if entity_type is None:
    #         return None
    #     if entity_type != "abstract":
    #         entity_id = entity_type.id
    #     else:
    #         entity_id = -1
    #     async with aiosqlite.connect(self.dbpath) as db:
    #         cursor = await db.execute(
    #             """
    #             SELECT value, expires_at FROM flags WHERE entity_type = ? AND entity_id = ? AND flag = ?
    #             """, (entity_type, entity_id, flag)
    #         )
    #         results = await cursor.fetchone()
    #         if results:
    #             if results[1] and self._isExpired(results[1]):
    #                 await self.removeFlag(entity,flag)
    #                 return None
    #             else:
    #                 return results
    #         return None

    async def hasFlag(self,
                    entity,
                    flag: str):
        """Проверяет, существует ли флаг у entity.

        :param entity_type: Объект disnake или строка-тип entity.
        :param entity_id: Discord ID entity.
        :param flag: Название флага.
        :returns: True если флаг есть, False если нет, None при ошибке типа.
        """
        entity_type, entity_id = self._resolveEntity(entity)
        if entity_type is None:
            return None
        async with aiosqlite.connect(self.dbpath) as db:
            cursor = await db.execute(
                """
                SELECT expires_at FROM flags WHERE entity_type = ? AND entity_id = ? AND flag = ?
                """, (entity_type, entity_id, flag)
            )
            results = await cursor.fetchone()
            if results:
                if self._isExpired(results[0]):
                    await self.removeFlag(entity, flag, "истёк")
                    return False
                return True
            return False
        
    # async def hasFlagRaw(self,
    #                 entity_type,
    #                 entity_id: int,
    #                 flag: str):
    #     """Проверяет, существует ли флаг у entity.

    #     :param entity_type: Объект disnake или строка-тип entity.
    #     :param entity_id: Discord ID entity.
    #     :param flag: Название флага.
    #     :returns: True если флаг есть, False если нет, None при ошибке типа.
    #     """
    #     entity_type = self._defineEntityType(entity_type)
    #     if entity_type is None:
    #         return None
    #     if entity_type != "abstract":
    #         entity_id = entity_type.id
    #     else:
    #         entity_id = -1
    #     async with aiosqlite.connect(self.dbpath) as db:
    #         cursor = await db.execute(
    #             """
    #             SELECT expires_at FROM flags WHERE entity_type = ? AND entity_id = ? AND flag = ?
    #             """, (entity_type, entity_id, flag)
    #         )
    #         results = await cursor.fetchone()
    #         if results:
    #             if self._isExpired(results[0]):
    #                 return False
    #             return True
    #         return False
        
    async def getAllFlags(self,
                        entity):
        """Возвращает все флаги, установленные у entity.

        :param entity_type: Объект disnake или строка-тип entity.
        :param entity_id: Discord ID entity.
        :returns: Список названий флагов или None при ошибке типа.
        """
        entity_type, entity_id = self._resolveEntity(entity)
        if entity_type is None:
            return None
        async with aiosqlite.connect(self.dbpath) as db:
            cursor = await db.execute(
                """
                SELECT flag, value, expires_at FROM flags WHERE entity_type = ? AND entity_id = ?
                """, (entity_type, entity_id)
            )
            results = await cursor.fetchall()
            if results:
                # not_expired_results = []
                # for res in results:
                #     if res[1] and self._isExpired(res[1]):
                #         await self.removeFlag(entity, res[0], "истёк")
                #     else:
                #         not_expired_results.append(res)
                # return not_expired_results
                return results
            return None
        
    async def getAllWithFlag(self,
                    flag: str):
        """Возвращает все entity заданного типа, у которых установлен указанный флаг.

        :param flag: Название флага.
        :returns: Список кортежей (entity_id, entity_type, expires_at) или None если никого нет / ошибка типа.
        """
        async with aiosqlite.connect(self.dbpath) as db:
            cursor = await db.execute(
                """
                SELECT entity_type, entity_id, expires_at FROM flags WHERE flag = ?
                """, (flag,)
            )
            results = await cursor.fetchall()
            if results:
                not_expired_results = []
                for res in results:
                    if res[2] and self._isExpired(res[2]):
                        await self._removeFlagRaw(res[0],res[1], flag)
                    else:
                        not_expired_results.append(res)
                return not_expired_results
            return None
        
    async def removeFlag(self,
                    entity,
                    flag: str,
                    reason: str = None):
        """Удаляет флаг у entity.

        :param entity_type: Объект disnake или строка-тип entity.
        :param entity_id: Discord ID entity.
        :param flag: Название флага для удаления.
        """
        entity_type, entity_id = self._resolveEntity(entity)
        if entity_type is None:
            return None
        self.logger.info("Удаляю флаг %s у энтити (%s, ID: %s), причина: %s", flag, entity_type, entity_id, reason or "Не указано")
        await self._removeFlagRaw(entity_type, entity_id, flag)
    
    async def _removeFlagRaw(self, entity_type, entity_id: int, flag: str):
        async with aiosqlite.connect(self.dbpath) as db:
            cursor = await db.execute(
                """
                DELETE FROM flags WHERE entity_type = ? AND entity_id = ? AND flag = ?
                """,
                (entity_type,entity_id,flag)
            )
            await db.commit()
        


    # async def test(self):
    #     await self.setFlag("textchannel", 12345678, "GOVNO", "TEST TEST TEST TEST")
    #     print(await self.hasFlag("textchannel", 12345678, "GOVNO"))
        #await self.setFlag("textchannel", 12345678, "GOVNO", "123123123123123123123123123")
        #await self.removeFlag("textchannel", 12345678, "GOVNO")
        # await self.setFlag("textchannel", 12345678, "GOVNO228", "AAAAAAAAAAAAAAAAAAAAAAA")
        # print(await self.getFlag("textchannel", 1234567, "GOVNO"))
        # print(await self.getAllWithFlag("GOVNO"))

flags = Flags()