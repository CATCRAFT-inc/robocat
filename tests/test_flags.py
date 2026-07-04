"""Тесты bot.flag_system.flag_system.Flags.

ВАЖНО: никогда не трогаем data/db.sqlite (прод-данные) — каждый тест
получает свежий Flags() с dbpath, подменённым на файл во tmp_path.
"""

import asyncio
import time

import aiosqlite
import disnake
import pytest
from unittest.mock import Mock

from bot.flag_system.flag_system import Flags

# Схема таблицы flags — 1:1 с data/db_init.py, чтобы тест не разъезжался
# с реальной миграцией.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS flags (
    entity_type TEXT NOT NULL,
    entity_id   INTEGER NOT NULL,
    flag        TEXT NOT NULL,
    value       TEXT,
    expires_at  INTEGER,
    PRIMARY KEY (entity_type, entity_id, flag)
)
"""


@pytest.fixture
async def flags_db(tmp_path):
    db_path = tmp_path / "test_flags.sqlite"
    async with aiosqlite.connect(db_path) as db:
        await db.execute(_SCHEMA)
        await db.commit()
    inst = Flags()
    inst.dbpath = db_path
    return inst


def make_member(entity_id: int):
    """Лёгкий фейк disnake.Member: Mock(spec=...) подделывает __class__,
    поэтому isinstance() в Flags._defineEntityType срабатывает как надо."""
    member = Mock(spec=disnake.Member)
    member.id = entity_id
    return member


def make_user(entity_id: int):
    user = Mock(spec=disnake.User)
    user.id = entity_id
    return user


@pytest.mark.asyncio
async def test_set_and_get_flag(flags_db):
    member = make_member(111)
    ok = await flags_db.setFlag(member, "test_flag", "hello")
    assert ok is True

    row = await flags_db.getFlag(member, "test_flag")
    assert row is not None
    assert row.value == "hello"
    assert row.expires_at is None


@pytest.mark.asyncio
async def test_get_missing_flag_is_none(flags_db):
    member = make_member(222)
    assert await flags_db.getFlag(member, "nope") is None


@pytest.mark.asyncio
async def test_expired_flag_returns_none_and_is_removed(flags_db):
    member = make_member(333)
    await flags_db.setFlag(member, "exp_flag", "v")
    past = int(time.time()) - 10
    async with aiosqlite.connect(flags_db.dbpath) as db:
        await db.execute(
            "UPDATE flags SET expires_at=? WHERE entity_type='member' AND entity_id=? AND flag='exp_flag'",
            (past, member.id),
        )
        await db.commit()

    row = await flags_db.getFlag(member, "exp_flag")
    assert row is None

    # getFlag должен был удалить протухшую запись
    async with aiosqlite.connect(flags_db.dbpath) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM flags WHERE entity_type='member' AND entity_id=? AND flag='exp_flag'",
            (member.id,),
        )
        (count,) = await cursor.fetchone()
    assert count == 0


@pytest.mark.asyncio
async def test_increment_preserves_expires_at(flags_db):
    member = make_member(444)
    await flags_db.setFlag(member, "counter", 1, expires_at="8ч")
    row1 = await flags_db.getFlag(member, "counter")
    assert row1.value == "1"
    assert row1.expires_at is not None

    ok = await flags_db.setFlag(member, "counter", "+1")
    assert ok is True

    row2 = await flags_db.getFlag(member, "counter")
    assert row2.value == "2"
    assert row2.expires_at == row1.expires_at


@pytest.mark.asyncio
async def test_20_parallel_increments_give_20(flags_db):
    member = make_member(555)
    await asyncio.gather(*[
        flags_db.setFlag(member, "par_counter", "+1") for _ in range(20)
    ])
    row = await flags_db.getFlag(member, "par_counter")
    assert row.value == "20"


@pytest.mark.asyncio
async def test_non_numeric_increment_fails_and_keeps_value(flags_db):
    member = make_member(666)
    await flags_db.setFlag(member, "text_flag", "hello")

    ok = await flags_db.setFlag(member, "text_flag", "+1")
    assert ok is False

    row = await flags_db.getFlag(member, "text_flag")
    assert row.value == "hello"


@pytest.mark.asyncio
async def test_user_like_entity_is_recognised(flags_db):
    user = make_user(777)
    ok = await flags_db.setFlag(user, "user_flag", "yes")
    assert ok is True

    row = await flags_db.getFlag(user, "user_flag")
    assert row is not None
    assert row.value == "yes"


@pytest.mark.asyncio
async def test_set_flag_returns_true_on_success(flags_db):
    member = make_member(888)
    assert await flags_db.setFlag(member, "flag_x", "val") is True


@pytest.mark.asyncio
async def test_set_flag_returns_false_for_unrecognised_entity(flags_db):
    # entity, не подходящий ни под один тип из _defineEntityType
    ok = await flags_db.setFlag(object(), "whatever", "val")
    assert ok is False
