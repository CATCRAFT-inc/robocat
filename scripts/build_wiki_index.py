"""Сборка `data/wiki_index.json` — RAG-индекс вики для тула `search_wiki`.

Запуск из корня репозитория:

    python scripts/build_wiki_index.py                 # инкрементально
    python scripts/build_wiki_index.py --chunks-only   # только текст, для глаз
    python scripts/build_wiki_index.py --full          # переэмбеддить всё

Источник — `https://wiki.catcraft.ru/sitemap.xml`, и только он. Часть страниц
вики намеренно скрыта через `srcExclude` в `.vitepress/config.mjs` (распущенный
КСБ, непере­несённая гардеробная): в сборку сайта они не попадают, значит нет их
и в сайтмапе. Брать список страниц из markdown-исходников нельзя — вернём боту
знание, которое сервер удалил специально.

Текст берём из отрендеренного HTML, а не из markdown: в исходниках вики контент
живёт внутри Vue-компонентов (`<ItemCard>`, `<CraftingGrid>`, `<script setup>`),
и эмбеддинг такого файла описывает разметку, а не механику сервера.

Зависимостей сверх `requirements.txt` нет: `urllib` + `html.parser` + уже
существующий `bot.ai.embeddings`.

🛑 Ключ `GEMINI` привязан к стране биллинга: с машины в РФ endpoint отвечает
403 PERMISSION_DENIED. Полный прогон гонять на VDS — там ключ рабочий:

    sudo -u bots .venv/bin/python scripts/build_wiki_index.py --out /tmp/idx.json

`--out` на проде обязателен: без него бот подхватит непроверенный индекс сразу.
`--chunks-only` считает те же чанки без единого запроса к эмбеддингам и кладёт
их в `data/wiki_chunks.json` — это материал ДЛЯ ГЛАЗ (посмотреть, что вообще
вытащилось из вёрстки), обратно в сборку он не подаётся: векторы всё равно
переиспользуются по `hash` из готового индекса.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

SITEMAP_URL = "https://wiki.catcraft.ru/sitemap.xml"
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

FETCH_RETRIES = 3
EMBED_CONCURRENCY = 4
# Free-tier gemini-embedding-001 — ~100 RPM и 1000 RPD. Полная пересборка это
# ~700 запросов: без троттлинга четыре корутины выдают под 800 RPM и ловят 429
# на середине, спалив дневную квоту впустую.
EMBED_RPM = 55
EMBED_RETRIES = 4
EMBED_DIMS = 768  # столько же просит bot/ai/embeddings.embed
PARTIAL_PATH = ROOT / "data" / "wiki_index.partial.json"

_SKIP_TAGS = {"script", "style", "noscript", "svg"}
# Перечисляем СТРОЧНЫЕ теги, а блочным считаем всё остальное. Обратный список
# («вот эти теги блочные») здесь не работает: вики собрана из Vue-компонентов,
# которые рендерят что угодно — ячейки таблиц, PrimeVue-табы на <button>,
# карточки. Каждый неучтённый тег склеивал соседний текст в одно слово:
# `<th>Иконка</th><th>Что обозначает?</th>` → «ИконкаЧто обозначает?»,
# `<button>Запрещено</button><button>Разрешено</button>` → «ЗапрещеноРазрешено».
# Список строчных тегов конечен и стабилен, список компонентов — нет.
_INLINE_TAGS = {
    "a", "abbr", "b", "bdi", "bdo", "cite", "code", "data", "dfn", "em", "i",
    "kbd", "mark", "q", "rp", "rt", "ruby", "s", "samp", "small", "span",
    "strong", "sub", "sup", "time", "u", "var", "wbr", "font",
}
_HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}


class _MainTextParser(HTMLParser):
    """Текст из <main> отрендеренной страницы VitePress, с разметкой заголовков.

    Заголовки восстанавливаем как `#`-строки, чтобы дальше резать по `##` —
    той же границей, по которой резался индекс от 07.05.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._depth = 0          # глубина вложенности внутри <main>
        self._skip_depth = 0     # >0 — мы внутри script/style
        self._anchor = False     # <a class="header-anchor"> — «¶» рядом с заголовком
        self.parts: list[str] = []

    @property
    def _inside(self) -> bool:
        return self._depth > 0 and self._skip_depth == 0

    def handle_starttag(self, tag, attrs):
        if tag == "main":
            self._depth = max(self._depth, 1)
            return
        if self._depth == 0:
            return
        self._depth += 1
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "a" and any(k == "class" and "header-anchor" in (v or "") for k, v in attrs):
            self._anchor = True
            return
        level = _HEADINGS.get(tag)
        if level:
            self.parts.append(f"\n\n{'#' * level} ")
        elif tag not in _INLINE_TAGS:
            self.parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        if self._inside and tag not in _INLINE_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if self._depth == 0:
            return
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "a" and self._anchor:
            self._anchor = False
        elif self._skip_depth == 0 and tag not in _INLINE_TAGS:
            self.parts.append("\n")
        self._depth -= 1
        if tag == "main":
            self._depth = 0

    def handle_data(self, data):
        if self._inside and not self._anchor:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _MainTextParser()
    parser.feed(html)
    text = "".join(parser.parts)
    text = text.replace("​", "").replace("\xa0", " ")
    # Схлопываем пробелы внутри строк и пустые строки между блоками: голый
    # strip_tags оставляет от вёрстки простыни отступов (видно в индексе 07.05).
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


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


