"""Сборка `data/wiki_index.json` — RAG-индекс вики для тула `search_wiki`.

Запуск из корня репозитория:

    python scripts/build_wiki_index.py --wiki-dir ../szarkans.github.io
    python scripts/build_wiki_index.py --wiki-dir ../szarkans.github.io --chunks-only
    python scripts/build_wiki_index.py --wiki-dir ../szarkans.github.io --full

Источник — checkout публичного VitePress-репозитория Вики. Список скрытых
страниц читается прямо из `srcExclude` в `.vitepress/config.mjs`, поэтому бот
видит тот же набор страниц, что сайт, без обхода sitemap и скачивания каждого
HTML. Markdown очищается только от служебной разметки; пользовательский текст и
имена предметов из Vue-рецептов остаются в корпусе.

🛑 Ключ `GEMINI` привязан к стране биллинга: с машины в РФ endpoint отвечает
403 PERMISSION_DENIED. Полный прогон гонять на VDS — там ключ рабочий:

    sudo -u bots .venv/bin/python scripts/build_wiki_index.py \
        --wiki-dir /opt/robocat/.wiki-source --out /tmp/idx.json

`--out` на проде обязателен: без него бот подхватит непроверенный индекс сразу.
`--chunks-only` считает те же чанки без единого запроса к эмбеддингам и кладёт
их в `data/wiki_chunks.json` — это материал ДЛЯ ГЛАЗ (посмотреть, что вообще
вытащилось из Markdown), обратно в сборку он не подаётся: векторы всё равно
переиспользуются по `hash` из готового индекса.
"""

from __future__ import annotations

import argparse
import asyncio
import fnmatch
import hashlib
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

WIKI_URL = "https://wiki.catcraft.ru"
INDEX_PATH = ROOT / "data" / "wiki_index.json"
CHUNKS_PATH = ROOT / "data" / "wiki_chunks.json"

# Верхняя граница чанка. В индексе от 04.07 самый большой чанк — 38504 символа
# (страница сезона без единого `##`): такой вектор усредняет всю страницу и в
# выдачу не попадает никогда. Режем длинные секции по абзацам.
MAX_CHUNK = 2000
# Порог считаем по СОДЕРЖИМОМУ секции, без строки заголовка: иначе короткая, но
# осмысленная страница («# 8 сезон / Эта история ещё не началась…») выпадает из
# индекса целиком, и бот перестаёт про неё знать.
MIN_CONTENT = 20

EMBED_CONCURRENCY = 4
# Free-tier gemini-embedding-2 — ~100 RPM и 1000 RPD. Полная пересборка это
# ~700 запросов: без троттлинга четыре корутины выдают под 800 RPM и ловят 429
# на середине, спалив дневную квоту впустую.
EMBED_RPM = 55
EMBED_RETRIES = 4
EMBED_DIMS = 768  # столько же просит bot/ai/embeddings.embed
# 🛑 Суточная квота (1000) ОБЩАЯ с живым ботом: тем же ключом он делает
# вики-поиск и дедуп багов. 02.08.2026 пересборка выбрала её до дна, и до конца
# суток каждый вопрос по вики отвечал «Поиск не удался». Оставляем боту запас;
# недосчитанное доедет следующим запуском из partial.
# Полная пересборка — ~710 запросов, плюс миграция дедупа багов; потолок должен
# её пропускать целиком и всё равно оставлять боту запас.
EMBED_DAILY_BUDGET = 850
PARTIAL_EVERY = 50  # как часто ронять прогресс на диск
PARTIAL_PATH = ROOT / "data" / "wiki_index.partial.json"

_HUMAN_ATTRS = {"alt", "description", "label", "name", "text", "title"}
_INLINE_TAGS = {
    "a", "abbr", "b", "bdi", "bdo", "cite", "code", "data", "dfn", "em", "i",
    "kbd", "mark", "q", "rp", "rt", "ruby", "s", "samp", "small", "span",
    "strong", "sub", "sup", "time", "u", "var", "wbr", "font",
}


def _script_content(match: re.Match) -> str:
    """Оставляет данные рецептов, убирая импорты и бесполезные URL ассетов."""
    body = re.sub(r"^\s*import\b.*$", "", match.group(1), flags=re.MULTILINE)
    body = re.sub(
        r"\b(?:image|link)\s*:\s*(['\"])(.*?)\1\s*,?",
        "",
        body,
        flags=re.DOTALL,
    )
    body = re.sub(r"//.*$", "", body, flags=re.MULTILINE)
    return f"\n{body.strip()}\n"


