"""Заявки на новость от игроков.

Панель с одной кнопкой (`Embeds.news_request_embed`, публикуется `/send_embed`)
→ модалка → приватный тред с автором и репортёрами → кнопки решения в треде.
Решение уезжает в три места: архив с транскриптом в `Channels.ticket_log`,
короткая карточка в чат репортёров и ЛС автору. Тред после решения удаляется —
переписка уже сохранена транскриптом в логе.

Движок багов/админ-тикетов (`engine.TicketEngine`) сознательно не расширялся:
у заявок другой канал, другие права (только репортёры) и третий адресат
уведомления. Общий у них только архив — `engine.archive_ticket`.
"""

import asyncio
import logging

import disnake
from disnake import TextInputStyle
from disnake.ext import commands

from bot.discord_config import Channels, Roles
from bot.flag_system.flag_system import flags
from bot.storage import Buttons, ColorStorage
from bot.utils import AnsweringModal, create_container
from .engine import archive_ticket

logger = logging.getLogger("robocat.news_request")

_DONE_ID = "NEWSREQ_DONE"
_DECLINE_ID = "NEWSREQ_DECLINE"

# ponytail: один лок на всех — заявки редки, контенции нет; per-user при необходимости
_rate_lock = asyncio.Lock()


async def news_rate_limit_ok(inter: disnake.MessageInteraction) -> bool:
    """Анти-спам заявок. True → можно открывать модалку; False → отказ уже отправлен.

    Счётчик растёт при ОТКРЫТИИ модалки, а не при создании треда: иначе двойной
    клик успевает открыть две модалки и создать два треда. Лок сериализует
    check-then-act по той же причине, что и у баг-репортов.
    """
    async with _rate_lock:
        if await flags.hasFlag(inter.author, "news_request_cooldown"):
            await inter.send(
                "Ты уже отправлял(а) новости совсем недавно! Дай репортёрам разгрести, "
                "и возвращайся попозже =)",
                ephemeral=True,
            )
            return False
        opened = await flags.getFlag(inter.author, "news_requests_opened")
        if opened and int(opened.value) >= 3:
            await inter.send(
                "Воу-воу, котик! Мы очень ценим твои новости, но это уже похоже на спам. "
                "Я вынужден дать тебе КД, попробуй попозже.",
                ephemeral=True,
            )
            await flags.setFlag(inter.author, "news_request_cooldown", "true", "15мин")
            return False
        if opened:
            # атомарный инкремент в SQL, а не read-modify-write в питоне
            await flags.incrementFlag(inter.author, "news_requests_opened", 1, expires_at="15мин")
        else:
            await flags.setFlag(inter.author, "news_requests_opened", 1, expires_at="15мин")
        return True


