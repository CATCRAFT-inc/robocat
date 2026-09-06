"""«На X лайков нажимаю кнопку» — бесконечный пост-счётчик.

Админ постит контейнер с картинкой командой `/like-button`. Каждый раз, когда
под постом набирается X лайков (👍 без учёта реакции самого бота), заголовок
переписывается на X+1. Живёт вечно: состояние в флаге `like_button` на
абстрактной сущности (`channel_id:message_id:threshold`), поэтому переживает
рестарт. Одновременно активен один такой пост — новый `/like-button`
заменяет слежение за старым.

Картинка лежит в репе (`data/like_button.webp`) и уходит вложением: подписанные
ссылки `media.discordapp.net` протухают через сутки. При `edit(components=...)`
без `attachments=` Discord сохраняет вложения, и `attachment://` в галерее
продолжает на них указывать.
"""

import logging
from pathlib import Path

import disnake
from disnake.ext import commands

from bot.discord_config import Channels, has_config_roles
from bot.flag_system.flag_system import flags
from bot.utils import DEFAULT_COLOUR, _as_colour

logger = logging.getLogger("robocat.like_button")

FLAG = "like_button"
LIKE = "\U0001f44d"
START_THRESHOLD = 2
_IMAGE = Path(__file__).resolve().parents[2] / "data" / "like_button.webp"
_UNLOADED = object()


def likes_word(n: int) -> str:
    """1 лайк, 2 лайка, 5 лайков, 21 лайк, 111 лайков."""
    if n % 100 in range(11, 20):
        return "лайков"
    if n % 10 == 1:
        return "лайк"
    if n % 10 in (2, 3, 4):
        return "лайка"
    return "лайков"


def build_container(threshold: int) -> disnake.ui.Container:
    return disnake.ui.Container(
        disnake.ui.TextDisplay(f"## На {threshold} {likes_word(threshold)} нажимаю кнопку"),
        disnake.ui.MediaGallery(disnake.MediaGalleryItem(f"attachment://{_IMAGE.name}")),
        accent_colour=_as_colour(DEFAULT_COLOUR),
    )


def _parse(value: str | None) -> tuple[int, int, int] | None:
    try:
        channel_id, message_id, threshold = (int(x) for x in (value or "").split(":"))
    except ValueError:
        return None
    return channel_id, message_id, threshold


class LikeButton(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # (channel_id, message_id, threshold); _UNLOADED — ещё не читали из БД.
        self._state: tuple[int, int, int] | None | object = _UNLOADED

    async def _load(self) -> tuple[int, int, int] | None:
        if self._state is _UNLOADED:
            row = await flags.getFlag("abstract", FLAG)
            # Пока ждали БД, `/like-button` мог уже записать свежий пост — его не затираем.
            if self._state is _UNLOADED:
                self._state = _parse(row.value if row else None)
        return self._state  # type: ignore[return-value]

    async def _save(self, channel_id: int, message_id: int, threshold: int) -> None:
        self._state = (channel_id, message_id, threshold)
        await flags.setFlag("abstract", FLAG, f"{channel_id}:{message_id}:{threshold}")

    @commands.slash_command(name="like-button", description="Запостить «На X лайков нажимаю кнопку»")
    @has_config_roles("admin", "st_admin")
    async def post(self, inter: disnake.ApplicationCommandInteraction):
        await inter.response.defer(ephemeral=True)
        channel = self.bot.get_channel(Channels.announcements)
        if channel is None:
            await inter.edit_original_response("Канал новостей не найден.")
            return
        message = await channel.send(
            components=[build_container(START_THRESHOLD)],
            file=disnake.File(_IMAGE),
        )
        # Сначала слежение, потом реакция: если реакция не встанет, пост не осиротеет.
        await self._save(channel.id, message.id, START_THRESHOLD)
        await message.add_reaction(LIKE)
        await inter.edit_original_response(f"Запостил: {message.jump_url}")

    @commands.Cog.listener("on_raw_reaction_add")
    async def on_like(self, payload: disnake.RawReactionActionEvent):
        if str(payload.emoji) != LIKE:
            return
        state = await self._load()
        if state is None or payload.message_id != state[1]:
            return
        channel_id, message_id, threshold = state
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            return
        try:
            message = await channel.fetch_message(message_id)
        except disnake.NotFound:
            logger.info("Пост «нажимаю кнопку» %s удалён — слежение снято", message_id)
            self._state = None
            return
        count = 0
        for reaction in message.reactions:
            if str(reaction.emoji) == LIKE:
                count = reaction.count - (1 if reaction.me else 0)
        if count < threshold:
            return
        # ponytail: два события одновременно дадут две одинаковые правки — безвредно.
        threshold = count + 1
        await message.edit(components=[build_container(threshold)])
        await self._save(channel_id, message_id, threshold)
        logger.info("«Нажимаю кнопку»: %s лайков, теперь нужно %s", count, threshold)


def setup(bot: commands.Bot):
    bot.add_cog(LikeButton(bot))
    logger.info("Ког LikeButton загружен")
