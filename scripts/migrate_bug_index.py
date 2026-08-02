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
кладётся `.bak-<модель, которая лежала внутри>` — откат ручным `mv`.

Скрипт ДОКАТЫВАЕМЫЙ: каждая посчитанная запись сразу пишется на диск, и
повторный запуск берёт только те, у которых `model` ещё не совпадает. Это
обязательное свойство, а не удобство — когда суточной квоты остаётся на пару
запросов, вариант «всё или ничего» выбрасывает оплаченное и не доедет никогда.
Наполовину мигрированный индекс безопасен: `bugs.py` сверяет `model` и просто
пропускает записи, до которых ещё не дошли.
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

    no_text = sorted(k for k, v in index.items() if not v.get("text"))
    done = {k for k, v in index.items() if v.get("model") == MODEL}
    todo = [k for k, v in index.items() if v.get("text") and k not in done]
    print(
        f"Записей: {len(index)}, уже на {MODEL}: {len(done)}, "
        f"пересчитать: {len(todo)}"
    )
    if no_text:
        # Без текста вектор не воспроизвести. Такие записи остаются в ЧУЖОМ
        # пространстве — дедуп их пропустит (bugs.py сверяет model), поэтому
        # молча удалять их не надо: пусть лежат видимым мусором.
        print(f"🛑 Без поля text ({len(no_text)}), пересчитать нельзя: {no_text}")
    if not todo:
        print("Мигрировать нечего.")
        return 0
    if args.dry_run:
        return 0

    # Бэкап делаем ОДИН раз, до первой правки, и называем по модели, которая
    # ЛЕЖИТ ВНУТРИ, а не по той, на которую переезжаем: иначе восстановление
    # из `.bak-gemini-embedding-2` вернёт ровно ту поломку, от которой
    # откатывались. Повторный запуск бэкап не перезаписывает — иначе докат
    # затёр бы исходник уже наполовину мигрированной копией.
    # Проверяем ЛЮБОЙ существующий бэкап, а не совпадение имени: на втором
    # прогоне состав моделей в файле уже другой, и проверка по имени создала бы
    # второй бэкап — с наполовину мигрированного индекса, то есть бесполезный
    # как точка отката.
    existing = sorted(INDEX_PATH.parent.glob(f"{INDEX_PATH.stem}.bak-*"))
    if existing:
        print(f"Бэкап уже есть, не трогаю: {existing[0].name}")
    else:
        was = sorted({str(v.get("model") or "неизвестна") for v in index.values()})
        backup = INDEX_PATH.with_suffix(f".bak-{'-'.join(was)}")
        shutil.copyfile(INDEX_PATH, backup)
        print(f"Бэкап (модели внутри: {', '.join(was)}): {backup.name}")

    def save():
        # Атомарно: убитый посреди записи процесс иначе оставит битый JSON
        tmp = INDEX_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
        tmp.replace(INDEX_PATH)

    for i, key in enumerate(todo, 1):
        for attempt in range(RETRIES):
            try:
                vector = await embed(index[key]["text"])
                break
            except Exception as exc:
                if attempt + 1 == RETRIES:
                    save()
                    # 🛑 Прогресс сохраняем ОБЯЗАТЕЛЬНО. Всё-или-ничего здесь
                    # не работает: когда квоты остаётся на пару запросов,
                    # такой скрипт выбрасывает оплаченное и не доедет никогда
                    # (прецедент 02.08 — три вектора в трубу). Смешанный
                    # индекс безопасен: bugs.py сверяет model и просто
                    # пропускает ещё не переехавшие записи.
                    raise RuntimeError(
                        f"{type(exc).__name__} на записи {i}/{len(todo)}. "
                        f"Переехало {len(done)} из {len(index)}, прогресс сохранён — "
                        f"запусти снова (при 429 — после сброса суточной квоты)."
                    ) from exc
                backoff = 10 * 2**attempt
                print(f"  {type(exc).__name__} — жду {backoff}s")
                await asyncio.sleep(backoff)
        if len(vector) != DIMENSIONS:
            raise RuntimeError(f"{key}: пришло {len(vector)} измерений вместо {DIMENSIONS}")
        index[key] = {**index[key], "vector": vector, "model": MODEL}
        done.add(key)
        save()
        print(f"  [{i}/{len(todo)}] {key}")
        if i < len(todo):
            await asyncio.sleep(60 / RPM)

    print(f"Готово: {len(done)} из {len(index)} записей на {MODEL}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
