"""Тесты лога действий ИИ (issue #2): история вызовов тулов в сообщении.

_streamAnswer копит статусы: прошлые — мелкими -#-строками, текущий — обычным
текстом; в финальном ответе лог остаётся сверху. buildConverstaion срезает
-#-лог у сообщений бота, чтобы модель не перечитывала его как свой текст.
Discord и engine мокаются целиком — сети нет.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

import disnake

from bot.ai.engine import AIEngine, AIError, Context, FinalAnswer, Status
from bot.ai.handler import AIMessageHandler
from bot.utils import component_text


class _FakeTyping:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _FakeMessage:
    """Сообщение, отправленное ботом: копит правки."""

    def __init__(self, content):
        self.content = content
        self.edits = []
        self.deleted = False

    async def edit(self, content=None, **kwargs):
        self.edits.append(content)
        self.content = content

    async def delete(self):
        self.deleted = True


@pytest.fixture
def handler():
    inst = object.__new__(AIMessageHandler)
    inst.sent = []  # (content, kwargs) каждого _send

    async def fake_send(message, ping, content=None, **kwargs):
        fake = _FakeMessage(content)
        inst.sent.append((content, kwargs, fake))
        return fake

    inst._send = fake_send
    return inst


def _user_message():
    message = MagicMock()
    message.channel.typing = lambda: _FakeTyping()
    message.author = MagicMock()
    return message


def _engine_with(events):
    engine = MagicMock()

    async def gen(conversation, user):
        for e in events:
            yield e

    engine.generateAnswer = gen
    return engine


async def test_stream_answer_accumulates_status_history(handler):
    handler.ai_engine = _engine_with([
        Status("🌐 Ищу в интернете..."),
        Status("🤤 Ещё чуть-чуть думаю...", ephemeral=True),
        FinalAnswer("Готовый ответ"),
    ])

    await handler._streamAnswer(_user_message(), [], ping=False)

    # Первый статус — обычным текстом, новое сообщение
    first_content, _, thinking = handler.sent[0]
    assert first_content == "🌐 Ищу в интернете..."
    # Второй статус: прошлый уходит в -#, текущий — обычным текстом
    assert thinking.edits[0] == "-# 🌐 Ищу в интернете...\n🤤 Ещё чуть-чуть думаю..."
    # Финал: ephemeral-статус остался только текущим, в лог не попал
    assert thinking.edits[1] == "-# 🌐 Ищу в интернете...\n\nГотовый ответ"
    assert not thinking.deleted


async def test_stream_answer_without_tools_has_no_log(handler):
    handler.ai_engine = _engine_with([FinalAnswer("Просто ответ")])

    await handler._streamAnswer(_user_message(), [], ping=False)

    assert len(handler.sent) == 1
    assert handler.sent[0][0] == "Просто ответ"


async def test_stream_answer_long_answer_keeps_log_as_separate_message(handler):
    long_text = "а" * 2500
    handler.ai_engine = _engine_with([
        Status("🌐 Ищу в интернете..."),
        FinalAnswer(long_text),
    ])

    await handler._streamAnswer(_user_message(), [], ping=False)

    _, _, thinking = handler.sent[0]
    # Лог остаётся отдельным сообщением, не удаляется
    assert thinking.edits[-1] == "-# 🌐 Ищу в интернете..."
    assert not thinking.deleted
    # Ответ уехал нарезкой контейнеров; content при этом не передаётся —
    # V2-компоненты несовместимы с content= (ValueError в disnake)
    chunks = handler.sent[1:]
    assert len(chunks) >= 1
    for content, kwargs, _m in chunks:
        assert content is None
        text = component_text([kwargs["components"]])
        assert text.startswith("-# cut\n")
        assert "а" in text


async def test_stream_answer_never_mixes_content_with_components(handler):
    """Контракт disnake: content вместе с V2-компонентами = ValueError."""
    handler.ai_engine = _engine_with([
        Status("🌐 Ищу в интернете..."),
        FinalAnswer("б" * 5000),
    ])

    await handler._streamAnswer(_user_message(), [], ping=False)

    for content, kwargs, _m in handler.sent:
        assert not (content is not None and kwargs.get("components") is not None)


def test_with_log_truncates_overlong_status_text():
    log = [f"🌐 Статус номер {i} с длинным текстом" for i in range(120)]
    text = AIMessageHandler._withLog(log, "текущий статус")
    assert len(text) <= 1999
    assert text.endswith("текущий статус")


async def test_stream_answer_error_keeps_log(handler):
    handler.ai_engine = _engine_with([
        Status("🌐 Ищу в интернете..."),
        AIError("😞 Ошибка"),
    ])

    await handler._streamAnswer(_user_message(), [], ping=False)

    _, _, thinking = handler.sent[0]
    assert thinking.edits[-1] == "-# 🌐 Ищу в интернете...\n😞 Ошибка"


async def test_mute_status_stays_in_action_log(handler, engine):
    user = MagicMock()
    user.timeout = AsyncMock()
    tool_call = MagicMock()
    tool_call.function.name = "mute_user"
    tool_call.function.arguments = '{"duration": "10m", "reason": "По просьбе пользователя"}'

    events = [event async for event in engine._executeTool(tool_call, Context(user))]

    assert isinstance(events[0], Status)
    assert events[0].content == "🔇 Выдаю тебе мут..."
    assert not events[0].ephemeral
    user.timeout.assert_awaited_once_with(duration="10m", reason="По просьбе пользователя")

    handler.ai_engine = _engine_with([events[0], FinalAnswer("Мут выдан")])
    await handler._streamAnswer(_user_message(), [], ping=False)

    _, _, thinking = handler.sent[0]
    assert thinking.edits[-1] == "-# 🔇 Выдаю тебе мут...\n\nМут выдан"


# --- buildConverstaion: чтение истории ---


def _bot_msg(engine, content):
    msg = MagicMock()
    msg.author = engine.bot.user
    msg.clean_content = content
    msg.attachments = []
    msg.components = []
    return msg


@pytest.fixture
def engine(monkeypatch):
    fake_llm = MagicMock()
    fake_llm.current_vendor = MagicMock()
    fake_llm.current_vendor.has_vision = False
    monkeypatch.setattr("bot.ai.engine.llm", fake_llm)
    inst = AIEngine()
    inst.system_prompt = "SYSTEM {date}"
    inst.bot = MagicMock()
    return inst


async def test_build_conversation_strips_action_log_from_bot_message(engine):
    msg = _bot_msg(engine, "-# 🌐 Ищу в интернете...\n-# 🔇 Выдаю тебе мут...\n\nГотовый ответ")

    conversation = await engine.buildConverstaion([msg])

    assert conversation[-1]["role"] == "assistant"
    assert conversation[-1]["content"] == "Готовый ответ"


async def test_build_conversation_skips_log_only_bot_message(engine):
    log_only = _bot_msg(engine, "-# 🌐 Ищу в интернете...\n-# 🤤 Ещё чуть-чуть думаю...")
    answer = _bot_msg(engine, "Сам ответ")

    conversation = await engine.buildConverstaion([log_only, answer])

    assistant = [m for m in conversation if m["role"] == "assistant"]
    assert len(assistant) == 1
    assert assistant[0]["content"] == "Сам ответ"


async def test_build_conversation_keeps_plain_bot_message_intact(engine):
    msg = _bot_msg(engine, "Обычный ответ без лога")

    conversation = await engine.buildConverstaion([msg])

    assert conversation[-1]["content"] == "Обычный ответ без лога"


async def test_build_conversation_reads_chunked_container_message(engine):
    """Нарезанный длинный ответ (контейнер с маркером -# cut) читается как текст."""
    msg = _bot_msg(engine, "")
    msg.components = [disnake.ui.Container(
        disnake.ui.TextDisplay("-# cut"),
        disnake.ui.TextDisplay("часть длинного ответа"),
    )]

    conversation = await engine.buildConverstaion([msg])

    assert conversation[-1]["role"] == "assistant"
    assert conversation[-1]["content"] == "часть длинного ответа"


def test_component_text_walks_nested_components():
    container = disnake.ui.Container(
        disnake.ui.TextDisplay("первый"),
        disnake.ui.Separator(),
        disnake.ui.TextDisplay("второй"),
    )
    assert component_text([container]) == "первый\nвторой"
    assert component_text([]) == ""
