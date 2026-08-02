"""Тесты генератора вики-индекса (scripts/build_wiki_index.py).

Сеть не трогаем: проверяем чистые функции — извлечение текста из HTML и резку
страницы на чанки. Именно они решают, что попадёт в эмбеддинги.
"""

import asyncio
import importlib.util
import json
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


def test_chunk_page_keeps_short_page_whole():
    # Страница целиком короче порога («# 8 сезон / Скоро.») раньше исчезала из
    # индекса — бот переставал знать о её существовании.
    chunks = build.chunk_page("# 8 сезон\n\nЭта история ещё не началась.", "s", "u")
    assert len(chunks) == 1
    assert "не началась" in chunks[0]["text"]


def test_html_to_text_separates_table_cells():
    # На живой вики `<th>Иконка</th><th>Что обозначает?</th>` склеивалось в
    # «ИконкаЧто обозначает?» и доезжало до игрока слипшимся.
    html = ("<main><table><tr><th>Иконка</th><th>Что обозначает?</th></tr>"
            "<tr><td>Меч</td><td>100</td></tr></table></main>")
    text = build.html_to_text(html)
    assert "ИконкаЧто" not in text
    assert "Меч100" not in text
    assert "Меч" in text and "100" in text


def test_html_to_text_separates_arbitrary_component_tags():
    # Вики собрана из Vue-компонентов: PrimeVue рисует вкладки правил на
    # <button>, и «Запрещено»/«Разрешено» слипались в «ЗапрещеноРазрешено».
    # Правило инвертировано — блочным считается всё, кроме строчных тегов.
    html = ("<main><button><span>Запрещено</span></button>"
            "<button><span>Разрешено</span></button>"
            "<custom-card>Столица</custom-card><custom-card>Спавн</custom-card></main>")
    text = build.html_to_text(html)
    assert "ЗапрещеноРазрешено" not in text
    assert "СтолицаСпавн" not in text


def test_html_to_text_keeps_inline_tags_inline():
    # Обратная сторона инверсии: строчная разметка не должна рвать предложение
    html = "<main><p>Купи <b>меч</b> у <a href='#'>НПС</a> за 10 мурок</p></main>"
    assert "Купи меч у НПС за 10 мурок" in build.html_to_text(html)


def test_split_long_does_not_cut_mid_word():
    para = "слово " * 700  # ~4200 символов без единого перевода строки
    parts = build._split_long(para.strip())
    assert len(parts) > 1
    for part in parts:
        assert part.startswith("слово") and part.endswith("слово"), part


def _index(tmp_path, name, chunks):
    path = tmp_path / name
    path.write_text(json.dumps(chunks, ensure_ascii=False), encoding="utf-8")
    return path


def test_load_reuse_full_ignores_every_source(tmp_path, monkeypatch):
    # `--full` обязан значить «пересчитать всё»: раньше он всё равно тянул
    # partial и выдавал смешанное пространство векторов за полную пересборку.
    partial = _index(tmp_path, "p.json", [{"hash": "a", "vector": [0.0] * 768}])
    monkeypatch.setattr(build, "PARTIAL_PATH", partial)
    monkeypatch.setattr(build, "INDEX_PATH", partial)
    assert build.load_reuse(full=True) == {}


def test_load_reuse_with_out_still_reads_the_main_index(tmp_path, monkeypatch):
    # `--out` обязателен на проде. Раньше он ОТКЛЮЧАЛ переиспользование из
    # data/wiki_index.json, и «поправить одну страницу» жгло дневную квоту.
    index = _index(tmp_path, "i.json", [{"hash": "a", "vector": [0.1] * 768}])
    out = _index(tmp_path, "o.json", [{"hash": "b", "vector": [0.2] * 768}])
    monkeypatch.setattr(build, "INDEX_PATH", index)
    monkeypatch.setattr(build, "PARTIAL_PATH", tmp_path / "нет.json")
    assert set(build.load_reuse(full=False, out=out)) == {"a", "b"}


def test_load_reuse_rejects_vectors_of_wrong_dimension(tmp_path, monkeypatch):
    index = _index(tmp_path, "i.json", [
        {"hash": "ok", "vector": [0.1] * 768},
        {"hash": "чужая модель", "vector": [0.1] * 1536},
    ])
    monkeypatch.setattr(build, "INDEX_PATH", index)
    monkeypatch.setattr(build, "PARTIAL_PATH", tmp_path / "нет.json")
    assert set(build.load_reuse(full=False)) == {"ok"}


def test_load_reuse_survives_garbage_file(tmp_path, monkeypatch):
    broken = tmp_path / "b.json"
    broken.write_text('{"это": "словарь, а не список"}', encoding="utf-8")
    monkeypatch.setattr(build, "INDEX_PATH", broken)
    monkeypatch.setattr(build, "PARTIAL_PATH", tmp_path / "нет.json")
    assert build.load_reuse(full=False) == {}


def test_module_import_does_not_read_dotenv():
    # Импорт модуля тестами не должен затягивать настоящие значения из .env
    # в окружение процесса мимо фейков из conftest.
    source = (ROOT / "scripts" / "build_wiki_index.py").read_text(encoding="utf-8")
    body = source.split("def main(")[0]
    assert "load_dotenv(" not in body


def test_rate_limiter_spaces_out_calls():
    async def run():
        limiter = build._RateLimiter(rpm=6000)  # интервал 10 мс
        loop = asyncio.get_running_loop()
        start = loop.time()
        await asyncio.gather(*(limiter.wait() for _ in range(5)))
        return loop.time() - start

    # Пять стартов с интервалом 10 мс — минимум 40 мс на последний
    assert asyncio.run(run()) >= 0.04
