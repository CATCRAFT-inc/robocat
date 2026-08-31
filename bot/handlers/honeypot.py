import io
import logging
from collections import defaultdict, deque
from datetime import timedelta
from time import monotonic

import disnake
from disnake.ext import commands

from bot import metrics as telemetry
from bot.discord_config import Channels, Roles

logger = logging.getLogger("robocat.honeypot")

_MENTION_RAID_WINDOW = 20.0
_MENTION_RAID_THRESHOLD = 3
_HONEYPOT_MUTE = timedelta(days=28)


def _staff_roles() -> list[int]:
    return [Roles.moderator, Roles.admin, Roles.st_admin]


def _staff_ping() -> str:
    return " ".join(f"<@&{role_id}>" for role_id in _staff_roles())


def _message_evidence(message: disnake.Message) -> str:
    text = (message.content or "").strip()
    attachments = " ".join(attachment.url for attachment in message.attachments)
    evidence = " ".join(part for part in (text, attachments) if part)
    # Полный оригинал уходит отдельным .txt. Превью режем ПОСЛЕ экранирования,
    # чтобы три TextDisplay вместе гарантированно помещались в общий лимит V2.
    escaped = disnake.utils.escape_markdown(evidence or "*(пусто/вложение)*")
    return escaped[:700]


def _raw_message_evidence(message: disnake.Message) -> str:
    attachments = " ".join(attachment.url for attachment in message.attachments)
    return " ".join(
        part for part in ((message.content or "").strip(), attachments) if part
    ) or "(пусто/вложение)"


