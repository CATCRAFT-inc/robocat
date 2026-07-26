"""Тесты ВК-клиента и преобразования Discord Markdown в обычный текст."""

import random
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest

from bot import vk


def _set_vk_env(monkeypatch):
    monkeypatch.setenv("VK_USER_TOKEN", "token")
    monkeypatch.setenv("VK_GROUP_ID", "123")
    monkeypatch.setattr(vk, "_call_lock", None)
    monkeypatch.setattr(vk.asyncio, "sleep", AsyncMock())


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("**жирный**", "жирный"),
        ("**жирный\nтак**", "жирный\nтак"),
        ("*курсив*", "курсив"),
        ("_курсив_", "курсив"),
        ("ник snake_case и kotik_plus", "ник snake_case и kotik_plus"),
        ("***жирный курсив***", "жирный курсив"),
        ("__подчёркнутый__", "подчёркнутый"),
        ("~~зачёркнутый~~", "зачёркнутый"),
        ("||спойлер||", "спойлер"),
        ("# Заголовок", "Заголовок"),
        ("-# Сабтекст", "Сабтекст"),
        ("> Цитата\n>>> Длинная цитата", "Цитата\nДлинная цитата"),
        ("> ## Заголовок", "Заголовок"),
        ("[текст](https://example.com)", "текст (https://example.com)"),
        ("[текст](<https://example.com>)", "текст (https://example.com)"),
        ("`код`", "код"),
        ("```hi```", "hi"),
        ("```python\nprint('кот')\n```", "print('кот')"),
        (
            "Привет <:cat_wave:123>",
            f"Привет {random.Random(0).choice(vk.EMOJI_POOL)}",
        ),
        (
            "Привет <a:cat_dance:456>",
            f"Привет {random.Random(0).choice(vk.EMOJI_POOL)}",
        ),
        ("Текст\n  <@&789>  \nДальше", "Текст\nДальше"),
        ("Текст\n<@&1> <@&2>\nДальше", "Текст\nДальше"),
        ("Текст\n-# <@&1>\nДальше", "Текст\nДальше"),
        ("Когда: <t:0:R>", "Когда: 01.01.1970 03:00"),
        ("Первая\n\n\n\nВторая", "Первая\n\nВторая"),
    ],
)
def test_discord_md_to_plain_supported_markup_returns_plain_text(raw, expected):
    # Таблица ловит возврат Discord-разметки и служебного роль-пинга в ВК.
    assert vk.discord_md_to_plain(raw, seed=0) == expected


def test_enabled_without_required_env_returns_false(monkeypatch):
    # Частично настроенная фича не должна просыпаться и пытаться ходить в сеть.
    monkeypatch.delenv("VK_USER_TOKEN", raising=False)
    monkeypatch.delenv("VK_GROUP_ID", raising=False)

    assert vk.enabled() is False


def test_enabled_with_both_env_returns_true(monkeypatch):
    # Валидный положительный id группы вместе с токеном включает клиент.
    _set_vk_env(monkeypatch)

    assert vk.enabled() is True


def test_group_id_invalid_env_returns_none(monkeypatch):
    # Единственный парсер id не должен бросать KeyError или принимать минус.
    monkeypatch.delenv("VK_GROUP_ID", raising=False)
    assert vk._group_id() is None

    monkeypatch.setenv("VK_GROUP_ID", "-123")
    assert vk._group_id() is None


@pytest.mark.parametrize("group_id", ["-123", "0", "abc"])
def test_enabled_invalid_group_id_returns_false(monkeypatch, group_id):
    # Минус, ноль и мусор не должны превращать групповой пост в личный.
    monkeypatch.setenv("VK_USER_TOKEN", "token")
    monkeypatch.setenv("VK_GROUP_ID", group_id)

    assert vk.enabled() is False


def test_post_url_with_group_env_returns_wall_link(monkeypatch):
    # Ссылка на результат должна использовать отрицательный owner_id группы.
    _set_vk_env(monkeypatch)

    assert vk.post_url(456) == "https://vk.com/wall-123_456"


