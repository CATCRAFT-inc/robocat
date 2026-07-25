"""Учёт расхода токенов по источнику — данные для дашборда /aistats.

Пишем в таблицу ``token_usage``: сутки × источник × модель. Источник берётся из
contextvars, а не из параметра: учёт живёт глубоко в ротации (``llm._execute``)
и не знает, кто её позвал, а тащить метку через ``complete``/``ask``/``parse``
означало бы править контракт llm.py — единственной точки выхода в LLM — ради
телеметрии. ``asyncio.create_task`` наследует контекст, поэтому фоновое сжатие
треда честно записывается на счёт того самого aichat, который его породил.
"""

import contextvars
import datetime

import aiosqlite

from bot.flag_system.flag_system import flags

MENTION = "mention"        # обращение к боту в обычном канале
AICHAT = "aichat"          # тред /aichat
BACKGROUND = "background"  # дайджест, тикеты, idiotCheck, модерация картинок

# Дефолт — background: неразмеченный вызов честнее записать в фон, чем приписать
# разговорам и раздуть цифру, ради которой дашборд и делается.
source = contextvars.ContextVar("llm_source", default=BACKGROUND)

_MSK = datetime.timezone(datetime.timedelta(hours=3))


def _today() -> str:
    return datetime.datetime.now(tz=_MSK).strftime("%Y-%m-%d")


async def record(model: str, prompt_tokens: int, completion_tokens: int) -> None:
    """Прибавить расход одного успешного запроса к суткам текущего источника."""
    async with aiosqlite.connect(flags.dbpath) as db:
        await db.execute(
            """
            INSERT INTO token_usage
                (day, source, model, requests, prompt_tokens, completion_tokens)
            VALUES (:day, :source, :model, 1, :prompt, :completion)
            ON CONFLICT(day, source, model) DO UPDATE SET
                requests          = requests + 1,
                prompt_tokens     = prompt_tokens + :prompt,
                completion_tokens = completion_tokens + :completion
            """,
            {
                "day": _today(),
                "source": source.get(),
                "model": str(model or "?"),
                "prompt": int(prompt_tokens or 0),
                "completion": int(completion_tokens or 0),
            },
        )
        await db.commit()


async def report(days: int | None = None) -> dict[str, tuple[int, int, int]]:
    """Суммы по источникам: ``{source: (requests, prompt, completion)}``.

    ``days=1`` — только сегодня, ``days=7`` — сегодня и шесть предыдущих суток,
    ``None`` — за всё время, что копится таблица.
    """
    query = (
        "SELECT source, SUM(requests), SUM(prompt_tokens), SUM(completion_tokens) "
        "FROM token_usage"
    )
    params: tuple = ()
    if days:
        since = datetime.datetime.now(tz=_MSK) - datetime.timedelta(days=days - 1)
        query += " WHERE day >= ?"
        params = (since.strftime("%Y-%m-%d"),)
    query += " GROUP BY source"

    async with aiosqlite.connect(flags.dbpath) as db:
        rows = await (await db.execute(query, params)).fetchall()
    return {row[0]: (row[1] or 0, row[2] or 0, row[3] or 0) for row in rows}
