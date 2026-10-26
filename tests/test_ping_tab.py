"""Тесты `!пинг` — парсеры RCON, безопасность голов и раскладка картинки.

Все фикстуры — РЕАЛЬНЫЙ выхлоп боевого сервера, снятый 08.08.2026 через
`/rcon` (скрины в задаче). Сеть тесты не трогают.
"""

import asyncio
import contextlib
import io
import json
import os
import struct
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import disnake
import pytest
from disnake.ext import commands
from PIL import Image, ImageFont

from bot.assets import DEFAULT_FONT_PATH


class _FakeHTTPResponse:
    """Минимум, который disnake.HTTPException читает из ответа."""
    status = 403
    reason = "Forbidden"

from bot.discord_config import Guilds
from bot.handlers import ping_tab
from bot.handlers.ping_tab import (
    BLOOD,
    EGG_H,
    EGG_NOTICE,
    EGG_W,
    EMPTY_NOTICE,
    GREEN,
    HEAD_TTL,
    HEAD_TTL_JITTER,
    MAX_SHOWN,
    OFFLINE_NOTICE,
    ROWS_PER_COL,
    RED,
    SCALE,
    SKY,
    SKY_TOP,
    VOID,
    YELLOW,
    _empty_state_notice,
    alt_text,
    render_notice,
    _decode_head,
    _download_head,
    _head_ttl,
    _layout,
    _players_text,
    _players_word,
    fetch_heads,
    needs_picture,
    parse_online_count,
    parse_names_from_list,
    parse_player_names,
    parse_tps,
    render_tab,
    strip_color_codes,
    tps_color,
)
from bot.handlers.rcon import rcon_exec_many


@pytest.fixture(autouse=True)
def _server_open(monkeypatch):
    """Старый RCON-путь проверяем с выключенным флагом закрытия.

    Живой ответ `:(` проверяет отдельный тест ниже. Так парсеры, рендер и
    ретраи остаются покрытыми на случай, если сервер когда-нибудь откроют.
    """
    monkeypatch.setattr("bot.handlers.ping_tab.SERVER_CLOSED", False)


# ── реальный выхлоп сервера ──────────────────────────────────────────────

# `list` режется сервером примерно на 200 символах — из 30 ников доезжает 14.
RAW_LIST = (
    "There are 30 of a max of 747 players online: vxpbuser, Lykeza, Chashi_, "
    "raskolfo, avokato_AMF, serfque, jeaas, SergeyTon, ay11oo, vlada_wavy, "
    "FoxiKsiK, Decorector, Endermen1250, ytrbqdkflbvbhy, ..."
)

# Тот же `list`, но согласованный с RAW_POS по числу игроков: коговым тестам
# нужен ШТАТНЫЙ сбор, а расхождение «3 имени при счётчике 30» бот теперь
# справедливо считает сбоем и переспрашивает сервер.
RAW_LIST_3 = "There are 3 of a max of 747 players online: vxpbuser, Lykeza, Chashi_"

# `execute as @a run data get entity @s Pos` — сообщения склеены без разделителя.
RAW_POS = (
    "vxpbuser has the following entity data: [784.6307279256314d, 12.0d, "
    "2397.1667040871002d]Lykeza has the following entity data: "
    "[1322.303329285583d, 64.0d, -1960.699999988079d]Chashi_ has the following "
    "entity data: [79.68553241120935d, 92.0d, -46.66415345125706d]"
)

RAW_TPS = "§6TPS from last 5s, 1m, 5m, 15m: §a20.0§r, §a20.0§r, §a20.0§r, §a20.0"


# ── парсеры ──────────────────────────────────────────────────────────────

def test_parse_online_count_reads_counter_from_truncated_list():
    assert parse_online_count(RAW_LIST) == 30


def test_parse_online_count_returns_none_on_garbage():
    assert parse_online_count("Unknown command") is None


def test_parse_player_names_splits_glued_messages():
    # Главная ловушка: имя следующего игрока прилипает к `]` предыдущего.
    assert parse_player_names(RAW_POS) == ["Chashi_", "Lykeza", "vxpbuser"]


def test_parse_player_names_ignores_non_latin_display_names():
    raw = (
        "vxpbuser has the following entity data: [1.0d]"
        "Пикми_модератор has the following entity data: [2.0d]"
    )
    assert parse_player_names(raw) == ["vxpbuser"]


@pytest.mark.parametrize("display_name", [
    "[VIP] Notch",            # префикс от плагина — раньше давало «Notch»
    "Пикми_moderator",        # смешанное имя — раньше давало «_moderator»
    "SuperLongNickname19",    # длиннее 16 — раньше давало последние 16 символов
    "Notch the Great",        # пробелы — раньше давало «Great»
])
def test_parse_player_names_never_invents_a_nick_from_a_tail(display_name):
    """Хвост чужого имени не должен становиться «настоящим ником».

    Выдуманный ник проходил валидацию и ехал в URL головы, в имя файла кэша и
    в картинку — вплоть до чужого лица под обрезанным именем.
    """
    raw = f"{display_name} has the following entity data: [1.0d]"
    assert parse_player_names(raw) == []


def test_parse_player_names_still_reads_a_glued_tail_after_bracket():
    """Якорь не должен ломать штатный случай — имена склеены через `]`."""
    raw = (
        "vxpbuser has the following entity data: [1.0d, 2.0d]"
        "Lykeza has the following entity data: [3.0d]"
    )
    assert parse_player_names(raw) == ["Lykeza", "vxpbuser"]


def test_parse_player_names_deduplicates_and_sorts_case_insensitively():
    raw = "".join(
        f"{nick} has the following entity data: [1.0d]"
        for nick in ("bob", "Alice", "bob", "aaron")
    )
    assert parse_player_names(raw) == ["aaron", "Alice", "bob"]


def test_parse_player_names_empty_when_nobody_online():
    assert parse_player_names("No entity was found") == []


def test_parse_tps_strips_color_codes_and_takes_freshest_value():
    assert parse_tps(RAW_TPS) == 20.0


def test_parse_tps_accepts_paper_estimate_marker():
    assert parse_tps("§6TPS from last 1m, 5m, 15m: §a*19.87§r, §a20.0") == 19.87


def test_parse_tps_returns_none_when_command_unknown():
    assert parse_tps("Unknown or incomplete command") is None


@pytest.mark.parametrize("raw", [
    "Server tick: 50ms",                                  # чужое «двоеточие + число»
    "There are 30 of a max of 747 players online: 5abc",  # ответ не той команды
    "[12:30:05 INFO]: 19.5",                              # время в строке лога
    "Lag detected: 3 players affected",
])
def test_parse_tps_refuses_numbers_that_are_not_tps(raw):
    """Правдоподобное чужое число хуже честного «?»: оно ещё и красится в красное."""
    assert parse_tps(raw) is None


def test_parse_tps_refuses_impossible_values():
    assert parse_tps("TPS from last 1m: 300.0") is None


def test_strip_color_codes_drops_code_and_its_letter():
    assert strip_color_codes("§aзелёный§r§lжирный") == "зелёныйжирный"


def test_tps_color_marks_health_like_the_game():
    assert tps_color(20.0) == GREEN
    assert tps_color(18.0) == GREEN
    assert tps_color(17.9) == YELLOW
    assert tps_color(15.0) == YELLOW
    assert tps_color(14.9) == RED


# ── текстовый режим ──────────────────────────────────────────────────────

@pytest.mark.parametrize("count, word", [
    (0, "игроков"), (1, "игрок"), (2, "игрока"), (4, "игрока"), (5, "игроков"),
    (11, "игроков"), (12, "игроков"), (14, "игроков"), (21, "игрок"), (22, "игрока"),
    (24, "игрока"), (25, "игроков"), (101, "игрок"), (111, "игроков"),
])
def test_players_word_declines_by_russian_rules(count, word):
    assert _players_word(count) == word


def test_players_text_lists_everyone_with_tps():
    body = _players_text(["vxpbuser", "Lykeza"], 2, 20.0)
    assert "`vxpbuser`" in body and "`Lykeza`" in body
    assert "20.0" in body
    assert "ещё" not in body


def test_players_text_reports_the_rest_when_counter_is_bigger():
    assert "…и ещё 5" in _players_text(["vxpbuser"], 6, 20.0)


def test_players_text_stays_under_component_limit():
    many = [f"player{i:04d}" for i in range(400)]
    body = _players_text(many, 400, 20.0)
    assert len(body) < 4000
    assert "ещё" in body, "обрезанный хвост обязан быть назван"