class NewsRequestHandler(commands.Cog):
    """Кнопка «Отправить новость», тред заявки и решение репортёров."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Треды в процессе закрытия: два репортёра, жмущие кнопку одновременно,
        # иначе делают двойной архив + необработанный 404 на втором delete
        self._closing: set[int] = set()

    @commands.Cog.listener("on_button_click")
    async def newsRequestButton(self, inter: disnake.MessageInteraction):
        if inter.component.custom_id != Buttons.NEWS_REQUEST.id:
            return
        if await news_rate_limit_ok(inter):
            await inter.response.send_modal(self.NewsModal(self))

    class NewsModal(AnsweringModal):
        def __init__(self, cog: "NewsRequestHandler"):
            self.cog = cog
            components = [
                disnake.ui.TextInput(
                    label="Твой ник",
                    placeholder="Введи свой ник",
                    custom_id="nick",
                    style=TextInputStyle.short,
                    max_length=50,
                ),
                disnake.ui.TextInput(
                    label="Новость",
                    placeholder="Что произошло, где, когда, кто участвовал",
                    custom_id="news",
                    style=TextInputStyle.long,
                    max_length=2000,
                ),
                disnake.ui.TextInput(
                    label="(Опционально) Ссылки, скриншоты, детали",
                    placeholder="Всё, что поможет репортёрам",
                    custom_id="extra",
                    style=TextInputStyle.long,
                    max_length=1000,
                    required=False,
                ),
            ]
            super().__init__(title="Заявка на новость", components=components)

        async def callback(self, inter: disnake.ModalInteraction):
            await inter.response.defer(ephemeral=True)
            await self.cog._createRequest(inter)

    async def _createRequest(self, inter: disnake.ModalInteraction):
        channel = inter.guild.get_channel(Channels.news_request) if inter.guild else None
        if channel is None:
            # без канала create_thread упал бы AttributeError мимо except ниже,
            # и юзер остался бы с вечным «думает» и потерянным текстом заявки
            logger.error("Канал заявок на новость %s не найден в кэше — заявка не создана", Channels.news_request)
            await inter.edit_original_response("Не получилось отправить новость (канал недоступен). Сообщи админам!")
            return

        values = inter.text_values
        nick = values.get("nick") or "???"
        news = values.get("news") or ""
        extra = values.get("extra") or ""
        # Первые 5 слов, но не длиннее лимита имени треда Discord (100): ссылка
        # в начале текста легко его превышает и роняет create_thread с 400.
        thread_name = " ".join(news.split(" ")[:5])[:100] or "Заявка на новость"

        try:
            thread = await channel.create_thread(
                name=thread_name,
                type=disnake.ChannelType.private_thread,
                auto_archive_duration=10080,
                reason=f"Заявка на новость от {nick}",
            )
        except disnake.HTTPException:
            logger.exception("Не удалось создать тред заявки на новость в канале %s", Channels.news_request)
            await inter.edit_original_response("Не получилось создать тред заявки. Попробуй позже или сообщи админам!")
            return

        # Пинг роли приватный тред НЕ раскрывает: в него надо добавлять поимённо,
        # иначе репортёры заявку просто не увидят.
        reporters_added = await self._inviteReporters(thread, inter.author)

        extra_block = f"\n### Дополнительно\n{extra}" if extra else ""
        container = disnake.ui.Container(
            disnake.ui.TextDisplay(f"# 📰 Новость!\nОт {nick} ({inter.author.mention})"),
            disnake.ui.Separator(),
            disnake.ui.TextDisplay(f"### Текст новости\n{news}{extra_block}"),
            disnake.ui.TextDisplay(f"-# ||<@&{Roles.reporter}> {inter.author.mention}||"),
            disnake.ui.ActionRow(
                disnake.ui.Button(
                    style=disnake.ButtonStyle.green,
                    label="✅ Новость опубликована",
                    custom_id=_DONE_ID,
                ),
                disnake.ui.Button(
                    style=disnake.ButtonStyle.danger,
                    label="⛔ Отклонить",
                    custom_id=_DECLINE_ID,
                ),
            ),
            accent_colour=disnake.Color.from_hex(ColorStorage.main),
        )
        try:
            await thread.send(components=[container])
        except disnake.HTTPException:
            logger.exception("Не удалось отправить карточку заявки в тред %s", thread.id)
            await inter.edit_original_response("Тред создан, но карточка не отправилась — напиши в нём вручную и позови админов.")
            return

        await flags.setFlag(thread, "created_by", inter.author.id)
        if reporters_added:
            await inter.edit_original_response(f"Новость отправлена репортёрам! Обсуждение здесь: <#{thread.id}>")
        else:
            # Пустой приватный тред выглядит для автора успехом, хотя заявку никто не
            # увидит — говорим прямо и зовём админов, а не рапортуем ложный успех.
            await inter.edit_original_response(
                f"Заявка создана (<#{thread.id}>), но добавить репортёров не вышло — я позвал админов, "
                "они разберутся."
            )
            try:
                await thread.send(f"⚠️ Не удалось добавить репортёров в тред! <@&{Roles.admin}> <@&{Roles.st_admin}>")
            except disnake.HTTPException:
                logger.exception("Не удалось позвать админов в тред заявки %s без репортёров", thread.id)
        try:
            await inter.author.send(
                components=create_container(
                    "## 📰 Твоя новость ушла репортёрам!",
                    f"Обсудить её можно здесь: https://discord.com/channels/{inter.guild_id}/{thread.id}\n"
                    "Как только репортёры примут решение, я напишу тебе сюда.",
                )
            )
        except disnake.HTTPException:
            # Закрытые ЛС — штатная ситуация; НЕ роняем callback, флаг created_by уже стоит
            logger.warning("Не удалось отправить ЛС автору заявки на новость %s (закрытые ЛС?)", inter.author.id)

    async def _inviteReporters(self, thread: disnake.Thread, author) -> bool:
        """Добавляет автора и всех репортёров в приватный тред.

        Возвращает False, если в треде не оказалось НИ ОДНОГО репортёра — заявку
        тогда некому увидеть, и вызывающий обязан сказать это вслух, а не
        отрапортовать автору успех.
        """
        role = thread.guild.get_role(Roles.reporter) if thread.guild else None
        if role is None:
            logger.error("Роль репортёров %s не найдена — в тред %s добавлен только автор", Roles.reporter, thread.id)
        reporters = list(role.members) if role else []
        added = 0
        for member in [author, *reporters]:
            try:
                await thread.add_user(member)
                if member is not author:
                    added += 1
            except disnake.HTTPException:
                # один недобавленный участник не должен ронять заявку
                logger.warning("Не удалось добавить %s в тред заявки %s", getattr(member, "id", "?"), thread.id)
        if not added:
            logger.error("В тред заявки %s не добавлен ни один репортёр — заявку никто не увидит", thread.id)
        return added > 0

    @commands.Cog.listener("on_button_click")
    async def newsDecisionButtons(self, inter: disnake.MessageInteraction):
        custom_id = inter.component.custom_id or ""
        if custom_id not in (_DONE_ID, _DECLINE_ID):
            return
        thread = inter.channel
        if not isinstance(thread, disnake.Thread) or thread.parent_id != Channels.news_request:
            await inter.response.send_message("Эти кнопки работают только в тредах заявок на новость.", ephemeral=True)
            return
        # Право: только репортёры (ручная проверка ролей — интеракция не команда)
        if not any(getattr(r, "id", None) == Roles.reporter for r in getattr(inter.author, "roles", ())):
            await inter.response.send_message("Решение по новости принимают только репортёры.", ephemeral=True)
            return

        published = custom_id == _DONE_ID
        await inter.response.send_modal(self.DecisionModal(self, published=published))

    class DecisionModal(AnsweringModal):
        def __init__(self, cog: "NewsRequestHandler", *, published: bool):
            self.cog = cog
            self.published = published
            label = "Ссылка на новость / комментарий" if published else "Причина отклонения"
            components = [
                disnake.ui.TextInput(
                    label=label,
                    placeholder="Автор увидит это в личных сообщениях",
                    custom_id="note",
                    style=TextInputStyle.paragraph,
                    max_length=1000,
                    required=False,
                )
            ]
            super().__init__(
                title="Новость опубликована" if published else "Отклонение новости",
                components=components,
            )

        async def callback(self, inter: disnake.ModalInteraction):
            # ephemeral: плейсхолдер modal_submit по умолчанию ПУБЛИЧЕН,
            # и сообщение об ошибке уходило в тред всем.
            await inter.response.defer(ephemeral=True)
            note = inter.text_values.get("note") or None
            await self.cog._closeRequest(inter, inter.channel, note, published=self.published)

    async def _closeRequest(self, inter, thread: disnake.Thread, note: str | None, *, published: bool):
        if thread.id in self._closing:
            await inter.edit_original_response("Эту заявку уже закрывает другой репортёр.")
            return
        self._closing.add(thread.id)
        try:
            await self._closeRequestInner(inter, thread, note, published=published)
        finally:
            self._closing.discard(thread.id)

    async def _closeRequestInner(self, inter, thread: disnake.Thread, note: str | None, *, published: bool):
        ticket_type = "Заявка на новость (опубликована)" if published else "Заявка на новость (отклонена)"
        if not await archive_ticket(self.bot, thread, ticket_type=ticket_type, closer=inter.author, note=note):
            await inter.edit_original_response("⚠️ Не удалось сохранить лог заявки — тред НЕ удалён. Проверь канал логов и повтори.")
            return

        creator_flag = await flags.getFlag(thread, "created_by")
        author_id = int(creator_flag.value) if creator_flag else None

        await self._notifyReportersChat(thread, inter.author, author_id, note, published=published)
        if author_id is not None:
            await self._notifyAuthor(inter, author_id, note, published=published)

        await flags.removeFlag(thread, "created_by")
        try:
            await thread.delete(reason=f"Заявка на новость закрыта {inter.author.id}")
        except disnake.HTTPException:
            logger.exception("Не удалось удалить тред заявки %s после закрытия", thread.id)
            raise

    async def _notifyReportersChat(self, thread, closer, author_id: int | None, note: str | None, *, published: bool):
        channel = self.bot.get_channel(Channels.reporters_chat)
        if channel is None:
            logger.error("Чат репортёров %s не найден — решение по заявке %s не залогировано туда", Channels.reporters_chat, thread.id)
            return
        author_line = f"<@{author_id}>" if author_id is not None else "неизвестен"
        note_line = f"\n**Комментарий:** {note}" if note else ""
        container = create_container(
            # 🛑 Имя треда — первые слова текста, который набрал репортёр. В
            # имени треда разметка инертна, в теле сообщения — нет: заявка,
            # начинающаяся с `**` или `[клик](…)`, разваливает заголовок и даёт
            # ссылку от имени бота. В `engine.py` это уже закрыто, а сюда
            # развёртка по классу не доехала.
            f"## {'✅ Новость опубликована' if published else '⛔ Новость отклонена'}: "
            f"{disnake.utils.escape_markdown(thread.name)}",
            f"**Автор:** {author_line}\n**Решил:** {closer.mention}{note_line}",
        )
        try:
            # Пинги автора и закрывшего — штатные, их и оставляем. Гасим только
            # everyone/роли: имя треда — текст репортёра, и `<@&роль>` в нём
            # пингнул бы канал. `AllowedMentions.none()` тут было ошибкой —
            # оно глушило и тех двоих, ради кого сообщение пишется.
            await channel.send(
                components=container,
                allowed_mentions=disnake.AllowedMentions(
                    everyone=False, roles=False, users=True
                ),
            )
        except disnake.HTTPException:
            logger.exception("Не удалось отправить решение по заявке %s в чат репортёров", thread.id)

    async def _notifyAuthor(self, inter, author_id: int, note: str | None, *, published: bool):
        member = inter.guild.get_member(author_id) if inter.guild else None
        if member is None:
            logger.info("Автор заявки %s не найден на сервере — ЛС о решении не отправлено", author_id)
            return
        if published:
            container = create_container(
                "## 🎉 Твоя новость опубликована!",
                "Огромное спасибо — ты делаешь нашу газету живее 💖"
                + (f"\n**Комментарий репортёров:**\n{note}" if note else ""),
            )
        else:
            container = create_container(
                "## 😔 Твоя новость отклонена...",
                "Огромное спасибо за заявку, но в этот раз она не подошла...\n"
                f"**Причина:** {note or 'Не указали...'}",
            )
        try:
            await member.send(components=container)
        except disnake.HTTPException:
            logger.info("Не удалось отправить ЛС автору заявки %s о решении (закрытые ЛС?)", author_id)


def setup(bot: commands.Bot):
    bot.add_cog(NewsRequestHandler(bot))