class Honeypot(commands.Cog):
    """Ловушка для спам-ботов: любое сообщение в канале-приманке = мут + кнопки модерации."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._mention_hits: dict[int, deque[tuple[float, disnake.Message]]] = defaultdict(deque)

    @commands.Cog.listener("on_message")
    async def honeypotTrap(self, message: disnake.Message):
        if message.channel.id != Channels.honeypot:
            return
        if message.author.bot:
            return

        telemetry.record_later("security", "honeypot")

        author = message.author
        text = (message.content or "").strip()

        # Удаляем сообщение-приманку
        try:
            await message.delete()
        except disnake.NotFound:
            pass
        except (disnake.Forbidden, disnake.HTTPException):
            logger.warning("Не удалось удалить сообщение-приманку %s от %s", message.id, author.id)

        # Мутим на максимум (28 дней)
        try:
            await author.timeout(
                duration=_HONEYPOT_MUTE,
                reason="Ловушка: сообщение в канале-приманке",
            )
            logger.info("Ловушка: %s (%s) замьючен на 28 дней", author, author.id)
        except disnake.Forbidden:
            logger.warning("Не удалось замутить %s (%s) — недостаточно прав", author, author.id)
        except disnake.HTTPException:
            logger.exception("Ошибка при мьюте %s", author.id)

        # Лог-пост с кнопками модерации
        log_channel = self.bot.get_channel(Channels.discord_logs)
        if log_channel is None:
            logger.error("Канал логов %s не найден — лог-пост ловушки для %s не отправлен", Channels.discord_logs, author.id)
            return

        container = disnake.ui.Container(
            disnake.ui.TextDisplay(_staff_ping()),
            disnake.ui.TextDisplay("## 🍯 Сработала ловушка для ботов"),
            disnake.ui.Separator(),
            disnake.ui.TextDisplay(
                f"**Кто:** {author.mention} (`{author.id}`)\n"
                f"**Сообщение:** {text[:1000] if text else '*(пусто/вложение)*'}"
            ),
            disnake.ui.TextDisplay("-# Замучен на 28 дней. Забанить или помиловать?"),
            accent_colour=disnake.Color.from_hex("#4f2dbe"),
        )
        buttons = disnake.ui.ActionRow(
            disnake.ui.Button(
                style=disnake.ButtonStyle.danger,
                label="🔨 Забанить",
                custom_id=f"HONEYPOT_BAN:{author.id}",
            ),
            disnake.ui.Button(
                style=disnake.ButtonStyle.green,
                label="😇 Помиловать",
                custom_id=f"HONEYPOT_PARDON:{author.id}",
            ),
        )
        try:
            # Пингуем только роль модераторов — @everyone/@here из текста спамера не сработают
            await log_channel.send(
                components=[container, buttons],
                allowed_mentions=disnake.AllowedMentions(
                    everyone=False,
                    users=False,
                    roles=[disnake.Object(id=role_id) for role_id in _staff_roles()],
                ),
            )
        except disnake.HTTPException:
            logger.exception("Не удалось отправить лог-пост ловушки")

    @commands.Cog.listener("on_message")
    async def mentionRaidTrap(self, message: disnake.Message):
        """Три сообщения с пингами за 20 секунд → удалить, замутить, сохранить текст."""
        if message.guild is None or message.channel.id == Channels.honeypot:
            return
        if message.author.bot:
            return

        if not (message.mention_everyone or message.mentions or message.role_mentions):
            return

        now = monotonic()
        hits = self._mention_hits[message.author.id]
        cutoff = now - _MENTION_RAID_WINDOW
        while hits and hits[0][0] < cutoff:
            hits.popleft()
        hits.append((now, message))
        if len(hits) < _MENTION_RAID_THRESHOLD:
            return

        evidence_messages = [stored for _seen_at, stored in hits]
        raw_evidence = "\n".join(
            f"{index}. channel={stored.channel.id}: {_raw_message_evidence(stored)}"
            for index, stored in enumerate(evidence_messages, start=1)
        )
        evidence_filename = f"mention-raid-{message.id}.txt"
        # Сбрасываем ДО первого await: параллельно приехавшее четвёртое сообщение
        # начинает новое окно, а не запускает второй мут и второй лог той же тройки.
        hits.clear()
        telemetry.record_later("security", "mention_raid")

        for stored in evidence_messages:
            try:
                await stored.delete()
            except disnake.NotFound:
                pass
            except (disnake.Forbidden, disnake.HTTPException):
                logger.warning(
                    "Анти-рейд: не удалось удалить сообщение %s от %s",
                    stored.id,
                    message.author.id,
                )

        mute_ok = True
        try:
            await message.author.timeout(
                duration=_HONEYPOT_MUTE,
                reason="Анти-рейд: три сообщения с упоминаниями за 20 секунд",
            )
        except disnake.Forbidden:
            mute_ok = False
            logger.warning(
                "Анти-рейд: не удалось замутить %s — недостаточно прав",
                message.author.id,
            )
        except disnake.HTTPException:
            mute_ok = False
            logger.exception("Анти-рейд: ошибка при мьюте %s", message.author.id)

        log_channel = self.bot.get_channel(Channels.discord_logs)
        if log_channel is None:
            logger.error(
                "Канал логов %s не найден — Discord-уведомление анти-рейда для %s "
                "не отправлено. Доказательства:\n%s",
                Channels.discord_logs,
                message.author.id,
                raw_evidence,
            )
            return

        outcome = "Замучен на 28 дней." if mute_ok else "⚠️ Автоматический мут не удался."
        container = disnake.ui.Container(
            disnake.ui.TextDisplay(_staff_ping()),
            disnake.ui.TextDisplay("## 🚨 Рейд упоминаниями"),
            disnake.ui.Separator(),
            disnake.ui.TextDisplay(
                f"**Кто:** {message.author.mention} (`{message.author.id}`)\n"
                f"**Окно:** {_MENTION_RAID_THRESHOLD} сообщения за "
                f"{int(_MENTION_RAID_WINDOW)} секунд\n"
                "**Полные тексты — в приложенном файле.**"
            ),
            *(
                disnake.ui.TextDisplay(
                    f"**{index}.** {stored.channel.mention}\n{_message_evidence(stored)}"
                )
                for index, stored in enumerate(evidence_messages, start=1)
            ),
            disnake.ui.File(file=f"attachment://{evidence_filename}"),
            disnake.ui.TextDisplay(f"-# {outcome} Забанить или помиловать?"),
            accent_colour=disnake.Color.from_hex("#b32020"),
        )
        buttons = disnake.ui.ActionRow(
            disnake.ui.Button(
                style=disnake.ButtonStyle.danger,
                label="🔨 Забанить",
                custom_id=f"HONEYPOT_BAN:{message.author.id}",
            ),
            disnake.ui.Button(
                style=disnake.ButtonStyle.green,
                label="😇 Помиловать",
                custom_id=f"HONEYPOT_PARDON:{message.author.id}",
            ),
        )
        try:
            await log_channel.send(
                components=[container, buttons],
                file=disnake.File(
                    io.BytesIO(raw_evidence.encode("utf-8")),
                    filename=evidence_filename,
                ),
                allowed_mentions=disnake.AllowedMentions(
                    everyone=False,
                    users=False,
                    roles=[disnake.Object(id=role_id) for role_id in _staff_roles()],
                ),
            )
        except disnake.HTTPException:
            logger.error(
                "Не удалось отправить Discord-лог анти-рейда. Доказательства:\n%s",
                raw_evidence,
                exc_info=True,
            )

    @commands.Cog.listener("on_button_click")
    async def honeypotButtons(self, inter: disnake.MessageInteraction):
        custom_id = inter.component.custom_id or ""
        if not (custom_id.startswith("HONEYPOT_BAN:") or custom_id.startswith("HONEYPOT_PARDON:")):
            return

        # Право: admin / st_admin / moderator
        allowed = {Roles.admin, Roles.st_admin, Roles.moderator}
        if not any(r.id in allowed for r in getattr(inter.author, "roles", [])):
            await inter.response.send_message("Недостаточно прав для этого действия.", ephemeral=True)
            return

        action, _, raw_id = custom_id.partition(":")
        try:
            user_id = int(raw_id)
        except ValueError:
            await inter.response.send_message("Не удалось разобрать ID пользователя.", ephemeral=True)
            return

        await inter.response.defer()  # ACK сразу: бан/снятие мута могут не уложиться в 3 секунды

        if action == "HONEYPOT_BAN":
            outcome = await self._ban(inter, user_id)
        else:
            outcome = await self._pardon(inter, user_id)

        # Перерисовываем лог-пост без кнопок, дописав итог
        result_container = disnake.ui.Container(
            disnake.ui.TextDisplay("## 🍯 Ловушка — решение принято"),
            disnake.ui.Separator(),
            disnake.ui.TextDisplay(f"<@{user_id}> (`{user_id}`)"),
            disnake.ui.TextDisplay(f"{outcome}\n-# Решил: {inter.author.mention}"),
            accent_colour=disnake.Color.from_hex("#4f2dbe"),
        )
        try:
            await inter.edit_original_response(components=[result_container])
        except disnake.HTTPException:
            logger.exception("Не удалось обновить лог-пост ловушки")

    async def _ban(self, inter: disnake.MessageInteraction, user_id: int) -> str:
        try:
            await inter.guild.ban(
                disnake.Object(id=user_id),
                reason=f"Ловушка: бан модератором {inter.author} ({inter.author.id})",
                clean_history_duration=timedelta(days=1),
            )
        except disnake.NotFound:
            return "⚠️ Пользователь не найден (возможно, уже забанен)."
        except disnake.Forbidden:
            return "⚠️ Недостаточно прав, чтобы забанить."
        except disnake.HTTPException:
            logger.exception("Ошибка бана %s", user_id)
            return "⚠️ Не удалось забанить (ошибка Discord)."
        logger.info("Ловушка: %s забанен модератором %s", user_id, inter.author.id)
        return "🔨 **Забанен.**"

    async def _pardon(self, inter: disnake.MessageInteraction, user_id: int) -> str:
        member = inter.guild.get_member(user_id)
        if member is None:
            # cache-miss ≠ «ушёл с сервера»: дозапрашиваем, иначе рапортуем
            # «Помилован», хотя мут на самом деле не снят
            try:
                member = await inter.guild.fetch_member(user_id)
            except disnake.NotFound:
                return "😇 Помилован (пользователя уже нет на сервере — мут снимать не с кого)."
            except disnake.HTTPException:
                logger.exception("Не удалось дозапросить участника %s при помиловании", user_id)
                return "⚠️ Не удалось проверить участника — попробуй ещё раз."
        try:
            await member.timeout(duration=None, reason=f"Ловушка: помилован {inter.author}")
        except disnake.Forbidden:
            return "⚠️ Недостаточно прав, чтобы снять мут."
        except disnake.HTTPException:
            logger.exception("Ошибка снятия мьюта %s", user_id)
            return "⚠️ Не удалось снять мут (ошибка Discord)."
        logger.info("Ловушка: с %s снят мут модератором %s", user_id, inter.author.id)
        return "😇 **Помилован**, мут снят."


def setup(bot: commands.Bot):
    bot.add_cog(Honeypot(bot))
