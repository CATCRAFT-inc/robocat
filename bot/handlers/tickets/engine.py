import datetime
import io
import logging

import disnake
from disnake.ext import commands, tasks

from bot.flag_system.flag_system import flags
from bot.storage import Channels, ColorStorage, Roles
from .admin_ticket import AdminTicket
from bot.handlers.tickets.bugs import BugHandler, remove_bug_from_index, _extract_component_text
from bot.utils import create_container, create_embed

try:  # W3 создаёт этот модуль; выжимка — необязательная фича
    from bot.ai.llm import llm, AIUnavailable
except Exception:  # pragma: no cover
    llm = None

    class AIUnavailable(Exception):
        pass

logger = logging.getLogger("robocat.tickets")

_MSK = datetime.timezone(datetime.timedelta(hours=3))


class TicketEngine(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.staleTicketReminder.start()
        logger.info("TicketEngine загружен — напоминалка о зависших тикетах запущена")

    def cog_unload(self):
        self.staleTicketReminder.cancel()
        logger.info("TicketEngine выгружен — напоминалка остановлена")

    async def _archiveTicket(self, thread: disnake.Thread, *, ticket_type: str, closer, note: str | None):
        """Собирает транскрипт треда, делает AI-выжимку и постит всё в Channels.ticket_log."""
        lines = []
        try:
            async for msg in thread.history(limit=None, oldest_first=True):
                ts = msg.created_at.strftime("%Y-%m-%d %H:%M")
                author = getattr(msg.author, "display_name", None) or "???"
                content = msg.content or _extract_component_text(msg.components)  # V2-контейнеры (описание тикета) тоже в транскрипт
                if msg.attachments:
                    content = (content + " " + " ".join(a.url for a in msg.attachments)).strip()
                lines.append(f"[{ts}] {author}: {content}")
        except disnake.HTTPException:
            logger.exception("Не удалось собрать историю тикета %s", thread.id)
        transcript = "\n".join(lines) if lines else "(пусто)"

        summary = "⚠️ Выжимка недоступна"
        if llm is not None:
            try:
                summary = await llm.ask(
                    "Сожми тикет в пару предложений: кто создал, суть проблемы, что решили.\n\nПереписка:\n"
                    + transcript[:12000],
                    max_tokens=600,
                )
            except AIUnavailable:
                logger.warning("AI недоступен — выжимка тикета %s пропущена", thread.id)
            except Exception:
                logger.exception("Ошибка AI-выжимки тикета %s", thread.id)

        creator_flag = await flags.getFlag(thread, "created_by")
        author_line = f"\n**Автор:** <@{creator_flag.value}>" if creator_flag else "\n**Автор:** неизвестен"

        buf = io.BytesIO(transcript.encode("utf-8"))
        transcript_file = disnake.File(buf, filename=f"ticket-{thread.id}.txt")
        note_line = f"\n**Комментарий/причина:** {note}" if note else ""
        container = disnake.ui.Container(
            disnake.ui.TextDisplay(f"## 📁 Тикет закрыт: {thread.name}"),
            disnake.ui.Separator(),
            disnake.ui.TextDisplay(f"**Тип:** {ticket_type}{author_line}\n**Закрыл:** {closer.mention}{note_line}"),
            disnake.ui.Separator(),
            disnake.ui.TextDisplay(f"### Выжимка\n{summary}"),
            # V2-сообщения прячут вложения без явной ссылки на них — иначе файл не виден
            disnake.ui.File(file=f"attachment://ticket-{thread.id}.txt"),
            accent_colour=disnake.Color.from_hex(ColorStorage.main),
        )
        log_channel = self.bot.get_channel(Channels.ticket_log)
        if log_channel is None:
            logger.error("Канал логов тикетов %s не найден", Channels.ticket_log)
            return False
        try:
            await log_channel.send(components=[container], file=transcript_file)
        except disnake.HTTPException:
            logger.exception("Не удалось запостить лог тикета %s", thread.id)
            return False
        return True

    async def _finishTicket(self, inter, thread: disnake.Thread, comment: str | None):
        """Успешное закрытие бага/админ-тикета. Интеракция ДОЛЖНА быть уже
        defer'нута снаружи (слэш /done и модалка кнопки делают это сами)."""
        is_support = thread.parent_id == Channels.support
        ticket_type = "Админ-тикет" if is_support else "Баг-репорт"
        if not await self._archiveTicket(thread, ticket_type=ticket_type, closer=inter.author, note=comment):
            await inter.edit_original_response("⚠️ Не удалось сохранить лог тикета — тред НЕ удалён. Проверь канал логов и повтори.")
            return

        if is_support:
            closed_embed = disnake.ui.Container(
                disnake.ui.TextDisplay("## ⭐️ Твой админский тикет закрыт!"),
                disnake.ui.Separator(),
                disnake.ui.TextDisplay(f"### Решение:\n{comment}\n-# Закрыл: <@{inter.author.id}>")
            )
        else:
            description = "Огромное спасибо за репорт бага! Ты помогаешь делать сервер лучше 💖"
            if comment:
                description += f"\n**Комментарий:**\n{comment}"
            closed_embed = create_container(
                title="💫 Сообщённый тобой баг пофикшен!",
                description=description
            )

        user_id_flag = await flags.getFlag(thread, "created_by")
        if user_id_flag:
            member = inter.guild.get_member(int(user_id_flag.value))
            if member:
                try:
                    await member.send(components=closed_embed)
                except disnake.HTTPException:
                    logger.info("Не удалось отправить ЛС автору тикета %s о закрытии (закрытые ЛС?)", member.id)
        await flags.removeFlag(thread, "created_by")
        if not is_support:
            await remove_bug_from_index(thread.id)
        try:
            await thread.delete(reason=f"Тикет закрыт {inter.author.id}")
        except disnake.HTTPException:
            logger.exception("Не удалось удалить тред тикета %s после закрытия", thread.id)
            raise

    async def _rejectTicket(self, inter, thread: disnake.Thread, reason: str | None):
        """Отклонение бага/админ-тикета. Интеракция ДОЛЖНА быть уже defer'нута снаружи."""
        is_support = thread.parent_id == Channels.support
        ticket_type = "Админ-тикет (отклонён)" if is_support else "Баг-репорт (отклонён)"
        if not await self._archiveTicket(thread, ticket_type=ticket_type, closer=inter.author, note=reason):
            await inter.edit_original_response("⚠️ Не удалось сохранить лог тикета — тред НЕ удалён. Проверь канал логов и повтори.")
            return

        if is_support:
            declined_embed = create_container(
                title="😔 Твой админский тикет отклонён...",
                description=f"К сожалению, твой тикет был отклонён...\n**Причина:** {reason or 'Не указали...'}"
            )
        else:
            declined_embed = create_container(
                title="😔 Сообщённый тобой баг отклонён...",
                description=f"Огромное спасибо за репорт бага, но он был отклонён...\n**Причина:** {reason or 'Не указали...'}"
            )

        user_id_flag = await flags.getFlag(thread, "created_by")
        if user_id_flag:
            member = inter.guild.get_member(int(user_id_flag.value))
            if member:
                try:
                    await member.send(components=declined_embed)
                except disnake.HTTPException:
                    logger.info("Не удалось отправить ЛС автору тикета %s об отклонении (закрытые ЛС?)", member.id)
        await flags.removeFlag(thread, "created_by")
        if not is_support:
            await remove_bug_from_index(thread.id)
        try:
            await thread.delete(reason=f"Тикет отклонён {inter.author.id}")
        except disnake.HTTPException:
            logger.exception("Не удалось удалить тред тикета %s после отклонения", thread.id)
            raise

    @tasks.loop(time=datetime.time(hour=12, tzinfo=_MSK))  # 12:00 МСК
    async def staleTicketReminder(self):
        """Раз в сутки напоминает в Channels.secret о зависших (>5 дней) багах и тикетах."""
        entities = await flags.getAllWithFlag("created_by")
        if not entities:
            return
        now = disnake.utils.utcnow()
        stale = []
        for entity_type, entity_id, _exp in entities:
            if entity_type != "thread":
                continue
            thread = self.bot.get_channel(entity_id)
            if not isinstance(thread, disnake.Thread):
                continue
            if thread.parent_id not in (Channels.bugs, Channels.support):
                continue
            last = disnake.utils.snowflake_time(thread.last_message_id or thread.id)
            if (now - last) > datetime.timedelta(days=5):
                stale.append(thread)
        if not stale:
            return
        listing = "\n".join(
            f"- <#{t.id}> ({'баг' if t.parent_id == Channels.bugs else 'тикет'})" for t in stale
        )
        container = disnake.ui.Container(
            disnake.ui.TextDisplay("## ⏰ Зависшие тикеты (>5 дней без ответа)"),
            disnake.ui.Separator(),
            disnake.ui.TextDisplay(listing),
            accent_colour=disnake.Color.from_hex(ColorStorage.main),
        )
        channel = self.bot.get_channel(Channels.secret)
        if channel is not None:
            try:
                await channel.send(components=[container])
            except disnake.HTTPException:
                logger.exception("Не удалось отправить напоминалку о зависших тикетах")

    @staleTicketReminder.before_loop
    async def beforeStaleReminder(self):
        await self.bot.wait_until_ready()

    @staleTicketReminder.error
    async def staleReminderError(self, exc: BaseException):
        logger.error("Напоминалка о зависших тикетах упала — цикл остановлен", exc_info=exc)

    @commands.Cog.listener("on_dropdown")
    async def chooseTicket(self, inter: disnake.MessageInteraction):
        if inter.component.custom_id != "CHOOSE_TICKET":
            return
        match inter.resolved_values[0]:
            case "TICKET_ADMIN":
                await inter.response.send_modal(AdminTicket.AdminTicketModal())
            case "TICKET_POLICE":
                await inter.send(components=create_container("## КСБ пока что нет!", "Сезон-то не начался, хех!"), ephemeral=True)
            case "TICKET_BUGREPORT":
                await inter.response.send_modal(BugHandler.BugModal())
            case _:
                await inter.send("Бот не нашёл такой тип тикета — сообщи в **баг-репорт**!", ephemeral=True)

    @commands.slash_command(name='done', description='Фикс бага/добавление идеи/выполнение запроса')
    @commands.has_any_role(Roles.admin, Roles.st_admin)
    async def doneCommand(self, inter: disnake.ApplicationCommandInteraction, comment: str = None):
        thread = inter.channel
        if not isinstance(thread, disnake.Thread):
            await inter.send("Команду можно прописывать только в тредах!", ephemeral=True)
            return
        forum = thread.parent
        if forum is None:
            await inter.send("Не могу определить родительский канал треда — попробуй позже.", ephemeral=True)
            return
        owner = thread.owner

        match forum.id:
            case Channels.ideas:
                tag_added = forum.get_tag_by_name('Добавлено')
                tag_rejected = forum.get_tag_by_name("Отклонено")
                if tag_added is None:
                    await inter.send("Не нашёл тег «Добавлено» — возможно, его переименовали. Сообщи админам!", ephemeral=True)
                    return
                try:
                    if tag_rejected is not None and tag_rejected in thread.applied_tags:
                        await thread.remove_tags(tag_rejected)
                    await thread.add_tags(tag_added)
                    idea_embed = create_embed(
                        title="💫 Идея добавлена!",
                        description="Предложенная тобой идея была реализована на сервере!\n**Спасибо**💖",
                        color=disnake.Colour.yellow
                    )
                    if comment:
                        idea_embed.add_field(name="Комментарий", value=comment)
                    await thread.send(f"{owner.mention if owner else ''}", embed=idea_embed)
                except disnake.HTTPException:
                    logger.exception("Не удалось проставить теги или уведомить в треде %s (/done)", thread.id)
                    raise

            case Channels.requests:
                tag_done = forum.get_tag_by_name('Исполнено')
                tag_rejected = forum.get_tag_by_name("Отказано")
                if tag_done is None:
                    await inter.send("Не нашёл тег «Исполнено» — возможно, его переименовали. Сообщи админам!", ephemeral=True)
                    return
                try:
                    if tag_rejected is not None and tag_rejected in thread.applied_tags:
                        await thread.remove_tags(tag_rejected)
                    await thread.add_tags(tag_done)
                    request_embed = create_embed(title="💫 Запрос выполнен!", color=disnake.Colour.yellow)
                    if comment:
                        request_embed.add_field(name="Комментарий", value=comment)
                    await thread.send(f"{owner.mention if owner else ''}", embed=request_embed)
                except disnake.HTTPException:
                    logger.exception("Не удалось проставить теги или уведомить в треде %s (/done)", thread.id)
                    raise

            case Channels.bugs:
                await inter.response.defer()
                await self._finishTicket(inter, thread, comment)

            case Channels.support:
                if not comment:
                    await inter.send("Для админских тикетов обязательно нужен комментарий с итогом!", ephemeral=True)
                    return
                await inter.response.defer()
                await self._finishTicket(inter, thread, comment)

            case _:
                await inter.send("Команду можно прописывать только в тредах багов, идей, тикетов или запросов!", ephemeral=True)

    @commands.slash_command(name='decline', description='Отклонить баг, идею, тикет')
    @commands.has_any_role(Roles.admin, Roles.st_admin)
    async def declineCommand(self, inter: disnake.ApplicationCommandInteraction, reason: str = None):
        thread = inter.channel
        if not isinstance(thread, disnake.Thread):
            await inter.send("Команду можно прописывать только в тредах!", ephemeral=True)
            return
        forum = thread.parent
        if forum is None:
            await inter.send("Не могу определить родительский канал треда — попробуй позже.", ephemeral=True)
            return
        owner = thread.owner

        match forum.id:
            case Channels.ideas:
                tag_added = forum.get_tag_by_name('Добавлено')
                tag_rejected = forum.get_tag_by_name("Отклонено")
                if tag_rejected is None:
                    await inter.send("Не нашёл тег «Отклонено» — возможно, его переименовали. Сообщи админам!", ephemeral=True)
                    return
                try:
                    if tag_added is not None and tag_added in thread.applied_tags:
                        await thread.remove_tags(tag_added)
                    await thread.add_tags(tag_rejected)
                    idea_embed = create_embed(
                        title="Идея отклонена...",
                        description="Большое спасибо за предложение, но, к сожалению, идея была отклонена...",
                        color=disnake.Colour.yellow
                    )
                    idea_embed.add_field(name="Причина:", value=reason or "Не указали...")
                    await thread.send(f"{owner.mention if owner else ''}", embed=idea_embed)
                except disnake.HTTPException:
                    logger.exception("Не удалось проставить теги или уведомить в треде %s (/decline)", thread.id)
                    raise

            case Channels.requests:
                tag_done = forum.get_tag_by_name('Исполнено')
                tag_rejected = forum.get_tag_by_name("Отказано")
                if tag_rejected is None:
                    await inter.send("Не нашёл тег «Отказано» — возможно, его переименовали. Сообщи админам!", ephemeral=True)
                    return
                try:
                    if tag_done is not None and tag_done in thread.applied_tags:
                        await thread.remove_tags(tag_done)
                    await thread.add_tags(tag_rejected)
                    request_embed = create_embed(title="😔 Запрос отклонён", color=disnake.Colour.yellow)
                    request_embed.add_field(name="Причина", value=reason or "Не указали...")
                    await thread.send(f"{owner.mention if owner else ''}", embed=request_embed)
                except disnake.HTTPException:
                    logger.exception("Не удалось проставить теги или уведомить в треде %s (/decline)", thread.id)
                    raise

            case Channels.bugs:
                await inter.response.defer()
                await self._rejectTicket(inter, thread, reason)

            case Channels.support:
                await inter.response.defer()
                await self._rejectTicket(inter, thread, reason)

            case _:
                await inter.send("Команду можно прописывать только в тредах багов, идей, тикетов или запросов!", ephemeral=True)

    @commands.Cog.listener("on_button_click")
    async def ticketButtons(self, inter: disnake.MessageInteraction):
        custom_id = inter.component.custom_id or ""
        if custom_id not in ("TICKET_DONE", "TICKET_DECLINE"):
            return
        thread = inter.channel
        if not isinstance(thread, disnake.Thread) or thread.parent_id not in (Channels.bugs, Channels.support):
            await inter.response.send_message("Эти кнопки работают только в тредах багов и админ-тикетов.", ephemeral=True)
            return

        # Право: admin / st_admin (по образцу honeypot — ручная проверка ролей)
        allowed = {Roles.admin, Roles.st_admin}
        if not any(r.id in allowed for r in getattr(inter.author, "roles", [])):
            await inter.response.send_message("Недостаточно прав для этого действия.", ephemeral=True)
            return

        if custom_id == "TICKET_DONE":
            # для админ-тикетов (support) комментарий обязателен, для багов — нет
            required = thread.parent_id == Channels.support
            await inter.response.send_modal(self.TicketDoneModal(self, required=required))
        else:
            await inter.response.send_modal(self.TicketDeclineModal(self))

    class TicketDoneModal(disnake.ui.Modal):
        def __init__(self, cog: "TicketEngine", *, required: bool):
            self.cog = cog
            components = [
                disnake.ui.TextInput(
                    label="Комментарий/итог",
                    placeholder="Что сделали / итог по тикету",
                    custom_id="comment",
                    style=disnake.TextInputStyle.paragraph,
                    max_length=1000,
                    required=required,
                )
            ]
            super().__init__(title="Завершение тикета", components=components)

        async def callback(self, inter: disnake.ModalInteraction):
            await inter.response.defer()
            comment = inter.text_values.get("comment") or None
            await self.cog._finishTicket(inter, inter.channel, comment)

    class TicketDeclineModal(disnake.ui.Modal):
        def __init__(self, cog: "TicketEngine"):
            self.cog = cog
            components = [
                disnake.ui.TextInput(
                    label="Причина",
                    placeholder="Причина отклонения (необязательно)",
                    custom_id="reason",
                    style=disnake.TextInputStyle.paragraph,
                    max_length=1000,
                    required=False,
                )
            ]
            super().__init__(title="Отклонение тикета", components=components)

        async def callback(self, inter: disnake.ModalInteraction):
            await inter.response.defer()
            reason = inter.text_values.get("reason") or None
            await self.cog._rejectTicket(inter, inter.channel, reason)


def setup(bot: commands.Bot):
    bot.add_cog(TicketEngine(bot))
