"""Тесты веб-поиска: цепочка бэкендов (SearXNG → ddgs), кэш, деградация, тул движка.

Сеть не трогается: оба бэкенда подменяются на уровне инстанса, llm мокается.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.ai.web_search import MAX_RESULTS, WebSearcher


def _result(n: int) -> dict:
    return {"title": f"Заголовок {n}", "url": f"https://example.com/{n}", "snippet": f"сниппет {n}"}


def _backend(name: str, **kwargs) -> AsyncMock:
    # search() логирует backend.__name__ на фейлах — мокам нужно имя
    mock = AsyncMock(**kwargs)
    mock.__name__ = name
    return mock


async def test_search_uses_searxng_first_when_url_set(monkeypatch):
    ws = WebSearcher()
    monkeypatch.setenv("SEARXNG_URL", "http://127.0.0.1:8888")
    searx = _backend("searx", return_value=[_result(1)])
    ddgs = _backend("ddgs")
    monkeypatch.setattr(ws, "_search_searxng", searx)
    monkeypatch.setattr(ws, "_search_ddgs", ddgs)

    assert await ws.search("minecraft 1.22") == [_result(1)]
    searx.assert_awaited_once()
    ddgs.assert_not_awaited()


async def test_search_falls_back_to_ddgs_when_searxng_fails(monkeypatch):
    ws = WebSearcher()
    monkeypatch.setenv("SEARXNG_URL", "http://127.0.0.1:8888")
    monkeypatch.setattr(ws, "_search_searxng", _backend("searx", side_effect=RuntimeError("503")))
    monkeypatch.setattr(ws, "_search_ddgs", _backend("ddgs", return_value=[_result(2)]))

    assert await ws.search("запрос") == [_result(2)]


async def test_search_skips_searxng_without_env(monkeypatch):
    ws = WebSearcher()
    monkeypatch.delenv("SEARXNG_URL", raising=False)
    searx = _backend("searx")
    monkeypatch.setattr(ws, "_search_searxng", searx)
    monkeypatch.setattr(ws, "_search_ddgs", _backend("ddgs", return_value=[_result(3)]))

    assert await ws.search("запрос") == [_result(3)]
    searx.assert_not_awaited()


async def test_search_caches_results_case_insensitive(monkeypatch):
    ws = WebSearcher()
    monkeypatch.delenv("SEARXNG_URL", raising=False)
    ddgs = _backend("ddgs", return_value=[_result(4)])
    monkeypatch.setattr(ws, "_search_ddgs", ddgs)

    await ws.search("Кто такой Стив")
    await ws.search("кто такой стив")

    assert ddgs.await_count == 1


async def test_search_returns_empty_when_all_backends_fail(monkeypatch):
    ws = WebSearcher()
    monkeypatch.delenv("SEARXNG_URL", raising=False)
    monkeypatch.setattr(ws, "_search_ddgs", _backend("ddgs", side_effect=RuntimeError("боль")))

    assert await ws.search("запрос") == []


async def test_search_empty_query_returns_empty_without_backends():
    assert await WebSearcher().search("   ") == []


def test_normalize_caps_count_and_lengths():
    rows = [("т" * 500, f"https://e/{i}", "с" * 1000) for i in range(10)]
    results = WebSearcher._normalize(rows)
    assert len(results) == MAX_RESULTS
    assert all(len(r["title"]) <= 200 and len(r["snippet"]) <= 300 for r in results)


def test_normalize_drops_rows_without_url():
    results = WebSearcher._normalize([("title", None, "snippet"), ("t2", "https://e/2", "s2")])
    assert results == [{"title": "t2", "url": "https://e/2", "snippet": "s2"}]


def test_build_context_contains_sources():
    ctx = WebSearcher().build_context([_result(1), _result(2)])
    assert "[Источник: https://example.com/1]" in ctx
    assert "сниппет 2" in ctx


# -------- тул web_search в движке --------


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


async def test_engine_web_search_tool_feeds_results_to_model(monkeypatch):
    from bot.ai.engine import AIEngine, FinalAnswer

    engine = AIEngine()
    monkeypatch.setattr("bot.ai.engine.web.search", AsyncMock(return_value=[_result(7)]))

    tool_msgs_per_call = []

    async def fake_complete(conversation, *, tools=None, **kwargs):
        tool_msgs_per_call.append([m for m in conversation if m.get("role") == "tool"])
        if len(tool_msgs_per_call) == 1:
            return _response(tool_calls=[_tool_call("web_search", {"query": "minecraft 1.22 release date"})])
        return _response(content="Вышел вчера!")

    monkeypatch.setattr("bot.ai.engine.llm.complete", fake_complete)

    events = [e async for e in engine.generateAnswer(
        [{"role": "user", "content": "(вася)когда вышел майн 1.22?"}], user=None,
    )]

    finals = [e for e in events if isinstance(e, FinalAnswer)]
    assert len(finals) == 1
    assert finals[0].content == "Вышел вчера!"
    # результат поиска попал в tool-сообщение второго вызова модели
    assert len(tool_msgs_per_call[1]) == 1
    assert "https://example.com/7" in tool_msgs_per_call[1][0]["content"]


async def test_engine_web_search_empty_results_tells_model_honestly(monkeypatch):
    from bot.ai.engine import AIEngine, FinalAnswer

    engine = AIEngine()
    monkeypatch.setattr("bot.ai.engine.web.search", AsyncMock(return_value=[]))

    tool_msgs_per_call = []

    async def fake_complete(conversation, *, tools=None, **kwargs):
        tool_msgs_per_call.append([m for m in conversation if m.get("role") == "tool"])
        if len(tool_msgs_per_call) == 1:
            return _response(tool_calls=[_tool_call("web_search", {"query": "что-то"})])
        return _response(content="Поиск лежит, отвечаю по памяти.")

    monkeypatch.setattr("bot.ai.engine.llm.complete", fake_complete)

    events = [e async for e in engine.generateAnswer(
        [{"role": "user", "content": "(вася)вопрос"}], user=None,
    )]

    finals = [e for e in events if isinstance(e, FinalAnswer)]
    assert len(finals) == 1
    assert "Web search returned nothing" in tool_msgs_per_call[1][0]["content"]


# --- контракт реального пакета ddgs (без сети) ---
# Остальные тесты мокают адаптер: несовместимый релиз ddgs менял бы API,
# CI оставался зелёным, а прод молча жил без поиска. Пиним то, что можно
# проверить офлайн: пути импорта, исключения, сигнатуру DDGS.text.
# (Форму результата — ключи title/href/body — офлайн проверить нельзя;
# при смене мажора ddgs глянуть _search_ddgs руками.)


def test_ddgs_package_contract():
    import inspect

    from ddgs import DDGS
    from ddgs.exceptions import DDGSException, RatelimitException

    assert issubclass(RatelimitException, DDGSException)

    sig = inspect.signature(DDGS.text)
    accepts_kwargs = any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    )
    for name in ("region", "safesearch", "max_results"):
        assert accepts_kwargs or name in sig.parameters, f"DDGS.text потерял {name}"

    init_sig = inspect.signature(DDGS.__init__)
    init_kwargs = any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in init_sig.parameters.values()
    )
    assert init_kwargs or "timeout" in init_sig.parameters