def test_players_text_when_nobody_online():
    assert _players_text([], 0, 20.0) == EMPTY_NOTICE


def test_notice_shown_to_players_never_reports_the_bots_own_breakage():
    """Игроку — число, а не отчёт бота о своей поломке.

    Скрин 08.08: «Игроков: 40, но список не читается» в общем канале. Причина
    была настоящая (ответы RCON разъехались), но игроку от этого ни тепло, ни
    холодно — подробности живут в логе.
    """
    for notice in (_empty_state_notice(40), _empty_state_notice(None)):
        assert "не читается" not in notice
        assert "разобрал" not in notice and "парс" not in notice.lower()
    assert "40" in _empty_state_notice(40), "число игроков всё равно надо сказать"


def test_players_text_never_claims_empty_while_counter_says_otherwise():
    """Счётчик 30 и ноль распознанных имён — это сломанный парсер, не пустота."""
    body = _players_text([], 30, 20.0)
    assert body != EMPTY_NOTICE
    assert "30" in body


# ── раскладка колонок ────────────────────────────────────────────────────

@pytest.mark.parametrize("count, expected", [
    (0, (1, 0)), (1, (1, 1)), (20, (1, 20)), (21, (2, 11)), (29, (2, 15)), (80, (4, 20)),
])
def test_layout_grows_columns_like_the_game(count, expected):
    assert _layout(count) == expected


def test_layout_never_exceeds_four_columns():
    columns, _ = _layout(500)
    assert columns == 4


# ── безопасность: ник как источник пути и URL ────────────────────────────

@pytest.mark.parametrize("nick", [
    "../../etc/passwd",
    "..\\..\\windows\\system32",
    "nick/../../../root",
    "sub.domain.example.com/evil",
    "ник",                       # кириллица — не бывает у лицензионного аккаунта
    "a" * 17,                    # длиннее лимита Minecraft
    "",
    "with space",
    "semi;colon",
])
def test_fetch_heads_rejects_nick_that_is_not_a_minecraft_username(nick, monkeypatch):
    """Невалидный ник не должен доехать ни до сети, ни до файловой системы."""
    def explode(*args, **kwargs):
        raise AssertionError(f"невалидный ник {nick!r} утёк наружу")

    monkeypatch.setattr("bot.handlers.ping_tab._cached_head", explode)
    monkeypatch.setattr("bot.handlers.ping_tab._download_head", explode)

    assert asyncio.run(fetch_heads([nick])) == {}


def test_decode_head_rejects_oversized_image():
    """64 КБ PNG разжимается в гигапиксели — размер читаем из заголовка."""
    buffer = io.BytesIO()
    Image.new("RGBA", (4096, 4096), (0, 0, 0, 0)).save(buffer, format="PNG")
    assert len(buffer.getvalue()) < 64 * 1024, "фикстура должна пролезать под лимит байт"
    assert _decode_head(buffer.getvalue()) is None


def test_decode_head_rejects_garbage():
    assert _decode_head(b"\x00 definitely not an image") is None


class _FakeResponse:
    def __init__(self, status: int, payload: bytes):
        self.status = status
        self.content = self
        self._payload = payload

    async def read(self, limit: int) -> bytes:
        return self._payload[:limit]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    def __init__(self, response: _FakeResponse):
        self._response = response
        self.requested: list[str] = []

    def get(self, url: str):
        self.requested.append(url)
        return self._response


async def test_download_head_drops_response_over_byte_limit(tmp_path, monkeypatch):
    """Сервис отдал не голову, а гигабайт — обрываем на лимите и выбрасываем.

    Полезная нагрузка — ВАЛИДНАЯ голова с длинным хвостом мусора: иначе ответ
    отсеет декодер, и тест зазеленеет даже без лимита байт (проверено мутацией).
    """
    monkeypatch.setattr("bot.handlers.ping_tab.HEAD_CACHE_DIR", tmp_path)
    buffer = io.BytesIO()
    Image.new("RGBA", (8, 8), (0, 255, 0, 255)).save(buffer, format="PNG")
    session = _FakeSession(_FakeResponse(200, buffer.getvalue() + b"\x00" * (128 * 1024)))

    assert await _download_head(session, "vxpbuser") is None
    assert not list(tmp_path.iterdir()), "переросток не должен попасть в кэш"


async def test_download_head_caches_valid_head(tmp_path, monkeypatch):
    monkeypatch.setattr("bot.handlers.ping_tab.HEAD_CACHE_DIR", tmp_path)
    buffer = io.BytesIO()
    Image.new("RGBA", (8, 8), (0, 255, 0, 255)).save(buffer, format="PNG")
    session = _FakeSession(_FakeResponse(200, buffer.getvalue()))

    head = await _download_head(session, "vxpbuser")

    assert head is not None
    assert session.requested == ["https://minotar.net/helm/vxpbuser/8.png"]
    assert (tmp_path / "vxpbuser.png").is_file()


async def test_download_head_ignores_error_status(tmp_path, monkeypatch):
    monkeypatch.setattr("bot.handlers.ping_tab.HEAD_CACHE_DIR", tmp_path)
    session = _FakeSession(_FakeResponse(404, b""))
    assert await _download_head(session, "vxpbuser") is None


def test_decode_head_accepts_real_head():
    buffer = io.BytesIO()
    Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(buffer, format="PNG")
    head = _decode_head(buffer.getvalue())
    assert head is not None and head.size == (8, 8)


# ── рендер ───────────────────────────────────────────────────────────────

def _render(names, total=None, tps=20.0, heads=None):
    png = render_tab(names, total if total is not None else len(names), tps, heads)
    return Image.open(io.BytesIO(png))


def test_render_tab_returns_valid_png_without_heads():
    """Сервис голов лёг — картинка всё равно рисуется."""
    image = _render(["vxpbuser", "Lykeza"], heads={})
    assert image.format == "PNG" and image.width > 0 and image.height > 0


def _blood_pixels(image) -> int:
    pixels = image.load()
    return sum(
        1
        for y in range(image.height)
        for x in range(image.width)
        if pixels[x, y][0] > 120 and pixels[x, y][1] < 80 and pixels[x, y][2] < 80
    )


def test_render_tab_shows_a_normal_picture_when_nobody_is_online(monkeypatch):
    """Штатный случай пустого сервера — обычная картинка на небе, без пасхалки."""
    monkeypatch.setattr(ping_tab.random, "random", lambda: 1.0)
    image = _render([], total=0).convert("RGB")

    assert image.getpixel((0, 0)) == SKY_TOP, "пустой сервер потерял небо"
    assert (image.width, image.height) != (EGG_W * SCALE, EGG_H * SCALE), \
        "пасхалка выпала там, где её не звали"


def test_empty_server_egg_is_rare(monkeypatch):
    """Пасхалка — редкая. Ровно на границе шанса её быть не должно."""
    calls: list[float] = []
    monkeypatch.setattr(ping_tab.random, "random", lambda: (calls.append(1), ping_tab.EGG_CHANCE)[1])

    image = Image.open(io.BytesIO(ping_tab.render_empty())).convert("RGB")
    assert calls, "шанс вообще не разыгрывается — пасхалка либо всегда, либо никогда"
    assert image.getpixel((0, 0)) == SKY_TOP
    assert 0 < ping_tab.EGG_CHANCE < 0.2, "это уже не пасхалка, а основной вид"


def test_empty_server_egg_is_a_big_void_with_a_small_line(monkeypatch):
    """Пасхалка Undertale: большая пустота и МАЛЕНЬКАЯ кровавая строчка."""
    monkeypatch.setattr(ping_tab.random, "random", lambda: 0.0)
    image = _render([], total=0).convert("RGB")

    assert (image.width, image.height) == (EGG_W * SCALE, EGG_H * SCALE)
    # Ни логотипа, ни градиента: пустота должна быть пустой.
    corners = [image.getpixel(xy) for xy in
               ((0, 0), (image.width - 1, 0), (0, image.height - 1),
                (image.width - 1, image.height - 1))]
    assert corners == [VOID[0]] * 4, "фон не ровный — вернулся градиент"

    blood = _blood_pixels(image)
    assert blood > 200, "кровавой надписи не видно"
    # Смысл картинки — в том, сколько вокруг надписи пусто.
    assert blood < image.width * image.height * 0.02, "надпись занимает пол-экрана"

    pixels = image.load()
    logo_ish = sum(
        1
        for y in range(image.height)
        for x in range(image.width)
        if pixels[x, y][2] > 120 and pixels[x, y][0] > 90
    )
    assert logo_ish == 0, "на картинке пустого сервера остался логотип"


