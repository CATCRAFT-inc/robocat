"""Компактная агрегированная телеметрия продуктовых событий Robocat.

В таблицу попадают только имена событий из кода и счётчики по суткам. Тексты,
Discord ID и другие пользовательские данные здесь не сохраняются.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import aiosqlite


logger = logging.getLogger("robocat.metrics")

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "db.sqlite"
MSK = datetime.timezone(datetime.timedelta(hours=3))
_SAFE_NAME = re.compile(r"[a-zа-яё0-9_]{1,64}")
_RANDOM_MODAL_ID = re.compile(r"[0-9a-f]{32}")
_queue: asyncio.Queue[tuple[str, str]] | None = None
_worker_task: asyncio.Task[None] | None = None


@dataclass(frozen=True, slots=True)
class MetricRow:
    kind: str
    name: str
    count: int


@dataclass(frozen=True, slots=True)
class MetricsSnapshot:
    total: int
    rows: tuple[MetricRow, ...]


def today() -> str:
    return datetime.datetime.now(tz=MSK).strftime("%Y-%m-%d")


def normalize_event_name(raw: str | None) -> str:
    """Убирает динамические суффиксы component ID и ограничивает кардинальность."""
    base = (raw or "").split(":", 1)[0].strip().casefold()
    base = base.replace("-", "_").replace(" ", "_")
    if _RANDOM_MODAL_ID.fullmatch(base):
        return "unknown"
    return base if _SAFE_NAME.fullmatch(base) else "unknown"


async def record_event(kind: str, name: str) -> None:
    """Атомарно увеличивает суточный счётчик одного события."""
    safe_kind = normalize_event_name(kind)
    safe_name = normalize_event_name(name)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO event_metrics (day, kind, name, count)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(day, kind, name) DO UPDATE SET count = count + 1
            """,
            (today(), safe_kind, safe_name),
        )
        await db.commit()


async def _worker(queue: asyncio.Queue[tuple[str, str]]) -> None:
    while True:
        kind, name = await queue.get()
        try:
            await record_event(kind, name)
        except Exception:
            logger.exception("Не удалось записать метрику %s/%s", kind, name)
        finally:
            queue.task_done()


def start_worker() -> None:
    """Запускает один последовательный писатель после загрузки MetricsCog."""
    global _queue, _worker_task
    if _worker_task is not None and not _worker_task.done():
        return
    queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue(maxsize=1000)
    _queue = queue
    _worker_task = asyncio.create_task(_worker(queue))


def stop_worker() -> None:
    global _queue, _worker_task
    if _worker_task is not None:
        _worker_task.cancel()
    _worker_task = None
    _queue = None


def record_later(kind: str, name: str) -> None:
    """Ставит событие в очередь; без MetricsCog безопасно ничего не делает."""
    if _queue is None or _worker_task is None or _worker_task.done():
        return
    try:
        _queue.put_nowait((kind, name))
    except asyncio.QueueFull:
        logger.warning("Очередь метрик переполнена — событие %s/%s пропущено", kind, name)


async def report_events(days: int | None = None) -> MetricsSnapshot:
    """Возвращает агрегаты за окно в днях либо за всё время."""
    where = ""
    params: tuple[str, ...] = ()
    if days is not None:
        if days < 1:
            raise ValueError("days must be positive")
        since = (
            datetime.datetime.now(tz=MSK) - datetime.timedelta(days=days - 1)
        ).strftime("%Y-%m-%d")
        where = "WHERE day >= ?"
        params = (since,)

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            f"""
            SELECT kind, name, SUM(count) AS total
            FROM event_metrics
            {where}
            GROUP BY kind, name
            ORDER BY total DESC, kind, name
            """,
            params,
        )
        rows = tuple(MetricRow(kind, name, count) for kind, name, count in await cursor.fetchall())

    return MetricsSnapshot(total=sum(row.count for row in rows), rows=rows)
