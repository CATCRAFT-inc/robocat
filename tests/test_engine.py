"""Тесты generateAnswer: лимит тул-раундов не глотает ответ.

llm и wiki мокаются целиком — сети нет. Прецедент: вопрос «опиши каждый сезон»
заставлял модель искать по вики оба доступных раунда, и цикл вываливался в
заглушку «Слетели гайки», не дав модели ответить по собранному материалу.
Теперь после исчерпания раундов идёт финальный вызов БЕЗ тулов.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.ai.engine import AIEngine, FinalAnswer, _codex_safe_env


def _tool_call(name: str, args: dict):
    tc = MagicMock()
    tc.id = "tc-1"
    tc.function.name = name
    tc.function.arguments = json.dumps(args)
    return tc


def _response(content: str | None = None, tool_calls: list | None = None):
    message = MagicMock()
    message.content = content
    message.tool_calls = tool_calls
    message.model_dump.return_value = {"role": "assistant", "content": content or ""}
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    return response


@pytest.fixture
def engine(monkeypatch):
    inst = AIEngine()
    monkeypatch.setattr(
        "bot.ai.engine.wiki.search",
        AsyncMock(return_value=[{"url": "https://wiki.example/x", "text": "чанк"}]),
    )
    return inst


async def test_tool_rounds_exhausted_forces_final_answer_without_tools(engine, monkeypatch):
    tools_per_call = []

    async def fake_complete(conversation, *, tools=None, **kwargs):
        tools_per_call.append(tools)
        if len(tools_per_call) <= 2:
            # модель упорно ищет по вики оба раунда
            return _response(tool_calls=[_tool_call("search_wiki", {"query": f"сезон {len(tools_per_call)}"})])
        return _response(content="Вот история всех сезонов по порядку.")

    monkeypatch.setattr("bot.ai.engine.llm.complete", fake_complete)

    events = [e async for e in engine.generateAnswer(
        [{"role": "user", "content": "(вася)опиши все сезоны"}], user=None,
    )]

    finals = [e for e in events if isinstance(e, FinalAnswer)]
    assert len(finals) == 1
    assert finals[0].content == "Вот история всех сезонов по порядку."
    assert "Слетели гайки" not in finals[0].content
    # ровно 3 вызова: 2 тул-раунда + финальный, и финальный — строго без тулов
    assert len(tools_per_call) == 3
    assert tools_per_call[0] is not None and tools_per_call[1] is not None
    assert tools_per_call[2] is None


def test_codex_safe_env_strips_bot_secrets(monkeypatch):
    # codex-подпроцесс не должен видеть секреты бота (стоп-правило №1)
    monkeypatch.setenv("DISCORD_TOKEN", "secret")
    monkeypatch.setenv("GEMINI", "key")
    monkeypatch.setenv("RCON_PASSWORD", "pw")
    monkeypatch.setenv("FAILURE_WEBHOOK_URL", "url")
    monkeypatch.setenv("PATH", "/usr/bin")
    env = _codex_safe_env()
    for secret in ("DISCORD_TOKEN", "GEMINI", "RCON_PASSWORD", "FAILURE_WEBHOOK_URL"):
        assert secret not in env
    assert "PATH" in env  # нужное для запуска codex остаётся


async def test_empty_choices_yields_error_not_indexerror(engine, monkeypatch):
    async def fake_complete(conversation, *, tools=None, **kwargs):
        response = MagicMock()
        response.choices = []  # safety-фильтр вендора вернул пустой список
        return response

    monkeypatch.setattr("bot.ai.engine.llm.complete", fake_complete)

    from bot.ai.engine import AIError
    events = [e async for e in engine.generateAnswer(
        [{"role": "user", "content": "(вася)привет"}], user=None,
    )]
    assert any(isinstance(e, AIError) for e in events)
    assert not any(isinstance(e, FinalAnswer) for e in events)


async def test_tool_crash_does_not_kill_answer(engine, monkeypatch):
    # wiki.search падает → тул-раунд не должен убить весь ответ, модель дответит
    monkeypatch.setattr("bot.ai.engine.wiki.search", AsyncMock(side_effect=RuntimeError("БД лежит")))
    calls = []

    async def fake_complete(conversation, *, tools=None, **kwargs):
        calls.append(tools)
        if len(calls) == 1:
            return _response(tool_calls=[_tool_call("search_wiki", {"query": "x"})])
        return _response(content="Ответил по памяти.")

    monkeypatch.setattr("bot.ai.engine.llm.complete", fake_complete)

    events = [e async for e in engine.generateAnswer(
        [{"role": "user", "content": "(вася)что там по вики"}], user=None,
    )]
    finals = [e for e in events if isinstance(e, FinalAnswer)]
    assert len(finals) == 1
    assert finals[0].content == "Ответил по памяти."


async def test_malformed_tool_arguments_do_not_crash(engine, monkeypatch):
    # Gemma иногда отдаёт tool_calls с невалидным JSON в arguments
    bad_tc = MagicMock()
    bad_tc.id = "tc-bad"
    bad_tc.function.name = "search_wiki"
    bad_tc.function.arguments = "{не json"
    calls = []

    async def fake_complete(conversation, *, tools=None, **kwargs):
        calls.append(tools)
        if len(calls) == 1:
            return _response(tool_calls=[bad_tc])
        return _response(content="Всё равно ответил.")

    monkeypatch.setattr("bot.ai.engine.llm.complete", fake_complete)

    events = [e async for e in engine.generateAnswer(
        [{"role": "user", "content": "(вася)вопрос"}], user=None,
    )]
    finals = [e for e in events if isinstance(e, FinalAnswer)]
    assert len(finals) == 1
    assert finals[0].content == "Всё равно ответил."


async def test_plain_answer_returns_immediately(engine, monkeypatch):
    calls = []

    async def fake_complete(conversation, *, tools=None, **kwargs):
        calls.append(tools)
        return _response(content="Привет-привет!")

    monkeypatch.setattr("bot.ai.engine.llm.complete", fake_complete)

    events = [e async for e in engine.generateAnswer(
        [{"role": "user", "content": "(вася)привет"}], user=None,
    )]

    finals = [e for e in events if isinstance(e, FinalAnswer)]
    assert len(finals) == 1
    assert finals[0].content == "Привет-привет!"
    assert len(calls) == 1  # без тул-раундов — один вызов
