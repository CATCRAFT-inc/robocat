import asyncio
import random
import uuid

import aiosqlite
import disnake
from disnake.ext import commands


class CatGameDB:

    def __init__(self):
        return
    
    async def _initDB(self):
        """
        Создание дефолтной ДБ для игры.
        """
        async with aiosqlite.connect("catgame.db") as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS catgame 
                (discord_id INTEGER PRIMARY KEY,
                cat_uuid TEXT UNIQUE,
                tg_id INTEGER,
                color INTEGER NOT NULL,
                gender TEXT NOT NULL,
                name TEXT NOT NULL,
                balance INTEGER NOT NULL,
                attack INTEGER NOT NULL,
                max_attack INTEGER NOT NULL,
                defense INTEGER NOT NULL,
                max_defense INTEGER NOT NULL,
                endurance INTEGER NOT NULL,
                max_endurance INTEGER NOT NULL,
                hp INTEGER NOT NULL,
                max_hp INTEGER NOT NULL,
                status TEXT NOT NULL,
                cosmetics TEXT NOT NULL,
                items TEXT NOT NULL,
                custom_data TEXT)
                """) # FIXME FIXME FIXME FIXME
            await db.commit()
        
    async def newCat(self, discord_id: int):
        """Создание нового кота

        Args:
            discord_id (int): Угадай что сюда нужно передать!
        """
        async with aiosqlite.connect("catgame.db") as db:
            cursor = await db.execute("SELECT * FROM catgame WHERE discord_id = ?", (discord_id,))
            is_new = await cursor.fetchone()
            if is_new is not None: # тоесть если мы нашли результат
                return False, None, None, None, None, None, None, None
        cat_uuid = str(uuid.uuid4) # FIXME # FIXME # FIXME # FIXME # FIXME # FIXME
        name = "a"
        color = "b"
        gender = random.choice(["male", "female"])
        attack = random(0, 3) # FIXME # FIXME # FIXME # FIXME # FIXME # FIXME
        defense = random(0, 3)  # FIXME # FIXME # FIXME # FIXME # FIXME # FIXME
        endurance = random(0, 3)  # FIXME  # FIXME # FIXME # FIXME # FIXME # FIXME
        hp = random(0, 3)
        async with aiosqlite.connect("catgame.db") as db:
            await db.execute(
                "INSERT INTO catgame VALUES (?,?,?,?,?,?,?,?,?,?)", (discord_id, cat_uuid, 
                                                                        None, color,gender,name,attack,defense,endurance,
                                                                        hp, None,) # FIXME # FIXME # FIXME # FIXME # FIXME # FIXME # FIXME # FIXME
            )
            await db.commit()
        return True, color, gender, name, attack, defense, endurance, hp
