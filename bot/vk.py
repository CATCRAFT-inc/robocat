"""Листовой ВК-клиент и преобразование Discord Markdown в обычный текст."""

import logging
import os
import random
import re
from datetime import datetime, timedelta, timezone


logger = logging.getLogger("robocat.vk")

API_BASE = "https://api.vk.com/method"
API_VERSION = "5.199"

# Пул для замены кастомных Discord-эмодзи, правится тут.
EMOJI_POOL = ("😺", "😸", "😹", "😻", "😼", "😽", "🙀", "🐱", "🐾", "❤️", "✨")

__all__ = (
    "VKError",
    "comment",
    "discord_md_to_plain",
    "download",
    "enabled",
    "post_to_group",
    "post_url",
    "upload_attachment",
)


class VKError(Exception):
    """Ошибка HTTP или метода ВК API."""

    def __init__(self, code: int, msg: str):
        self.code = code
        self.msg = msg
        super().__init__(f"VK error {code}: {msg}")


def enabled() -> bool:
    """Вернуть True, когда настроены и токен, и группа ВК."""
    return bool(os.getenv("VK_USER_TOKEN") and os.getenv("VK_GROUP_ID"))


def _group_id() -> int:
    """Прочитать положительный id группы только в момент запроса."""
    return int(os.environ["VK_GROUP_ID"])


def post_url(post_id: int) -> str:
    """Вернуть публичную ссылку на пост группы."""
    return f"https://vk.com/wall-{_group_id()}_{post_id}"


async def _post(
    url: str,
    *,
    data: dict | None = None,
    file: tuple[str, bytes, str] | None = None,
) -> dict:
    """Выполнить один POST-запрос и разобрать JSON-ответ."""
    import aiohttp  # транзитивная зависимость disnake, импорт остаётся ленивым

    timeout = aiohttp.ClientTimeout(total=300 if file else 60)
    if file:
        body = aiohttp.FormData()
        for key, value in (data or {}).items():
            body.add_field(key, str(value))
        field, content, filename = file
        body.add_field(field, content, filename=filename)
    else:
        body = {key: str(value) for key, value in (data or {}).items()}

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, data=body) as response:
            if response.status != 200:
                logger.warning("ВК вернул HTTP %s", response.status)
                raise VKError(response.status, f"HTTP {response.status}")
            return await response.json()


async def download(url: str) -> tuple[bytes, str]:
    """Скачать внешнее вложение для последующей загрузки в ВК."""
    import aiohttp  # транзитивная зависимость disnake, импорт остаётся ленивым

    timeout = aiohttp.ClientTimeout(total=300)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as response:
            if response.status != 200:
                logger.warning("Скачивание вложения вернуло HTTP %s", response.status)
                raise VKError(response.status, f"HTTP {response.status}")
            return (
                await response.read(),
                response.content_type or "application/octet-stream",
            )


async def _vk_call(method: str, **params):
    """Вызвать метод ВК API, автоматически добавив токен и версию."""
    result = await _post(
        f"{API_BASE}/{method}",
        data={
            **params,
            "access_token": os.environ["VK_USER_TOKEN"],
            "v": API_VERSION,
        },
    )
    if "error" in result:
        error = result["error"]
        raise VKError(error["error_code"], error["error_msg"])
    return result["response"]


async def _upload_photo(data: bytes, filename: str) -> str:
    """Загрузить фотографию на стену и вернуть строку вложения ВК."""
    group_id = _group_id()
    server = await _vk_call("photos.getWallUploadServer", group_id=group_id)
    uploaded = await _post(
        server["upload_url"], file=("photo", data, filename)
    )
    saved = await _vk_call(
        "photos.saveWallPhoto",
        group_id=group_id,
        photo=uploaded["photo"],
        server=uploaded["server"],
        hash=uploaded["hash"],
    )
    photo = saved[0]
    return f"photo{photo['owner_id']}_{photo['id']}"


