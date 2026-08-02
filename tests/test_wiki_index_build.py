"""Тесты генератора вики-индекса (scripts/build_wiki_index.py).

Сеть не трогаем: проверяем чистые функции — извлечение текста из HTML и резку
страницы на чанки. Именно они решают, что попадёт в эмбеддинги.
"""

import asyncio
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "build_wiki_index", ROOT / "scripts" / "build_wiki_index.py"
)
build = importlib.util.module_from_spec(_spec)
sys.modules["build_wiki_index"] = build
_spec.loader.exec_module(build)


PAGE = """<html><head><title>x</title><style>.a{color:red}</style></head>
<body><nav>Меню сайта</nav>
<main class="main"><div class="vp-doc">
<h1 id="p">Пивоварение <a class="header-anchor" href="#p">​</a></h1>
<p>Десятки новых рецептов.</p>
<h2 id="v">Как варить?</h2>
<p>Ставишь котёл,   наливаешь&nbsp;водички</p>
<ul><li>раз</li><li>два</li></ul>
<script>const x = {a: 1}; // это не текст страницы</script>
<h2 id="b">Как бродить?</h2>
<p>Нужна бочка.</p>
</div></main>
<footer>Подвал сайта</footer></body></html>"""


def test_html_to_text_takes_main_only():
    text = build.html_to_text(PAGE)
    assert "Меню сайта" not in text
    assert "Подвал сайта" not in text
    assert "color:red" not in text


def test_html_to_text_drops_script_body():
    assert "это не текст страницы" not in build.html_to_text(PAGE)
    assert "const x" not in build.html_to_text(PAGE)


def test_html_to_text_restores_heading_levels():
    text = build.html_to_text(PAGE)
    assert "# Пивоварение" in text
    assert "## Как варить?" in text
    # Якорь-ссылка рядом с заголовком не должна попадать в текст
    assert "​" not in text


def test_html_to_text_collapses_whitespace():
    text = build.html_to_text(PAGE)
    assert "котёл, наливаешь водички" in text
    assert "   " not in text
    assert "\n\n\n" not in text


def test_chunk_page_splits_on_h2():
    chunks = build.chunk_page(build.html_to_text(PAGE), "gameplay/unique/brewery", "u")
    assert [c["heading"] for c in chunks] == ["Пивоварение", "Как варить?"]


def test_chunk_page_merges_short_section_instead_of_dropping_it():
    # «## Как бродить? / Нужна бочка.» коротко для своего вектора, но это ответ
    # на вопрос игрока — он обязан остаться в индексе, приклеенным к соседу.
    chunks = build.chunk_page(build.html_to_text(PAGE), "src", "u")
    assert any("Нужна бочка." in c["text"] for c in chunks)
    assert any("Как бродить?" in c["text"] for c in chunks)


def test_chunk_page_repeats_title_in_every_chunk():
    # Без титула чанк «## Как бродить?» эмбеддится без слова «пивоварение»
    # и не находится по запросу про алкоголь.
    chunks = build.chunk_page(build.html_to_text(PAGE), "src", "u")
    assert all(c["text"].startswith("# Пивоварение") for c in chunks)


def test_chunk_page_hash_is_stable_and_unique():
    text = build.html_to_text(PAGE)
    first = build.chunk_page(text, "src", "u")
    second = build.chunk_page(text, "src", "u")
    assert [c["hash"] for c in first] == [c["hash"] for c in second]
    assert len({c["hash"] for c in first}) == len(first)


def test_chunk_page_caps_long_sections():
    para = "Очень длинный абзац про котиков. " * 40  # ~1300 символов
    text = "# Сезон\n\n## Статистика\n\n" + "\n\n".join([para] * 6)
    chunks = build.chunk_page(text, "history/5season/5season", "u")
    assert len(chunks) > 1
    # Титул дописывается после резки, поэтому запас сверх MAX_CHUNK
    assert all(len(c["text"]) <= build.MAX_CHUNK + 100 for c in chunks)
    assert all(c["heading"] == "Статистика" for c in chunks)


def test_chunk_page_drops_empty_page():
    assert build.chunk_page("", "src", "u") == []


def test_chunk_page_skips_page_without_content():
    # Ни в одной секции нет содержимого — приклеивать не к чему, чанков ноль
    assert build.chunk_page("# А\n\n## Б\n\nв", "src", "u") == []


def test_rate_limiter_spaces_out_calls():
    async def run():
        limiter = build._RateLimiter(rpm=6000)  # интервал 10 мс
        loop = asyncio.get_running_loop()
        start = loop.time()
        await asyncio.gather(*(limiter.wait() for _ in range(5)))
        return loop.time() - start

    # Пять стартов с интервалом 10 мс — минимум 40 мс на последний
    assert asyncio.run(run()) >= 0.04