def _tag_content(match: re.Match) -> str:
    tag = match.group(0)
    values = re.findall(r"\b([\w:-]+)\s*=\s*(['\"])(.*?)\2", tag, flags=re.DOTALL)
    human = [value.strip() for name, _, value in values if name.lower() in _HUMAN_ATTRS]
    human = [value for value in human if value]
    if human:
        return "\n" + "\n".join(human) + "\n"
    tag_name = re.match(r"</?\s*([A-Za-z][\w-]*)", tag).group(1).lower()
    return "" if tag_name in _INLINE_TAGS else "\n"


def markdown_to_text(markdown: str) -> str:
    """Убирает VitePress/Vue-разметку, не теряя факты из компонентов."""
    text = re.sub(r"\A---\s*\n.*?\n---\s*(?:\n|\Z)", "", markdown, flags=re.DOTALL)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = re.sub(r"<style\b[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(
        r"<script\s+setup\b[^>]*>(.*?)</script>",
        _script_content,
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    text = re.sub(r"^\s*import\b.*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"!\[([^]]*)]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^]]+)]\([^)]*\)", r"\1", text)
    text = re.sub(r"</?[A-Za-z][^>]*>", _tag_content, text)
    text = re.sub(r"^\s*:::\s*\w*\s*", "", text, flags=re.MULTILINE)
    text = html.unescape(text).replace("​", "").replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def load_src_excludes(wiki_dir: Path) -> list[str]:
    """Читает строковые шаблоны `srcExclude` из VitePress-конфига."""
    config_path = wiki_dir / ".vitepress" / "config.mjs"
    try:
        config = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"не прочитать {config_path}: {exc}") from exc
    match = re.search(r"\bsrcExclude\s*:\s*\[(.*?)]\s*,", config, flags=re.DOTALL)
    if not match:
        raise RuntimeError(f"в {config_path} не найден srcExclude")
    block = re.sub(r"//.*$", "", match.group(1), flags=re.MULTILINE)
    return [value for _, value in re.findall(r"(['\"])(.*?)\1", block)]


def _is_excluded(relative: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(relative, pattern) for pattern in patterns)


def _page_identity(relative: Path) -> tuple[str, str]:
    path = relative.with_suffix("").as_posix()
    if path == "index":
        return "index", f"{WIKI_URL}/"
    if path.endswith("/index"):
        source = path.removesuffix("/index")
        return source, f"{WIKI_URL}/{source}/"
    return path, f"{WIKI_URL}/{path}"


def _split_long(section: str) -> list[str]:
    """Длинную секцию — по абзацам, не по символам: рвать предложение нельзя."""
    if len(section) <= MAX_CHUNK:
        return [section]
    out: list[str] = []
    current = ""
    for para in section.split("\n\n"):
        if current and len(current) + len(para) + 2 > MAX_CHUNK:
            out.append(current)
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para
        # Один абзац сам по себе длиннее лимита — режем его по строкам
        while len(current) > MAX_CHUNK:
            # По строке, иначе по пробелу: у абзаца без переносов длиннее
            # лимита `rfind("\n")` не находил ничего и резал посреди слова
            cut = current.rfind("\n", 0, MAX_CHUNK)
            if cut <= 0:
                cut = current.rfind(" ", 0, MAX_CHUNK)
            if cut <= 0:
                cut = MAX_CHUNK
            out.append(current[:cut].strip())
            current = current[cut:].strip()
    if current:
        out.append(current)
    return [c for c in out if c]


