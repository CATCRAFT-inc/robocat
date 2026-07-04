import asyncio
import json
import logging
import pathlib

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
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_bug_index(data: dict) -> None:
    with open(_BUG_INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


async def remove_bug_from_index(thread_id: int) -> None:
    """Удаляет баг из индекса дедупликации (вызывается при закрытии треда)."""
    async with _bug_index_lock:
        index = _load_bug_index()
        if str(thread_id) in index:
            index.pop(str(thread_id))
            _save_bug_index(index)


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
            has_bug_cd = await flags.hasFlag(inter.author,"create_bug_cooldown")
            if has_bug_cd:
                await inter.send("Ты отправлял(а) слишком много багов за короткое время! Отдохни и сообщи о них попозже =)", ephemeral=True)
            else:
                created_bugs = await flags.getFlag(inter.author,"created_bugs")
                if created_bugs and int(created_bugs.value) > 3:
                    await inter.send("Воу-воу, котик! Мы очень ценим твою помощь, но твои действия смахивают на спам тикетами... Я вынужден дать тебе КД, попробуй попозже.")
                    await flags.setFlag(inter.author,"create_bug_cooldown", "true","15мин")
                    return
                elif created_bugs:
                    await flags.setFlag(inter.author, "created_bugs", int(created_bugs.value) + 1, expires_at="15мин")
                else:
                    await flags.setFlag(inter.author, "created_bugs", 1, expires_at="15мин")
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
            modal = inter.resolved_values
            nick = modal["Никнейм"]
            bug_description = modal["Описание бага"]
            priority = modal["Приоритет"]
            bug_thread_name = " ".join(bug_description.split(" ")[:5])
            bug_thread = await channel.create_thread(
                name=bug_thread_name,
                type=disnake.ChannelType.private_thread,
                auto_archive_duration=10080,
                reason=f"Новый баг-репорт от {nick}"
            )
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
            await inter.author.send(
                components=create_container( 
                    f"## Спасибо за репорт бага ''{bug_thread_name}''!",
                    f"Сохраню канал баг-репорта здесь: https://discord.com/channels/{inter.guild_id}/{inter.channel_id}",
                    "Треды пропадают через некоторое время, но эта ссылка позволяет тебе в любой момент вернуться!"
                )
            )
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
                                continue
                        if similar:
                            links = "\n".join(f"- {e['url']}" for e in similar[:3])
                            await bug_thread.send(components=create_container(
                                "🔍 Возможно, этот баг уже репортили:", links
                            ))
                        index[str(bug_thread.id)] = {
                            "text": bug_description,
                            "url": f"https://discord.com/channels/{inter.guild_id}/{bug_thread.id}",
                            "vector": vector,
                        }
                        _save_bug_index(index)

            if 'getsockopt' in bug_description or 'гетсокопт' in bug_description:
                await bug_thread.send(components=create_container(
                    "## Авто-ответ по частой проблеме: `getsockopt`",
                    FAQStorage.getsockopt
                ))


    @commands.slash_command(name='clearbugs', description='Удаляет все треды с багами')
    async def doneCommand(self, inter: disnake.ApplicationCommandInteraction):
        if inter.channel_id == Channels.temp_bugs and inter.author.id == 531208170098655233:
            threads = inter.channel.threads
            amount = len(threads)
            for trd in threads:
                await remove_bug_from_index(trd.id)
                await trd.delete()
            await inter.send(f"Удалено {amount} тредов!")

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
        index = {}
        count = 0
        for thread in channel.threads:
            first_bot_msg = None
            async for msg in thread.history(limit=None, oldest_first=True):
                if msg.author and self.bot.user and msg.author.id == self.bot.user.id:
                    first_bot_msg = msg
                    break
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
            _save_bug_index(index)
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