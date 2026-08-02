"""Миграция дедупа багов на новую модель эмбеддингов.

Сеть не трогаем: `embed` подменяется. Проверяем то, что стоило реальных
оплаченных запросов — докатываемость и единственность точки отката.
"""

import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "migrate_bug_index", ROOT / "scripts" / "migrate_bug_index.py"
)
mig = importlib.util.module_from_spec(_spec)
sys.modules["migrate_bug_index"] = mig
_spec.loader.exec_module(mig)

MODEL = "gemini-embedding-2"


@pytest.fixture
def index(tmp_path, monkeypatch):
    path = tmp_path / "bug_index.json"
    path.write_text(
        json.dumps(
            {
                str(i): {"text": f"баг {i}", "url": f"u{i}", "vector": [0.1] * 768}
                for i in range(5)
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(mig, "INDEX_PATH", path)
    monkeypatch.setattr(mig, "RPM", 10**6)
    monkeypatch.setattr(mig, "RETRIES", 1)
    monkeypatch.setattr(sys, "argv", ["migrate_bug_index"])
    return path


def _stub_embed(fn):
    stub = types.ModuleType("bot.ai.embeddings")
    stub.embed = fn
    stub.MODEL = MODEL
    stub.DIMENSIONS = 768
    sys.modules["bot.ai.embeddings"] = stub


def _run():
    return asyncio.run(mig.main())


def test_progress_survives_quota_exhaustion(index):
    # 🛑 Всё-или-ничего здесь не работает: когда квоты остаётся на пару
    # запросов, такой скрипт выбрасывает оплаченное и не доедет никогда
    # (прецедент 02.08 — три вектора в трубу).
    calls = {"n": 0}

    async def flaky(text):
        calls["n"] += 1
        if calls["n"] > 2:
            raise RuntimeError("429 quota")
        return [0.9] * 768

    _stub_embed(flaky)
    with pytest.raises(RuntimeError, match="прогресс сохранён"):
        _run()

    saved = json.loads(index.read_text(encoding="utf-8"))
    assert sum(1 for v in saved.values() if v.get("model") == MODEL) == 2


def test_second_run_only_picks_up_what_is_left(index):
    calls = {"n": 0}

    async def flaky(text):
        calls["n"] += 1
        if calls["n"] > 2:
            raise RuntimeError("429 quota")
        return [0.9] * 768

    _stub_embed(flaky)
    with pytest.raises(RuntimeError):
        _run()
    migrated_first = {
        k for k, v in json.loads(index.read_text(encoding="utf-8")).items()
        if v.get("model") == MODEL
    }

    async def ok(text):
        return [0.8] * 768

    _stub_embed(ok)
    assert _run() == 0

    saved = json.loads(index.read_text(encoding="utf-8"))
    assert all(v["model"] == MODEL for v in saved.values())
    # Уже посчитанное не пересчитывается заново — иначе докат стоил бы столько
    # же, сколько полный прогон
    assert all(saved[k]["vector"][0] == 0.9 for k in migrated_first)


def test_backup_is_made_once_from_the_pre_migration_state(index):
    calls = {"n": 0}

    async def flaky(text):
        calls["n"] += 1
        if calls["n"] > 2:
            raise RuntimeError("429 quota")
        return [0.9] * 768

    _stub_embed(flaky)
    with pytest.raises(RuntimeError):
        _run()

    async def ok(text):
        return [0.8] * 768

    _stub_embed(ok)
    _run()

    backups = sorted(index.parent.glob("bug_index.bak-*"))
    assert len(backups) == 1, [p.name for p in backups]
    # Точка отката — состояние ДО миграции, а не наполовину переехавшее
    restored = json.loads(backups[0].read_text(encoding="utf-8"))
    assert not any(v.get("model") for v in restored.values())


def test_nothing_to_do_is_not_an_error(index):
    async def ok(text):
        return [0.8] * 768

    _stub_embed(ok)
    assert _run() == 0
    assert _run() == 0  # повторный запуск на мигрированном индексе


def test_entry_without_text_is_kept_not_deleted(index, capsys):
    data = json.loads(index.read_text(encoding="utf-8"))
    data["без-текста"] = {"url": "u", "vector": [0.1] * 768}
    index.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    async def ok(text):
        return [0.8] * 768

    _stub_embed(ok)
    _run()

    saved = json.loads(index.read_text(encoding="utf-8"))
    # Пересчитать нельзя, но и молча удалять нечестно: дедуп её пропустит,
    # потому что bugs.py сверяет model
    assert "без-текста" in saved
    assert saved["без-текста"].get("model") is None
