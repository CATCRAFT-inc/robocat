"""Тесты видео-входа: buildConverstaion с видео-аттачментами (кадры для vision).

llm и media мокаются целиком — ни сети, ни ffmpeg. Аудио бот сознательно
НЕ слушает (у hosted-Gemma нет аудио-модальности, а внешних транскрибаторов
не держим) — голосовые получают вежливую заглушку «не могу обработать».
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.ai.engine import AIEngine


def _attachment(content_type: str, *, size: int = 1024, data: bytes = b"data") -> MagicMock:
    attach = MagicMock()
    attach.content_type = content_type
    attach.size = size
    attach.id = 42
    attach.read = AsyncMock(return_value=data)
    return attach


def _user_msg(content: str = "привет", attachment: MagicMock | None = None) -> MagicMock:
    msg = MagicMock()
    msg.author = MagicMock()          # != engine.bot.user → ветка юзера
    msg.author.display_name = "вася"
    msg.clean_content = content
    msg.attachments = [attachment] if attachment else []
    msg.components = []
    return msg


@pytest.fixture
def fake_llm(monkeypatch):
    fake = MagicMock()
    fake.current_vendor = MagicMock()
    fake.current_vendor.has_vision = True
    monkeypatch.setattr("bot.ai.engine.llm", fake)
    return fake


@pytest.fixture
def engine(fake_llm):
    inst = AIEngine()
    inst.system_prompt = "SYSTEM {date}"
    inst.bot = MagicMock()
    return inst


async def test_video_frames_injected_as_image_parts(engine, fake_llm, monkeypatch):
    monkeypatch.setattr(
        "bot.ai.engine.media.extract_frames",
        AsyncMock(return_value=([b"jpg1", b"jpg2"], 12.0)),
    )

    conversation = await engine.buildConverstaion([_user_msg("смотри", _attachment("video/mp4"))])

    last = conversation[-1]
    assert isinstance(last["content"], list)
    text_part = last["content"][0]
    assert text_part["type"] == "text"
    assert "2 frames" in text_part["text"]
    assert "(12s)" in text_part["text"]
    assert "can't hear" in text_part["text"]
    image_parts = [p for p in last["content"] if p["type"] == "image_url"]
    assert len(image_parts) == 2
    assert all(p["image_url"]["url"].startswith("data:image/jpeg;base64,") for p in image_parts)


async def test_video_extraction_failure_degrades_to_polite_hint(engine, fake_llm, monkeypatch):
    monkeypatch.setattr("bot.ai.engine.media.extract_frames", AsyncMock(return_value=([], None)))

    conversation = await engine.buildConverstaion([_user_msg("видео", _attachment("video/mp4"))])

    last = conversation[-1]
    assert isinstance(last["content"], str)
    assert "couldn't watch" in last["content"]


async def test_video_oversized_skips_extraction(engine, fake_llm, monkeypatch):
    extract = AsyncMock()
    monkeypatch.setattr("bot.ai.engine.media.extract_frames", extract)
    big = _attachment("video/mp4", size=100 * 1024 * 1024)

    conversation = await engine.buildConverstaion([_user_msg("видео", big)])

    assert "too large" in conversation[-1]["content"]
    extract.assert_not_awaited()


async def test_video_without_vision_gets_visual_hint(engine, fake_llm, monkeypatch):
    fake_llm.current_vendor.has_vision = False
    extract = AsyncMock()
    monkeypatch.setattr("bot.ai.engine.media.extract_frames", extract)

    conversation = await engine.buildConverstaion([_user_msg("видео", _attachment("video/mp4"))])

    assert "can't view" in conversation[-1]["content"]
    extract.assert_not_awaited()


async def test_voice_message_gets_cant_process_hint(engine, fake_llm):
    # Аудио сознательно не обрабатываем — модель должна вежливо отказаться
    conversation = await engine.buildConverstaion([_user_msg("го", _attachment("audio/ogg"))])

    last = conversation[-1]
    assert isinstance(last["content"], str)
    assert "can't process" in last["content"]


async def test_audio_in_older_message_marked_unavailable(engine, fake_llm):
    old = _user_msg("старое войс", _attachment("audio/ogg"))
    new = _user_msg("а теперь текстом")

    conversation = await engine.buildConverstaion([old, new])

    assert "not available" in conversation[-2]["content"]


async def test_image_path_unchanged(engine, fake_llm, monkeypatch):
    # Регрессия: старый путь картинок не должен был сломаться
    monkeypatch.setattr(
        "bot.ai.engine.AIEngine._base64Image",
        AsyncMock(return_value="data:image/jpeg;base64,AAA"),
    )

    conversation = await engine.buildConverstaion([_user_msg("фото", _attachment("image/png"))])

    last = conversation[-1]
    assert isinstance(last["content"], list)
    assert last["content"][1]["image_url"]["url"] == "data:image/jpeg;base64,AAA"
