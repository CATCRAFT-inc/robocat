"""Тесты учёта расхода токенов (таблица token_usage + дашборд /aistats).

Как в test_memory.py: прод data/db.sqlite не трогаем — свежая БД на tmp_path,
источник БД в bot.ai.usage подменяется monkeypatch'ем.
"""

import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from bot.ai import usage
from bot.ai.handler import _fmt_tokens, _tokens
from data.db_init import dbInit


@pytest.fixture
async def db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_usage.sqlite"
    await dbInit(db_path)
    monkeypatch.setattr(usage, "flags", SimpleNamespace(dbpath=db_path))
    # каждый тест стартует с чистого дефолта, а не с источника соседнего теста
    monkeypatch.setattr(usage, "source", usage.contextvars.ContextVar(
        "llm_source_test", default=usage.BACKGROUND))
    return db_path


async def _rows(db_path):
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            "SELECT day, source, model, requests, prompt_tokens, completion_tokens "
            "FROM token_usage ORDER BY source, model"
        )
        return await cursor.fetchall()


async def test_db_init_creates_token_usage_table(db):
    async with aiosqlite.connect(db) as conn:
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='token_usage'"
        )
        assert await cursor.fetchone() is not None


async def test_record_writes_current_source(db):
    usage.source.set(usage.MENTION)
    await usage.record("gemma-4-31b-it", 100, 20)

    rows = await _rows(db)
    assert len(rows) == 1
    _day, source, model, requests, prompt, completion = rows[0]
    assert (source, model, requests, prompt, completion) == (usage.MENTION, "gemma-4-31b-it", 1, 100, 20)


async def test_record_defaults_to_background(db):
    # неразмеченный вызов (дайджест, тикеты) не должен приписываться разговорам
    await usage.record("gemma-4-26b-a4b-it", 10, 5)
    assert (await _rows(db))[0][1] == usage.BACKGROUND


async def test_record_accumulates_within_day_source_model(db):
    usage.source.set(usage.AICHAT)
    await usage.record("gemma-4-31b-it", 100, 20)
    await usage.record("gemma-4-31b-it", 50, 10)

    rows = await _rows(db)
    assert len(rows) == 1  # upsert, а не вторая строка
    assert rows[0][3:] == (2, 150, 30)


async def test_record_splits_sources_and_models(db):
    usage.source.set(usage.MENTION)
    await usage.record("gemma-4-31b-it", 100, 20)
    usage.source.set(usage.AICHAT)
    await usage.record("gemma-4-31b-it", 7, 3)
    await usage.record("gemma-4-26b-a4b-it", 1, 1)

    assert len(await _rows(db)) == 3


async def test_record_survives_non_numeric_usage(db):
    # compat-вендор может прислать None вместо чисел — телеметрия не должна падать
    usage.source.set(usage.MENTION)
    await usage.record("gemma-4-31b-it", None, None)
    assert (await _rows(db))[0][3:] == (1, 0, 0)


async def test_report_groups_by_source(db):
    usage.source.set(usage.MENTION)
    await usage.record("gemma-4-31b-it", 100, 20)
    await usage.record("gemma-4-26b-a4b-it", 10, 2)
    usage.source.set(usage.BACKGROUND)
    await usage.record("gemma-4-31b-it", 5, 1)

    report = await usage.report()
    assert report[usage.MENTION] == (2, 110, 22)
    assert report[usage.BACKGROUND] == (1, 5, 1)
    assert usage.AICHAT not in report  # молчавший источник в отчёте не появляется


async def test_report_window_excludes_older_days(db):
    usage.source.set(usage.MENTION)
    await usage.record("gemma-4-31b-it", 100, 20)
    # дописываем «старую» строку руками: _today() врать не заставишь
    old_day = (datetime.datetime.now(tz=usage._MSK) - datetime.timedelta(days=30)).strftime("%Y-%m-%d")
    async with aiosqlite.connect(db) as conn:
        await conn.execute(
            "INSERT INTO token_usage (day, source, model, requests, prompt_tokens, completion_tokens) "
            "VALUES (?, ?, ?, 1, 9000, 1000)",
            (old_day, usage.MENTION, "gemma-4-31b-it"),
        )
        await conn.commit()

    assert (await usage.report(days=1))[usage.MENTION] == (1, 100, 20)
    assert (await usage.report(days=7))[usage.MENTION] == (1, 100, 20)
    assert (await usage.report())[usage.MENTION] == (2, 9100, 1020)  # без окна — всё


@pytest.mark.parametrize("age_days, in_week, in_today", [
    (0, True, True),    # сегодня
    (1, True, False),   # вчера — в неделе есть, в «сегодня» нет
    (6, True, False),   # ровно край окна days=7 — ВНУТРИ
    (7, False, False),  # на сутки старше края — уже снаружи
])
async def test_report_window_boundaries(db, age_days, in_week, in_today):
    """Дырка в 30 дней пропустила бы и days-1, и days+1 — сторожим сам край."""
    day = (datetime.datetime.now(tz=usage._MSK) - datetime.timedelta(days=age_days)).strftime("%Y-%m-%d")
    async with aiosqlite.connect(db) as conn:
        await conn.execute(
            "INSERT INTO token_usage (day, source, model, requests, prompt_tokens, completion_tokens) "
            "VALUES (?, ?, ?, 1, 10, 1)",
            (day, usage.MENTION, "gemma-4-31b-it"),
        )
        await conn.commit()

    assert (usage.MENTION in await usage.report(days=7)) is in_week
    assert (usage.MENTION in await usage.report(days=1)) is in_today
    assert usage.MENTION in await usage.report()  # без окна видно всегда