def test_picture_is_drawn_only_when_there_is_something_to_draw():
    """Главный дефект ревью, теперь на уровне решения «рисовать или нет».

    Тридцать онлайн и ноль распознанных имён — рисовать нечего и врать про
    пустоту нельзя: такой случай уходит текстом. Честно пустой сервер — картинка.
    """
    assert needs_picture(["Lykeza"], 1)
    assert needs_picture([], 0), "пустой сервер рисуем: там пасхалка и там она к месту"
    assert not needs_picture([], 30), "картинка «никто не пришёл» при 30 онлайн"
    assert not needs_picture([], None), "рисовать нечего, а плакат врёт видом штатного ответа"


def test_alt_text_says_the_same_thing_as_the_picture():
    """Подпись — единственный доступ к содержимому без картинки.

    Раньше она бралась из счётчика напрямую и рапортовала «Игроков: 30» о
    картинке, на которой написано «список не читается».
    """
    assert alt_text(["Lykeza"], 1, 20.0) == "Игроков: 1, ТПС: 20.0"
    assert alt_text(["Lykeza"], 1, None).endswith("ТПС: ?")
    assert alt_text([], 0, 20.0) == EMPTY_NOTICE
    assert alt_text([], 30, 20.0) == _empty_state_notice(30) != EMPTY_NOTICE
    assert alt_text([], None, None) == _empty_state_notice(None)


def test_render_notice_draws_on_the_sky():
    notice = Image.open(io.BytesIO(render_notice(EMPTY_NOTICE))).convert("RGB")
    assert notice.getpixel((0, 0)) == SKY_TOP


def test_render_notice_never_clips_a_long_line():
    """Строка плаката задаёт ширину: обрезать её нечем, но проверить стоит."""
    long_line = "Сейчас на сервере никого нет, заходи попозже пожалуйста"
    image = Image.open(io.BytesIO(render_notice(long_line))).convert("RGB")
    pixels = image.load()

    text_columns = [
        x
        for x in range(image.width)
        for y in range(image.height)
        if pixels[x, y][0] > 120 and pixels[x, y][1] < 90 and pixels[x, y][2] < 90
    ]
    assert text_columns, "надпись не нарисовалась"
    assert min(text_columns) > 0 and max(text_columns) < image.width - 1


def test_render_tab_keeps_longest_nick_inside_its_plate(tmp_path, monkeypatch):
    """Текст не должен вылезать за плашку — проверяем по самим пикселям.

    Логотип на время теста убран: он шире колонки и содержит и тёмные, и белые
    пиксели, поэтому попадал бы в замер и краснил тест по чужой причине.
    """
    monkeypatch.setattr("bot.handlers.ping_tab.LOGO_PATH", tmp_path / "нет-логотипа.png")
    image = _render(["W" * 16], heads={}).convert("RGB")
    pixels = image.load()

    # Плашка — полупрозрачное чёрное поверх неба: тёмная, но всё ещё синеватая.
    # Тень текста тоже тёмная, но СЕРАЯ — без проверки на синеву в «плашку»
    # попадала тень шапки, и тест мерил её ширину вместо ширины колонки.
    def is_plate(rgb):
        red, _, blue = rgb
        return all(channel < 150 for channel in rgb) and blue > red + 20

    def is_text(rgb):
        return all(channel > 230 for channel in rgb)

    rows = [y for y in range(image.height)
            if any(is_plate(pixels[x, y]) for x in range(image.width))]
    assert rows, "плашка не нарисовалась — тест ничего не проверил"

    plate_right = text_right = -1
    for y in rows:
        for x in range(image.width):
            rgb = pixels[x, y]
            if is_plate(rgb):
                plate_right = max(plate_right, x)
            elif is_text(rgb):
                text_right = max(text_right, x)

    assert text_right > 0, "текст не нарисовался — тест ничего не проверил"
    # Не просто «влез», а влез с полем: текст впритык к краю плашки читается
    # как обрезанный, даже когда формально помещается.
    assert plate_right - text_right >= 8


def _plate_rows(image: Image.Image) -> int:
    """Сколько строк-плашек реально нарисовано (по пикселям, а не по вере)."""
    pixels = image.convert("RGB").load()

    def dark_blue(rgb):
        red, _, blue = rgb
        return all(channel < 150 for channel in rgb) and blue > red + 20

    rows, inside = 0, False
    for y in range(image.height):
        has_plate = any(dark_blue(pixels[x, y]) for x in range(image.width))
        if has_plate and not inside:
            rows += 1
        inside = has_plate
    return rows


def test_render_tab_caps_rows_and_reports_the_rest(tmp_path, monkeypatch):
    """300 игроков рисуют РОВНО потолок строк, а остальные уходят в подпись.

    Логотип убран: у него есть тёмно-синие пиксели, и он считался бы за
    лишнюю строку — тест краснел бы по чужой причине.
    """
    monkeypatch.setattr("bot.handlers.ping_tab.LOGO_PATH", tmp_path / "нет.png")
    many = [f"player{i:04d}" for i in range(300)]
    capped = _render(many, total=300, heads={})

    assert _plate_rows(capped) == ROWS_PER_COL, "потолок строк не соблюдён"
    # И подпись про остаток на месте: без неё «300 игроков» превращались бы
    # в молча показанные 80.
    assert capped.height > _render(many[:MAX_SHOWN], total=MAX_SHOWN, heads={}).height


def test_render_tab_stays_within_discord_limits_at_worst_case():
    """Худший случай: забитый сервер и все ники предельной длины.

    Границы взяты с запасом от увиденного (1836×1176, 58 КБ): тест сторожит
    не пиксель в пиксель, а то, что картинка не уедет в мегабайты и в размер,
    который Discord откажется показывать.
    """
    longest = [f"{'W' * 13}{i:03d}"[:16] for i in range(MAX_SHOWN)]
    heads = {nick: Image.new("RGBA", (8, 8), (255, 0, 0, 255)) for nick in longest}

    png = render_tab(longest, 747, 20.0, heads)
    image = Image.open(io.BytesIO(png))

    assert image.width <= 2200 and image.height <= 1600, f"полотно раздулось: {image.size}"
    assert len(png) < 2 * 1024 * 1024, f"картинка весит {len(png) / 1024:.0f} КБ"


@pytest.mark.parametrize("names", [
    ["a"],                                  # предельно короткий
    ["W" * 16],                             # предельно длинный
    ["a", "b", "W" * 16],                   # ширину колонки задаёт самый длинный
    [f"{chr(97 + i % 26)}{i}" for i in range(MAX_SHOWN)],   # все короткие, 4 колонки
])
def test_render_tab_survives_extreme_nick_lengths(names):
    image = _render(names, heads={})
    assert image.format == "PNG" and image.width > 0


@pytest.mark.parametrize("total", [0, 1, 20, 21, 80, 100, 200, 747])
def test_render_tab_survives_any_online_count(total):
    names = [f"player{i:04d}" for i in range(min(total, 300))]
    image = _render(names, total=total, heads={})
    assert image.format == "PNG"


def test_render_tab_widens_with_more_columns():
    # Ники длинные намеренно: на коротких ширину картинки задаёт шапка, а не
    # сетка, и рост колонок в размере не виден.
    names = [f"longplayer{i:04d}" for i in range(30)]
    one_column = _render(names[:10])
    two_columns = _render(names)
    assert two_columns.width > one_column.width


def test_render_tab_handles_unknown_tps():
    image = _render(["vxpbuser"], tps=None)
    assert image.format == "PNG"


def test_render_tab_adds_footer_when_names_are_fewer_than_counter():
    """Ников распозналось меньше, чем сказал `list` → внизу «и ещё N»."""
    without_footer = _render(["vxpbuser", "Lykeza"], total=2, heads={})
    with_footer = _render(["vxpbuser", "Lykeza"], total=9, heads={})
    assert with_footer.height > without_footer.height


def test_render_tab_survives_a_broken_logo(tmp_path, monkeypatch):
    """Битый логотип не должен ронять каждый `!пинг` навсегда."""
    broken = tmp_path / "logo.png"
    broken.write_bytes(b"\x89PNG definitely truncated".ljust(200, b"\x00"))
    monkeypatch.setattr("bot.handlers.ping_tab.LOGO_PATH", broken)

    assert _render(["vxpbuser"], heads={}).format == "PNG"


