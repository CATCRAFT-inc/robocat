"""Редактор новостей (issue #4): /news → модалка → ephemeral-превью → публикация.

Новости уходят V2-контейнерами в новостные каналы. Черновики живут в памяти
процесса до публикации; рестарт бота их теряет — превью живёт минуты, тащить
их в БД незачем. /news_edit разбирает опубликованный контейнер обратно в поля
и открывает ту же модалку.

Тело новости — размеченный текст: строка-ссылка становится галереей картинок,
строка из трёх дефисов — разделителем, остальное уходит в TextDisplay как есть
(markdown Discord рисует сам). Так в одном поле модалки задаётся произвольное
чередование текста и картинок — форма на пять полей этого не умеет.
"""

import asyncio
import itertools
import logging
import re
import time
from dataclasses import dataclass

import disnake
from disnake.ext import commands

from bot.discord_config import Channels, Roles, has_config_roles
from bot.storage import ColorStorage
from bot.utils import AnsweringModal, decorate_news

_NEWS_CHANNEL_NAMES = ("Объявления", "Газета", "Медиа", "Информатор", "Секретный")
_NO_PING = "Без пинга"
# placeholder модалки: у Discord на него 100 символов, поэтому пример короткий
_TEXT_HINT = "Что нового.\nhttps://ссылка-на-картинку.png\n---\n**Дальше** ещё текст."

_PING_LINE = re.compile(r"^<@&(\d+)>$")
# только картинки: голая ссылка на discord.gg или сайт — обычный кликабельный
# текст, в галерее она стала бы битой плиткой (или отказом Discord)
_IMAGE_LINE = re.compile(r"^https?://\S+\.(?:png|jpe?g|gif|webp)(?:\?\S*)?$", re.IGNORECASE)
_DIVIDER_LINE = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})$")
_CODE_FENCE = "```"
_DIVIDER = "---"
_GALLERY_LIMIT = 10  # столько картинок держит одна MediaGallery
# у сообщения V2 потолки: 40 компонентов и 4000 символов текста, оба считают
# ВЛОЖЕННЫЕ. Тяжелее публикации не она, а превью: подпись + контейнер + три кнопки
_PREVIEW_OVERHEAD = 6
_MAX_BLOCKS = 40 - _PREVIEW_OVERHEAD
_MAX_TEXT = 4000 - 200  # запас на подпись превью и заголовок
# id служебных блоков пинга: Discord возвращает заданный id обратно, и это
# единственный способ отличить наш пинг от строки-упоминания, набранной руками
_PING_TEXT_ID = 99
_PING_SEPARATOR_ID = 98

# message.components отдаёт read-side классы, в тестах контейнер собран из ui-классов —
# атрибуты (children/content/items) совпадают, парсер принимает оба
_TEXT_TYPES = (disnake.ui.TextDisplay, disnake.TextDisplay)
_GALLERY_TYPES = (disnake.ui.MediaGallery, disnake.MediaGallery)
_SEPARATOR_TYPES = (disnake.ui.Separator, disnake.Separator)


def _news_channels() -> dict[str, int]:
    return {
        "Объявления": Channels.announcements,
        "Газета": Channels.newspaper,
        "Медиа": Channels.media_news,
        "Информатор": Channels.informator,
        "Секретный": Channels.secret,
    }


def _ping_roles() -> dict[str, int]:
    return {
        "Обновления сервера": Roles.server_updates,
        "Ивенты": Roles.events,
        "РП": Roles.rp,
        "Тех. работы": Roles.maintanence,
        "Медиа": Roles.media,
        "Обновления сайта": Roles.site_updates,
    }


def _admin_roles() -> set[int]:
    return {Roles.admin, Roles.st_admin, Roles.media, Roles.developer}


@dataclass
class _Draft:
    author_id: int
    channel_id: int
    title: str = ""
    text: str = ""
    ping_role_id: int | None = None
    edit_message_id: int | None = None  # /news_edit: правим существующий пост


