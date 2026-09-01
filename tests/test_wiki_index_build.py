"""Тесты генератора вики-индекса (scripts/build_wiki_index.py).

Сеть не трогаем: проверяем чистые функции — извлечение текста из Markdown и
резку страницы на чанки. Именно они решают, что попадёт в эмбеддинги.
"""

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "build_wiki_index", ROOT / "scripts" / "build_wiki_index.py"
)
build = importlib.util.module_from_spec(_spec)
sys.modules["build_wiki_index"] = build
_spec.loader.exec_module(build)


PAGE_TEXT = """# Пивоварение

Десятки новых рецептов.

## Как варить?

Ставишь котёл, наливаешь водички.

- раз
- два

## Как бродить?

Нужна бочка.
"""

MARKDOWN_PAGE = """---
aside: false
description: служебное описание
---

# Фотоаппарат

<!-- Старый рецепт: алмаз -->
<ItemCard>
<Image alt="Фотоаппарат" src="/assets/camera.webp" />
<template #content>
Ослепляет цель во время вспышки.
</template>
</ItemCard>

## Получение

<CraftingGrid :ingredients="cameraRecipe" />

<script setup>
import CraftingGrid from './CraftingGrid.vue'
const spyglass = { image: "https://example.com/a.png", name: "Подзорная труба" }
const copper = { name: 'Медный слиток', count: 4 }
const cameraRecipe = [
  [null, spyglass, null],
  [copper, copper, copper],
]
const cameraResult = { image: '/assets/camera.webp', name: 'Фотоаппарат', count: 1 }
</script>
"""


def test_markdown_to_text_removes_service_markup_but_keeps_content():
    text = build.markdown_to_text(MARKDOWN_PAGE)

    assert "aside: false" not in text
    assert "Старый рецепт" not in text
    assert "<ItemCard>" not in text
    assert "import CraftingGrid" not in text
    assert "# Фотоаппарат" in text
    assert "Ослепляет цель" in text


def test_markdown_to_text_keeps_component_alt_and_recipe_names():
    text = build.markdown_to_text(MARKDOWN_PAGE)

    assert "Подзорная труба" in text
    assert "Медный слиток" in text
    assert "Фотоаппарат" in text
    assert "https://example.com" not in text
    assert "/assets/camera.webp" not in text


def test_markdown_to_text_keeps_recipe_layout_and_result_count():
    text = build.markdown_to_text(MARKDOWN_PAGE)

    assert "cameraRecipe" in text
    assert "[copper, copper, copper]" in text
    assert "const cameraResult" in text
    assert "name: 'Фотоаппарат', count: 1" in text
    assert "name: 'Медный слиток', count: 4" in text


def test_markdown_to_text_preserves_inline_sentences_and_splits_blocks():
    text = build.markdown_to_text(
        "Купи <b>меч</b> у <a href='#'>НПС</a>."
        "<button>Запрещено</button><button>Разрешено</button>"
    )

    assert "Купи меч у НПС." in text
    assert "ЗапрещеноРазрешено" not in text


def test_load_src_excludes_reads_vitepress_config(tmp_path):
    config = tmp_path / ".vitepress" / "config.mjs"
    config.parent.mkdir()
    config.write_text(
        """export default defineConfig({
          srcExclude: [
            'docs/**', 'README.md',
            // В тексте комментария 'не-паттерн.md'
            'gameplay/hidden.md', // причина
          ],
        })""",
        encoding="utf-8",
    )

    assert build.load_src_excludes(tmp_path) == [
        "docs/**",
        "README.md",
        "gameplay/hidden.md",
    ]


