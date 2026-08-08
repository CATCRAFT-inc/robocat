"""`!пинг` — список игроков онлайн картинкой в стиле внутриигрового TAB.

Данные берутся одним RCON-соединением на три команды (см. `_collect`), картинка
рисуется Pillow'ом в отдельном потоке. Головы игроков тянутся с minotar.net в
нативном разрешении 8×8 и лежат в дисковом кэше — на повторный `!пинг` сеть не
трогается вовсе.
"""

import asyncio
import io
import logging
import os
import random
import re
import struct
import time
import zlib
from pathlib import Path

import disnake
from disnake.ext import commands
from PIL import Image, ImageDraw, ImageFont

from bot.handlers.rcon import rcon_exec_many
from bot.utils import create_container

logger = logging.getLogger("robocat.ping_tab")

_DATA = Path(__file__).resolve().parent.parent.parent / "data"
FONT_PATH = _DATA / "tab" / "Monocraft.ttf"
LOGO_PATH = _DATA / "tab" / "logo.png"
HEAD_CACHE_DIR = _DATA / "heads"

# Ник в Minecraft — только латиница, цифры и подчёркивание, до 16 символов.
# Всё, что не прошло эту проверку, не попадает НИ в URL головы, НИ в имя файла
# кэша: иначе ник с сервера (в т.ч. подменённый плагином ника) стал бы вектором
# для path traversal и подстановки чужого адреса.
_NICK_RE = re.compile(r"\A[A-Za-z0-9_]{1,16}\Z")

# Ответ `list`: "There are 30 of a max of 747 players online: a, b, c, ..."
_LIST_RE = re.compile(r"There are (\d+) of a max of (\d+) players online")

# Ответ `execute as @a run data get entity @s Pos` — по строке на игрока, слитно:
# "…, 2397.16d]Lykeza has the following entity data: […]".
# 🛑 Имя обязано начинаться сразу после `]` предыдущего значения или в начале
# ответа. Без этого якоря движок сдвигается вправо и отдаёт ХВОСТ чужого
# отображаемого имени как настоящий ник: `[VIP] Notch` → `Notch`,
# `Пикми_moderator` → `_moderator`, ник длиннее 16 → его последние 16 символов.
# Такой выдуманный ник проходит `_NICK_RE` и едет и в URL, и в имя файла кэша.
_ENTITY_RE = re.compile(r"(?:\A|\])([A-Za-z0-9_]{1,16}) has the following entity data:")

# Ответ `tps`: "§6TPS from last 5s, 1m, 5m, 15m: §a20.0§r, …" — берём первое
# число после двоеточия. `*` перед числом Paper ставит для оценочного значения.
# Якорь на слово TPS обязателен: без него подходит любое «двоеточие + число»,
# и `"Server tick: 50ms"` превращался в «ТПС: 50».
_TPS_RE = re.compile(
    r"^\s*TPS\b[^:]{0,40}:\s*\*?(\d+(?:[.,]\d+)?)", re.IGNORECASE | re.MULTILINE,
)
TPS_MAX = 21.0  # 20 — потолок, но Spigot после догона лага печатает 20.02

_COLOR_CODE_RE = re.compile(r"§.", re.S)

HEAD_URL = "https://minotar.net/helm/{nick}/8.png"
HEAD_TTL = 30 * 86400         # месяц. Меньше нет смысла: прогретый кэш должен
                              # переживать деплой и простой, иначе прогрев зря.
                              # Цена — сменивший скин виден старым лицом до месяца.
HEAD_TTL_JITTER = 7 * 86400   # разброс срока годности, см. `_head_ttl`
HEAD_MAX_BYTES = 64 * 1024    # голова 8×8 весит ~100 байт; всё крупнее — не наше
HEAD_MAX_SIDE = 64            # защита от «зип-бомбы»: 64 КБ PNG разжимаются в гигапиксели
HEAD_CONCURRENCY = 8
HEAD_TIMEOUT = 6.0        # на один запрос
HEADS_TOTAL_TIMEOUT = 12.0  # на всю пачку: 80 холодных голов по 8 в ряд иначе
                            # растянутся на минуту, а команда живёт в чате

RCON_TIMEOUT = 6.0        # на ОДНО чтение пакета
# 🛑 Потолок на весь сбор. Без него `timeout` работал попакетно: три команды по
# ~7 чтений давали до 84 сек на попытку и до четырёх минут на три — и всё это
# время был закрыт общий замок снимка, то есть `!пинг` во всех каналах стоял
# в очереди. Замерено на стенде: при номинальном таймауте 1 сек вызов
# возвращался за 5.41.
RCON_TOTAL_BUDGET = 15.0
RCON_ATTEMPTS = 3         # сеть до Майнкрафта подрагивает — молча сдаваться рано
RCON_RETRY_DELAY = 1.5
# Потолок на ВСЕ попытки вместе с паузами между ними. Отдельный от предыдущего:
# тот ограничивает одну попытку, а замок снимка держится всю серию — три полных
# попытки плюс паузы давали под 48 секунд, и это была та же болезнь этажом выше.
RCON_RETRY_BUDGET = 25.0
# Отказ кэшируется отдельно и коротко: иначе каждый ожидающий в очереди
# повторял полный цикл ретраев по лежащему серверу.
FAILURE_TTL = 15.0

