"""Общая продуктовая телеметрия Robocat без персональных данных."""

import datetime
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from bot import metrics
from data.db_init import dbInit


@pytest.fixture
async def db(tmp_path, monkeypatch):
    db_path = tmp_path / "metrics.sqlite"
    await dbInit(db_path)
    monkeypatch.setattr(metrics, "DB_PATH", db_path)
    return db_path


async def test_db_init_creates_event_metrics_table(tmp_path):
    db_path = tmp_path / "metrics.sqlite"

    await dbInit(db_path)

    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='event_metrics'"
        )
        assert await cursor.fetchone() is not None


async def test_record_event_accumulates_same_day(db):
    await metrics.record_event("ticket", "created")
    await metrics.record_event("ticket", "created")
    await metrics.record_event("ticket", "closed")

    snapshot = await metrics.report_events()

    assert snapshot.total == 3
    assert [(row.kind, row.name, row.count) for row in snapshot.rows] == [
        ("ticket", "created", 2),
        ("ticket", "closed", 1),
    ]


async def test_report_event_window_excludes_old_days(db):
    old_day = (datetime.datetime.now(tz=metrics.MSK) - datetime.timedelta(days=8)).strftime("%Y-%m-%d")
    async with aiosqlite.connect(db) as conn:
        await conn.executemany(
            "INSERT INTO event_metrics (day, kind, name, count) VALUES (?, ?, ?, ?)",
            [
                (old_day, "ticket", "created", 50),
                (metrics.today(), "security", "mention_raid", 2),
            ],
        )
        await conn.commit()

    week = await metrics.report_events(days=7)
    all_time = await metrics.report_events()

    assert week.total == 2
    assert all_time.total == 52


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("VACANCY_ACCEPT:123456789", "vacancy_accept"),
        ("ticket done", "ticket_done"),
        ("Странная-Кнопка", "странная_кнопка"),
        ("вики", "вики"),
        ("0123456789abcdef0123456789abcdef", "unknown"),
        ("", "unknown"),
    ],
)
def test_normalize_event_name_bounds_cardinality(raw, expected):
    assert metrics.normalize_event_name(raw) == expected


async def test_background_worker_flushes_queued_event(monkeypatch):
    recorded = []
    done = asyncio.Event()

    async def fake_record(kind, name):
        recorded.append((kind, name))
        done.set()

    monkeypatch.setattr(metrics, "record_event", fake_record)
    metrics.start_worker()
    try:
        metrics.record_later("ticket", "created")
        await asyncio.wait_for(done.wait(), timeout=1)
    finally:
        metrics.stop_worker()

    assert recorded == [("ticket", "created")]


async def test_cog_records_commands_and_components_without_dynamic_ids(monkeypatch):
    from bot.handlers import metrics as handler_mod

    record = MagicMock()
    monkeypatch.setattr(handler_mod.telemetry, "record_later", record)
    cog = handler_mod.MetricsCog(MagicMock())

    await cog.prefixCommandCompleted(SimpleNamespace(command=SimpleNamespace(qualified_name="репорты")))
    await cog.slashCommandCompleted(SimpleNamespace(application_command=SimpleNamespace(qualified_name="delete-until")))
    await cog.buttonClicked(SimpleNamespace(component=SimpleNamespace(custom_id="VACANCY_ACCEPT:123456789")))
    await cog.modalSubmitted(SimpleNamespace(custom_id="job_application:987654321"))

    assert record.call_args_list == [
        (("prefix_command", "репорты"),),
        (("slash_command", "delete_until"),),
        (("button", "vacancy_accept"),),
        (("modal", "job_application"),),
    ]


async def test_metrics_dashboard_renders_top_events(monkeypatch):
    from bot.handlers import metrics as handler_mod
    from bot.utils import component_text

    snapshot = metrics.MetricsSnapshot(
        total=12,
        rows=(
            metrics.MetricRow("ticket", "created", 7),
            metrics.MetricRow("security", "mention_raid", 5),
        ),
    )
    monkeypatch.setattr(handler_mod.telemetry, "report_events", AsyncMock(return_value=snapshot))
    captured = {}

    async def edit_original_response(*, content=None, components=None):
        captured["content"] = content
        captured["components"] = components

    inter = SimpleNamespace(
        response=SimpleNamespace(defer=AsyncMock()),
        edit_original_response=edit_original_response,
    )
    cog = handler_mod.MetricsCog(MagicMock())

    await handler_mod.MetricsCog.metricsDashboard.callback(cog, inter, days=7)

    rendered = component_text(captured["components"])
    assert "12 событий" in rendered
    assert "Создан тикет" in rendered
    assert "Антирейд по пингам" in rendered
    assert captured["content"] is None