def test_build_chunks_reads_only_published_markdown(tmp_path):
    config = tmp_path / ".vitepress" / "config.mjs"
    config.parent.mkdir()
    config.write_text(
        "srcExclude: ['docs/**', 'hidden.md'],",
        encoding="utf-8",
    )
    (tmp_path / "guide.md").write_text(
        "# Гайд\n\nДостаточно длинный полезный текст страницы.", encoding="utf-8"
    )
    (tmp_path / "hidden.md").write_text(
        "# Скрыто\n\nЭтого знания в индексе быть не должно.", encoding="utf-8"
    )
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "plan.md").write_text(
        "# План\n\nВнутренний документ команды.", encoding="utf-8"
    )

    chunks = build.build_chunks(tmp_path)

    assert {chunk["source"] for chunk in chunks} == {"guide"}
    assert {chunk["url"] for chunk in chunks} == {"https://wiki.catcraft.ru/guide"}


def test_chunk_page_splits_on_h2():
    chunks = build.chunk_page(PAGE_TEXT, "gameplay/unique/brewery", "u")
    assert [c["heading"] for c in chunks] == ["Пивоварение", "Как варить?"]


def test_chunk_page_merges_short_section_instead_of_dropping_it():
    # «## Как бродить? / Нужна бочка.» коротко для своего вектора, но это ответ
    # на вопрос игрока — он обязан остаться в индексе, приклеенным к соседу.
    chunks = build.chunk_page(PAGE_TEXT, "src", "u")
    assert any("Нужна бочка." in c["text"] for c in chunks)
    assert any("Как бродить?" in c["text"] for c in chunks)


def test_chunk_page_repeats_title_in_every_chunk():
    # Без титула чанк «## Как бродить?» эмбеддится без слова «пивоварение»
    # и не находится по запросу про алкоголь.
    chunks = build.chunk_page(PAGE_TEXT, "src", "u")
    assert all(c["text"].startswith("# Пивоварение") for c in chunks)


def test_chunk_page_hash_is_stable_and_unique():
    first = build.chunk_page(PAGE_TEXT, "src", "u")
    second = build.chunk_page(PAGE_TEXT, "src", "u")
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
    from bot.ai.embeddings import MODEL

    index = _index(tmp_path, "i.json", [{"hash": "a", "vector": [0.1] * 768, "model": MODEL}])
    out = _index(tmp_path, "o.json", [{"hash": "b", "vector": [0.2] * 768, "model": MODEL}])
    monkeypatch.setattr(build, "INDEX_PATH", index)
    monkeypatch.setattr(build, "PARTIAL_PATH", tmp_path / "нет.json")
    assert set(build.load_reuse(full=False, out=out)) == {"a", "b"}


def test_load_reuse_rejects_vectors_of_wrong_dimension(tmp_path, monkeypatch):
    from bot.ai.embeddings import MODEL

    index = _index(tmp_path, "i.json", [
        {"hash": "ok", "vector": [0.1] * 768, "model": MODEL},
        {"hash": "другая размерность", "vector": [0.1] * 1536, "model": MODEL},
    ])
    monkeypatch.setattr(build, "INDEX_PATH", index)
    monkeypatch.setattr(build, "PARTIAL_PATH", tmp_path / "нет.json")
    assert set(build.load_reuse(full=False)) == {"ok"}


def test_load_reuse_rejects_vectors_from_another_model(tmp_path, monkeypatch):
    # 🛑 `hash` считается по ТЕКСТУ: без сверки модели инкрементальный прогон
    # после переезда переиспользовал бы старые векторы, и в индексе оказались бы
    # два несовместимых пространства — молча, без единой ошибки.
    from bot.ai.embeddings import MODEL

    index = _index(tmp_path, "i.json", [
        {"hash": "свой", "vector": [0.1] * 768, "model": MODEL},
        {"hash": "чужой", "vector": [0.1] * 768, "model": "gemini-embedding-001"},
        {"hash": "без метки", "vector": [0.1] * 768},
    ])
    monkeypatch.setattr(build, "INDEX_PATH", index)
    monkeypatch.setattr(build, "PARTIAL_PATH", tmp_path / "нет.json")
    assert set(build.load_reuse(full=False)) == {"свой"}


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