def chunk_page(text: str, source: str, url: str) -> list[dict]:
    """Страница → чанки. Границей служит `##`; титул страницы едет в каждый чанк.

    Титул дублируется намеренно: без него чанк «## Как варить?» эмбеддится без
    единого упоминания пивоварения и не находится по запросу про алкоголь.
    """
    if not text:
        return []
    # h1 ищем ГДЕ УГОДНО в тексте, а не только в первой строке: VitePress
    # рендерит баннер `::: warning` выше заголовка, и на таких страницах
    # (`5season`, `6season`, `qol/profile`, `4_3_3`) титулом вставал путь.
    # Найденную строку убираем — ниже она приписывается каждому чанку сама.
    title_match = re.search(r"^# (.+)$", text, re.M)
    title = title_match.group(1).strip() if title_match else source
    if title_match:
        text = (text[: title_match.start()] + text[title_match.end():]).strip()

    sections: list[str] = []
    buffer: list[str] = []
    for line in text.split("\n"):
        if line.startswith("## ") and buffer:
            sections.append("\n".join(buffer).strip())
            buffer = [line]
        else:
            buffer.append(line)
    if buffer:
        sections.append("\n".join(buffer).strip())

    # Короткую секцию не выбрасываем, а приклеиваем к предыдущей: «## Как бродить?
    # / Нужна бочка.» как отдельный вектор бесполезно, но это готовый ответ на
    # вопрос игрока — терять его нельзя. Обрывки без содержимого вообще (пункт
    # навигации, «📄») отсеиваются тем же порогом.
    merged: list[str] = []
    for section in sections:
        if not section:
            continue
        content = re.sub(r"^#{1,6} .*", "", section).strip()
        if len(content) >= MIN_CONTENT or not merged:
            # `not merged` — первая секция страницы. Раньше короткое вступление
            # молча пропадало, а страница целиком короче порога («# 8 сезон /
            # Скоро.») не давала вообще ни одного чанка и исчезала из индекса.
            merged.append(section)
        else:
            merged[-1] = f"{merged[-1]}\n\n{section}"
    # Единственная секция вообще без содержимого — страница пустая
    if len(merged) == 1 and len(re.sub(r"^#{1,6} .*", "", merged[0]).strip()) < MIN_CONTENT:
        return []

    chunks: list[dict] = []
    for section in merged:
        heading_match = re.match(r"#{1,6} (.+)", section)
        heading = heading_match.group(1).strip() if heading_match else title
        for piece in _split_long(section):
            body = f"# {title}\n\n{piece}"
            chunks.append(
                {
                    "text": body,
                    "heading": heading,
                    "source": source,
                    "url": url,
                    "hash": hashlib.sha256(body.encode("utf-8")).hexdigest()[:16],
                }
            )
    return chunks


def build_chunks(wiki_dir: Path) -> list[dict]:
    wiki_dir = wiki_dir.resolve()
    excludes = load_src_excludes(wiki_dir)
    pages = []
    for path in wiki_dir.rglob("*.md"):
        relative = path.relative_to(wiki_dir)
        relative_text = relative.as_posix()
        if any(part.startswith(".") or part == "node_modules" for part in relative.parts):
            continue
        if _is_excluded(relative_text, excludes):
            continue
        pages.append((relative_text, path, relative))
    pages.sort(key=lambda page: page[0])
    print(f"Markdown: {len(pages)} опубликованных страниц")

    chunks: list[dict] = []
    failed: list[str] = []
    for i, (relative_text, path, relative) in enumerate(pages, 1):
        source, url = _page_identity(relative)
        try:
            markdown = path.read_text(encoding="utf-8")
            page = chunk_page(markdown_to_text(markdown), source, url)
        except Exception as exc:
            failed.append(f"{source}: {exc}")
            print(f"[{i}/{len(pages)}] ✗ {relative_text} — {exc}")
            continue
        chunks.extend(page)
        print(f"[{i}/{len(pages)}] {relative_text}: {len(page)} чанк(ов)")
    if failed:
        raise RuntimeError("страницы не прочитались:\n  " + "\n  ".join(failed))
    return chunks


class _RateLimiter:
    """Не больше `rpm` стартов запроса в минуту, на все корутины разом."""

    def __init__(self, rpm: int):
        self._interval = 60.0 / rpm
        self._lock = asyncio.Lock()
        self._next = 0.0

    async def wait(self):
        loop = asyncio.get_running_loop()
        async with self._lock:
            delay = max(0.0, self._next - loop.time())
            self._next = max(self._next, loop.time()) + self._interval
        if delay:
            await asyncio.sleep(delay)