@pytest.mark.parametrize(
    "url",
    [
        "http://cdn.discordapp.com/attachments/1/cat.png",
        "https://example.com/cat.png",
    ],
)
async def test_download_untrusted_url_raises_without_network(url):
    # Проверка схемы и хоста обязана сработать до создания HTTP-сессии.
    with pytest.raises(vk.SkippedURL) as exc_info:
        await vk.download(url)

    assert exc_info.value.code == 0
    assert "внешний URL пропущен" in exc_info.value.msg


async def test_download_chunked_response_returns_all_bytes(monkeypatch):
    # StreamReader.read(n) обрезал файл до первого буфера; чанки нужны до EOF.
    class Content:
        async def iter_chunked(self, size):
            assert size == 65536
            for chunk in (b"first", b"second", b"third"):
                yield chunk

    class Response:
        status = 200
        content_type = "image/jpeg"
        content = Content()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    class Session:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def get(self, url, **kwargs):
            return Response()

    fake_aiohttp = SimpleNamespace(
        ClientTimeout=lambda **kwargs: object(),
        ClientSession=Session,
    )
    monkeypatch.setitem(sys.modules, "aiohttp", fake_aiohttp)

    data, content_type = await vk.download(
        "https://cdn.discordapp.com/attachments/cat.jpg"
    )

    assert data == b"firstsecondthird"
    assert content_type == "image/jpeg"


async def test_download_chunked_response_over_limit_raises(monkeypatch):
    # Переполнение в позднем чанке должно оборвать скачивание до загрузки в ВК.
    class Content:
        async def iter_chunked(self, size):
            for chunk in (b"ab", b"cd"):
                yield chunk

    class Response:
        status = 200
        content_type = "image/jpeg"
        content = Content()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    class Session:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def get(self, url, **kwargs):
            return Response()

    monkeypatch.setitem(
        sys.modules,
        "aiohttp",
        SimpleNamespace(
            ClientTimeout=lambda **kwargs: object(),
            ClientSession=Session,
        ),
    )
    monkeypatch.setattr(vk, "_MAX_DOWNLOAD", 3)

    with pytest.raises(vk.VKError):
        await vk.download("https://media.discordapp.net/attachments/cat.jpg")


def test_discord_md_to_plain_overflow_timestamp_keeps_original():
    # JS-миллисекунды не должны ронять конверсию всего сообщения.
    raw = "<t:1753500000000:R>"

    assert vk.discord_md_to_plain(raw) == raw


def test_discord_md_to_plain_same_seed_returns_same_emoji():
    # Превью и фактический пост с одним message.id обязаны совпадать.
    raw = "<:cat:1> <a:cat:2> <:cat:3>"

    first = vk.discord_md_to_plain(raw, seed=555)
    second = vk.discord_md_to_plain(raw, seed=555)

    assert first == second


def test_discord_md_to_plain_code_placeholder_restores_once():
    # Восстановленный код не должен повторно раскрывать похожий плейсхолдер.
    marker = "\x00VKCODE1\x00"

    assert vk.discord_md_to_plain(f"`{marker}` `код`") == f"{marker} код"


async def test_vk_call_success_returns_response(monkeypatch):
    # Обёртка обязана подмешать токен и версию, не заставляя вызывающий код делать это.
    _set_vk_env(monkeypatch)
    post = AsyncMock(return_value={"response": {"post_id": 42}})
    monkeypatch.setattr(vk, "_post", post)

    result = await vk._vk_call("wall.post", owner_id=-123)

    assert result == {"post_id": 42}
    vk.asyncio.sleep.assert_awaited_once_with(0.35)
    post.assert_awaited_once_with(
        "https://api.vk.com/method/wall.post",
        data={"owner_id": -123, "access_token": "token", "v": "5.199"},
    )


async def test_vk_call_error_raises_vk_error_with_code(monkeypatch):
    # Ошибка ВК не должна маскироваться под успешный пустой ответ.
    _set_vk_env(monkeypatch)
    monkeypatch.setattr(
        vk,
        "_post",
        AsyncMock(return_value={"error": {"error_code": 5, "error_msg": "denied"}}),
    )

    with pytest.raises(vk.VKError) as exc_info:
        await vk._vk_call("wall.post")

    assert exc_info.value.code == 5
    assert exc_info.value.msg == "denied"


