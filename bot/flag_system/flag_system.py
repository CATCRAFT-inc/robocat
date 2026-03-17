import time
import datetime
import aiosqlite
import pathlib 

import disnake

from bot.utils import parse_duration


class Flags():

    def __init__(self):
        p = pathlib.Path(__file__).parent.parent.parent
        self.dbpath = p / "data" / "db.sqlite"
        print(self.dbpath)
        print(self.dbpath.exists())

    def defineEntityType(self, entity: str) -> str | None:
        """Определяет строковый тип entity по объекту disnake или строке.

        :param entity: Объект disnake (Thread, TextChannel и т.д.) или строка-тип.
        :returns: Строка типа ("thread", "member" и т.д.) или None, если тип не распознан.
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
        elif isinstance(entity, str) and entity in ["thread","textchannel","voicechannel","member","category","forumchannel"]:
            return entity
        else:
            return None

    async def setFlag(self,
                    entity_type,
                    entity_id: int,
                    flag: str,
                    value = None,
                    expires_at: int = None):
        """Устанавливает флаг для entity. Если флаг уже существует — обновляет value и expires_at.

        :param entity_type: Объект disnake или строка-тип entity.
        :param entity_id: Discord ID entity.
        :param flag: Название флага.
        :param value: Значение флага (опционально).
        :param expires_at: Unix timestamp истечения флага (опционально).
        """
        entity_type = self.defineEntityType(entity_type)
        if entity_type is None:
            print('='*30, 'entity_type НЕ НАЙДЕН! ТЫ ПЕРЕДАЛ КАКОЕ-ТО ГОВНО!', '='*30, sep='\n\n')
            return None
        if expires_at and isinstance(expires_at, str): # Если expires_at передано как "28д" и прочее
                seconds = parse_duration(expires_at)
                if seconds is not False:
                    now = int(time.time())
                    expires_at = now + seconds
        async with aiosqlite.connect(self.dbpath) as db:
            cursor = await db.execute(
                """
                INSERT INTO flags VALUES
                (?,?,?,?,?) ON CONFLICT(entity_type, entity_id, flag)
                DO UPDATE SET value = ?, expires_at = ?
                """,
                (entity_type,entity_id,flag,value,expires_at,value,expires_at)
            )
            await db.commit()

    async def getFlag(self,
                    entity_type,
                    entity_id,
                    flag: str):
        """Возвращает значение и expires_at конкретного флага у entity.

        :param entity_type: Объект disnake или строка-тип entity.
        :param entity_id: Discord ID entity.
        :param flag: Название флага.
        :returns: Кортеж (value, expires_at) или None, если флаг не найден.
        """
        entity_type = self.defineEntityType(entity_type)
        if entity_type is None:
            print('='*30, 'entity_type НЕ НАЙДЕН! ТЫ ПЕРЕДАЛ КАКОЕ-ТО ГОВНО!', '='*30, sep='\n\n')
            return None
        async with aiosqlite.connect(self.dbpath) as db:
            cursor = await db.execute(
                """
                SELECT value, expires_at FROM flags WHERE entity_type = ? AND entity_id = ? AND flag = ?
                """, (entity_type, entity_id, flag)
            )
            results = await cursor.fetchone()
            if results:
                print(results)
                return results
            return None

    async def hasFlag(self,
                    entity_type,
                    entity_id,
                    flag: str):
        """Проверяет, существует ли флаг у entity.

        :param entity_type: Объект disnake или строка-тип entity.
        :param entity_id: Discord ID entity.
        :param flag: Название флага.
        :returns: True если флаг есть, False если нет, None при ошибке типа.
        """
        entity_type = self.defineEntityType(entity_type)
        if entity_type is None:
            print('='*30, 'entity_type НЕ НАЙДЕН! ТЫ ПЕРЕДАЛ КАКОЕ-ТО ГОВНО!', '='*30, sep='\n\n')
            return None
        async with aiosqlite.connect(self.dbpath) as db:
            cursor = await db.execute(
                """
                SELECT value, expires_at FROM flags WHERE entity_type = ? AND entity_id = ? AND flag = ?
                """, (entity_type, entity_id, flag)
            )
            results = await cursor.fetchone()
            if results:
                return True
            return False
        
    async def listAllFlags(self,
                    entity_type,
                    entity_id):
        """Возвращает все флаги, установленные у entity.

        :param entity_type: Объект disnake или строка-тип entity.
        :param entity_id: Discord ID entity.
        :returns: Список названий флагов или None при ошибке типа.
        """
        entity_type = self.defineEntityType(entity_type)
        if entity_type is None:
            print('='*30, 'entity_type НЕ НАЙДЕН! ТЫ ПЕРЕДАЛ КАКОЕ-ТО ГОВНО!', '='*30, sep='\n\n')
            return None
        async with aiosqlite.connect(self.dbpath) as db:
            cursor = await db.execute(
                """
                SELECT flag, value, expires_at FROM flags WHERE entity_type = ? AND entity_id = ?
                """, (entity_type, entity_id)
            )
            results = await cursor.fetchall()
            if results:
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
                SELECT entity_id, entity_type, expires_at FROM flags WHERE flag = ?
                """, (flag,)
            )
            results = await cursor.fetchall()
            if results:
                return results
            return None
        
    async def removeFlag(self,
                    entity_type,
                    entity_id: int,
                    flag: str,):
        """Удаляет флаг у entity.

        :param entity_type: Объект disnake или строка-тип entity.
        :param entity_id: Discord ID entity.
        :param flag: Название флага для удаления.
        """
        entity_type = self.defineEntityType(entity_type)
        if entity_type is None:
            print('='*30, 'entity_type НЕ НАЙДЕН! ТЫ ПЕРЕДАЛ КАКОЕ-ТО ГОВНО!', '='*30, sep='\n\n')
            return None
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