# Всё, чем может отвалиться сеть или битый ответ RCON. `struct.error` и
# `ValueError` — это мусор в заголовке пакета: раньше они пролетали мимо
# ретраев прямо в «Что-то сломалось».
RCON_FAILURES = (
    OSError, asyncio.TimeoutError, asyncio.IncompleteReadError, struct.error, ValueError,
)

# Свежесть снимка сервера. Внутри окна повторный `!пинг` отвечает той же
# картинкой мгновенно: и в RCON не идём, и человеку не отказываем.
SNAPSHOT_TTL = 20.0
# Спам-фильтр сторожит уже не цену вызова (её съел кэш), а шум в канале.
SPAM_CALLS = 3
SPAM_WINDOW = 30.0

TEXT_HINT = '-# Чтобы получить список игроков текстом, напиши `!пинг текст`'
TEXT_LIST_LIMIT = 3000  # запас под лимит Components V2 в 4000 символов на сообщение

# --- геометрия макета (в пикселях холста 1×, потом всё умножается на SCALE) ---
SCALE = 2
LOGO_SCALE = 2   # логотип — растр, поэтому увеличиваем ровно вдвое: каждый его
                 # пиксель становится квадратом 2×2 и ничего не мылится
LOGO_MAX_SIDE = 512  # логотип задаёт ширину холста, поэтому у него есть потолок
PAD = 12
FONT_SIZE = 16
LINE_H = 18
NOTICE_FONT_SIZE = FONT_SIZE * 2  # ровно вдвое: пиксельная сетка шрифта не плывёт
ROW_H = 20
HEAD_PX = 16
HEAD_X = 2                       # отступ головы от левого края плашки
NICK_X = HEAD_X + HEAD_PX + 5    # где начинается ник внутри плашки
NICK_PAD = 14                    # запас справа: getlength даёт ширину пера, а
                                 # последний глиф может выйти за неё на пиксель
COL_GAP = 14
ROWS_PER_COL = 20
MAX_COLS = 4
MAX_SHOWN = ROWS_PER_COL * MAX_COLS

# Небо Minecraft: сверху насыщеннее, к горизонту светлеет.
SKY_TOP = (91, 143, 232)
SKY_BOTTOM = (191, 220, 255)
SKY = (SKY_TOP, SKY_BOTTOM)
# Пасхалка на пустом сервере — отсылка к Undertale: большая тёмная комната и
# маленькая кровавая строчка посередине. Фон РОВНЫЙ (обе точки градиента
# одинаковы) и логотипа нет: пустота должна быть пустой.
VOID = ((10, 8, 10), (10, 8, 10))
BLOOD = (190, 22, 22)
EGG_CHANCE = 0.05          # примерно каждый двадцатый пустой сервер
EGG_W, EGG_H = 480, 300    # в координатах 1×, на выходе вдвое больше

EMPTY_NOTICE = "Сейчас на сервере никого нет"
EGG_NOTICE = "Но никто не пришёл..."
# 🛑 Ровно то, что бот ЗНАЕТ: RCON не ответил за три попытки. «Сервер на тех.
# работах» отсюда не следует — сервер может прекрасно работать, а молчать может
# закрытый порт, выключенный RCON или сеть у самого бота. Выдавать свою слепоту
# за факт о сервере нельзя.
OFFLINE_NOTICE = "Не получилось связаться с Кошкокрафтом"
# 🛑 Третье состояние, без которого фича врала: счётчик говорит «онлайн 40», а
# имён нет ни из `execute`, ни из `list`. Пустым сервером это называть НЕЛЬЗЯ —
# молчаливая поломка парсера выглядела бы как штатный ответ и жила бы месяцами.
# Но игроку говорим только то, что знаем честно, — число. Подробности в лог.
UNREADABLE_NOTICE = "Сейчас на Кошкокрафте {total} {word}"
# Сломались ОБА парсера: непонятно даже сколько игроков.
UNKNOWN_NOTICE = "Не получилось узнать, кто на сервере"
# Плашки строк — полупрозрачный чёрный поверх неба, как TAB поверх мира.
PLATE = (0, 0, 0, 120)
TEXT = (255, 255, 255)
MUTED = (206, 206, 214)
GREEN = (85, 255, 85)
YELLOW = (255, 255, 85)
RED = (255, 85, 85)


# ─────────────────────────────── парсеры ───────────────────────────────

def strip_color_codes(raw: str) -> str:
    """Убирает §-коды Minecraft вместе с символом форматирования."""
    return _COLOR_CODE_RE.sub("", raw)


def parse_online_count(raw: str) -> int | None:
    """Сколько игроков онлайн по данным `list`. None — формат не распознан.

    Сам список ников отсюда НЕ берём: сервер режет это сообщение примерно на
    двухстах символах и дописывает «...», из 30 ников доезжает 14.
    """
    match = _LIST_RE.search(strip_color_codes(raw))
    return int(match.group(1)) if match else None


def parse_player_names(raw: str) -> list[str]:
    """Ники из выхлопа `execute as @a run data get entity @s Pos`.

    Порядок ответа — порядок захода на сервер; сортируем без учёта регистра,
    чтобы картинка не прыгала между вызовами.
    """
    names = _ENTITY_RE.findall(strip_color_codes(raw))
    unique = {name: None for name in names}  # дедуп с сохранением порядка
    return sorted(unique, key=str.casefold)


