"""Тесты ротации вендоров bot.ai.llm.LLM.

Клиенты вендоров подменяются вручную (vendor._client = AsyncMock()-подобный
объект) — никакой реальной сети/ключей. Используем свежий LLM()-инстанс
(не модульный синглтон llm), чтобы тесты не делили состояние (кулдауны)
друг с другом.
"""

from unittest.mock import AsyncMock, MagicMock

import httpx
import openai
import pytest
from pydantic import BaseModel

from bot.ai.llm import LLM, AIUnavailable, _Vendor, strip_thoughts


def _rate_limit_error() -> openai.RateLimitError:
    request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
    response = httpx.Response(429, request=request, json={"error": {"message": "rate limited"}})
    return openai.RateLimitError("rate limited", response=response, body=None)


def _fake_completion(content: str):
    """Минимальный дублёр openai.ChatCompletion, которого хватает _execute/ask."""
    message = MagicMock()
    message.content = content
    choice = MagicMock()
    choice.message = message
    completion = MagicMock()
    completion.choices = [choice]
    completion.usage = None  # не даём _track_usage лезть во флаги/БД
    return completion


def make_vendor(env: str) -> _Vendor:
    vendor = _Vendor({"model": f"model-{env}", "base_url": "https://example.invalid", "env": env})
    vendor._client = MagicMock()  # подменяем ленивое свойство .client целиком
    vendor._client.chat.completions.create = AsyncMock()
    vendor._client.chat.completions.parse = AsyncMock()
    return vendor


@pytest.fixture
def llm_instance():
    inst = LLM()
    inst._loaded = True  # не даём _ensure_loaded читать data/ai_settings.yaml
    return inst


@pytest.mark.asyncio
async def test_rate_limit_on_first_vendor_falls_back_to_second(llm_instance):
    vendor1 = make_vendor("V1")
    vendor2 = make_vendor("V2")
    vendor1._client.chat.completions.create.side_effect = _rate_limit_error()
    vendor2._client.chat.completions.create.return_value = _fake_completion("ответ от второго вендора")

    llm_instance.vendors = [vendor1, vendor2]

    answer = await llm_instance.ask("привет")

    assert answer == "ответ от второго вендора"
    vendor1._client.chat.completions.create.assert_awaited_once()
    vendor2._client.chat.completions.create.assert_awaited_once()
    # Первый вендор ушёл в кулдаун (15 минут)
    assert vendor1.available is False
    assert vendor2.available is True


@pytest.mark.asyncio
async def test_all_vendors_on_cooldown_raises_ai_unavailable(llm_instance):
    vendor1 = make_vendor("V1")
    vendor2 = make_vendor("V2")
    vendor1.cooldown(9999)
    vendor2.cooldown(9999)
    llm_instance.vendors = [vendor1, vendor2]

    with pytest.raises(AIUnavailable):
        await llm_instance.ask("привет")

    vendor1._client.chat.completions.create.assert_not_awaited()
    vendor2._client.chat.completions.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_vendors_available_raises_ai_unavailable(llm_instance):
    llm_instance.vendors = []
    with pytest.raises(AIUnavailable):
        await llm_instance.ask("привет")


class _FakeSchema(BaseModel):
    answer: str


@pytest.mark.asyncio
async def test_parse_builds_params_without_stream_key(llm_instance):
    vendor = make_vendor("UTIL")
    parsed_message = MagicMock()
    parsed_message.parsed = _FakeSchema(answer="42")
    choice = MagicMock()
    choice.message = parsed_message
    completion = MagicMock()
    completion.choices = [choice]
    completion.usage = None
    vendor._client.chat.completions.parse.return_value = completion

    llm_instance.utility = vendor

    result = await llm_instance.parse("вопрос", _FakeSchema)

    assert result == _FakeSchema(answer="42")
    vendor._client.chat.completions.parse.assert_awaited_once()
    _, kwargs = vendor._client.chat.completions.parse.await_args
    assert "stream" not in kwargs


@pytest.mark.asyncio
async def test_parse_without_utility_raises_ai_unavailable(llm_instance):
    llm_instance.utility = None
    with pytest.raises(AIUnavailable):
        await llm_instance.parse("вопрос", _FakeSchema)


# -------- strip_thoughts --------


def test_strip_thoughts_removes_closed_tag():
    assert strip_thoughts("<thought>я размышляю</thought>Привет, котик!") == "Привет, котик!"


def test_strip_thoughts_removes_unclosed_tag():
    # ответ обрезали по max_tokens прямо посреди размышления — закрывающего тега нет
    assert strip_thoughts("Держи ответ.\n<thought>дальше меня обрезало на середине") == "Держи ответ."


def test_strip_thoughts_keeps_plain_text():
    assert strip_thoughts("Просто ответ без размышлений") == "Просто ответ без размышлений"


def test_strip_thoughts_removes_tag_in_the_middle():
    # закрывающий тег и следующий за ним пробел съедаются \s* — склейки слов не будет
    assert strip_thoughts("До <thought>секрет</thought> После") == "До После"


def test_strip_thoughts_empty_string():
    assert strip_thoughts("") == ""