def fetch(url: str) -> str:
    last: Exception | None = None
    for attempt in range(FETCH_RETRIES):
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "robocat-wiki-indexer/1.0"}
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            if attempt + 1 < FETCH_RETRIES:
                import time

                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"не удалось скачать {url}: {last}")


def sitemap_urls() -> list[str]:
    xml = fetch(SITEMAP_URL)
    urls = re.findall(r"<loc>(.*?)</loc>", xml)
    # Корень — лендинг из Vue-компонентов, текста в HTML нет: индексировать нечего
    return [u for u in urls if u.rstrip("/") != "https://wiki.catcraft.ru"]


def build_chunks() -> list[dict]:
    urls = sitemap_urls()
    print(f"Сайтмап: {len(urls)} страниц")
    chunks: list[dict] = []
    failed: list[str] = []
    for i, url in enumerate(urls, 1):
        source = url.replace("https://wiki.catcraft.ru/", "").strip("/")
        try:
            page = chunk_page(html_to_text(fetch(url)), source, url)
        except Exception as exc:
            failed.append(f"{source}: {exc}")
            print(f"[{i}/{len(urls)}] ✗ {source} — {exc}")
            continue
        chunks.extend(page)
        print(f"[{i}/{len(urls)}] {source}: {len(page)} чанк(ов)")
    if failed:
        raise RuntimeError("страницы не скачались:\n  " + "\n  ".join(failed))
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


async def embed_chunks(chunks: list[dict], reuse: dict[str, list[float]]) -> list[dict]:
    from bot.ai.embeddings import embed

    for chunk in chunks:
        if chunk["hash"] in reuse:
            chunk["vector"] = reuse[chunk["hash"]]
    todo = [c for c in chunks if not c.get("vector")]
    print(f"Векторов взято из прошлых сборок: {len(chunks) - len(todo)}, считать: {len(todo)}")
    if not todo:
        return chunks

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
    return chunks


def _dump_partial(chunks: list[dict]):
    ready = [c for c in chunks if c.get("vector")]
    PARTIAL_PATH.write_text(json.dumps(ready, ensure_ascii=False), encoding="utf-8")
    print(f"Сохранено готовых векторов: {len(ready)} → {PARTIAL_PATH}")


def load_reuse(full: bool, out: Path | None = None) -> dict[str, list[float]]:
    """Векторы, которые можно не пересчитывать: ключ — hash текста чанка.

    Источники: прошлый успешный индекс (текст страницы не изменился) и partial
    от оборвавшегося запуска (докат по дневной квоте). При `--out` прошлым
    индексом считается сам файл назначения — иначе повторный прогон в тот же
    файл пересчитывал бы всё заново, хотя рядом лежит готовый результат.
    """
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
                # Размерность сверяем: вектор от другой модели или от другого
                # `dimensions` молча испортил бы всё векторное пространство
                if c.get("hash") and len(c.get("vector") or ()) == EMBED_DIMS
            }
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            print(f"{path.name} не прочитался ({exc}) — пропускаю")
            continue
        print(f"{path.name}: {len(found)} готовых векторов")
        reuse.update(found)
    return reuse


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
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

    chunks = build_chunks()
    if not chunks:
        raise RuntimeError("сайт не отдал ни одного чанка — проверь вёрстку страниц")
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
