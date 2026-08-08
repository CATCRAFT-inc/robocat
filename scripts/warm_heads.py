"""Прогрев кэша голов для `!пинг`.

🛑 Гонять ЛОКАЛЬНО, а готовые PNG заливать на VDS руками — так IP бота не
рискует попасть под троттлинг minotar (решение Серёжи, 08.08.2026). Каталог
назначения на проде — `/opt/robocat/data/heads/`, и после заливки ему нужен
`chown -R bots:bots`, иначе бот молча перестанет пополнять кэш.

Список ников берётся из выгрузки с сервера Minecraft — обычно это
`usercache.json`, но годится и простой список по строке на ник.

    python3 scripts/warm_heads.py usercache.json
    python3 scripts/warm_heads.py -            # список со стдина

Адреса и доступы сюда не попадают: репозиторий публичный, выгрузку приносят
руками. Скрипт только читает файл и ходит на minotar — ничего не удаляет.
"""

import asyncio
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot.bot  # noqa: F401,E402  — первым, иначе циклический импорт

from bot.handlers.ping_tab import HEAD_CACHE_DIR, fetch_heads  # noqa: E402

# Головы качаются пачками: у fetch_heads есть общий потолок времени на вызов,
# и сотня ников одним куском в него упирается.
BATCH = 40


def parse_nicks(raw: str) -> list[str]:
    """Ники из usercache.json либо из простого списка строк.

    usercache — это `[{"name": "...", "uuid": "..."}, ...]`; всё остальное
    читаем как «ник в строке», допуская запятые.
    """
    try:
        data = json.loads(raw)
    except ValueError:
        data = None

    if isinstance(data, list):
        names = [
            entry.get("name", "") if isinstance(entry, dict) else str(entry)
            for entry in data
        ]
    else:
        names = re.split(r"[\s,]+", raw)

    unique = {name.strip(): None for name in names if name and name.strip()}
    return list(unique)


async def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2

    source = sys.argv[1]
    raw = sys.stdin.read() if source == "-" else open(source, encoding="utf-8").read()

    nicks = parse_nicks(raw)
    if not nicks:
        print("В выгрузке не нашлось ни одного ника")
        return 1

    print(f"ников в выгрузке: {len(nicks)}, кэш: {HEAD_CACHE_DIR}")
    warmed = 0
    for start in range(0, len(nicks), BATCH):
        batch = nicks[start:start + BATCH]
        heads = await fetch_heads(batch)
        warmed += len(heads)
        print(f"  {start + len(batch)}/{len(nicks)} — всего голов {warmed}", flush=True)

    missed = len(nicks) - warmed
    print(f"готово: {warmed} голов в кэше, не получили {missed}")
    # 🛑 Не писать «не получили = пиратка»: minotar отвечает 200 и дефолтным
    # Стивом на ЛЮБОЙ ник, в том числе несуществующий (проверено curl'ом).
    # Значит «голова получена» ничего не говорит о том, есть ли такой аккаунт.
    print("часть голов может оказаться дефолтным скином: minotar отдаёт его")
    print("на неизвестный ник с кодом 200, отличить это по ответу нельзя")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
