import json
import os

from bot.ai.wiki_search import WikiSearcher


def _write_index(path, chunks, mtime_ns):
    path.write_text(json.dumps(chunks), encoding="utf-8")
    os.utime(path, ns=(mtime_ns, mtime_ns))


def test_index_reloads_after_atomic_update(tmp_path):
    path = tmp_path / "wiki_index.json"
    first = [{"text": "старое", "vector": [1.0]}]
    second = [{"text": "новое", "vector": [2.0]}]
    _write_index(path, first, 1_000_000_000)

    searcher = WikiSearcher()
    searcher._index_file = path
    assert searcher.index == first

    replacement = tmp_path / "wiki_index.next.json"
    _write_index(replacement, second, 2_000_000_000)
    replacement.replace(path)

    assert searcher.index == second


def test_index_is_not_reparsed_while_mtime_is_unchanged(tmp_path, monkeypatch):
    path = tmp_path / "wiki_index.json"
    chunks = [{"text": "данные", "vector": [1.0]}]
    _write_index(path, chunks, 1_000_000_000)
    searcher = WikiSearcher()
    searcher._index_file = path

    assert searcher.index == chunks
    monkeypatch.setattr(
        "bot.ai.wiki_search.json.loads",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("повторный parse")),
    )

    assert searcher.index == chunks