async def embed_chunks(
    chunks: list[dict], reuse: dict[str, list[float]], budget: int = EMBED_DAILY_BUDGET
) -> list[dict]:
    from bot.ai.embeddings import MODEL, embed

    for chunk in chunks:
        if chunk["hash"] in reuse:
            chunk["vector"] = reuse[chunk["hash"]]
            # model — часть контракта индекса. Без неё следующий инкрементальный
            # запуск отвергнет собственный reuse и заново сожжёт дневную квоту.
            chunk["model"] = MODEL
    todo = [c for c in chunks if not c.get("vector")]
    print(f"Векторов взято из прошлых сборок: {len(chunks) - len(todo)}, считать: {len(todo)}")
    if not todo:
        return chunks
    if len(todo) > budget:
        # Молча урезать нельзя: недосчитанные страницы выпали бы из индекса, а
        # прогон отрапортовал бы успех. Считаем сколько влезло и честно падаем
        # в partial — следующий запуск (завтра) доберёт остаток.
        print(
            f"🛑 Нужно {len(todo)} запросов, бюджет прогона — {budget}. "
            f"Считаю {budget}, остальные {len(todo) - budget} доедут следующим запуском."
        )
        todo = todo[:budget]

    semaphore = asyncio.Semaphore(EMBED_CONCURRENCY)
    limiter = _RateLimiter(EMBED_RPM)
    done = 0

    async def one(chunk: dict):
        nonlocal done
        async with semaphore:
            for attempt in range(EMBED_RETRIES):
                await limiter.wait()
                try:
                    chunk["vector"] = await embed(chunk["text"])
                    # Чем посчитан вектор — часть данных, а не примечание:
                    # `hash` считается по ТЕКСТУ, поэтому без этой метки
                    # инкрементальный прогон после смены модели переиспользовал
                    # бы старые векторы и намешал два несовместимых пространства
                    chunk["model"] = MODEL
                    break
                except Exception as exc:
                    if attempt + 1 == EMBED_RETRIES:
                        raise
                    backoff = 5 * 2**attempt
                    print(f"  {type(exc).__name__} на {chunk['source']} — жду {backoff}s")
                    await asyncio.sleep(backoff)
        done += 1
        if done % 25 == 0 or done == len(todo):
            print(f"  эмбеддинги: {done}/{len(todo)}")
        # Периодический дамп: аварийный `_dump_partial` спасает только на путях,
        # где мы сами ловим ошибку. SIGKILL (обрыв сессии, OOM) их обходит, и
        # тогда теряется ВСЁ оплаченное — 02.08 так сгорело 125 векторов.
        if done % PARTIAL_EVERY == 0:
            _dump_partial(chunks, quiet=True)

    # return_exceptions: одна упавшая страница не должна выбрасывать сотни уже
    # посчитанных векторов — недосчитанное доедет следующим запуском из partial
    results = await asyncio.gather(*(one(c) for c in todo), return_exceptions=True)
    errors = [r for r in results if isinstance(r, Exception)]
    if errors:
        _dump_partial(chunks)
        raise RuntimeError(
            f"{len(errors)} чанков не посчитались (первая ошибка: {errors[0]!r}). "
            f"Прогресс сохранён в {PARTIAL_PATH.name} — просто запусти скрипт снова."
        )
    if any(not c.get("vector") for c in chunks):
        _dump_partial(chunks)
        raise RuntimeError(
            f"Бюджет прогона ({budget} запросов) исчерпан — посчитано не всё. "
            f"Прогресс в {PARTIAL_PATH.name}; следующий запуск продолжит. Если "
            "упёрлись в RPD, завершится после сброса суточной квоты."
        )
    return chunks


def _dump_partial(chunks: list[dict], quiet: bool = False):
    ready = [c for c in chunks if c.get("vector")]
    # Через временный файл: убитый посреди записи процесс иначе оставил бы
    # обрезанный JSON, и следующий запуск выбросил бы весь прогресс как битый
    tmp = PARTIAL_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(ready, ensure_ascii=False), encoding="utf-8")
    tmp.replace(PARTIAL_PATH)
    print(f"Сохранено готовых векторов: {len(ready)} → {PARTIAL_PATH}", flush=True)
    if quiet:
        return