# -------------------- форматирование дашборда --------------------

@pytest.mark.parametrize("raw, expected", [
    (0, "0"),
    (999, "999"),
    (1000, "1.0k"),
    (123_456, "123.5k"),
    (1_000_000, "1.0M"),
    (12_345_678, "12.3M"),
])
def test_fmt_tokens(raw, expected):
    assert _fmt_tokens(raw) == expected


def test_tokens_of_missing_source_is_zero():
    assert _tokens(None) == 0
    assert _tokens((3, 100, 20)) == 120


# -------------------- связка с llm и хендлером --------------------

async def test_track_usage_records_model_and_cumulative_flag(monkeypatch):
    from bot.ai.llm import llm as llm_singleton

    increment = AsyncMock()
    record = AsyncMock()
    monkeypatch.setattr("bot.flag_system.flag_system.flags.incrementFlag", increment)
    monkeypatch.setattr(usage, "record", record)
    response = SimpleNamespace(usage=SimpleNamespace(
        total_tokens=120, prompt_tokens=100, completion_tokens=20))

    await llm_singleton._track_usage(response, "gemma-4-31b-it")

    increment.assert_awaited_once_with("abstract", "token_used", 120)
    record.assert_awaited_once_with("gemma-4-31b-it", 100, 20)


async def test_track_usage_failure_does_not_raise(monkeypatch):
    from bot.ai.llm import llm as llm_singleton

    monkeypatch.setattr(usage, "record", AsyncMock(side_effect=RuntimeError("БД лежит")))
    monkeypatch.setattr("bot.flag_system.flag_system.flags.incrementFlag", AsyncMock())
    # через monkeypatch: llm — модульный синглтон, подменённый логгер иначе
    # переживёт тест и сломает первый же тест, который начнёт смотреть логи
    monkeypatch.setattr(llm_singleton, "logger", MagicMock())
    response = SimpleNamespace(usage=SimpleNamespace(
        total_tokens=1, prompt_tokens=1, completion_tokens=0))

    await llm_singleton._track_usage(response, "gemma-4-31b-it")  # не должно кинуть


# -------------------- дашборд /aistats --------------------

async def _run_aistats(db_path, monkeypatch):
    """Позвать команду в обход чеков прав и вернуть (текст контейнера, простой текст)."""
    from bot.ai import handler as handler_mod
    from bot.flag_system.flag_system import Flags
    from bot.utils import component_text

    inst = Flags()
    inst.dbpath = db_path
    monkeypatch.setattr(handler_mod, "flags", inst)

    handler = object.__new__(handler_mod.AIMessageHandler)
    handler.logger = MagicMock()

    captured = {}

    async def edit_original_response(content=None, *, components=None):
        captured["content"] = content
        captured["components"] = components

    inter = SimpleNamespace(
        response=SimpleNamespace(defer=AsyncMock()),
        edit_original_response=edit_original_response,
    )
    await handler_mod.AIMessageHandler.aiStats.callback(handler, inter)
    rendered = component_text(captured["components"]) if captured.get("components") else ""
    return rendered, captured.get("content")


async def test_aistats_renders_on_empty_db(db, monkeypatch):
    rendered, content = await _run_aistats(db, monkeypatch)

    assert content is None  # отрисовали контейнер, а не текстовую ошибку
    for label in ("Каналы", "/aichat", "Фон"):
        assert label in rendered
    assert "—" in rendered  # среднее на нуле вызовов, а не ZeroDivisionError


async def test_aistats_shows_mention_numbers(db, monkeypatch):
    usage.source.set(usage.MENTION)
    await usage.record("gemma-4-31b-it", 1200, 300)
    await usage.record("gemma-4-31b-it", 800, 200)

    rendered, _content = await _run_aistats(db, monkeypatch)

    assert "2.5k" in rendered              # 1200+300+800+200 сумма по каналам
    assert "2 вызовов модели" in rendered  # не «запросов»: квота юзера — другое
    assert "промпт 2.0k" in rendered


async def test_aistats_answers_instead_of_hanging_when_table_missing(db, monkeypatch):
    """defer уже ушёл — без ответа админ смотрит на вечное «думаю»."""
    async with aiosqlite.connect(db) as conn:
        await conn.execute("DROP TABLE token_usage")
        await conn.commit()

    rendered, content = await _run_aistats(db, monkeypatch)

    assert content and "db_init" in content
    assert not rendered


async def test_mention_and_thread_paths_label_their_source(monkeypatch):
    """Метка ставится ДО первого вызова модели, иначе расход уедет в background."""
    from bot.ai.handler import AIMessageHandler

    handler = object.__new__(AIMessageHandler)
    handler._chat_blocked = AsyncMock(return_value=True)  # ранний выход, дальше не идём

    monkeypatch.setattr(usage, "source", usage.contextvars.ContextVar(
        "llm_source_test", default=usage.BACKGROUND))

    await handler._handleMention(MagicMock())
    assert usage.source.get() == usage.MENTION

    await handler._handleThreadMessage(MagicMock())
    assert usage.source.get() == usage.AICHAT