def parse_names_from_list(raw: str, total: int | None) -> list[str]:
    """Запасной источник ников — хвост `list` после двоеточия.

    Основной источник — `execute`; сюда приходим, только когда он не отдал
    имён. Список сервер режет примерно на двухстах символах, поэтому ПОСЛЕДНИЙ
    ник в неполном списке под подозрением: обрыв бывает и посреди слова, а
    обрубок из букв неотличим от настоящего ника — он пройдёт `_NICK_RE`,
    поедет в URL головы и покажется игрокам как несуществующий человек.
    Поэтому при неполном списке хвост отбрасываем.
    """
    clean = strip_color_codes(raw)
    match = _LIST_RE.search(clean)
    if not match:
        return []
    tail = clean[match.end():].lstrip(":").strip()
    if not tail:
        return []
    parts = [part.strip() for part in tail.split(",")]
    if total is None or len(parts) < total:
        parts = parts[:-1]
    return sorted({part for part in parts if _NICK_RE.match(part)}, key=str.casefold)


def _is_incomplete(names: list[str], total: int | None) -> bool:
    """Сбор явно неполный — повод собрать заново.

    Порог именно «меньше половины», а не «меньше счётчика»: между командами
    `execute` и `list` кто-то заходит и выходит, и расхождение на одного-двух
    игроков — норма, из-за которой гонять RCON второй раз незачем.
    """
    return bool(total) and len(names) * 2 < total


def parse_tps(raw: str) -> float | None:
    """Первое (самое свежее) значение TPS из выхлопа `tps`.

    `None` — «не распознали»; лучше честная «?» в шапке, чем правдоподобное
    чужое число, которое покрасится в красное и напугает игроков.
    """
    match = _TPS_RE.search(strip_color_codes(raw))
    if not match:
        return None
    try:
        value = float(match.group(1).replace(",", "."))
    except ValueError:
        return None
    return value if 0 <= value <= TPS_MAX else None


def _players_word(count: int) -> str:
    """«1 игрок», «2 игрока», «5 игроков» — русское склонение по числу."""
    tail_hundred = count % 100
    if 11 <= tail_hundred <= 14:
        return "игроков"
    return {1: "игрок", 2: "игрока", 3: "игрока", 4: "игрока"}.get(count % 10, "игроков")


def _empty_state_notice(total: int | None) -> str:
    """Что сказать, когда имён нет. Три РАЗНЫХ состояния, путать их нельзя:
    сервер пуст, список не читается, ответ вообще не разобран.

    Одна функция на картинку, текстовый режим и alt-текст: раньше разбор был
    скопирован по местам, и alt-текст успел разойтись с картинкой — подпись
    говорила «Игроков: 30», пока на картинке было «список не читается».
    """
    if total is None:
        return UNKNOWN_NOTICE
    if total <= 0:
        return EMPTY_NOTICE
    return UNREADABLE_NOTICE.format(total=total, word=_players_word(total))


def needs_picture(names: list[str], total: int | None) -> bool:
    """Есть ли что рисовать.

    Картинка оправдана только там, где показывает то, чего текстом не покажешь:
    сетку ников с лицами или пустую комнату. Аварийное состояние — это одна
    строка, и плакат с одной строкой хуже простого текста: дольше грузится,
    хуже читается с телефона и выглядит как штатный ответ.
    """
    return bool(names) or (total is not None and total <= 0)


def alt_text(names: list[str], total: int | None, tps: float | None) -> str:
    """Подпись к картинке — она же единственный доступ к содержимому для тех,
    у кого картинки не грузятся или включён скринридер."""
    if not names:
        return _empty_state_notice(total)
    return f"Игроков: {total}, ТПС: {tps:.1f}" if tps is not None else f"Игроков: {total}, ТПС: ?"


def _players_text(names: list[str], total: int | None, tps: float | None) -> str:
    """Тот же список, но текстом — для тех, у кого не грузятся картинки."""
    if not names:
        return _empty_state_notice(total)

    listed: list[str] = []
    length = 0
    for nick in names:
        entry = f"`{nick}`"
        if length + len(entry) + 2 > TEXT_LIST_LIMIT:
            break
        listed.append(entry)
        length += len(entry) + 2

    body = f"**ТПС:** {tps:.1f}\n\n" if tps is not None else ""
    body += ", ".join(listed)
    hidden = (total or 0) - len(listed)
    if hidden > 0:
        body += f"\n\n-# …и ещё {hidden}"
    return body


def tps_color(tps: float) -> tuple[int, int, int]:
    if tps >= 18.0:
        return GREEN
    if tps >= 15.0:
        return YELLOW
    return RED


# ─────────────────────────────── головы ───────────────────────────────

def _decode_head(data: bytes) -> Image.Image | None:
    """Байты → RGBA-картинка головы. Любой мусор и переросток отбрасываются."""
    try:
        image = Image.open(io.BytesIO(data))
        # Размер читается из заголовка ДО распаковки пикселей — иначе
        # сильно сжатый PNG развернётся в память целиком.
        if image.width > HEAD_MAX_SIDE or image.height > HEAD_MAX_SIDE:
            logger.warning("Голова отброшена: подозрительный размер %sx%s", image.width, image.height)
            return None
        return image.convert("RGBA")
    except Exception:
        logger.debug("Голова не распозналась как картинка", exc_info=True)
        return None


