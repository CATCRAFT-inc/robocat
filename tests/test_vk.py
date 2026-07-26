"""Тесты ВК-клиента и преобразования Discord Markdown в обычный текст."""

from unittest.mock import AsyncMock, call

import pytest

from bot import vk


def _set_vk_env(monkeypatch):
    monkeypatch.setenv("VK_USER_TOKEN", "token")
    monkeypatch.setenv("VK_GROUP_ID", "123")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("**жирный**", "жирный"),
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
        ("[текст](https://example.com)", "текст (https://example.com)"),
        ("`код`", "код"),
        ("```python\nprint('кот')\n```", "print('кот')"),
        ("Привет <:cat_wave:123>", "Привет 😺"),
        ("Привет <a:cat_dance:456>", "Привет 😺"),
        ("Текст\n  <@&789>  \nДальше", "Текст\nДальше"),
        ("Когда: <t:0:R>", "Когда: 01.01.1970 03:00"),
        ("Первая\n\n\n\nВторая", "Первая\n\nВторая"),
    ],
)
def test_discord_md_to_plain_supported_markup_returns_plain_text(monkeypatch, raw, expected):
    # Таблица ловит возврат Discord-разметки и служебного роль-пинга в ВК.
    monkeypatch.setattr(vk.random, "choice", lambda pool: pool[0])

    assert vk.discord_md_to_plain(raw) == expected


def test_enabled_without_required_env_returns_false(monkeypatch):
    # Частично настроенная фича не должна просыпаться и пытаться ходить в сеть.
    monkeypatch.delenv("VK_USER_TOKEN", raising=False)
    monkeypatch.delenv("VK_GROUP_ID", raising=False)

    assert vk.enabled() is False


def test_enabled_with_both_env_returns_true(monkeypatch):
    # Наличие обоих секретов — единственное условие включения клиента.
    _set_vk_env(monkeypatch)

    assert vk.enabled() is True


def test_post_url_with_group_env_returns_wall_link(monkeypatch):
    # Ссылка на результат должна использовать отрицательный owner_id группы.
    _set_vk_env(monkeypatch)

    assert vk.post_url(456) == "https://vk.com/wall-123_456"


async def test_vk_call_success_returns_response(monkeypatch):
    # Обёртка обязана подмешать токен и версию, не заставляя вызывающий код делать это.
    _set_vk_env(monkeypatch)
    post = AsyncMock(return_value={"response": {"post_id": 42}})
    monkeypatch.setattr(vk, "_post", post)

    result = await vk._vk_call("wall.post", owner_id=-123)

    assert result == {"post_id": 42}
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

    await vk.comment(42, "Discord: https://discord.com/x и http://t.me/x")

    assert post.await_args_list[0].kwargs["data"]["message"] == (
        "Discord: https://discord.com/x и http://t.me/x"
    )
    assert post.await_args_list[1].kwargs["data"]["message"] == (
        "Discord: discord.com/x и t.me/x"
    )


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
                {},
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

    result = await vk.post_to_group("Текст", ["photo-1_2", "doc-1_3"])

    assert result == 42
    post.assert_awaited_once_with(
        "https://api.vk.com/method/wall.post",
        data={
            "owner_id": -123,
            "from_group": 1,
            "message": "Текст",
            "attachments": "photo-1_2,doc-1_3",
            "access_token": "token",
            "v": "5.199",
        },
    )
