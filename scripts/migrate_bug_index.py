"""Пересчёт `data/bug_index.json` под текущую модель эмбеддингов.

    python scripts/migrate_bug_index.py --dry-run   # посмотреть, ничего не трогая
    python scripts/migrate_bug_index.py             # пересчитать

Зачем: дедуп баг-репортов (`bot/handlers/tickets/bugs.py`) сравнивает косинусом
вектор нового репорта с векторами старых и склеивает их при `>= 0.72`. После
смены модели в `bot/ai/embeddings.py` старые векторы живут в ЧУЖОМ пространстве:
сравнение перестаёт значить хоть что-то, но не падает — дедуп просто тихо
перестаёт ловить дубликаты. Поэтому индекс переезжает вместе с моделью.

Индекс живёт ТОЛЬКО на проде (в git не попадает), поэтому скрипт гоняется там:

    sudo -u bots .venv/bin/python scripts/migrate_bug_index.py

Записи хранят исходный `text`, так что пересчёт полный и без потерь. Рядом
кладётся `.bak-<модель>` — откат ручным `mv`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

INDEX_PATH = ROOT / "data" / "bug_index.json"
RPM = 55  # тот же троттлинг, что и у сборки вики-индекса
RETRIES = 3


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="только показать план")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    from bot.ai.embeddings import DIMENSIONS, MODEL, embed

    if not INDEX_PATH.exists():
        print(f"{INDEX_PATH} нет — мигрировать нечего")
        return 0
    index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))

    todo = {k: v for k, v in index.items() if v.get("text")}
    skipped = sorted(set(index) - set(todo))
    print(f"Записей: {len(index)}, пересчитать: {len(todo)}, модель: {MODEL}")
    if skipped:
        # Без текста вектор не воспроизвести. Такие записи останутся в ЧУЖОМ
        # пространстве и будут тихо мешать дедупу — их честнее удалить.
        print(f"🛑 Без поля text ({len(skipped)}), будут УДАЛЕНЫ: {skipped}")
    if args.dry_run:
        return 0

    # Имя бэкапа — по модели, которая ЛЕЖИТ ВНУТРИ, а не по той, на которую
    # переезжаем: иначе `.bak-gemini-embedding-2` содержит векторы от 001, и
    # восстановление из него вернёт ровно ту поломку, от которой откатывались.
    was = {v.get("model") for v in index.values()} or {None}
    backup = INDEX_PATH.with_suffix(f".bak-{'-'.join(sorted(str(m) for m in was))}")
    shutil.copyfile(INDEX_PATH, backup)
    print(f"Бэкап (модель внутри: {', '.join(str(m) for m in was)}): {backup}")

    migrated: dict = {}
    for i, (key, value) in enumerate(todo.items(), 1):
        for attempt in range(RETRIES):
            try:
                vector = await embed(value["text"])
                break
            except Exception as exc:
                if attempt + 1 == RETRIES:
                    # Прогресса не сохраняем намеренно: записей два десятка,
                    # частично мигрированный индекс сложнее объяснить, чем
                    # повторный запуск. Исходник цел — мы его не переписывали.
                    raise RuntimeError(
                        f"{type(exc).__name__} на записи {i}/{len(todo)}. "
                        f"Индекс НЕ изменён, запусти снова (при 429 — завтра)."
                    ) from exc
                backoff = 10 * 2**attempt
                print(f"  {type(exc).__name__} — жду {backoff}s")
                await asyncio.sleep(backoff)
        if len(vector) != DIMENSIONS:
            raise RuntimeError(f"{key}: пришло {len(vector)} измерений вместо {DIMENSIONS}")
        migrated[key] = {**value, "vector": vector, "model": MODEL}
        print(f"  [{i}/{len(todo)}] {key}")
        if i < len(todo):
            await asyncio.sleep(60 / RPM)

    INDEX_PATH.write_text(json.dumps(migrated, ensure_ascii=False), encoding="utf-8")
    print(f"Готово: {len(migrated)} записей на {MODEL}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
