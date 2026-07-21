"""Редактор новостей (issue #4): /news → модалка → ephemeral-превью → публикация.

Новости уходят V2-контейнерами в новостные каналы. Черновики живут в памяти
процесса до публикации; рестарт бота их теряет — превью живёт минуты, тащить
их в БД незачем. /news_edit разбирает опубликованный контейнер обратно в поля
и открывает ту же модалку.
"""

import asyncio
import itertools
import logging
import re
import time
from dataclasses import dataclass

import disnake
from disnake.ext import commands

from bot.storage import Channels, ColorStorage, Roles

_NEWS_CHANNELS = {
    "Объявления": Channels.announcements,
    "Газета": Channels.newspaper,
    "Медиа": Channels.media_news,
    "Информатор": Channels.informator,
    "Секретный": Channels.secret
}

_PING_ROLES = {
    "Обновления сервера": Roles.server_updates,
    "Ивенты": Roles.events,
    "РП": Roles.rp,
    "Тех. работы": Roles.maintanence,
    "Медиа": Roles.media,
    "Обновления сайта": Roles.site_updates,
}
_NO_PING = "Без пинга"

_ADMIN_ROLES = {Roles.admin, Roles.st_admin, Roles.media}
_PING_LINE = re.compile(r"^<@&(\d+)>$")

# message.components отдаёт read-side классы, в тестах контейнер собран из ui-классов —
# атрибуты (children/content/items) совпадают, парсер принимает оба
_TEXT_TYPES = (disnake.ui.TextDisplay, disnake.TextDisplay)
_GALLERY_TYPES = (disnake.ui.MediaGallery, disnake.MediaGallery)


@dataclass
class _Draft:
    author_id: int
    channel_id: int
    title: str = ""
    text: str = ""
    image_url: str = ""
    ping_role_id: int | None = None
    edit_message_id: int | None = None  # /news_edit: правим существующий пост


def _news_container(draft: _Draft) -> disnake.ui.Container:
    children = [
        disnake.ui.TextDisplay(f"# {draft.title}"),
        disnake.ui.Separator(),
        disnake.ui.TextDisplay(draft.text),
    ]
    if draft.image_url:
        children.append(disnake.ui.MediaGallery(disnake.MediaGalleryItem(draft.image_url)))
    if draft.ping_role_id:
        children.append(disnake.ui.Separator())
        children.append(disnake.ui.TextDisplay(f"<@&{draft.ping_role_id}>"))
    return disnake.ui.Container(*children, accent_colour=disnake.Colour.from_hex(ColorStorage.main))


def _parse_container(container) -> "_Draft | None":
    """Опубликованный контейнер → черновик. None — не похоже на нашу новость."""
    draft = _Draft(author_id=0, channel_id=0)
    texts = []
    for child in container.children:
        if isinstance(child, _TEXT_TYPES):
            content = (child.content or "").strip()
            m = _PING_LINE.match(content)
            if m:
                draft.ping_role_id = int(m.group(1))
            elif not draft.title and content.startswith("# "):
                draft.title = content[2:].strip()
            else:
                texts.append(child.content)
        elif isinstance(child, _GALLERY_TYPES) and child.items:
            draft.image_url = child.items[0].media.url
    draft.text = "\n".join(texts).strip()
    if not draft.title or not draft.text:
        return None
    return draft


class NewsModal(disnake.ui.Modal):
    def __init__(self, cog: "NewsEditor", draft_id: int):
        self.cog = cog
        self.draft_id = draft_id
        draft = cog.drafts[draft_id]
        ping_options = [
            disnake.SelectOption(label=_NO_PING, value=_NO_PING, default=draft.ping_role_id is None)
        ] + [
            disnake.SelectOption(label=label, value=str(role_id), default=draft.ping_role_id == role_id)
            for label, role_id in _PING_ROLES.items()
        ]
        components = [
            disnake.ui.TextInput(
                label="Заголовок",
                custom_id="news_title",
                value=draft.title or None,
                max_length=150,
            ),
            disnake.ui.TextInput(
                label="Текст",
                custom_id="news_text",
                style=disnake.TextInputStyle.paragraph,
                value=draft.text or None,
                max_length=3000,
            ),
            disnake.ui.TextInput(
                label="Ссылка на картинку (необязательно)",
                custom_id="news_image",
                value=draft.image_url or None,
                required=False,
                max_length=500,
            ),
            disnake.ui.Label(
                text="Кого пинговать",
                component=disnake.ui.StringSelect(custom_id="news_ping", options=ping_options),
            ),
        ]
        super().__init__(title="Новость", custom_id=f"news_modal:{draft_id}", components=components)

    async def callback(self, inter: disnake.ModalInteraction):
        draft = self.cog.drafts.get(self.draft_id)
        if draft is None:
            await inter.response.send_message("Черновик потерян — начни заново: /news", ephemeral=True)
            return
        draft.title = inter.text_values["news_title"].strip()
        draft.text = inter.text_values["news_text"].strip()
        draft.image_url = (inter.text_values.get("news_image") or "").strip()
        picked = inter.resolved_values.get("news_ping")
        value = picked[0] if isinstance(picked, (list, tuple)) else picked
        draft.ping_role_id = int(value) if value and value != _NO_PING else None

        components = self.cog._preview(draft, self.draft_id)
        if inter.message is not None:
            # модалка открыта кнопкой «Править» с превью — обновляем то же превью
            await inter.response.edit_message(components=components)
        else:
            await inter.response.send_message(components=components, ephemeral=True)