async def test_embed_reuse_keeps_model_marker():
    from bot.ai.embeddings import MODEL

    vector = [0.1] * 768
    chunks = [{"hash": "unchanged", "text": "тот же текст"}]

    result = await build.embed_chunks(chunks, {"unchanged": vector})

    assert result[0]["vector"] is vector
    assert result[0]["model"] == MODEL


async def test_embed_only_calls_api_for_changed_chunks(monkeypatch):
    calls: list[str] = []

    async def fake_embed(text):
        calls.append(text)
        return [0.5] * 768

    stub = type(sys)("bot.ai.embeddings")
    stub.embed = fake_embed
    stub.MODEL = "тестовая-модель"
    monkeypatch.setitem(sys.modules, "bot.ai.embeddings", stub)
    monkeypatch.setattr(build, "EMBED_RPM", 60_000)

    old_vector = [0.1] * 768
    chunks = [
        {"hash": "same", "text": "не менялся", "source": "a"},
        {"hash": "new", "text": "новый текст", "source": "b"},
    ]
    result = await build.embed_chunks(chunks, {"same": old_vector})

    assert calls == ["новый текст"]
    assert result[0]["vector"] is old_vector
    assert result[1]["vector"] == [0.5] * 768


def test_embed_stops_at_budget_and_saves_progress(tmp_path, monkeypatch):
    # 🛑 Суточная квота общая с живым ботом: 02.08.2026 пересборка выбрала её
    # до дна, и до конца суток вики-поиск отвечал «Поиск не удался».
    # Урезание обязано быть громким и сохранять прогресс, а не молчать.
    partial = tmp_path / "partial.json"
    monkeypatch.setattr(build, "PARTIAL_PATH", partial)
    # Интервалы RPM отдельно проверяет test_rate_limiter_spaces_out_calls;
    # здесь нужны бюджет и partial, а не две секунды настоящего троттлинга.
    monkeypatch.setattr(build, "EMBED_RPM", 60_000)

    async def fake_embed(text):
        return [0.5] * 768

    stub = type(sys)("bot.ai.embeddings")
    stub.embed = fake_embed
    stub.MODEL = "тестовая-модель"
    monkeypatch.setitem(sys.modules, "bot.ai.embeddings", stub)

    chunks = [{"hash": str(i), "text": f"чанк {i}", "source": "s"} for i in range(5)]
    with pytest.raises(RuntimeError, match="Бюджет прогона"):
        asyncio.run(build.embed_chunks(chunks, reuse={}, budget=3))

    saved = json.loads(partial.read_text(encoding="utf-8"))
    assert len(saved) == 3  # оплаченное не потеряно
    assert all(len(c["vector"]) == 768 for c in saved)


def test_partial_dump_is_atomic(tmp_path, monkeypatch):
    # Процесс, убитый посреди записи, иначе оставил бы обрезанный JSON, и
    # следующий запуск выбросил бы весь прогресс как битый.
    partial = tmp_path / "partial.json"
    monkeypatch.setattr(build, "PARTIAL_PATH", partial)
    real_replace = Path.replace
    seen: list[bool] = []

    def spy(self, target):
        # В момент подмены целевой файл либо ещё старый, либо уже целый —
        # промежуточного состояния «наполовину записан» быть не может
        seen.append(self.suffix == ".tmp")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", spy)
    build._dump_partial([{"hash": "a", "vector": [0.1] * 768}, {"hash": "b"}])

    assert seen == [True]
    assert len(json.loads(partial.read_text(encoding="utf-8"))) == 1


def test_rate_limiter_spaces_out_calls():
    async def run():
        limiter = build._RateLimiter(rpm=6000)  # интервал 10 мс
        loop = asyncio.get_running_loop()
        start = loop.time()
        await asyncio.gather(*(limiter.wait() for _ in range(5)))
        return loop.time() - start

    # Пять стартов с интервалом 10 мс — минимум 40 мс на последний
    assert asyncio.run(run()) >= 0.04