def load_reuse(full: bool, out: Path | None = None) -> dict[str, list[float]]:
    """Векторы, которые можно не пересчитывать: ключ — hash текста чанка.

    Источники: прошлый успешный индекс (текст страницы не изменился) и partial
    от оборвавшегося запуска (докат по дневной квоте). При `--out` прошлым
    индексом считается сам файл назначения — иначе повторный прогон в тот же
    файл пересчитывал бы всё заново, хотя рядом лежит готовый результат.
    """
    from bot.ai.embeddings import MODEL

    if full:
        # `--full` обязан значить «пересчитать всё». Раньше он всё равно тянул
        # partial, и прогон, заявленный как полная пересборка, молча смешивал
        # векторы неизвестного происхождения.
        return {}
    reuse: dict[str, list[float]] = {}
    # Файл назначения — тоже источник: без него `--out` (обязательный на проде)
    # игнорировал бы готовый data/wiki_index.json и жёг дневную квоту целиком.
    sources = [INDEX_PATH, PARTIAL_PATH] + ([out] if out else [])
    seen: set[Path] = set()
    for path in sources:
        path = path.resolve()
        if path in seen or not path.exists():
            continue
        seen.add(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            found = {
                c["hash"]: c["vector"]
                for c in data
                # Модель и размерность сверяем: вектор из чужого пространства
                # не «похуже», а мусор — и мусор молчаливый. Индекс без метки
                # `model` собран до её появления, то есть точно не текущей.
                if c.get("hash")
                and c.get("model") == MODEL
                and len(c.get("vector") or ()) == EMBED_DIMS
            }
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            print(f"{path.name} не прочитался ({exc}) — пропускаю")
            continue
        print(f"{path.name}: {len(found)} готовых векторов")
        reuse.update(found)
    return reuse


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--wiki-dir",
        type=Path,
        required=True,
        help="checkout репозитория CATCRAFT-inc/szarkans.github.io",
    )
    parser.add_argument("--full", action="store_true", help="переэмбеддить всё")
    parser.add_argument(
        "--out",
        type=Path,
        help="куда писать индекс (по умолчанию data/wiki_index.json). На проде "
        "обязательно указывать: иначе бот подхватит непроверенный индекс сразу",
    )
    parser.add_argument(
        "--chunks-only",
        action="store_true",
        help=f"только текст, без эмбеддингов → {CHUNKS_PATH.name}",
    )
    args = parser.parse_args()

    # Ключ GEMINI берётся так же, как его берёт бот. Читаем .env ЗДЕСЬ, а не при
    # импорте: тесты импортируют этот модуль, и на импорте настоящие значения
    # протекали бы в окружение тестового процесса мимо фейков из conftest.
    load_dotenv(ROOT / ".env")

    chunks = build_chunks(args.wiki_dir)
    if not chunks:
        raise RuntimeError("в исходниках Вики не найдено ни одного чанка")
    lengths = [len(c["text"]) for c in chunks]
    print(
        f"\nВсего чанков: {len(chunks)} со {len({c['source'] for c in chunks})} страниц; "
        f"длина текста: min {min(lengths)}, max {max(lengths)}"
    )

    if args.chunks_only:
        CHUNKS_PATH.write_text(
            json.dumps(chunks, ensure_ascii=False), encoding="utf-8"
        )
        print(f"Чанки без векторов: {CHUNKS_PATH}")
        return 0

    # Докат после обрыва: чанки уже посчитаны локально — берём их, а не тянем
    # сайт заново (список страниц и так сверяется по хешам)
    chunks = asyncio.run(embed_chunks(chunks, load_reuse(args.full, args.out)))
    missing = [c["source"] for c in chunks if not c.get("vector")]
    if missing:
        # Тоже сохраняем прогресс: без этого путь «вектор не пришёл, но и
        # исключения не было» терял всю посчитанную пачку и дневную квоту
        _dump_partial(chunks)
        raise RuntimeError(f"без вектора остались чанки: {sorted(set(missing))[:5]}")

    out = args.out or INDEX_PATH
    try:
        out.write_text(json.dumps(chunks, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        # Векторы уже оплачены дневной квотой — терять их из-за прав на файл
        # или кончившегося места нельзя (прецедент 02.08: файл назначения на
        # VDS принадлежал root, скрипт бежит от bots, 48 эмбеддингов в трубу)
        _dump_partial(chunks)
        raise RuntimeError(f"не записать {out}: {exc}. Прогресс — в {PARTIAL_PATH.name}")

    PARTIAL_PATH.unlink(missing_ok=True)
    print(f"Готово: {out} ({out.stat().st_size / 1e6:.1f} МБ)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
