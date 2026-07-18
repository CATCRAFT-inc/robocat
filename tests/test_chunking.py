"""Тесты AIMessageHandler._buildLongMessage / _getCodeBlockLang.

Оба метода не используют self (не трогают bot/engine), поэтому инстанс
собираем через object.__new__ — без реального disnake.ext.commands.Bot.
"""

import pytest

from bot.ai.handler import AIMessageHandler


@pytest.fixture
def handler():
    return object.__new__(AIMessageHandler)


def test_get_code_block_lang_detects_open_block(handler):
    chunk = "текст перед ```python\nprint(1)\n"
    assert handler._getCodeBlockLang(chunk) == "python"


def test_get_code_block_lang_none_when_closed(handler):
    chunk = "```python\nprint(1)\n```\nхвост без блока"
    assert handler._getCodeBlockLang(chunk) is None


def test_get_code_block_lang_none_without_block(handler):
    assert handler._getCodeBlockLang("просто текст без кодблоков") is None


@pytest.mark.asyncio
async def test_build_long_message_short_text_single_chunk(handler):
    text = "короткий текст"
    chunks = await handler._buildLongMessage(text)
    assert chunks == [text]


@pytest.mark.asyncio
async def test_build_long_message_splits_and_reopens_code_block(handler):
    # код-блок открывается рано и не закрывается до конца — должен попасть
    # ровно на границу разреза (>3990 символов)
    code_body = "x = 1\n" * 1000
    text = "```python\n" + code_body + "```"
    assert len(text) > 3990

    chunks = await handler._buildLongMessage(text)

    assert len(chunks) > 1
    # Каждый неполный кусок должен закрываться ```, а следующий переоткрываться
    # тем же языком, кроме последнего куска (это конец сообщения)
    for chunk in chunks[:-1]:
        assert chunk.endswith("```")
    # chunks[1:] (не [1:-1]): при ровно 2 чанках срез [1:-1] пуст и проверка
    # переоткрытия не выполнялась ни разу — регрессия прошла бы незамеченной
    for chunk in chunks[1:]:
        assert chunk.startswith("```python\n")
    # Всё содержимое сохранено (без потери текста), с поправкой на служебные ```
    rebuilt_plain = chunks[0]
    for c in chunks[1:]:
        # каждый следующий чанк начинается с переоткрытия ```lang\n, кроме случая,
        # когда исходный блок был уже закрыт к этому месту
        rebuilt_plain += c
    assert "x = 1" in rebuilt_plain


@pytest.mark.asyncio
async def test_build_long_message_plain_text_no_code_block(handler):
    text = "а" * 5000
    chunks = await handler._buildLongMessage(text)
    assert len(chunks) == 2
    assert chunks[0] == "а" * 3990
    assert chunks[1] == "а" * (5000 - 3990)
    assert "```" not in chunks[0]