class NewsEditor(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = logging.getLogger("robocat.news")
        self.drafts: dict[int, _Draft] = {}
        # Сид временем: после рестарта счётчик с 1 позволил бы старой ephemeral-кнопке
        # NEWS_PUB:1 захватить чужой новый черновик с тем же id
        self._seq = itertools.count(int(time.time()))

    def _preview(self, draft: _Draft, draft_id: int) -> list:
        verb = "обновится" if draft.edit_message_id else "будет опубликована"
        return [
            disnake.ui.TextDisplay(f"-# Превью: так новость {verb} в <#{draft.channel_id}>"),
            _news_container(draft),
            disnake.ui.ActionRow(
                disnake.ui.Button(
                    style=disnake.ButtonStyle.success,
                    label="Сохранить" if draft.edit_message_id else "Опубликовать",
                    custom_id=f"NEWS_PUB:{draft_id}",
                ),
                disnake.ui.Button(
                    style=disnake.ButtonStyle.secondary, label="Править",
                    custom_id=f"NEWS_EDIT:{draft_id}",
                ),
                disnake.ui.Button(
                    style=disnake.ButtonStyle.danger, label="Отмена",
                    custom_id=f"NEWS_CANCEL:{draft_id}",
                ),
            ),
        ]

    @commands.slash_command(name="news", description="Составить новость: модалка → превью → публикация")
    @commands.has_any_role(Roles.admin, Roles.st_admin)
    async def news(
        self,
        inter: disnake.AppCmdInter,
        channel: str = commands.Param(
            default="Объявления", choices=list(_NEWS_CHANNELS), description="Куда публиковать"
        ),
    ):
        draft_id = next(self._seq)
        self.drafts[draft_id] = _Draft(author_id=inter.author.id, channel_id=_NEWS_CHANNELS[channel])
        await inter.response.send_modal(NewsModal(self, draft_id))

    @commands.slash_command(name="news_edit", description="Править опубликованную ботом новость")
    @commands.has_any_role(Roles.admin, Roles.st_admin)
    async def newsEdit(
        self,
        inter: disnake.AppCmdInter,
        message: str = commands.Param(description="Ссылка на сообщение новости или его ID"),
        channel: str = commands.Param(
            default="Объявления", choices=list(_NEWS_CHANNELS), description="Канал (если дал ID, а не ссылку)"
        ),
    ):
        message_id, channel_id = self._parse_message_ref(message, _NEWS_CHANNELS[channel])
        if message_id is None:
            await inter.response.send_message("Не понял ссылку/ID сообщения.", ephemeral=True)
            return
        # только новостные каналы: иначе кривой ссылкой можно перезаписать
        # любой контейнер бота с «# »-заголовком (роль-селект, интро AI-треда)
        if channel_id not in _NEWS_CHANNELS.values():
            await inter.response.send_message("Это сообщение не в новостном канале — править можно только новости.", ephemeral=True)
            return
        target_channel = self.bot.get_channel(channel_id)
        if target_channel is None:
            await inter.response.send_message("Канал не найден.", ephemeral=True)
            return
        try:
            msg = await target_channel.fetch_message(message_id)
        except disnake.HTTPException:
            await inter.response.send_message("Не смог получить сообщение (не найдено или нет доступа).", ephemeral=True)
            return
        if msg.author.id != self.bot.user.id or not msg.components:
            await inter.response.send_message("Это не новость-контейнер от бота — править нечего.", ephemeral=True)
            return
        draft = _parse_container(msg.components[0])
        if draft is None:
            await inter.response.send_message("Не смог разобрать этот контейнер на поля новости.", ephemeral=True)
            return
        draft.author_id = inter.author.id
        draft.channel_id = channel_id
        draft.edit_message_id = message_id
        draft_id = next(self._seq)
        self.drafts[draft_id] = draft
        await inter.response.send_modal(NewsModal(self, draft_id))

    @staticmethod
    def _parse_message_ref(ref: str, fallback_channel_id: int) -> tuple[int | None, int]:
        """«Ссылка или ID» → (message_id, channel_id). В ссылке канал надёжнее аргумента."""
        ref = ref.strip().rstrip("/")
        parts = ref.split("/")
        if len(parts) >= 3 and parts[-1].isdigit() and parts[-2].isdigit():
            return int(parts[-1]), int(parts[-2])
        if ref.isdigit():
            return int(ref), fallback_channel_id
        return None, fallback_channel_id

    @commands.Cog.listener("on_button_click")
    async def newsButtons(self, inter: disnake.MessageInteraction):
        cid = inter.component.custom_id or ""
        if not cid.startswith("NEWS_"):
            return
        # превью ephemeral и так видит только автор, но кнопки — граница доверия
        if not any(r.id in _ADMIN_ROLES for r in getattr(inter.author, "roles", [])):
            await inter.response.send_message("Только для админов.", ephemeral=True)
            return
        action, _, raw_id = cid.partition(":")
        draft_id = int(raw_id) if raw_id.isdigit() else -1
        draft = self.drafts.get(draft_id)
        if draft is None:
            await inter.response.edit_message(
                components=[disnake.ui.TextDisplay("Черновик потерян (бот перезапускался?) — начни заново: /news")]
            )
            return
        if draft.author_id != inter.author.id:
            # чужая кнопка (коллизия id после рестарта) не должна трогать черновик
            await inter.response.send_message("Этот черновик не твой — начни свой: /news", ephemeral=True)
            return
        if action == "NEWS_EDIT":
            await inter.response.send_modal(NewsModal(self, draft_id))
        elif action == "NEWS_CANCEL":
            del self.drafts[draft_id]
            await inter.response.edit_message(components=[disnake.ui.TextDisplay("❌ Черновик удалён.")])
        elif action == "NEWS_PUB":
            # Извлекаем черновик СИНХРОННО до первого await: два быстрых клика
            # иначе оба нашли бы его и опубликовали новость дважды (двойной пинг
            # ~500 участникам). Второй клик увидит None и получит «черновик потерян».
            claimed = self.drafts.pop(draft_id, None)
            if claimed is None:
                await inter.response.edit_message(
                    components=[disnake.ui.TextDisplay("Уже публикуется или опубликовано.")]
                )
                return
            await self._publish(inter, draft_id, claimed)

    async def _publish(self, inter: disnake.MessageInteraction, draft_id: int, draft: _Draft):
        channel = self.bot.get_channel(draft.channel_id)
        if channel is None:
            self.logger.error("Канал новостей %s не найден в кэше", draft.channel_id)
            # черновик возвращаем: набранный текст дороже, чем чистота словаря
            self.drafts[draft_id] = draft
            await inter.response.edit_message(
                components=[disnake.ui.TextDisplay("😞 Канал не найден — попробуй ещё раз или сообщи админам."),
                            *self._preview(draft, draft_id)]
            )
            return
        # публикация с реакциями занимает >3с — сначала отвечаем на интеракцию
        await inter.response.edit_message(components=[disnake.ui.TextDisplay("⏳ Публикую...")])
        container = _news_container(draft)
        mentions = disnake.AllowedMentions(
            everyone=False, users=False,
            roles=[disnake.Object(id=draft.ping_role_id)] if draft.ping_role_id else False,
        )
        try:
            if draft.edit_message_id:
                msg = await channel.fetch_message(draft.edit_message_id)
                # allowed_mentions и на edit: без него Discord перепарсит упоминания
                # с дефолтными разрешениями (риск повторного пинга роли)
                await msg.edit(components=[container], allowed_mentions=mentions)
                note = f"✏️ Новость обновлена: {msg.jump_url}"
            else:
                msg = await channel.send(components=[container], allowed_mentions=mentions)
                note = f"✅ Опубликовано: {msg.jump_url}"
                # автореакции + тред — как у новостей от людей (on_message посты ботов игнорирует).
                # Новость уже опубликована: сбой «декора» не должен читаться как сбой публикации,
                # а сбой реакций (нет ADD_REACTIONS) — блокировать создание треда
                if draft.channel_id in Channels.news_reaction_channels:
                    try:
                        for emoji in ("❤️", "👍", "👎"):
                            await msg.add_reaction(emoji)
                            await asyncio.sleep(1)
                    except disnake.HTTPException:
                        self.logger.warning("Не удалось повесить реакции под новостью %s", msg.id)
                    try:
                        await msg.create_thread(name="Обсуждение", reason="Тред на новую новость")
                    except disnake.HTTPException:
                        self.logger.warning("Не удалось создать тред под новостью %s", msg.id)
        except disnake.HTTPException:
            self.logger.exception("Не удалось опубликовать новость в канал %s", draft.channel_id)
            # публикация НЕ случилась — вернуть черновик и кнопки, текст не теряем
            self.drafts[draft_id] = draft
            await inter.edit_original_response(
                components=[disnake.ui.TextDisplay("😞 Не получилось опубликовать — попробуй ещё раз (детали в логах)."),
                            *self._preview(draft, draft_id)]
            )
            return
        self.drafts.pop(draft_id, None)
        await inter.edit_original_response(components=[disnake.ui.TextDisplay(note)])


def setup(bot: commands.Bot):
    bot.add_cog(NewsEditor(bot))