def test_render_tab_ignores_an_oversized_logo(tmp_path, monkeypatch):
    """Логотип задаёт ширину холста — «покрасивее в 4×» раздувал картинку."""
    huge = tmp_path / "logo.png"
    Image.new("RGBA", (1024, 512), (255, 0, 0, 255)).save(huge)
    monkeypatch.setattr("bot.handlers.ping_tab.LOGO_PATH", huge)

    with_huge = _render(["vxpbuser"], heads={})
    assert with_huge.width < 1024


def test_render_tab_survives_a_missing_font(tmp_path, monkeypatch):
    monkeypatch.setattr("bot.handlers.ping_tab.FONT_PATH", tmp_path / "нет.ttf")
    assert _render(["vxpbuser"], heads={}).format == "PNG"


def test_ping_uses_bundled_minecraft_rus_font():
    font = ImageFont.truetype(str(ping_tab.FONT_PATH), ping_tab.FONT_SIZE)

    assert ping_tab.FONT_PATH == DEFAULT_FONT_PATH
    assert font.getname()[0] == "Minecraft Rus"


def test_spam_filter_is_wired_with_the_intended_numbers():
    """Тесты зовут `.callback` в обход декораторов — сам фильтр иначе не проверен.

    Удали декоратор целиком — раньше все тесты оставались зелёными.
    """
    from bot.handlers.ping_tab import SPAM_CALLS, SPAM_WINDOW, PingTab

    buckets = PingTab.pingPlayers._buckets
    assert buckets is not None, "кулдаун снят с команды"
    assert (buckets._cooldown.rate, buckets._cooldown.per) == (SPAM_CALLS, SPAM_WINDOW)
    assert buckets._type is commands.BucketType.channel


# ── сборка данных: три команды, один коннект ─────────────────────────────

def test_parse_names_from_list_reads_the_truncated_tail():
    """Из обрезанного `list` берём то, что доехало, — это лучше, чем ничего."""
    names = parse_names_from_list(RAW_LIST, 30)
    assert "vxpbuser" in names and "Lykeza" in names
    assert 10 <= len(names) <= 14, "должен приехать доехавший кусок, не больше"


def test_parse_names_from_list_drops_the_last_nick_when_cut():
    """🛑 Обрубок посреди ника неотличим от настоящего ника — он бы поехал в
    URL головы и показался игрокам как несуществующий человек."""
    raw = "There are 30 of a max of 747 players online: Alice, Bob, Charl"
    assert parse_names_from_list(raw, 30) == ["Alice", "Bob"]
    assert "..." not in "".join(parse_names_from_list(RAW_LIST, 30))


def test_parse_names_from_list_keeps_everyone_when_list_is_whole():
    raw = "There are 3 of a max of 747 players online: Alice, Bob, Charlie"
    assert parse_names_from_list(raw, 3) == ["Alice", "Bob", "Charlie"]


def test_parse_names_from_list_survives_garbage():
    assert parse_names_from_list("Unknown command", None) == []
    assert parse_names_from_list("There are 0 of a max of 747 players online:", 0) == []


async def test_collect_falls_back_to_list_names_when_execute_says_nothing(monkeypatch):
    """Скрин 08.08: `execute` отдал чужой ответ, счётчик доехал.

    Вместо «список не читается» показываем тех, кто есть в `list`.
    """
    from bot.handlers.ping_tab import PingTab

    async def fake_rcon(host, port, password, commands_, timeout=10.0):
        return [RAW_TPS, RAW_LIST, RAW_TPS]  # в поле имён — чужой ответ

    monkeypatch.setattr("bot.handlers.ping_tab.rcon_exec_many", fake_rcon)
    names, total, _ = await PingTab(None)._collect()

    assert total == 30, "счётчик не потерялся"
    assert 10 <= len(names) <= 14, "запасной список не подхватился"
    assert "vxpbuser" in names


async def test_retries_once_when_the_haul_is_suspiciously_short(monkeypatch):
    """Имён меньше половины счётчика — это сбой сбора, а не картина мира."""
    from bot.handlers.ping_tab import PingTab

    hauls = [(["one"], 30, 20.0), (["a", "b", "c"], 3, 20.0)]

    async def collect(self, budget=None):
        return hauls.pop(0)

    monkeypatch.setattr(PingTab, "_collect", collect)
    result = await PingTab(None)._collect_with_retries(_FakeContext(), [])

    assert result == (["a", "b", "c"], 3, 20.0), "повтор не сделан или не принят"
    assert not hauls


async def test_short_haul_is_served_when_the_retry_does_not_help(monkeypatch):
    """Повтор не помог — отдаём неполный список, а не отказ: четырнадцать
    ников игроку полезнее, чем «не смог посмотреть»."""
    from bot.handlers.ping_tab import PingTab

    calls = []

    async def collect(self, budget=None):
        calls.append(1)
        return (["one"], 30, 20.0)

    monkeypatch.setattr(PingTab, "_collect", collect)
    result = await PingTab(None)._collect_with_retries(_FakeContext(), [])

    assert result == (["one"], 30, 20.0)
    assert len(calls) == 2, "повтор должен быть ровно один, а не по числу попыток"


async def test_short_haul_survives_a_failing_retry(monkeypatch):
    """Неполный сбор придержан, а повтор упал по сети — отдаём то, что есть.

    Именно этот путь и вёл к отказу: сначала разъехались ответы, потом моргнула
    сеть, и четырнадцать честных ников улетали в мусор вместе с попыткой.
    """
    from bot.handlers.ping_tab import PingTab

    calls = []

    async def collect(self, budget=None):
        calls.append(1)
        if len(calls) == 1:
            return (["one"], 30, 20.0)
        raise OSError("сеть моргнула")

    monkeypatch.setattr(PingTab, "_collect", collect)
    monkeypatch.setattr("bot.handlers.ping_tab.RCON_RETRY_DELAY", 0)

    result = await PingTab(None)._collect_with_retries(_FakeContext(), [])
    assert result == (["one"], 30, 20.0), "неполный сбор выброшен, хотя он был на руках"


async def test_no_retry_when_a_player_left_between_the_two_commands(monkeypatch):
    """Расхождение на одного — гонка, а не сбой: второй заход в RCON не нужен."""
    from bot.handlers.ping_tab import PingTab

    calls = []

    async def collect(self, budget=None):
        calls.append(1)
        return (["a", "b", "c"], 4, 20.0)

    monkeypatch.setattr(PingTab, "_collect", collect)
    assert await PingTab(None)._collect_with_retries(_FakeContext(), []) == (["a", "b", "c"], 4, 20.0)
    assert len(calls) == 1


async def test_collect_reconciles_names_with_authoritative_counter(monkeypatch):
    """Счётчик берётся из `list`, ники — из `execute`; их расхождение сохраняется."""
    from bot.handlers.ping_tab import PingTab

    sent: list[list[str]] = []

    async def fake_rcon(host, port, password, commands_, timeout=10.0):
        sent.append(commands_)
        return [RAW_POS, RAW_LIST, RAW_TPS]  # именно расходящаяся пара

    monkeypatch.setattr("bot.handlers.ping_tab.rcon_exec_many", fake_rcon)

    names, total, tps = await PingTab(None)._collect()

    assert names == ["Chashi_", "Lykeza", "vxpbuser"]
    assert total == 30, "счётчик обязан приехать из list, а не из числа ников"
    assert tps == 20.0
    assert len(sent) == 1, "три команды должны уехать одним соединением"
    assert sent[0] == [
        "execute as @a run data get entity @s Pos", "list", "tps",
    ]


@pytest.fixture(autouse=True)
def _clean_snapshot(monkeypatch):
    """Модульные глобалы: без сброса тесты влияют друг на друга.

    Замок тоже заменяем на свежий: каждый тест живёт в своём цикле событий, а
    замок, захваченный упавшим тестом, отравил бы следующий.
    """
    monkeypatch.setattr("bot.handlers.ping_tab._snapshot", None)
    monkeypatch.setattr("bot.handlers.ping_tab._failure_at", None)
    monkeypatch.setattr("bot.handlers.ping_tab._snapshot_lock", asyncio.Lock())


async def _no_heads(nicks):
    return {}