def _head_ttl(nick: str) -> float:
    """Срок годности головы, разный для разных ников.

    Прогрев кладёт все файлы за секунды, поэтому с общим TTL они протухают
    одной минутой — ровно через месяц первый же `!пинг` уходит выкачивать
    восемьдесят голов залпом. Разброс детерминированный (от самого ника),
    иначе одна и та же голова прыгала бы между «свежей» и «протухшей».
    """
    spread = zlib.crc32(nick.encode()) % HEAD_TTL_JITTER
    return HEAD_TTL + spread


def _cached_head(nick: str) -> Image.Image | None:
    path = HEAD_CACHE_DIR / f"{nick}.png"
    try:
        if not path.is_file() or time.time() - path.stat().st_mtime > _head_ttl(nick):
            return None
        return _decode_head(path.read_bytes())
    except OSError:
        logger.debug("Не прочитался кэш головы %s", nick, exc_info=True)
        return None


def _store_head(nick: str, data: bytes) -> None:
    try:
        HEAD_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        # Имя временного файла уникально на процесс: `warm_heads.py` может
        # писать в тот же каталог рядом с живым ботом, и общий `.tmp` они
        # затирали бы друг другу прямо под `os.replace`.
        tmp = HEAD_CACHE_DIR / f"{nick}.{os.getpid()}.tmp"
        tmp.write_bytes(data)
        os.replace(tmp, HEAD_CACHE_DIR / f"{nick}.png")
    except OSError:
        logger.warning("Не записался кэш головы %s — проверь права на %s",
                       nick, HEAD_CACHE_DIR)


def _read_cached_heads(nicks: list[str]) -> tuple[dict[str, Image.Image], list[str]]:
    """Что нашлось в кэше и чего не хватает. Синхронно — зовётся из потока."""
    found: dict[str, Image.Image] = {}
    missing: list[str] = []
    for nick in nicks:
        head = _cached_head(nick)
        if head is None:
            missing.append(nick)
        else:
            found[nick] = head
    return found, missing


def _decode_and_store(nick: str, data: bytes) -> Image.Image | None:
    head = _decode_head(data)
    if head is not None:
        _store_head(nick, data)
    return head


async def _download_head(session, nick: str) -> Image.Image | None:
    import aiohttp  # транзитивная зависимость disnake, импорт остаётся ленивым

    try:
        async with session.get(HEAD_URL.format(nick=nick)) as response:
            if response.status != 200:
                logger.debug("minotar вернул %s на голову %s", response.status, nick)
                return None
            data = await response.content.read(HEAD_MAX_BYTES + 1)
            if len(data) > HEAD_MAX_BYTES:
                logger.warning("Голова %s больше %s байт — отброшена", nick, HEAD_MAX_BYTES)
                return None
    except (aiohttp.ClientError, asyncio.TimeoutError):
        logger.debug("minotar недоступен для ника %s", nick, exc_info=True)
        return None

    # Декод и запись на диск — блокирующие, в цикле событий им не место.
    return await asyncio.to_thread(_decode_and_store, nick, data)


async def _drain(tasks) -> None:
    """Отменить качалки и ДОЖДАТЬСЯ их.

    Без ожидания `async with` закрывает сессию прямо под ещё живыми задачами:
    в лог сыплется «Task was destroyed but it is pending», а соединения
    остаются незакрытыми.
    """
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def fetch_heads(nicks: list[str]) -> dict[str, Image.Image]:
    """Головы для ников: сначала кэш, потом сеть. Сбой сети — просто нет головы."""
    import aiohttp  # транзитивная зависимость disnake, импорт остаётся ленивым

    valid = [nick for nick in nicks if _NICK_RE.match(nick)]
    if not valid:
        return {}

    # Чтение кэша — это stat + read + декод PNG на каждый из 80 ников;
    # в цикле событий это блокировка, которую ловит watchdog «Heartbeat blocked».
    heads, to_download = await asyncio.to_thread(_read_cached_heads, valid)
    if not to_download:
        return heads

    timeout = aiohttp.ClientTimeout(total=HEAD_TIMEOUT)
    # Параллелизм ограничивает сам коннектор — свой семафор для этого не нужен.
    connector = aiohttp.TCPConnector(limit=HEAD_CONCURRENCY)
    try:
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            tasks = {
                asyncio.create_task(_download_head(session, nick)): nick
                for nick in to_download
            }
            # `wait` вместо `gather`: по общему таймауту успевшие головы
            # остаются с нами, а не отменяются вместе с опоздавшими.
            # 🛑 Но `wait`, в отличие от `gather`, НЕ передаёт отмену детям и не
            # снимает опоздавших — поэтому уборка в `finally` на оба выхода:
            # иначе `async with` закрывает сессию под живыми задачами.
            try:
                done, _ = await asyncio.wait(tasks, timeout=HEADS_TOTAL_TIMEOUT)
                for task in done:
                    nick = tasks[task]
                    try:
                        head = task.result()
                    except Exception:
                        logger.debug("Голова %s не приехала", nick, exc_info=True)
                        continue
                    if head is not None:
                        heads[nick] = head
            finally:
                await _drain(tasks)  # завершённым `cancel()` уже ничего не делает
    except (aiohttp.ClientError, asyncio.TimeoutError):
        logger.warning("Сервис голов недоступен — рисую что есть")

    if len(heads) < len(valid):
        logger.info("Головы: %s из %s", len(heads), len(valid))
    return heads


# ─────────────────────────────── рендер ───────────────────────────────

