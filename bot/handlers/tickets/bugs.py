import asyncio
import json
import logging
import pathlib
from datetime import datetime, timezone

import aiosqlite
import disnake
from disnake import TextInputStyle, TextInput, StringSelectMenu, SelectOption
from disnake.ext import commands
from bot.storage import Buttons, Channels, ColorStorage, FAQStorage, Roles, Users
from bot.utils import create_container, create_embed
from bot.flag_system.flag_system import flags

try:  # W3 создаёт этот модуль; дедуп — необязательная фича
    from bot.ai.embeddings import embed, cosine
except Exception:  # pragma: no cover
    embed = None
    cosine = None

logger = logging.getLogger("robocat.bugs")

# Индекс баг-репортов для дедупликации: {thread_id: {"text", "url", "vector"}}
_BUG_INDEX_PATH = pathlib.Path(__file__).parent.parent.parent.parent / "data" / "bug_index.json"
_bug_index_lock = asyncio.Lock()


# ponytail: синхронный json I/O (миллисекунды на десятки багов); asyncio.to_thread, если индекс разрастётся
def _load_bug_index() -> dict:
    try:
        with open(_BUG_INDEX_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        logger.warning("Индекс багов %s повреждён — начинаю с пустого", _BUG_INDEX_PATH)
        return {}


def _save_bug_index(data: dict) -> None:
    try:
        with open(_BUG_INDEX_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except OSError:
        logger.exception("Не удалось сохранить индекс багов в %s", _BUG_INDEX_PATH)
        raise


async def remove_bug_from_index(thread_id: int) -> None:
    """Удаляет баг из индекса дедупликации (вызывается при закрытии треда)."""
    async with _bug_index_lock:
        index = _load_bug_index()
        if str(thread_id) in index:
            index.pop(str(thread_id))
            _save_bug_index(index)


# ponytail: один лок на всех — репорты редки, контенции нет; per-user при необходимости
_bug_rate_lock = asyncio.Lock()


async def bug_rate_limit_ok(inter: disnake.MessageInteraction) -> bool:
    """Анти-спам баг-репортов. True → можно открывать модалку; False → лимит,
    отказ уже отправлен. Общая точка для кнопки и дропдауна выбора тикета —
    иначе дропдаун открывал модалку в обход кулдауна (обход рейт-лимита).
    Лок сериализует check-then-act: одновременные клики кнопки и дропдауна
    иначе читали одинаковый счётчик и открывали пачку модалок."""
    async with _bug_rate_lock:
        if await flags.hasFlag(inter.author, "create_bug_cooldown"):
            await inter.send("Ты отправлял(а) слишком много багов за короткое время! Отдохни и сообщи о них попозже =)", ephemeral=True)
            return False
        created_bugs = await flags.getFlag(inter.author, "created_bugs")
        if created_bugs and int(created_bugs.value) > 3:
            await inter.send("Воу-воу, котик! Мы очень ценим твою помощь, но твои действия смахивают на спам тикетами... Я вынужден дать тебе КД, попробуй попозже.", ephemeral=True)
            await flags.setFlag(inter.author, "create_bug_cooldown", "true", "15мин")
            return False
        if created_bugs:
            # атомарный инкремент в SQL, а не read-modify-write в питоне
            await flags.incrementFlag(inter.author, "created_bugs", 1, expires_at="15мин")
        else:
            await flags.setFlag(inter.author, "created_bugs", 1, expires_at="15мин")
        return True


class BugHandler(commands.Cog):
    """
    Хендлер репорта багов
    ---
    Создаёт треды на каждый баг и т.д.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener("on_button_click")
    async def bugThreadCreate(self, inter: disnake.MessageInteraction):
        if inter.component.custom_id == Buttons.BUG_REPORT.id:
            if await bug_rate_limit_ok(inter):
                await inter.response.send_modal(modal=self.BugModal())

    class BugModal(disnake.ui.Modal):
        def __init__(self):
            # The details of the modal, and its components
            components = [
                disnake.ui.TextInput(
                    label="Твой ник",
                    placeholder="Введи свой ник",
                    custom_id="Никнейм",
                    style=TextInputStyle.short,
                    max_length=50,
                ),
                disnake.ui.TextInput(
                    label="В чём баг?",
                    placeholder="Опиши подробно в чём баг, как воспроизвести, когда обнаружил(а) и т.д.",
                    custom_id="Описание бага",
                    style=TextInputStyle.long
                ),
                disnake.ui.Label(text="(Опционально) Критичность бага",
                component=
                    disnake.ui.RadioGroup(
                        custom_id="Приоритет",
                        options=[
                            disnake.GroupOption(label="😸 Минимальный",
                                                description="Баг не влияющий на игру"),
                            disnake.GroupOption(label="😿 Средний",
                                                description="Баг влияющий на игру"),
                            disnake.GroupOption(label="🙀 Критический",
                                                description="Дюп, безопасность, критеский баг"),
                            disnake.GroupOption(label="👀 Это баг?",
                                                description="Если не уверен(а) что это вообще баг.")
                        ],
                        required=False)
                )
            ]
            super().__init__(title='Баг-репорт', components=components)
    
        # The callback received when the user input is completed.
        async def callback(self, inter: disnake.ModalInteraction):
            await inter.response.defer(ephemeral=True)
            channel = inter.guild.get_channel(Channels.bugs)
            if channel is None:
                # без канала create_thread упал бы AttributeError мимо except ниже,
                # и юзер остался бы с вечным «думает» и потерянным текстом репорта
                logger.error("Канал багов %s не найден в кэше — репорт не создан", Channels.bugs)
                await inter.edit_original_response("Не получилось создать баг-репорт (канал недоступен). Сообщи админам!")
                return
            modal = inter.resolved_values
            nick = modal["Никнейм"]
            bug_description = modal["Описание бага"]
            priority = modal["Приоритет"]
            # Первые 5 слов, но не длиннее лимита имени треда Discord (100): URL/лог-строка
            # в начале описания легко его превышает и роняет create_thread с 400.
            bug_thread_name = " ".join(bug_description.split(" ")[:5])[:100] or "Баг-репорт"
            try:
                bug_thread = await channel.create_thread(
                    name=bug_thread_name,
                    type=disnake.ChannelType.private_thread,
                    auto_archive_duration=10080,
                    reason=f"Новый баг-репорт от {nick}"
                )
            except disnake.HTTPException:
                logger.exception("Не удалось создать тред баг-репорта в канале %s", Channels.bugs)
                raise
            bug_container = disnake.ui.Container(
                disnake.ui.TextDisplay(
                    content=f"# Баг! \nОт {nick} ({inter.author.mention})"
                ),
                disnake.ui.TextDisplay(
                    content="## Описание бага"
                ),
                disnake.ui.TextDisplay(
                    content=bug_description
                ),
                disnake.ui.Separator(),
                disnake.ui.TextDisplay(
                    content=f"Критичность бага:  {priority or "Не указана"}"
                ),
                disnake.ui.TextDisplay(
                    content=f"-# ||<@&{Roles.st_admin}> <@&{Roles.admin}> <@&{Roles.moderator}> {inter.author.mention}||"
                ),
                disnake.ui.ActionRow(
                    disnake.ui.Button(
                        style=disnake.ButtonStyle.green,
                        label="✅ Завершить",
                        custom_id="TICKET_DONE",
                    ),
                    disnake.ui.Button(
                        style=disnake.ButtonStyle.danger,
                        label="⛔ Отклонить",
                        custom_id="TICKET_DECLINE",
                    ),
                ),
                accent_colour=disnake.Color.from_hex(ColorStorage.main),
            )
            await bug_thread.send(components=[bug_container])
            await inter.edit_original_response(f"Баг-репорт создан! Перейди в него: <#{bug_thread.id}>")
            try:
                await inter.author.send(
                    components=create_container(
                        f"## Спасибо за репорт бага ''{bug_thread_name}''!",
                        f"Сохраню канал баг-репорта здесь: https://discord.com/channels/{inter.guild_id}/{bug_thread.id}",
                        "Треды пропадают через некоторое время, но эта ссылка позволяет тебе в любой момент вернуться!"
                    )
                )
            except disnake.HTTPException:
                # Закрытые ЛС — штатная ситуация; НЕ роняем callback, иначе теряются
                # created_by (архив «Автор: неизвестен») и вся дедупликация ниже.
                logger.warning("Не удалось отправить ЛС автору баг-репорта %s (закрытые ЛС?)", inter.author.id)
            await flags.setFlag(bug_thread,"created_by",inter.author.id)

            # Дедупликация: ищем похожие баги по эмбеддингу описания
            if embed is not None and cosine is not None:
                try:
                    vector = await embed(bug_description)
                except Exception:
                    vector = None
                    logger.exception("Не удалось получить эмбеддинг баг-репорта")
                if vector is not None:
                    async with _bug_index_lock:
                        index = _load_bug_index()
                        similar = []
                        for entry in index.values():
                            vec = entry.get("vector")
                            if not vec:
                                continue
                            try:
                                if cosine(vector, vec) >= 0.72:
                                    similar.append(entry)
                            except Exception:
                                logger.exception("Ошибка сравнения эмбеддингов с багом %s", entry.get("url"))
                                continue
                        if similar:
                            links = "\n".join(f"- {e['url']}" for e in similar[:3])
                            try:
                                await bug_thread.send(components=create_container(
                                    "🔍 Возможно, этот баг уже репортили:", links
                                ))
                            except disnake.HTTPException:
                                logger.exception("Не удалось отправить подсказку о дубликатах в тред %s", bug_thread.id)
                                raise
                        index[str(bug_thread.id)] = {
                            "text": bug_description,
                            "url": f"https://discord.com/channels/{inter.guild_id}/{bug_thread.id}",
                            "vector": vector,
                        }
                        _save_bug_index(index)

            if 'getsockopt' in bug_description or 'гетсокопт' in bug_description:
                try:
                    await bug_thread.send(components=create_container(
                        "## Авто-ответ по частой проблеме: `getsockopt`",
                        FAQStorage.getsockopt
                    ))
                except disnake.HTTPException:
                    logger.exception("Не удалось отправить авто-ответ getsockopt в тред %s", bug_thread.id)
                    raise


    @commands.slash_command(name='clearbugs', description='Удаляет все треды с багами')
    async def doneCommand(self, inter: disnake.ApplicationCommandInteraction):
        if inter.channel_id != Channels.temp_bugs or inter.author.id != 531208170098655233:
            await inter.send("Эта команда не для тебя (или не для этого канала).", ephemeral=True)
            return
        # defer: удаление десятков тредов не укладывается в 3с-дедлайн интеракции
        await inter.response.defer(ephemeral=True)
        threads = inter.channel.threads
        deleted = 0
        for trd in threads:
            await remove_bug_from_index(trd.id)
            try:
                await trd.delete()
                deleted += 1
            except disnake.HTTPException:
                # один упавший тред не должен обрывать чистку остальных
                logger.exception("Не удалось удалить тред бага %s при чистке", trd.id)
        await inter.edit_original_response(f"Удалено {deleted} из {len(threads)} тредов!")

    @commands.slash_command(name='rebuild_bug_index', description='Перестроить индекс дедупликации багов')
    @commands.has_any_role(Roles.admin, Roles.st_admin)
    async def rebuildBugIndex(self, inter: disnake.ApplicationCommandInteraction):
        await inter.response.defer(ephemeral=True)
        if embed is None:
            await inter.edit_original_response("Модуль эмбеддингов недоступен — индекс не перестроить.")
            return
        channel = inter.guild.get_channel(Channels.bugs)
        if channel is None:
            await inter.edit_original_response("Канал багов не найден.")
            return
        # Снапшот времени старта: баги, созданные ВО ВРЕМЯ скана, при записи
        # индекса сохраняем из текущего индекса, а не теряем перезаписью
        scan_start_snowflake = disnake.utils.time_snowflake(datetime.now(timezone.utc))
        index = {}
        count = 0
        for thread in channel.threads:
            first_bot_msg = None
            try:
                async for msg in thread.history(limit=None, oldest_first=True):
                    if msg.author and self.bot.user and msg.author.id == self.bot.user.id:
                        first_bot_msg = msg
                        break
            except disnake.HTTPException:
                logger.exception("Не удалось прочитать историю треда %s при перестройке индекса", thread.id)
                raise
            if first_bot_msg is None:
                continue
            text = first_bot_msg.content or _extract_component_text(first_bot_msg.components)
            # Убираем заголовки/пинги/критичность, чтобы вектор совпадал с «живым» путём (чистое описание)
            text = "\n".join(
                line for line in text.splitlines()
                if line.strip()
                and not line.lstrip().startswith(("#", "-#"))
                and not line.lstrip().startswith("Критичность")
            )
            if not text.strip():
                continue
            try:
                vector = await embed(text)
            except Exception:
                logger.exception("Не удалось получить эмбеддинг при перестройке индекса (тред %s)", thread.id)
                continue
            index[str(thread.id)] = {
                "text": text,
                "url": f"https://discord.com/channels/{inter.guild_id}/{thread.id}",
                "vector": vector,
            }
            count += 1
        async with _bug_index_lock:
            current = _load_bug_index()
            for tid, entry in current.items():
                if tid not in index and int(tid) >= scan_start_snowflake:
                    index[tid] = entry  # свежий баг, добавленный параллельно скану
            _save_bug_index(index)
        logger.info("Индекс багов перестроен: %d записей", count)
        await inter.edit_original_response(f"Индекс перестроен: {count} багов.")


def _extract_component_text(components) -> str:
    """Рекурсивно собирает текст из полученных v2-компонентов (TextDisplay внутри Container)."""
    parts = []
    for comp in components or []:
        content = getattr(comp, "content", None)
        if content:
            parts.append(content)
        children = getattr(comp, "children", None)
        if children:
            nested = _extract_component_text(children)
            if nested:
                parts.append(nested)
    return "\n".join(parts)


def setup(bot: commands.Bot):
    bot.add_cog(BugHandler(bot))