async def test_comment_error_222_retries_without_url_prefixes(monkeypatch):
    # ВК запрещает гиперссылки в части групп: второй запрос оставляет адрес читаемым.
    _set_vk_env(monkeypatch)
    post = AsyncMock(
        side_effect=[
            {"error": {"error_code": 222, "error_msg": "Hyperlinks are forbidden"}},
            {"response": {"comment_id": 7}},
        ]
    )
    monkeypatch.setattr(vk, "_post", post)

    await vk.comment(
        42,
        "Discord: https://discord.com/x и http://t.me/x",
        guid="c555",
    )

    assert post.await_args_list[0].kwargs["data"] == {
        "owner_id": -123,
        "post_id": 42,
        "from_group": 123,
        "message": "Discord: https://discord.com/x и http://t.me/x",
        "guid": "c555",
        "access_token": "token",
        "v": "5.199",
    }
    assert post.await_args_list[1].kwargs["data"] == {
        "owner_id": -123,
        "post_id": 42,
        "from_group": 123,
        "message": "Discord: discord.com/x и t.me/x",
        "guid": "c555",
        "access_token": "token",
        "v": "5.199",
    }


async def test_comment_second_error_raises(monkeypatch):
    # Повторная ошибка не должна превращаться в молчаливый успех гейта.
    _set_vk_env(monkeypatch)
    post = AsyncMock(
        side_effect=[
            {"error": {"error_code": 222, "error_msg": "forbidden"}},
            {"error": {"error_code": 222, "error_msg": "still forbidden"}},
        ]
    )
    monkeypatch.setattr(vk, "_post", post)

    with pytest.raises(vk.VKError) as exc_info:
        await vk.comment(42, "https://example.com")

    assert exc_info.value.code == 222
    assert post.await_count == 2


async def test_upload_photo_responses_return_photo_attachment(monkeypatch):
    # owner_id и id берутся из photos.saveWallPhoto, а не угадываются по группе.
    _set_vk_env(monkeypatch)
    post = AsyncMock(
        side_effect=[
            {"response": {"upload_url": "https://upload/photo"}},
            {"photo": "photo-token", "server": 10, "hash": "hash-token"},
            {"response": [{"owner_id": -321, "id": 654}]},
        ]
    )
    monkeypatch.setattr(vk, "_post", post)

    result = await vk._upload_photo(b"image", "cat.png")

    assert result == "photo-321_654"
    assert post.await_args_list == [
        call(
            "https://api.vk.com/method/photos.getWallUploadServer",
            data={"group_id": 123, "access_token": "token", "v": "5.199"},
        ),
        call("https://upload/photo", file=("photo", b"image", "cat.png")),
        call(
            "https://api.vk.com/method/photos.saveWallPhoto",
            data={
                "group_id": 123,
                "photo": "photo-token",
                "server": 10,
                "hash": "hash-token",
                "access_token": "token",
                "v": "5.199",
            },
        ),
    ]


async def test_upload_video_save_response_returns_video_attachment(monkeypatch):
    # video_id фиксируется ответом video.save, ответ аплоада не должен его подменять.
    _set_vk_env(monkeypatch)
    post = AsyncMock(
        side_effect=[
            {
                "response": {
                    "upload_url": "https://upload/video",
                    "owner_id": -321,
                    "video_id": 654,
                }
            },
            {"video_id": 999},
        ]
    )
    monkeypatch.setattr(vk, "_post", post)

    result = await vk._upload_video(b"video", "cat.mp4")

    assert result == "video-321_654"
    assert post.await_args_list == [
        call(
            "https://api.vk.com/method/video.save",
            data={
                "group_id": 123,
                "name": "cat.mp4",
                "wallpost": 0,
                "access_token": "token",
                "v": "5.199",
            },
        ),
        call("https://upload/video", file=("video_file", b"video", "cat.mp4")),
    ]


async def test_upload_video_empty_upload_response_raises_vk_error(monkeypatch):
    # Пустой ответ upload_url не должен превращать мёртвое видео в успех.
    _set_vk_env(monkeypatch)
    monkeypatch.setattr(
        vk,
        "_post",
        AsyncMock(
            side_effect=[
                {
                    "response": {
                        "upload_url": "https://upload/video",
                        "owner_id": -321,
                        "video_id": 654,
                    }
                },
                {},
            ]
        ),
    )

    with pytest.raises(vk.VKError):
        await vk._upload_video(b"video", "cat.mp4")


