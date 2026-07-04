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

    def cog_unload(self):
        self.staleTicketReminder.cancel()

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
                pass
            except Exception:
                logger.exception("Ошибка AI-выжимки тикета %s", thread.id)

        buf = io.BytesIO(transcript.encode("utf-8"))
        transcript_file = disnake.File(buf, filename=f"ticket-{thread.id}.txt")
        note_line = f"\n**Комментарий/причина:** {note}" if note else ""
        container = disnake.ui.Container(
            disnake.ui.TextDisplay(f"## 📁 Тикет закрыт: {thread.name}"),
            disnake.ui.Separator(),
            disnake.ui.TextDisplay(f"**Тип:** {ticket_type}\n**Закрыл:** {closer.mention}{note_line}"),
            disnake.ui.Separator(),
            disnake.ui.TextDisplay(f"### Выжимка\n{summary}"),
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

            case Channels.requests:
                tag_done = forum.get_tag_by_name('Исполнено')
                tag_rejected = forum.get_tag_by_name("Отказано")
                if tag_done is None:
                    await inter.send("Не нашёл тег «Исполнено» — возможно, его переименовали. Сообщи админам!", ephemeral=True)
                    return
                if tag_rejected is not None and tag_rejected in thread.applied_tags:
                    await thread.remove_tags(tag_rejected)
                await thread.add_tags(tag_done)
                request_embed = create_embed(title="💫 Запрос выполнен!", color=disnake.Colour.yellow)
                if comment:
                    request_embed.add_field(name="Комментарий", value=comment)
                await thread.send(f"{owner.mention if owner else ''}", embed=request_embed)

            case Channels.bugs:
                await inter.response.defer()
                if not await self._archiveTicket(thread, ticket_type="Баг-репорт", closer=inter.author, note=comment):
                    await inter.edit_original_response("⚠️ Не удалось сохранить лог тикета — тред НЕ удалён. Проверь канал логов и повтори.")
                    return
                description = "Огромное спасибо за репорт бага! Ты помогаешь делать сервер лучше 💖"
                if comment:
                    description += f"\n**Комментарий:**\n{comment}"
                bug_fixed_embed = create_container(
                    title="💫 Сообщённый тобой баг пофикшен!",
                    description=description
                )
                user_id_flag = await flags.getFlag(thread, "created_by")
                if user_id_flag:
                    member = inter.guild.get_member(int(user_id_flag.value))
                    if member:
                        try:
                            await member.send(components=bug_fixed_embed)
                        except disnake.HTTPException:
                            pass
                await flags.removeFlag(thread, "created_by")
                await remove_bug_from_index(thread.id)
                await thread.delete(reason=f"Баг закрыт {inter.author.id}")

            case Channels.support:
                if not comment:
                    await inter.send("Для админских тикетов обязательно нужен комментарий с итогом!", ephemeral=True)
                    return
                await inter.response.defer()
                if not await self._archiveTicket(thread, ticket_type="Админ-тикет", closer=inter.author, note=comment):
                    await inter.edit_original_response("⚠️ Не удалось сохранить лог тикета — тред НЕ удалён. Проверь канал логов и повтори.")
                    return
                ticket_closed_embed = disnake.ui.Container(
                    disnake.ui.TextDisplay("## ⭐️ Твой админский тикет закрыт!"),
                    disnake.ui.Separator(),
                    disnake.ui.TextDisplay(f"### Решение:\n{comment}\n-# Закрыл: <@{inter.author.id}>")
                )
                user_id_flag = await flags.getFlag(thread, "created_by")
                if user_id_flag:
                    member = inter.guild.get_member(int(user_id_flag.value))
                    if member:
                        try:
                            await member.send(components=ticket_closed_embed)
                        except disnake.HTTPException:
                            pass
                await flags.removeFlag(thread, "created_by")
                await thread.delete(reason=f"Тикет закрыт {inter.author.id}")

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

            case Channels.requests:
                tag_done = forum.get_tag_by_name('Исполнено')
                tag_rejected = forum.get_tag_by_name("Отказано")
                if tag_rejected is None:
                    await inter.send("Не нашёл тег «Отказано» — возможно, его переименовали. Сообщи админам!", ephemeral=True)
                    return
                if tag_done is not None and tag_done in thread.applied_tags:
                    await thread.remove_tags(tag_done)
                await thread.add_tags(tag_rejected)
                request_embed = create_embed(title="😔 Запрос отклонён", color=disnake.Colour.yellow)
                request_embed.add_field(name="Причина", value=reason or "Не указали...")
                await thread.send(f"{owner.mention if owner else ''}", embed=request_embed)

            case Channels.bugs:
                await inter.response.defer()
                if not await self._archiveTicket(thread, ticket_type="Баг-репорт (отклонён)", closer=inter.author, note=reason):
                    await inter.edit_original_response("⚠️ Не удалось сохранить лог тикета — тред НЕ удалён. Проверь канал логов и повтори.")
                    return
                bug_declined_embed = create_container(
                    title="😔 Сообщённый тобой баг отклонён...",
                    description=f"Огромное спасибо за репорт бага, но он был отклонён...\n**Причина:** {reason or 'Не указали...'}"
                )
                user_id_flag = await flags.getFlag(thread, "created_by")
                if user_id_flag:
                    member = inter.guild.get_member(int(user_id_flag.value))
                    if member:
                        try:
                            await member.send(components=bug_declined_embed)
                        except disnake.HTTPException:
                            pass
                await flags.removeFlag(thread, "created_by")
                await remove_bug_from_index(thread.id)
                await thread.delete(reason=f"Баг отклонён {inter.author.id}")

            case _:
                await inter.send("Команду можно прописывать только в тредах багов, идей, тикетов или запросов!", ephemeral=True)


def setup(bot: commands.Bot):
    bot.add_cog(TicketEngine(bot))