async def _upload_video(data: bytes, name: str) -> str:
    """Загрузить видео на стену и вернуть строку вложения ВК."""
    saved = await _vk_call(
        "video.save", group_id=_group_id(), name=name, wallpost=0
    )
    await _post(
        saved["upload_url"], file=("video_file", data, name)
    )
    return f"video{saved['owner_id']}_{saved['video_id']}"


async def _upload_doc(data: bytes, filename: str) -> str:
    """Загрузить документ на стену и вернуть строку вложения ВК."""
    server = await _vk_call("docs.getWallUploadServer", group_id=_group_id())
    uploaded = await _post(server["upload_url"], file=("file", data, filename))
    saved = await _vk_call("docs.save", file=uploaded["file"], title=filename)
    document = saved["doc"]
    return f"doc{document['owner_id']}_{document['id']}"


async def upload_attachment(data: bytes, filename: str, content_type: str) -> str:
    """Выбрать загрузчик ВК по MIME-типу вложения."""
    content_type = content_type.lower().split(";", 1)[0]
    if content_type.startswith("image/") and content_type != "image/gif":
        return await _upload_photo(data, filename)
    if content_type.startswith("video/"):
        return await _upload_video(data, filename)
    return await _upload_doc(data, filename)


async def post_to_group(text: str, attachments: list[str]) -> int:
    """Опубликовать запись от имени группы и вернуть id поста."""
    group_id = _group_id()
    result = await _vk_call(
        "wall.post",
        owner_id=-group_id,
        from_group=1,
        message=text,
        attachments=",".join(attachments),
    )
    return result["post_id"]


async def comment(post_id: int, text: str):
    """Оставить комментарий, один раз убрав протокол при ошибке 222."""
    group_id = _group_id()
    params = {
        "owner_id": -group_id,
        "post_id": post_id,
        "from_group": group_id,
        "message": text,
    }
    try:
        return await _vk_call("wall.createComment", **params)
    except VKError as error:
        if error.code != 222:
            raise
    params["message"] = re.sub(r"https?://", "", text)
    return await _vk_call("wall.createComment", **params)


def discord_md_to_plain(text: str) -> str:
    """Преобразовать поддерживаемую Discord-разметку в обычный текст."""
    code_parts: list[str] = []

    def stash_code(match: re.Match) -> str:
        code_parts.append(match.group(1))
        return f"\x00VKCODE{len(code_parts) - 1}\x00"

    text = re.sub(r"```[^\n`]*\n(.*?)```", stash_code, text, flags=re.DOTALL)
    text = re.sub(r"`([^`\n]+)`", stash_code, text)
    text = re.sub(
        r"(?m)^[ \t]*<@&\d+>[ \t]*(?:\n|$)",
        "",
        text,
    )
    text = re.sub(
        r"<a?:[A-Za-z0-9_]+:\d+>",
        lambda _: random.choice(EMOJI_POOL),
        text,
    )
    text = re.sub(
        r"<t:(\d+)(?::[^>]+)?>",
        lambda match: datetime.fromtimestamp(
            int(match.group(1)), timezone(timedelta(hours=3))
        ).strftime("%d.%m.%Y %H:%M"),
        text,
    )
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)
    text = re.sub(r"(?m)^(?:#{1,3}|-#|>>>|>) ", "", text)

    for pattern in (
        r"\*\*\*([^*\n]+)\*\*\*",
        r"\*\*([^*\n]+)\*\*",
        r"__([^_\n]+)__",
        r"~~([^~\n]+)~~",
        r"\|\|([^|\n]+)\|\|",
    ):
        text = re.sub(pattern, r"\1", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", text)
    text = re.sub(r"(?<!\w)_(\w(?:[^_\n]*\w)?)_(?!\w)", r"\1", text)
    text = re.sub(r"\n(?:[ \t]*\n){2,}", "\n\n", text)

    for index, content in enumerate(code_parts):
        text = text.replace(f"\x00VKCODE{index}\x00", content)
    return text.strip()