def _body_blocks(text: str) -> list:
    """Размеченный текст → компоненты тела: TextDisplay / MediaGallery / Separator."""
    blocks: list = []
    paragraph: list[str] = []
    images: list[str] = []

    def flush_paragraph() -> None:
        chunk = "\n".join(paragraph).strip()
        paragraph.clear()
        if chunk:  # пустой TextDisplay Discord не принимает
            blocks.append(disnake.ui.TextDisplay(chunk))

    def flush_images() -> None:
        while images:
            batch = images[:_GALLERY_LIMIT]
            del images[:_GALLERY_LIMIT]
            blocks.append(disnake.ui.MediaGallery(*(disnake.MediaGalleryItem(u) for u in batch)))

    in_code = False
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith(_CODE_FENCE):
            # содержимое ``` не разбираем: разрезанный по строкам блок кода
            # не закроется, и у читателя останутся голые backtick'и
            in_code = not in_code
            flush_images()
            paragraph.append(line)
        elif in_code:
            paragraph.append(line)
        elif _IMAGE_LINE.match(stripped):
            flush_paragraph()
            images.append(stripped)
        elif _DIVIDER_LINE.match(stripped):
            flush_paragraph()
            flush_images()
            blocks.append(disnake.ui.Separator())
        else:
            # пустая строка между ссылками не рвёт галерею: они собираются в сетку
            if stripped:
                flush_images()
            paragraph.append(line)
    flush_paragraph()
    flush_images()
    return blocks


def _too_big(draft: _Draft) -> str:
    """Причина, по которой Discord не примет пост, либо пустая строка."""
    children = _news_container(draft).children
    blocks = len(children)
    if blocks > _MAX_BLOCKS:
        return (
            f"Слишком много блоков: {blocks} при потолке {_MAX_BLOCKS} "
            "(каждая картинка и каждый `---` — блок)"
        )
    symbols = sum(len(c.content) for c in children if isinstance(c, _TEXT_TYPES))
    if symbols > _MAX_TEXT:
        return f"Слишком много текста: {symbols} символов при потолке {_MAX_TEXT}"
    return ""


def _news_container(draft: _Draft) -> disnake.ui.Container:
    children = [
        disnake.ui.TextDisplay(f"# {draft.title}"),
        disnake.ui.Separator(),
        *_body_blocks(draft.text),
    ]
    if draft.ping_role_id:
        children.append(disnake.ui.Separator(id=_PING_SEPARATOR_ID))
        children.append(disnake.ui.TextDisplay(f"<@&{draft.ping_role_id}>", id=_PING_TEXT_ID))
    return disnake.ui.Container(*children, accent_colour=disnake.Colour.from_hex(ColorStorage.main))


def _parse_container(container) -> "_Draft | None":
    """Опубликованный контейнер → черновик. None — не похоже на нашу новость.

    Обратная сторона `_news_container`: тело сворачивается назад в размеченный
    текст — не байт в байт (пустые строки вокруг картинок и вид разделителя
    нормализуются), но с тем же набором и порядком блоков.

    🛑 Пинг снимается ТОЛЬКО по служебному id, а не по виду строки. Иначе тело,
    заканчивающееся упоминанием роли, при «Править» теряло бы две строки и молча
    подставляло роль в выпадашку — а следом уходило в публикацию с разрешением
    пинговать, которого автор не давал.
    """
    children = list(container.children)
    if not children or not isinstance(children[0], _TEXT_TYPES):
        return None
    head = (children[0].content or "").strip()
    if not head.startswith("# "):
        return None
    draft = _Draft(author_id=0, channel_id=0, title=head[2:].strip())

    body = children[1:]
    if body and isinstance(body[0], _SEPARATOR_TYPES):
        body = body[1:]  # служебный разделитель под заголовком
    if body and getattr(body[-1], "id", 0) == _PING_TEXT_ID:
        m = _PING_LINE.match((body[-1].content or "").strip())
        if m:
            draft.ping_role_id = int(m.group(1))
            body = body[:-1]
            if body and getattr(body[-1], "id", 0) == _PING_SEPARATOR_ID:
                body = body[:-1]

    lines: list[str] = []
    for child in body:
        if isinstance(child, _TEXT_TYPES):
            lines.append(child.content or "")
        elif isinstance(child, _GALLERY_TYPES):
            lines.extend(item.media.url for item in child.items)
        elif isinstance(child, _SEPARATOR_TYPES):
            lines.append(_DIVIDER)
    draft.text = "\n".join(lines).strip()
    if not draft.title or not draft.text:
        return None
    return draft