def _layout(count: int) -> tuple[int, int]:
    """(колонок, строк в колонке) — как в игре: колонка растёт до 20 строк."""
    if count <= 0:
        return 1, 0
    columns = min(MAX_COLS, -(-count // ROWS_PER_COL))  # деление вверх
    return columns, -(-count // columns)


def _gradient(width: int, height: int, background: tuple) -> Image.Image:
    """Вертикальный градиент из пары цветов «сверху, снизу»."""
    top_color, bottom_color = background
    if top_color == bottom_color:
        return Image.new("RGBA", (width, height), top_color + (255,))
    # Считаем один столбец пикселей и растягиваем его вширь — вместо
    # питоновского цикла по каждой строке холста.
    column = Image.new("RGBA", (1, height))
    column.putdata([
        tuple(
            int(top + (bottom - top) * y / (height - 1))
            for top, bottom in zip(top_color, bottom_color)
        ) + (255,)
        for y in range(height)
    ])
    return column.resize((width, height))


def _load_logo() -> Image.Image | None:
    """Логотип, увеличенный целым числом раз. Нет или битый — рисуем без него.

    Замысел «нет логотипа → просто без логотипа» раньше работал только для
    отсутствующего файла: повреждённый ронял рендер, и `!пинг` навсегда
    отвечал «Что-то сломалось».
    """
    try:
        raw = Image.open(LOGO_PATH).convert("RGBA")
    except (OSError, ValueError):
        logger.warning("Логотип %s не читается — рисую без него", LOGO_PATH)
        return None
    if raw.width > LOGO_MAX_SIDE or raw.height > LOGO_MAX_SIDE:
        # Логотип задаёт ширину холста: замена «на покрасивее» в 4× молча
        # раздувала картинку с одним игроком до двух мегапикселей.
        logger.warning("Логотип %sx%s слишком большой — рисую без него", raw.width, raw.height)
        return None
    return raw.resize((raw.width * LOGO_SCALE, raw.height * LOGO_SCALE), Image.NEAREST)


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    """Шрифт из ассетов; если его нет — штатный, лишь бы команда ответила."""
    try:
        return ImageFont.truetype(str(FONT_PATH), size)
    except OSError:
        logger.error("Шрифт %s не читается — картинка будет без пиксельного стиля", FONT_PATH)
        return ImageFont.load_default(size)


def _shadowed(draw: ImageDraw.ImageDraw, xy, text: str, font, fill, **kwargs) -> None:
    """Текст с тенью на пиксель вправо-вниз — как рисует шрифт сам Minecraft."""
    x, y = xy
    draw.text((x + 1, y + 1), text, font=font, fill=tuple(c // 4 for c in fill), **kwargs)
    draw.text((x, y), text, font=font, fill=fill, **kwargs)


def _png(canvas: Image.Image) -> bytes:
    buffer = io.BytesIO()
    canvas.convert("RGB").save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def render_notice(text: str) -> bytes:
    """Плакат вместо списка: логотип и одна крупная строка на небе.

    Остался ровно один повод его рисовать — пустой сервер. Аварийные состояния
    ушли в текст, и вместе с ними ушли параметры цвета, фона и логотипа: у
    каждого оставалось по одному значению.
    """
    font = _load_font(NOTICE_FONT_SIZE)
    logo = _load_logo()

    text_w = int(font.getlength(text))
    logo_w = logo.width / SCALE if logo else 0
    logo_h = logo.height / SCALE if logo else 0
    gap = 18 if logo else 0

    width = int(max(text_w, logo_w)) + PAD * 4
    height = int(PAD + logo_h + gap + NOTICE_FONT_SIZE + PAD * 2)

    canvas = _gradient(width, height, SKY)
    draw = ImageDraw.Draw(canvas)
    draw.fontmode = "1"
    _shadowed(draw, (width / 2, PAD + logo_h + gap), text, font, TEXT, anchor="ma")

    canvas = canvas.resize((width * SCALE, height * SCALE), Image.NEAREST)
    if logo:
        canvas.alpha_composite(logo, (int((canvas.width - logo.width) / 2), PAD * SCALE))

    return _png(canvas)


def render_empty() -> bytes:
    """Пустой сервер. Изредка вместо штатного плаката выпадает пасхалка."""
    if random.random() < EGG_CHANCE:
        return render_egg()
    return render_notice(EMPTY_NOTICE)


def render_egg() -> bytes:
    """Большая тёмная пустота с маленькой строчкой посередине (Undertale).

    Размер холста фиксированный и НЕ зависит от длины текста, в отличие от
    `render_notice`: весь смысл картинки — в том, сколько вокруг надписи пусто.
    """
    font = _load_font(FONT_SIZE)
    canvas = _gradient(EGG_W, EGG_H, VOID)
    draw = ImageDraw.Draw(canvas)
    draw.fontmode = "1"
    _shadowed(draw, (EGG_W / 2, EGG_H / 2), EGG_NOTICE, font, BLOOD, anchor="mm")
    return _png(canvas.resize((EGG_W * SCALE, EGG_H * SCALE), Image.NEAREST))


def render_tab(
    names: list[str],
    total_online: int | None,
    tps: float | None,
    heads: dict[str, Image.Image] | None = None,
) -> bytes:
    """Рисует TAB-картинку и отдаёт PNG байтами. Сеть не трогает."""
    heads = heads or {}
    shown = names[:MAX_SHOWN]
    hidden = max(0, (total_online or 0) - len(shown))

    if not shown:
        # Сюда доходит только ЧЕСТНО пустой сервер: аварийные состояния
        # (имён нет, а счётчик говорит про сорок) картинкой не рисуются вовсе —
        # плакат с одной строкой это тот же текст, только тяжелее и с видом
        # штатного ответа. Их отсекает `needs_picture` в коге.
        return render_empty()

    font = _load_font(FONT_SIZE)
    logo = _load_logo()

    columns, rows = _layout(len(shown))
    column_names = [shown[i * rows:(i + 1) * rows] for i in range(columns)]
    # Все колонки одной ширины — по самому длинному нику из всех, как в игре.
    column_w = NICK_X + max((int(font.getlength(name)) for name in shown), default=0) + NICK_PAD

    header = f"Игроков: {total_online}"
    separator = " | "
    tps_label = "ТПС: "
    tps_value = f"{tps:.1f}" if tps is not None else "?"
    header_w = int(font.getlength(header + separator + tps_label + tps_value))

    footer = f"и ещё {hidden}" if hidden else ""
    footer_w = int(font.getlength(footer)) if footer else 0

    grid_w = column_w * columns + COL_GAP * (columns - 1)
    # Логотип вставляется нативным размером поверх увеличенного холста, поэтому
    # в координатах 1× он занимает ровно половину своих пикселей.
    logo_w = logo.width / SCALE if logo else 0
    logo_h = logo.height / SCALE if logo else 0

    content_w = max(grid_w, header_w, footer_w, logo_w)
    width = int(content_w) + PAD * 2
    height = int(
        PAD + logo_h + (8 if logo else 0) + LINE_H + 10
        + rows * ROW_H
        + (LINE_H + 4 if footer else 0)
        + PAD
    )

    canvas = _gradient(width, height, SKY)

    # Плашки рисуются на отдельном слое: ImageDraw по RGBA ЗАМЕНЯЕТ альфу, а не
    # смешивает — прямо по небу полупрозрачный чёрный дал бы дыру, а не стекло.
    y = PAD + logo_h + (8 if logo else 0) + LINE_H + 10
    grid_x = (width - grid_w) / 2
    plate_h = ROW_H - 3
    plates = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    plates_draw = ImageDraw.Draw(plates)
    for column_index, column in enumerate(column_names):
        column_x = grid_x + (column_w + COL_GAP) * column_index
        for row_index, _ in enumerate(column):
            row_y = y + row_index * ROW_H
            plates_draw.rectangle(
                [column_x, row_y, column_x + column_w - 1, row_y + plate_h], fill=PLATE
            )
    canvas = Image.alpha_composite(canvas, plates)

    draw = ImageDraw.Draw(canvas)
    draw.fontmode = "1"  # без сглаживания — иначе пиксельный шрифт «мылится»

    # Шапка: «Игроков: N | ТПС: X», TPS покрашен как в игре.
    header_y = PAD + logo_h + (8 if logo else 0)
    x = (width - header_w) / 2
    for chunk, color in (
        (header, TEXT),
        (separator, MUTED),
        (tps_label, TEXT),
        (tps_value, tps_color(tps) if tps is not None else MUTED),
    ):
        _shadowed(draw, (x, header_y), chunk, font, color)
        x += font.getlength(chunk)

    for column_index, column in enumerate(column_names):
        column_x = grid_x + (column_w + COL_GAP) * column_index
        for row_index, nick in enumerate(column):
            row_y = y + row_index * ROW_H
            middle = row_y + plate_h / 2
            head = heads.get(nick)
            if head is not None:
                scaled = head.resize((HEAD_PX, HEAD_PX), Image.NEAREST)
                canvas.paste(scaled, (int(column_x + HEAD_X), int(middle - HEAD_PX / 2)), scaled)
            _shadowed(draw, (column_x + NICK_X, middle), nick, font, TEXT, anchor="lm")

    if footer:
        _shadowed(
            draw, ((width - footer_w) / 2, y + rows * ROW_H + 4), footer, font, MUTED
        )

    canvas = canvas.resize((width * SCALE, height * SCALE), Image.NEAREST)

    if logo:
        canvas.alpha_composite(logo, (int((canvas.width - logo.width) / 2), PAD * SCALE))

    return _png(canvas)


# ─────────────────────────────── ког ───────────────────────────────

# Снимок сервера общий на всех: игроки на Кошкокрафте одни и те же в любом
# канале. Замок гасит «стадо»: пока первый вызов ходит в RCON, остальные ждут
# его результат, а не идут туда же следом.
_snapshot: dict | None = None
_failure_at: float | None = None
# С Python 3.10 `asyncio.Lock()` не привязывается к циклу событий при создании,
# поэтому модульного замка достаточно. Ленивое создание тут было бессмысленным:
# `reload_extension` исполняет модуль заново, и «ленивый» замок точно так же
# получается новым — от рассинхрона старых и новых вызовов оно не спасало.
_snapshot_lock = asyncio.Lock()


def _attach(png: bytes, description: str) -> disnake.File:
    """Вложение с описанием: вся полезная нагрузка команды живёт в картинке,
    и без alt-текста она недоступна скринридеру и тем, у кого картинки off."""
    return disnake.File(io.BytesIO(png), filename="tab.png", description=description[:1000])


class PingTab(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.host = os.getenv("RCON_HOST", "")
        self.port = int(os.getenv("RCON_PORT", "25575"))
        self.password = os.getenv("RCON_PASSWORD", "")

    async def _collect(self, budget: float | None = None) -> tuple[list[str], int | None, float | None]:
        """Одно соединение — три команды: имена, счётчик, TPS.

        Весь сбор ограничен сверху `RCON_TOTAL_BUDGET`: попакетный таймаут
        такой границы не даёт, а замок снимка ждёт именно этот вызов.
        """
        raw_names, raw_list, raw_tps = await asyncio.wait_for(
            rcon_exec_many(
                self.host,
                self.port,
                self.password,
                [
                    # `list` режется сервером на ~200 символах, поэтому полный
                    # список ников собираем построчно: каждая строка —
                    # отдельное сообщение, под обрезку не попадает. `Pos` есть
                    # у любой сущности, а закрывающая `]` отделяет значение от
                    # имени следующего игрока в слитом ответе.
                    "execute as @a run data get entity @s Pos",
                    "list",
                    "tps",
                ],
                timeout=RCON_TIMEOUT,
            ),
            # Значение по умолчанию берётся ЗДЕСЬ, а не в сигнатуре: аргумент
            # по умолчанию замораживается на импорте, и подмена константы
            # (тестом или конфигом) молча ни на что не влияла бы.
            timeout=RCON_TOTAL_BUDGET if budget is None else budget,
        )
        names = parse_player_names(raw_names)
        count = parse_online_count(raw_list)
        if count is None:
            # Формат `list` сменился — счётчик теперь наш собственный, а не
            # серверный. Без лога такая подмена источника невидима.
            logger.warning("Пинг: не распознал счётчик из `list`: %r", raw_list[:120])
        if count is None and not names:
            # 🛑 Сломались ОБА парсера. `max(count or 0, 0)` схлопывал это в
            # ноль, и «сервер пуст» показывался снова — дыра, ради которой
            # заводили третье состояние, была закрыта лишь наполовину.
            logger.error("Пинг: ни счётчик, ни имена не распознаны — формат ответа сменился")
            return [], None, parse_tps(raw_tps)
        if not names and count:
            # Основной источник имён молчит, а игроки есть. Показать неполный
            # список лучше, чем никакого: обрезанный `list` доносит ~14 ников
            # из 40, а остальные честно уедут в «и ещё N».
            names = parse_names_from_list(raw_list, count)
            logger.error(
                "Пинг: сервер говорит про %s игроков, имён из `execute` нет — "
                "взял %s из `list`. Ответ был: %r",
                count, len(names), raw_names[:200],
            )
        total = max(count or 0, len(names))
        return names, total, parse_tps(raw_tps)

    async def _collect_with_retries(self, ctx: commands.Context, status: list):
        """Собирает данные, переживая моргание сети. Отчитывается в тот же чат.

        `status` — список-держатель служебного сообщения. Именно список, а не
        возвращаемое значение: при исключении внутри сбора возврата не будет
        вовсе, и ссылка на сообщение потерялась бы — оно осталось бы висеть
        в канале навсегда рядом с «Что-то сломалось».
        """
        deadline = time.monotonic() + RCON_RETRY_BUDGET
        # Неполный сбор придерживаем: если повтор не поможет, отдадим его —
        # четырнадцать ников лучше, чем отказ.
        incomplete = None
        for attempt in range(1, RCON_ATTEMPTS + 1):
            left = deadline - time.monotonic()
            if left <= 0:
                logger.warning("Пинг: бюджет на попытки исчерпан, сдаюсь")
                break
            try:
                collected = await self._collect(min(RCON_TOTAL_BUDGET, left))
            except PermissionError:
                logger.error("Пинг: RCON не пустил — неверный пароль")
                break
            except RCON_FAILURES as error:
                # В тексте ошибки бывает адрес сервера из окружения — в лог
                # уходит только тип, наружу не уходит ничего.
                logger.warning(
                    "Пинг: RCON недоступен, попытка %s из %s (%s)",
                    attempt, RCON_ATTEMPTS, type(error).__name__,
                )
                if attempt == RCON_ATTEMPTS:
                    break
                await self._say(ctx, status, "Не получилось, пытаюсь ещё раз…")
                await asyncio.sleep(RCON_RETRY_DELAY)
                continue

            names, total, _ = collected
            if _is_incomplete(names, total) and incomplete is None and attempt < RCON_ATTEMPTS:
                # Счётчик говорит про сорок игроков, а имён меньше половины —
                # это сбой сбора, а не картина мира. Молча пробуем ещё раз:
                # человеку про кухню бота знать незачем.
                logger.warning(
                    "Пинг: имён %s при счётчике %s — собираю заново", len(names), total
                )
                incomplete = collected
                continue
            return collected

        return incomplete

    @staticmethod
    async def _say(ctx: commands.Context, status: list, text: str) -> None:
        """Первый раз отвечает, дальше правит то же сообщение — чтобы не спамить."""
        try:
            if status:
                await status[0].edit(content=text)
                return
            message = await ctx.reply(text)
            if message is not None:
                status.append(message)
        except disnake.HTTPException:
            logger.warning("Пинг: не получилось написать в канал", exc_info=True)

    async def _fresh_snapshot(self, ctx: commands.Context, need_png: bool, status: list):
        """Снимок сервера: свежий из кэша либо новый поход в RCON.

        `None` — сервер так и не ответил. Под замком делается ВСЯ дорогая
        работа, включая выкачку голов и рендер: иначе пять одновременных
        вызовов давали один поход в RCON, но пять выкачек по 80 голов — залп
        в четыреста запросов к стороннему сервису с одного адреса.
        """
        global _snapshot, _failure_at
        async with _snapshot_lock:
            now = time.monotonic()
            fresh = _snapshot is not None and now - _snapshot["at"] < SNAPSHOT_TTL

            if not fresh:
                if _failure_at is not None and now - _failure_at < FAILURE_TTL:
                    # Сервер только что не ответил — не гоняем по нему всю
                    # очередь ожидающих ещё раз, каждого по три попытки.
                    return None

                collected = await self._collect_with_retries(ctx, status)
                if collected is None:
                    _failure_at = time.monotonic()
                    return None

                names, total, tps = collected
                _failure_at = None
                # Время берётся ПОСЛЕ сбора: сбор бывает долгим, и снимок,
                # помеченный временем начала, рождался уже протухшим.
                _snapshot = {
                    "at": time.monotonic(),
                    "names": names, "total": total, "tps": tps, "png": None,
                }

            # Картинка рисуется лениво: текстовому режиму она не нужна вовсе,
            # а аварийному состоянию не нужна никому — оно уходит текстом.
            if need_png and _snapshot["png"] is None and needs_picture(
                _snapshot["names"], _snapshot["total"]
            ):
                heads = await fetch_heads(_snapshot["names"][:MAX_SHOWN])
                _snapshot["png"] = await asyncio.to_thread(
                    render_tab, _snapshot["names"], _snapshot["total"],
                    _snapshot["tps"], heads,
                )
            return _snapshot

    @commands.command(name="пинг", description="Кто сейчас на сервере", aliases=["ping", "gbyu", "зштп"])
    @commands.cooldown(SPAM_CALLS, SPAM_WINDOW, commands.BucketType.channel)
    async def pingPlayers(self, ctx: commands.Context, *, mode: str = ""):
        if not self.host or not self.password:
            await ctx.reply(components=create_container(
                "## Пинг не настроен", "Нет `RCON_HOST` или `RCON_PASSWORD` в окружении бота."
            ))
            return

        as_text = mode.strip().lower() in ("текст", "текстом", "text")
        status: list = []

        try:
            async with ctx.typing():
                snapshot = await self._fresh_snapshot(ctx, not as_text, status)

                if snapshot is None:
                    # RCON не ответил. Это всё, что бот знает, — и говорит он
                    # ровно это, без картинки и без догадок про сервер.
                    answer = dict(components=create_container(
                        f"## {OFFLINE_NOTICE}", "Попробуй через минутку.",
                    ))
                elif not snapshot["names"]:
                    # Показывать нечего: либо честно пусто, либо сбор развалился.
                    # Пустой сервер — картинка (там она к месту и там пасхалка),
                    # остальное — одна строка текстом.
                    notice = _empty_state_notice(snapshot["total"])
                    if snapshot["png"] is not None:
                        answer = dict(file=_attach(snapshot["png"], notice))
                    else:
                        answer = dict(components=create_container(
                            f"## {notice}", "Список сейчас не показать, попробуй через минутку.",
                        ))
                elif as_text:
                    total = snapshot["total"]
                    answer = dict(components=create_container(
                        f"## На Кошкокрафте {total} {_players_word(total)}",
                        _players_text(snapshot["names"], total, snapshot["tps"]),
                    ))
                else:
                    answer = dict(
                        content=TEXT_HINT,
                        file=_attach(
                            snapshot["png"],
                            alt_text(snapshot["names"], snapshot["total"], snapshot["tps"]),
                        ),
                    )
            await ctx.reply(**answer)
        finally:
            # Уборка обязана случиться на ЛЮБОМ выходе: при исключении в
            # рендере служебное «пытаюсь ещё раз» оставалось висеть навсегда
            # рядом с «Что-то сломалось».
            for message in status:
                try:
                    await message.delete()
                except disnake.HTTPException:
                    logger.debug("Пинг: служебное сообщение уже удалено", exc_info=True)

    async def cog_command_error(self, ctx: commands.Context, error: Exception):
        if isinstance(error, commands.CommandOnCooldown):
            # Отвечать текстом на спам — значит спамить в ответ. Ставим значок
            # на само сообщение: видно, что бот услышал, а канал чистый.
            try:
                await ctx.message.add_reaction("⏳")
                return
            except disnake.HTTPException:
                # Нет права на реакции — молчать нельзя, иначе человек не
                # получает вообще ничего и считает бота сломанным.
                logger.debug("Пинг: не получилось поставить реакцию", exc_info=True)
            try:
                await ctx.reply(
                    f"Уже смотрел, подожди {error.retry_after:.0f} сек.", delete_after=10
                )
            except disnake.HTTPException:
                logger.warning("Пинг: не получилось ответить на кулдаун", exc_info=True)
            return

        logger.exception("Пинг упал", exc_info=error)
        answer = dict(components=create_container(
            "## Что-то сломалось", "Картинка не нарисовалась. Админы уже видят это в логах."
        ))
        try:
            await ctx.reply(**answer)
        except disnake.HTTPException:
            # Сообщение удалили, канал закрыли, Discord отдал 5xx — отчитаться
            # уже некуда, но падать в обработчике ошибок точно не надо.
            logger.warning("Пинг: не получилось доложить об ошибке в канал", exc_info=True)


def setup(bot: commands.Bot):
    bot.add_cog(PingTab(bot))
    logger.info("Ког PingTab загружен")