async def test_upload_doc_responses_return_doc_attachment(monkeypatch):
    # docs.save оборачивает итоговый объект в ключ doc — клиент обязан раскрыть его.
    _set_vk_env(monkeypatch)
    post = AsyncMock(
        side_effect=[
            {"response": {"upload_url": "https://upload/doc"}},
            {"file": "file-token"},
            {"response": {"doc": {"owner_id": -321, "id": 654}}},
        ]
    )
    monkeypatch.setattr(vk, "_post", post)

    result = await vk._upload_doc(b"document", "cat.gif")

    assert result == "doc-321_654"
    assert post.await_args_list == [
        call(
            "https://api.vk.com/method/docs.getWallUploadServer",
            data={"group_id": 123, "access_token": "token", "v": "5.199"},
        ),
        call("https://upload/doc", file=("file", b"document", "cat.gif")),
        call(
            "https://api.vk.com/method/docs.save",
            data={
                "file": "file-token",
                "title": "cat.gif",
                "access_token": "token",
                "v": "5.199",
            },
        ),
    ]


@pytest.mark.parametrize(
    ("content_type", "responses", "expected", "first_method"),
    [
        (
            "image/png",
            [
                {"response": {"upload_url": "https://upload/photo"}},
                {"photo": "p", "server": 1, "hash": "h"},
                {"response": [{"owner_id": -1, "id": 2}]},
            ],
            "photo-1_2",
            "photos.getWallUploadServer",
        ),
        (
            "image/gif",
            [
                {"response": {"upload_url": "https://upload/doc"}},
                {"file": "f"},
                {"response": {"doc": {"owner_id": -1, "id": 2}}},
            ],
            "doc-1_2",
            "docs.getWallUploadServer",
        ),
        (
            "video/mp4",
            [
                {
                    "response": {
                        "upload_url": "https://upload/video",
                        "owner_id": -1,
                        "video_id": 2,
                    }
                },
                {"size": 1},
            ],
            "video-1_2",
            "video.save",
        ),
        (
            "application/pdf",
            [
                {"response": {"upload_url": "https://upload/doc"}},
                {"file": "f"},
                {"response": {"doc": {"owner_id": -1, "id": 2}}},
            ],
            "doc-1_2",
            "docs.getWallUploadServer",
        ),
    ],
)
async def test_upload_attachment_content_type_routes_to_vk_method(
    monkeypatch, content_type, responses, expected, first_method
):
    # GIF должен оставаться анимированным документом, остальные типы идут по MIME.
    _set_vk_env(monkeypatch)
    post = AsyncMock(side_effect=responses)
    monkeypatch.setattr(vk, "_post", post)

    result = await vk.upload_attachment(b"data", "file.bin", content_type)

    assert result == expected
    assert post.await_args_list[0].args[0] == (
        f"https://api.vk.com/method/{first_method}"
    )


async def test_post_to_group_attachments_returns_post_id(monkeypatch):
    # Вложения должны отправляться одной строкой и поститься от имени группы.
    _set_vk_env(monkeypatch)
    post = AsyncMock(return_value={"response": {"post_id": 42}})
    monkeypatch.setattr(vk, "_post", post)

    result = await vk.post_to_group(
        "Текст", ["photo-1_2", "doc-1_3"], guid="555"
    )

    assert result == 42
    post.assert_awaited_once_with(
        "https://api.vk.com/method/wall.post",
        data={
            "owner_id": -123,
            "from_group": 1,
            "message": "Текст",
            "attachments": "photo-1_2,doc-1_3",
            "guid": "555",
            "access_token": "token",
            "v": "5.199",
        },
    )


async def test_post_to_group_empty_attachments_omits_parameter(monkeypatch):
    # Пустую строку attachments ВК трактовать не обязан — параметра быть не должно.
    _set_vk_env(monkeypatch)
    post = AsyncMock(return_value={"response": {"post_id": 42}})
    monkeypatch.setattr(vk, "_post", post)

    await vk.post_to_group("Текст", [], guid="555")

    assert "attachments" not in post.await_args.kwargs["data"]