class NewsModal(AnsweringModal):
    def __init__(self, cog: "NewsEditor", draft_id: int):
        self.cog = cog
        self.draft_id = draft_id
        draft = cog.drafts[draft_id]
        ping_options = [
            disnake.SelectOption(label=_NO_PING, value=_NO_PING, default=draft.ping_role_id is None)
        ] + [
            disnake.SelectOption(label=label, value=str(role_id), default=draft.ping_role_id == role_id)
            for label, role_id in _ping_roles().items()
        ]
        components = [
            # все поля — через Label: disnake заворачивает голый TextInput в legacy
            # ActionRow, а смешивать два формата компонентов в одной модалке рискованно
            disnake.ui.Label(
                text="Заголовок",
                component=disnake.ui.TextInput(
                    custom_id="news_title",
                    value=draft.title or None,
                    max_length=150,
                ),
            ),
            disnake.ui.Label(
                text="Текст",
                description="Ссылка на картинку отдельной строкой, --- — разделитель",
                component=disnake.ui.TextInput(
                    custom_id="news_text",
                    style=disnake.TextInputStyle.paragraph,
                    value=draft.text or None,
                    placeholder=_TEXT_HINT,
                    max_length=4000,
                ),
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
        picked = inter.resolved_values.get("news_ping")
        value = picked[0] if isinstance(picked, (list, tuple)) else picked
        draft.ping_role_id = int(value) if value and value != _NO_PING else None

        # ловим здесь, а не на публикации: там отказ Discord читается как
        # «сбой, попробуй ещё раз», и человек не понимает, что именно чинить
        problem = _too_big(draft)
        if problem:
            components = self._refuse(problem)
        else:
            components = self.cog._preview(draft, self.draft_id)
        try:
            if inter.message is not None:
                # модалка открыта кнопкой «Править» с превью — обновляем то же превью
                await inter.response.edit_message(components=components)
            else:
                await inter.response.send_message(components=components, ephemeral=True)
        except disnake.HTTPException:
            # превью не приняли по причине, которой нет в наших проверках. Без этой
            # ветки интеракция остаётся без ответа, а текст — в черновике, до
            # которого не дотянуться: кнопки «Править» ещё не существует
            self.cog.logger.exception("Превью новости отвергнуто Discord")
            await inter.response.send_message(
                components=self._refuse("Discord не принял превью — скорее всего пост слишком большой"),
                ephemeral=True,
            )

    def _refuse(self, reason: str) -> list:
        """Отказ, из которого есть выход: текст остаётся в черновике за кнопкой."""
        return [
            disnake.ui.TextDisplay(f"{reason}. Текст не потерян — жми «Править»."),
            disnake.ui.ActionRow(
                disnake.ui.Button(
                    style=disnake.ButtonStyle.secondary, label="Править",
                    custom_id=f"NEWS_EDIT:{self.draft_id}",
                ),
            ),
        ]


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
    @has_config_roles("admin", "st_admin", "developer")
    async def news(
        self,
        inter: disnake.AppCmdInter,
        channel: str = commands.Param(
            default="Объявления", choices=list(_NEWS_CHANNEL_NAMES), description="Куда публиковать"
        ),
    ):
        draft_id = next(self._seq)
        self.drafts[draft_id] = _Draft(
            author_id=inter.author.id, channel_id=_news_channels()[channel]
        )
        await inter.response.send_modal(NewsModal(self, draft_id))

    @commands.slash_command(name="news_edit", description="Править опубликованную ботом новость")
    @has_config_roles("admin", "st_admin", "developer")
    async def newsEdit(
        self,
        inter: disnake.AppCmdInter,
        message: str = commands.Param(description="Ссылка на сообщение новости или его ID"),
        channel: str = commands.Param(
            default="Объявления", choices=list(_NEWS_CHANNEL_NAMES), description="Канал (если дал ID, а не ссылку)"
        ),
    ):
        message_id, channel_id = self._parse_message_ref(message, _news_channels()[channel])
        if message_id is None:
            await inter.response.send_message("Не понял ссылку/ID сообщения.", ephemeral=True)
            return
        # только новостные каналы: иначе кривой ссылкой можно перезаписать
        # любой контейнер бота с «# »-заголовком (роль-селект, интро AI-треда)
        if channel_id not in _news_channels().values():
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
        if not any(r.id in _admin_roles() for r in getattr(inter.author, "roles", [])):
            await inter.response.send_message("Только для админов.", ephemeral=True)
            return
        action, _, raw_id = cid.partition(":")
        draft_id = int(raw_id) if raw_id.isascii() and raw_id.isdigit() else -1
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
        try:
            await inter.response.edit_message(components=[disnake.ui.TextDisplay("⏳ Публикую...")])
        except disnake.HTTPException:
            # ack не доставлен (интеракция протухла или её перехватил другой
            # инстанс — инцидент 26.07.2026): черновик уже вынут из словаря,
            # без возврата он терялся безвозвратно
            self.drafts[draft_id] = draft
            raise
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
                    await decorate_news(msg, self.logger)
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