class _FakeMessage:
    def __init__(self, content: str):
        self.content = content
        self.edits: list[str] = []
        self.deleted = False
        self.reactions: list[str] = []
        self.react_fails = False

    async def add_reaction(self, emoji: str):
        if self.react_fails:
            raise disnake.HTTPException(_FakeHTTPResponse(), "нет прав")
        self.reactions.append(emoji)

    async def edit(self, content=None, **kwargs):
        self.content = content
        self.edits.append(content)

    async def delete(self):
        self.deleted = True


class _FakeContext:
    """Минимальный ctx: запоминает, чем ответили, и умеет быть typing-контекстом.

    По умолчанию — ДОМАШНИЙ контекст: гостевой отличается тем, что почти всё
    видимое там подавлено, и тест на «бот ответил» в нём был бы бессмысленным.
    """

    def __init__(self, guild_id=Guilds.main):
        self.replies: list[dict] = []
        self.messages: list[_FakeMessage] = []
        self.message = _FakeMessage("!пинг")
        self.message.guild = None if guild_id is None else SimpleNamespace(id=guild_id)
        self.author = SimpleNamespace(id=424242424242424242)

    async def reply(self, content=None, **kwargs):
        self.replies.append({"content": content, **kwargs})
        message = _FakeMessage(content)
        self.messages.append(message)
        return message

    def typing(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


async def test_ping_replies_only_sad_face_when_server_is_closed(monkeypatch):
    from bot.handlers.ping_tab import PingTab

    async def explode(*args, **kwargs):
        raise AssertionError("сервер закрыт — в RCON ходить нельзя")

    monkeypatch.setattr("bot.handlers.ping_tab.SERVER_CLOSED", True)
    monkeypatch.setattr("bot.handlers.ping_tab.rcon_exec_many", explode)

    ctx = _FakeContext()
    await PingTab.pingPlayers.callback(PingTab(None), ctx)

    assert ctx.replies == [{"content": ":("}]


@pytest.mark.parametrize("failure", [
    OSError("connection refused to 10.0.0.1:25575"),
    asyncio.TimeoutError(),
    asyncio.IncompleteReadError(b"", 4),
])
async def test_ping_retries_then_gives_up_politely(failure, monkeypatch):
    """Сеть моргает → три попытки с отчётом, и лишь потом извинение."""
    from bot.handlers.ping_tab import PingTab, RCON_ATTEMPTS

    attempts = 0

    async def boom(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        raise failure

    monkeypatch.setattr("bot.handlers.ping_tab.rcon_exec_many", boom)
    monkeypatch.setattr("bot.handlers.ping_tab.RCON_RETRY_DELAY", 0)
    monkeypatch.setenv("RCON_HOST", "10.0.0.1")
    monkeypatch.setenv("RCON_PASSWORD", "secret")

    ctx = _FakeContext()
    # .callback — тело команды в обход декораторов disnake (кулдаун тут мешает)
    await PingTab.pingPlayers.callback(PingTab(None), ctx)

    # Число пиннится литералом: сравнение с той же импортированной константой
    # было тавтологией и пропускало любое её изменение.
    assert RCON_ATTEMPTS == 3, "число попыток изменили — это видно людям в чате"
    assert attempts == 3, "сдались раньше, чем исчерпали попытки"
    # Служебное сообщение ровно одно и оно правится, а не плодит новые:
    # на три попытки приходится один «Не получилось…» плюс финальный ответ.
    status, final = ctx.messages
    assert "пытаюсь ещё раз" in status.content
    assert len(status.edits) == RCON_ATTEMPTS - 2, "каждая попытка родила новое сообщение"
    assert status.deleted, "служебное сообщение осталось висеть под картинкой"
    assert not final.deleted

    # Итог — ТЕКСТ, а не картинка: рисовать нечего, а плакат с одной строкой
    # выглядит как штатный ответ и грузится дольше.
    assert "file" not in ctx.replies[-1], "отказ снова нарисован картинкой"
    assert OFFLINE_NOTICE in str(ctx.replies[-1])
    # 🛑 И он не выдаёт слепоту бота за факт о сервере.
    assert "тех. работах" not in str(ctx.replies[-1])

    everything = str(ctx.replies) + str(ctx.messages[0].edits)
    assert "10.0.0.1" not in everything and "secret" not in everything


async def test_ping_does_not_retry_a_wrong_password(monkeypatch):
    """Пароль не станет верным от повтора — долбить сервер незачем."""
    from bot.handlers.ping_tab import PingTab

    attempts = 0

    async def boom(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        raise PermissionError("RCON: неверный пароль")

    monkeypatch.setattr("bot.handlers.ping_tab.rcon_exec_many", boom)
    monkeypatch.setenv("RCON_HOST", "10.0.0.1")
    monkeypatch.setenv("RCON_PASSWORD", "secret")

    ctx = _FakeContext()
    await PingTab.pingPlayers.callback(PingTab(None), ctx)

    assert attempts == 1
    # Ни одной попытки повтора → ни одного «Не получилось…», сразу ответ.
    assert len(ctx.messages) == 1
    assert "пытаюсь" not in str(ctx.replies)
    assert "file" not in ctx.replies[-1]


async def test_ping_sends_picture_with_text_mode_hint(monkeypatch):
    from bot.handlers.ping_tab import PingTab

    async def fake_rcon(host, port, password, commands_, timeout=10.0):
        return [RAW_POS, RAW_LIST_3, RAW_TPS]

    async def no_heads(nicks):
        return {}

    monkeypatch.setattr("bot.handlers.ping_tab.rcon_exec_many", fake_rcon)
    monkeypatch.setattr("bot.handlers.ping_tab.fetch_heads", no_heads)
    monkeypatch.setenv("RCON_HOST", "10.0.0.1")
    monkeypatch.setenv("RCON_PASSWORD", "secret")

    ctx = _FakeContext()
    await PingTab.pingPlayers.callback(PingTab(None), ctx)

    assert len(ctx.replies) == 1
    assert "file" in ctx.replies[0]
    assert "!пинг текст" in ctx.replies[0]["content"]


async def test_ping_serves_the_same_picture_within_the_snapshot_window(monkeypatch):
    """Повторный `!пинг` внутри окна — та же картинка и НИ ОДНОГО похода в RCON."""
    from bot.handlers.ping_tab import PingTab

    trips = 0

    async def fake_rcon(host, port, password, commands_, timeout=10.0):
        nonlocal trips
        trips += 1
        return [RAW_POS, RAW_LIST_3, RAW_TPS]

    heads_calls = 0

    async def no_heads(nicks):
        nonlocal heads_calls
        heads_calls += 1
        return {}

    monkeypatch.setattr("bot.handlers.ping_tab.rcon_exec_many", fake_rcon)
    monkeypatch.setattr("bot.handlers.ping_tab.fetch_heads", no_heads)
    monkeypatch.setenv("RCON_HOST", "10.0.0.1")
    monkeypatch.setenv("RCON_PASSWORD", "secret")

    cog = PingTab(None)
    first, second = _FakeContext(), _FakeContext()
    await PingTab.pingPlayers.callback(cog, first)
    await PingTab.pingPlayers.callback(cog, second)

    assert trips == 1, "второй вызов всё-таки сходил в RCON"
    assert heads_calls == 1, "второй вызов заново качал головы"
    assert first.replies[0]["file"].fp.getvalue() == second.replies[0]["file"].fp.getvalue()


async def test_ping_goes_back_to_rcon_after_the_window_expires(monkeypatch):
    from bot.handlers.ping_tab import PingTab

    trips = 0

    async def fake_rcon(host, port, password, commands_, timeout=10.0):
        nonlocal trips
        trips += 1
        return [RAW_POS, RAW_LIST_3, RAW_TPS]

    async def no_heads(nicks):
        return {}

    monkeypatch.setattr("bot.handlers.ping_tab.rcon_exec_many", fake_rcon)
    monkeypatch.setattr("bot.handlers.ping_tab.fetch_heads", no_heads)
    monkeypatch.setattr("bot.handlers.ping_tab.SNAPSHOT_TTL", 0)
    monkeypatch.setenv("RCON_HOST", "10.0.0.1")
    monkeypatch.setenv("RCON_PASSWORD", "secret")

    cog = PingTab(None)
    await PingTab.pingPlayers.callback(cog, _FakeContext())
    await PingTab.pingPlayers.callback(cog, _FakeContext())

    assert trips == 2


async def test_ping_text_mode_does_not_render_a_picture(monkeypatch):
    """Текстовый режим не должен платить за рендер и за головы."""
    from bot.handlers.ping_tab import PingTab

    async def fake_rcon(host, port, password, commands_, timeout=10.0):
        return [RAW_POS, RAW_LIST_3, RAW_TPS]

    def explode(*args, **kwargs):
        raise AssertionError("картинка рисоваться не должна")

    monkeypatch.setattr("bot.handlers.ping_tab.rcon_exec_many", fake_rcon)
    monkeypatch.setattr("bot.handlers.ping_tab.render_tab", explode)
    monkeypatch.setenv("RCON_HOST", "10.0.0.1")
    monkeypatch.setenv("RCON_PASSWORD", "secret")

    ctx = _FakeContext()
    await PingTab.pingPlayers.callback(PingTab(None), ctx, mode="текст")
    assert "components" in ctx.replies[0]


async def test_ping_answers_spam_with_a_reaction_not_a_message():
    """Отвечать текстом на спам — значит спамить в ответ."""
    from bot.handlers.ping_tab import PingTab

    ctx = _FakeContext()
    error = commands.CommandOnCooldown(None, retry_after=7.0, type=None)

    await PingTab(None).cog_command_error(ctx, error)

    assert ctx.replies == [], "в канал ушло сообщение вместо реакции"
    assert ctx.message.reactions == ["⏳"]


async def test_ping_refusal_costs_nothing_visible_and_nothing_in_the_log(caplog):
    """🛑 Отказ барьера обязан быть ДЕШЕВЛЕ разрешённой команды, а не дороже.

    `CheckFailure` от гостевого барьера приходит в коговый обработчик напрямую
    (disnake зовёт `cog_command_error` мимо `dispatch`), поэтому без отдельной
    ветки он проваливался в общий хвост: гость получал «Что-то сломалось», а в
    `bot.log` уходил ERROR с полным трейсбеком — на КАЖДОЕ сообщение спамера.
    Ответ (⏳ или тишину) даёт сама проверка в `bot/bot.py`.
    """
    import logging

    from bot.handlers.ping_tab import PingTab

    ctx = _FakeContext(guild_id=99999999999999999)  # чужой сервер

    with caplog.at_level(logging.DEBUG, logger="robocat.ping_tab"):
        await PingTab(None).cog_command_error(ctx, commands.CheckFailure("нельзя"))

    assert ctx.replies == [], "гость получил сообщение об ошибке вместо тишины"
    assert ctx.message.reactions == [], "лишняя реакция на отказ барьера"
    assert caplog.records == [], [r.getMessage() for r in caplog.records]


async def test_ping_does_not_hammer_a_dead_server_with_every_caller(monkeypatch):
    """Отказ кэшируется: очередь ожидающих не повторяет полный цикл ретраев."""
    from bot.handlers.ping_tab import PingTab, RCON_ATTEMPTS

    attempts = 0

    async def boom(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        raise OSError("connection refused")

    monkeypatch.setattr("bot.handlers.ping_tab.rcon_exec_many", boom)
    monkeypatch.setattr("bot.handlers.ping_tab.RCON_RETRY_DELAY", 0)
    monkeypatch.setenv("RCON_HOST", "10.0.0.1")
    monkeypatch.setenv("RCON_PASSWORD", "secret")

    cog = PingTab(None)
    for _ in range(4):
        await PingTab.pingPlayers.callback(cog, _FakeContext())

    assert attempts == RCON_ATTEMPTS, "каждый вызывающий заново долбил лежащий сервер"


async def test_ping_renders_once_for_simultaneous_callers(monkeypatch):
    """Замок обязан гасить стадо и на головах с рендером, а не только на RCON.

    Иначе всплеск в пять каналов давал пять выкачек по 80 голов — залп в
    четыреста запросов к стороннему сервису с одного IP.
    """
    from bot.handlers.ping_tab import PingTab

    fetches = renders = 0

    async def fake_rcon(host, port, password, commands_, timeout=10.0):
        return [RAW_POS, RAW_LIST_3, RAW_TPS]

    async def counting_heads(nicks):
        nonlocal fetches
        fetches += 1
        return {}

    real_render = render_tab

    def counting_render(*args, **kwargs):
        nonlocal renders
        renders += 1
        return real_render(*args, **kwargs)

    monkeypatch.setattr("bot.handlers.ping_tab.rcon_exec_many", fake_rcon)
    monkeypatch.setattr("bot.handlers.ping_tab.fetch_heads", counting_heads)
    monkeypatch.setattr("bot.handlers.ping_tab.render_tab", counting_render)
    monkeypatch.setenv("RCON_HOST", "10.0.0.1")
    monkeypatch.setenv("RCON_PASSWORD", "secret")

    cog = PingTab(None)
    await asyncio.gather(*(
        PingTab.pingPlayers.callback(cog, _FakeContext()) for _ in range(5)
    ))

    assert fetches == 1 and renders == 1


async def test_ping_stamps_the_snapshot_after_collecting_not_before(monkeypatch):
    """Долгий сбор не должен рождать снимок, протухший в момент записи."""
    import bot.handlers.ping_tab as module
    from bot.handlers.ping_tab import PingTab

    collecting = 0.05

    async def slow_rcon(host, port, password, commands_, timeout=10.0):
        await asyncio.sleep(collecting)
        return [RAW_POS, RAW_LIST_3, RAW_TPS]

    monkeypatch.setattr("bot.handlers.ping_tab.rcon_exec_many", slow_rcon)
    monkeypatch.setattr("bot.handlers.ping_tab.fetch_heads", _no_heads)
    monkeypatch.setenv("RCON_HOST", "10.0.0.1")
    monkeypatch.setenv("RCON_PASSWORD", "secret")

    started = time.monotonic()
    await PingTab.pingPlayers.callback(PingTab(None), _FakeContext())

    # Отметка обязана быть ПОЗЖЕ, чем закончился сбор. Сравниваем с моментом
    # старта плюс длительность сбора, а не с «сейчас»: иначе тест зависел бы
    # от скорости рендера и мигал бы на медленной машине.
    assert module._snapshot["at"] >= started + collecting, (
        "снимок помечен временем НАЧАЛА сбора и рождается протухшим"
    )


async def test_ping_cleans_up_the_status_message_even_when_render_dies(monkeypatch):
    """Служебное «пытаюсь ещё раз» не должно пережить падение рендера."""
    from bot.handlers.ping_tab import PingTab

    calls = 0

    async def flaky_rcon(host, port, password, commands_, timeout=10.0):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("moment")
        return [RAW_POS, RAW_LIST_3, RAW_TPS]

    async def exploding_heads(nicks):
        raise RuntimeError("рендер лёг")

    monkeypatch.setattr("bot.handlers.ping_tab.rcon_exec_many", flaky_rcon)
    monkeypatch.setattr("bot.handlers.ping_tab.fetch_heads", exploding_heads)
    monkeypatch.setattr("bot.handlers.ping_tab.RCON_RETRY_DELAY", 0)
    monkeypatch.setenv("RCON_HOST", "10.0.0.1")
    monkeypatch.setenv("RCON_PASSWORD", "secret")

    ctx = _FakeContext()
    with pytest.raises(RuntimeError):
        await PingTab.pingPlayers.callback(PingTab(None), ctx)

    assert ctx.messages[0].deleted, "«пытаюсь ещё раз…» осталось висеть навсегда"


async def test_ping_answers_on_cooldown_when_it_cannot_react():
    """Нет права на реакции — молчать нельзя, человек решит, что бот сломан."""
    from bot.handlers.ping_tab import PingTab

    ctx = _FakeContext()
    ctx.message.react_fails = True
    error = commands.CommandOnCooldown(None, retry_after=7.0, type=None)

    await PingTab(None).cog_command_error(ctx, error)

    assert ctx.replies, "ни реакции, ни сообщения — пользователь не получил ничего"


def test_ping_attaches_alt_text_to_the_picture():
    """Вся полезная нагрузка — картинка; без описания она недоступна."""
    from bot.handlers.ping_tab import _attach

    attachment = _attach(b"\x89PNG", "Игроков: 30, ТПС: 20.0")
    assert attachment.description == "Игроков: 30, ТПС: 20.0"


@pytest.mark.parametrize("mode", ["текст", " Текст ", "текстом", "text"])
async def test_ping_text_mode_lists_players_without_a_picture(mode, monkeypatch):
    from bot.handlers.ping_tab import PingTab

    async def fake_rcon(host, port, password, commands_, timeout=10.0):
        return [RAW_POS, RAW_LIST_3, RAW_TPS]

    async def explode(nicks):
        raise AssertionError("в текстовом режиме головы качать незачем")

    monkeypatch.setattr("bot.handlers.ping_tab.rcon_exec_many", fake_rcon)
    monkeypatch.setattr("bot.handlers.ping_tab.fetch_heads", explode)
    monkeypatch.setenv("RCON_HOST", "10.0.0.1")
    monkeypatch.setenv("RCON_PASSWORD", "secret")

    ctx = _FakeContext()
    await PingTab.pingPlayers.callback(PingTab(None), ctx, mode=mode)

    assert "file" not in ctx.replies[0]
    assert "components" in ctx.replies[0]


@pytest.mark.parametrize("raw_list, expect", [
    ("There are 40 of a max of 747 players online:", "40"),   # счётчик есть, имён нет
    ("Unknown or incomplete command", "узнать"),               # не разобрали вообще
])
async def test_ping_answers_with_text_when_there_is_nothing_to_draw(
    raw_list, expect, monkeypatch,
):
    """Плакат с одной строкой — тот же текст, только тяжелее и с видом штатного
    ответа. Аварийные состояния уходят обычным сообщением."""
    from bot.handlers.ping_tab import PingTab

    async def fake_rcon(host, port, password, commands_, timeout=10.0):
        return ["", raw_list, RAW_TPS]

    async def explode(nicks):
        raise AssertionError("рисовать нечего — за головами ходить незачем")

    monkeypatch.setattr("bot.handlers.ping_tab.rcon_exec_many", fake_rcon)
    monkeypatch.setattr("bot.handlers.ping_tab.fetch_heads", explode)
    monkeypatch.setenv("RCON_HOST", "10.0.0.1")
    monkeypatch.setenv("RCON_PASSWORD", "secret")

    ctx = _FakeContext()
    await PingTab.pingPlayers.callback(PingTab(None), ctx)

    answer = str(ctx.replies[-1])
    assert "file" not in ctx.replies[-1], "аварийное состояние снова нарисовано"
    assert expect in answer
    assert "не читается" not in answer and "разобрал" not in answer


async def test_ping_still_draws_the_empty_server(monkeypatch):
    """Пустой сервер остаётся картинкой: там она к месту и там пасхалка."""
    from bot.handlers.ping_tab import PingTab

    async def fake_rcon(host, port, password, commands_, timeout=10.0):
        return ["No entity was found", "There are 0 of a max of 747 players online:", RAW_TPS]

    monkeypatch.setattr("bot.handlers.ping_tab.rcon_exec_many", fake_rcon)
    monkeypatch.setattr("bot.handlers.ping_tab.fetch_heads", _no_heads)
    monkeypatch.setenv("RCON_HOST", "10.0.0.1")
    monkeypatch.setenv("RCON_PASSWORD", "secret")

    ctx = _FakeContext()
    await PingTab.pingPlayers.callback(PingTab(None), ctx)
    assert "file" in ctx.replies[-1]


async def test_ping_says_so_when_rcon_is_not_configured(monkeypatch):
    from bot.handlers.ping_tab import PingTab

    async def explode(*args, **kwargs):
        raise AssertionError("без настроенного RCON в сеть ходить нельзя")

    monkeypatch.setattr("bot.handlers.ping_tab.rcon_exec_many", explode)
    monkeypatch.setenv("RCON_HOST", "")
    monkeypatch.setenv("RCON_PASSWORD", "")

    ctx = _FakeContext()
    await PingTab.pingPlayers.callback(PingTab(None), ctx)
    assert len(ctx.replies) == 1


async def test_collect_trusts_names_when_counter_is_unreadable(monkeypatch):
    from bot.handlers.ping_tab import PingTab

    async def fake_rcon(host, port, password, commands_, timeout=10.0):
        return [RAW_POS, "Unknown command", RAW_TPS]

    monkeypatch.setattr("bot.handlers.ping_tab.rcon_exec_many", fake_rcon)

    names, total, _ = await PingTab(None)._collect()
    assert total == len(names) == 3


# ── дисковый кэш голов (раньше не исполнялся тестами ни разу) ────────────

def _write_head(directory, nick: str, colour=(0, 255, 0, 255)) -> None:
    buffer = io.BytesIO()
    Image.new("RGBA", (8, 8), colour).save(buffer, format="PNG")
    (directory / f"{nick}.png").write_bytes(buffer.getvalue())


async def test_fetch_heads_serves_from_disk_without_touching_network(tmp_path, monkeypatch):
    monkeypatch.setattr("bot.handlers.ping_tab.HEAD_CACHE_DIR", tmp_path)
    _write_head(tmp_path, "vxpbuser")

    async def explode(*args, **kwargs):
        raise AssertionError("голова лежит в кэше, в сеть ходить нельзя")

    monkeypatch.setattr("bot.handlers.ping_tab._download_head", explode)

    heads = await fetch_heads(["vxpbuser"])
    assert list(heads) == ["vxpbuser"]


async def test_fetch_heads_refetches_when_cache_expired(tmp_path, monkeypatch):
    """Протухшая голова обязана поехать в сеть заново — TTL должен работать."""
    monkeypatch.setattr("bot.handlers.ping_tab.HEAD_CACHE_DIR", tmp_path)
    _write_head(tmp_path, "vxpbuser")
    stale = time.time() - (HEAD_TTL + HEAD_TTL_JITTER + 86400)
    os.utime(tmp_path / "vxpbuser.png", (stale, stale))

    asked: list[str] = []

    async def fake_download(session, nick):
        asked.append(nick)
        return Image.new("RGBA", (8, 8), (255, 0, 0, 255))

    monkeypatch.setattr("bot.handlers.ping_tab._download_head", fake_download)

    await fetch_heads(["vxpbuser"])
    assert asked == ["vxpbuser"]


def test_head_ttl_is_spread_out_so_the_cache_does_not_expire_at_once():
    """Прогрев кладёт файлы за секунды — с общим TTL они протухнут одной минутой."""
    spread = {_head_ttl(f"player{i:03d}") for i in range(200)}
    assert len(spread) > 100, "срок годности одинаковый — будет залп через месяц"
    assert all(HEAD_TTL <= value < HEAD_TTL + HEAD_TTL_JITTER for value in spread)
    assert _head_ttl("vxpbuser") == _head_ttl("vxpbuser"), "разброс обязан быть стабильным"


# ── прогрев кэша голов ───────────────────────────────────────────────────

def test_warm_heads_reads_usercache_json():
    from scripts.warm_heads import parse_nicks

    raw = json.dumps([
        {"name": "vxpbuser", "uuid": "91857d9d-9e49-4767-ab29-7d8a473136f9"},
        {"name": "Lykeza", "uuid": "7cac655c-803d-43d8-a036-0986cb5b6552"},
    ])
    assert parse_nicks(raw) == ["vxpbuser", "Lykeza"]


def test_warm_heads_reads_a_plain_list():
    from scripts.warm_heads import parse_nicks

    assert parse_nicks("vxpbuser\nLykeza\n\n Chashi_ ") == ["vxpbuser", "Lykeza", "Chashi_"]


def test_warm_heads_deduplicates():
    from scripts.warm_heads import parse_nicks

    assert parse_nicks("vxpbuser, Lykeza, vxpbuser") == ["vxpbuser", "Lykeza"]


# ── RCON: несколько команд в одном соединении ────────────────────────────

@contextlib.asynccontextmanager
async def _serving(handler, port):
    """Поднятый сервер, который ГАРАНТИРОВАННО отпускает тест.

    🛑 Штатный `async with server` использовать нельзя: на Python 3.12 (а CI
    гоняет именно его) `Server.wait_closed()` не будит ожидающих, если клиент
    отвалился с ошибкой, а обработчик его пережил. Тест виснет навсегда —
    именно так один зависший тест съел десятиминутный лимит job'а и отменил
    деплой прода. На 3.14 того же зависания нет, поэтому локально всё зелёное.
    Нам от сервера нужно только закрыть слушающий сокет — этого достаточно.
    """
    server = await asyncio.start_server(handler, "127.0.0.1", port)
    try:
        yield server
    finally:
        server.close()


def _packet(req_id: int, ptype: int, payload: str) -> bytes:
    # req_id знаковый: провал аутентификации сервер отвечает -1.
    body = payload.encode() + b"\x00\x00"
    return struct.pack("<Iii", 8 + len(body), req_id, ptype) + body


async def test_rcon_exec_many_keeps_response_order(unused_tcp_port):
    """Три команды в одном соединении возвращаются в порядке запроса."""
    answers = {2: "имена", 4: "список", 6: "тпс"}

    async def serve(reader, writer):
        while True:
            header = await reader.readexactly(4)
            length = struct.unpack("<I", header)[0]
            data = await reader.readexactly(length)
            req_id, ptype = struct.unpack("<II", data[0:8])
            if ptype == 3:  # auth
                writer.write(_packet(req_id, 2, ""))
            else:
                writer.write(_packet(req_id, 0, answers.get(req_id, "")))
            await writer.drain()

    async with _serving(serve, unused_tcp_port):
        result = await rcon_exec_many(
            "127.0.0.1", unused_tcp_port, "pass", ["a", "b", "c"], timeout=5.0
        )
    assert result == ["имена", "список", "тпс"]


async def test_rcon_exec_many_never_sends_two_packets_in_one_read(unused_tcp_port):
    """Регрессия: ванильный RCON рвёт связь, получив два пакета за один read().

    Сервер здесь ведёт себя ровно как ванильный `RconClient`: читает всё, что
    накопилось, и если длина первого пакета не сходится с объёмом прочитанного —
    закрывает соединение. Клиент, шлющий команду и маркер одним всплеском,
    падает тут с IncompleteReadError (наблюдалось на проде 08.08.2026).
    """
    answers = {2: "имена", 4: "список", 6: "тпс"}

    async def serve(reader, writer):
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    return
                declared = struct.unpack("<I", data[0:4])[0]
                if declared != len(data) - 4:
                    return  # как вправду: молча закрываем соединение
                req_id, ptype = struct.unpack("<ii", data[4:12])
                if ptype == 3:
                    writer.write(_packet(req_id, 2, ""))
                else:
                    writer.write(_packet(req_id, 0, answers.get(req_id, "")))
                await writer.drain()
        finally:
            writer.close()

    async with _serving(serve, unused_tcp_port):
        result = await rcon_exec_many(
            "127.0.0.1", unused_tcp_port, "pass", ["a", "b", "c"], timeout=5.0
        )
    assert result == ["имена", "список", "тпс"]


async def test_rcon_exec_many_returns_empty_for_a_silent_command(
    unused_tcp_port, monkeypatch,
):
    """Команда без вывода обязана отдать пустую строку, а не повиснуть.

    Регрессия из ревью: маркер стали слать только ПОСЛЕ первого пакета, и
    команда, на которую сервер молчит, начала падать по таймауту. Бьёт по
    админской `/rcon`, а не только по `!пинг`.
    """
    # Боевые две секунды защищают медленный сервер, но сам протокол маркера
    # проверяется и с коротким ожиданием; бюджеты времени покрыты отдельно.
    monkeypatch.setattr("bot.handlers.rcon.FIRST_PACKET_WAIT", 0.01)

    async def serve(reader, writer):
        while True:
            header = await reader.readexactly(4)
            length = struct.unpack("<I", header)[0]
            data = await reader.readexactly(length)
            # `data` уже без 4-байтового префикса длины: id и тип лежат в начале.
            req_id, ptype = struct.unpack("<ii", data[0:8])
            payload = data[8:-2].decode()
            if ptype == 3:
                writer.write(_packet(req_id, 2, ""))
            elif payload == "":          # маркер — отвечаем
                writer.write(_packet(req_id, 0, ""))
            else:                        # на саму команду молчим
                continue
            await writer.drain()

    async with _serving(serve, unused_tcp_port):
        result = await asyncio.wait_for(
            rcon_exec_many("127.0.0.1", unused_tcp_port, "pass", ["тихая"], timeout=5.0),
            timeout=10.0,
        )
    assert result == [""]


async def test_rcon_exec_many_rejects_an_absurd_packet_length(unused_tcp_port):
    """Битый заголовок не должен заставлять читателя копить буфер до таймаута."""
    async def serve(reader, writer):
        await reader.readexactly(4)
        header = await reader.readexactly(0)  # noqa: F841 — просто ждём
        writer.write(struct.pack("<I", 0xFFFFFFFF))
        await writer.drain()
        await asyncio.sleep(5)

    async with _serving(serve, unused_tcp_port):
        with pytest.raises(ValueError):
            await asyncio.wait_for(
                rcon_exec_many("127.0.0.1", unused_tcp_port, "pass", ["a"], timeout=5.0),
                timeout=8.0,
            )


async def test_collect_is_bounded_by_a_total_budget(monkeypatch):
    """Попакетный таймаут не даёт границы на вызов — она нужна отдельно.

    Без неё три команды растягивались на минуты, и всё это время был закрыт
    общий замок снимка.
    """
    from bot.handlers.ping_tab import PingTab

    async def never(*args, **kwargs):
        await asyncio.sleep(60)

    monkeypatch.setattr("bot.handlers.ping_tab.rcon_exec_many", never)
    monkeypatch.setattr("bot.handlers.ping_tab.RCON_TOTAL_BUDGET", 0.05)
    monkeypatch.setenv("RCON_HOST", "10.0.0.1")
    monkeypatch.setenv("RCON_PASSWORD", "secret")

    started = time.monotonic()
    with pytest.raises(asyncio.TimeoutError):
        await PingTab(None)._collect()
    assert time.monotonic() - started < 1.0


async def test_retries_as_a_whole_are_bounded_too(monkeypatch):
    """Потолок на одну попытку не ограничивает серию.

    Замок снимка держится ВСЮ серию: три полных попытки плюс паузы — это была
    та же болезнь этажом выше, под сорок восемь секунд общей блокировки.
    """
    from bot.handlers.ping_tab import PingTab

    budgets: list[float | None] = []

    async def flaky(self, budget=None):
        budgets.append(budget)
        await asyncio.sleep(0.05)
        raise OSError("сеть моргнула")

    monkeypatch.setattr(PingTab, "_collect", flaky)
    monkeypatch.setattr("bot.handlers.ping_tab.RCON_RETRY_BUDGET", 0.12)
    monkeypatch.setattr("bot.handlers.ping_tab.RCON_RETRY_DELAY", 0.01)

    started = time.monotonic()
    assert await PingTab(None)._collect_with_retries(_FakeContext(), []) is None
    assert time.monotonic() - started < 0.5, "серия не остановилась по бюджету"
    assert len(budgets) < 3, "бюджет серии не оборвал попытки"
    assert all(b is not None and b <= 0.12 for b in budgets), \
        "попытке выдан бюджет больше, чем осталось у серии"


async def test_drain_waits_for_the_tasks_it_cancels():
    """`cancel()` без ожидания НЕ завершает задачу: она умирает уже после того,
    как `async with` закрыл сессию под ней."""
    started = asyncio.Event()
    finished: list[int] = []

    async def slow():
        started.set()
        try:
            await asyncio.sleep(5)
        finally:
            await asyncio.sleep(0)  # уборке тоже нужен шаг цикла
            finished.append(1)

    task = asyncio.create_task(slow())
    await started.wait()
    await ping_tab._drain([task])

    assert task.done(), "задача не дождана — отмена только заказана"
    assert finished == [1], "уборка внутри задачи не доработала"


async def test_fetch_heads_never_leaves_a_download_running(monkeypatch):
    """Опоздавшие качалки снимаются и ДОЖИДАЮТСЯ.

    `asyncio.wait` не отменяет опоздавших сам, а `async with` закрывает сессию
    сразу после него — живые задачи остались бы стучаться в закрытую сессию.
    """
    cancelled: list[str] = []

    async def slow(session, nick):
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            cancelled.append(nick)
            raise
        return None

    monkeypatch.setattr("bot.handlers.ping_tab._download_head", slow)
    monkeypatch.setattr("bot.handlers.ping_tab.HEADS_TOTAL_TIMEOUT", 0.05)
    monkeypatch.setattr("bot.handlers.ping_tab._read_cached_heads", lambda nicks: ({}, list(nicks)))

    assert await fetch_heads(["Lykeza", "vxpbuser"]) == {}
    assert sorted(cancelled) == ["Lykeza", "vxpbuser"], "качалки остались жить после выхода"


async def test_rcon_exec_many_raises_on_wrong_password(unused_tcp_port):
    async def serve(reader, writer):
        header = await reader.readexactly(4)
        length = struct.unpack("<I", header)[0]
        await reader.readexactly(length)
        writer.write(_packet(-1, 2, ""))
        await writer.drain()

    async with _serving(serve, unused_tcp_port):
        with pytest.raises(PermissionError):
            await rcon_exec_many("127.0.0.1", unused_tcp_port, "bad", ["a"], timeout=5.0)
