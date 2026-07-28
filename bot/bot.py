import re

import asyncio
import logging
import random
import sys
import time

import disnake
from disnake.ext import commands, tasks
from disnake import DMChannel

import os

from bot import storage
from bot.discord_config import Bots, Channels, Guilds, Users
from bot.flag_system.flag_system import flags
from bot.utils import create_embed
from bot.storage import Messages

os.environ["PYTHONIOENCODING"] = "UTF-8"

logger = logging.getLogger("robocat.bot")

intents = disnake.Intents.all()


def _event_guild_id(args) -> int | None:
    for arg in args:
        guild_id = getattr(arg, "guild_id", None)
        if guild_id is not None:
            return guild_id
        guild = getattr(arg, "guild", None)
        if guild is not None:
            return getattr(guild, "id", None)
    return None


class MainGuildBot(commands.Bot):
    """Do not dispatch guild events received outside the configured server."""

    def __init__(self, *args, main_guild_id: int, **kwargs):
        self.main_guild_id = main_guild_id
        super().__init__(*args, **kwargs)

    def dispatch(self, event_name, *args, **kwargs):
        guild_id = _event_guild_id(args)
        if guild_id is not None and guild_id != self.main_guild_id:
            return None
        return super().dispatch(event_name, *args, **kwargs)


bot = MainGuildBot(
    main_guild_id=Guilds.main,
    intents=intents,
    activity=disnake.Game(name='Кошкокрафт'),
    command_prefix='!',
    reload=True,
    command_sync_flags=commands.CommandSyncFlags.all(),
    strip_after_prefix=True,
    test_guilds=[Guilds.main],
    owner_id=Users.szarkan
)

_greeted = False  # «Я тут!» — только на первый ready: reconnect-ready дёргается постоянно
_lag_watchdog_task = None

# Порог: интеракциям Discord даётся 3с на ответ; лаг лупа >1.5с — уже половина бюджета.
_LAG_THRESHOLD = 1.5


async def _loop_lag_watchdog():
    """Ловит затыки event loop (инцидент 25-26.07.2026: кнопки по всему серверу
    опаздывали к 3с-дедлайну). sleep(5) проснулся заметно позже — луп был занят;
    loadavg в той же строке отличает блокировку в процессе от голода CPU хоста."""
    while True:
        started = time.monotonic()
        await asyncio.sleep(5)
        drift = time.monotonic() - started - 5
        if drift > _LAG_THRESHOLD:
            # на Windows (дев-запуск) getloadavg нет
            load1, load5, load15 = (
                os.getloadavg() if hasattr(os, "getloadavg") else (0.0, 0.0, 0.0)
            )
            logger.warning(
                "Event loop опоздал на %.2fс (loadavg %.2f %.2f %.2f)",
                drift, load1, load5, load15,
            )


@bot.event
async def on_ready():
    logger.info("Бот готов: залогинен как %s (id=%s)", bot.user, bot.user.id)
    global _greeted, _lag_watchdog_task
    if _lag_watchdog_task is None:
        _lag_watchdog_task = asyncio.create_task(_loop_lag_watchdog())
    if _greeted:
        return
    _greeted = True
    channel = bot.get_channel(Channels.secret)
    if channel:
        await channel.send('Я тут!')
    else:
        logger.warning("Секретный канал %s не найден — стартовое сообщение не отправлено", Channels.secret)


@bot.listen()
async def on_slash_command_error(inter: disnake.ApplicationCommandInteraction, error: commands.CommandError):
    """Глобальный обработчик слэш-команд: не-админ получает внятный ephemeral-отказ
    вместо «Приложение не отвечает», остальные ошибки — в bot.log."""
    if isinstance(error, commands.CheckFailure):
        try:
            await inter.response.send_message("Эта команда не для тебя :3", ephemeral=True)
        except disnake.HTTPException:
            pass  # интеракция протухла/уже отвечена — отказ и так случился
        return
    logger.error("Ошибка слэш-команды %s", inter.application_command.name, exc_info=error)

@bot.event
async def on_error(event, *args, **kwargs):
    """Пишет необработанные исключения из listeners в bot.log (дефолт disnake печатает только в stderr).

    К записи добавляется возраст интеракции и разбор кодов 40060/10062 — без них
    инцидент со вторым инстансом (25-28.07.2026) читался как «затык event loop»
    и лечился не в том слое."""
    logger.exception("Необработанная ошибка в событии %s%s", event, _interaction_hint(args))


def _interaction_hint(args) -> str:
    """Диагностический хвост к записи об ошибке: возраст интеракции и код отказа.

    Возраст меряется по снежинке (время создания интеракции у Discord) — он
    отделяет «мы отвечали слишком долго» от «ответ отверг Discord сразу»:
    - 10062 при возрасте <3с и 40060 в любом виде = интеракцию подтвердил
      кто-то другой, то есть боевым токеном залогинен ВТОРОЙ процесс;
    - 10062 при возрасте >3с = мы действительно не уложились в дедлайн.
    """
    inter = next((a for a in args if isinstance(a, disnake.Interaction)), None)
    if inter is None:
        return ""
    age = (disnake.utils.utcnow() - inter.created_at).total_seconds()
    tail = f" (интеракция {inter.id}, возраст {age:.2f}с)"
    error = sys.exc_info()[1]
    code = getattr(error, "code", None)
    if code == 40060 or (code == 10062 and age < 3):
        tail += (
            " — интеракцию подтвердил не мы: почти наверняка вторым инстансом"
            " запущен боевой токен, см. .claude/rules/local-run.md"
        )
    return tail

@bot.listen()
async def on_message(message: disnake.Message):
    """
    Функция на добавление реакций и создания треда под каждым постом в заданных каналах
    + пишет, если игрок ошибся при написании Робокотику, а не Робокотику SRV
    """
    if message.channel.id in Channels.news_reaction_channels:
        if message.author.bot or message.type not in (disnake.MessageType.default, disnake.MessageType.reply):
            return
        reactions = ['❤️', '👍', '👎']
        for reaction in reactions:
            await message.add_reaction(reaction)
            await asyncio.sleep(1)
        try:
            await message.create_thread(name="Обсуждение", reason="Тред на новую новость")
        except disnake.HTTPException:
            logger.warning("Не удалось создать тред под новостью %s в канале %s", message.id, message.channel.id)
    elif isinstance(message.channel, DMChannel):
        # Если сообщение — ровно 4 цифры
        if re.fullmatch(r'\d{4}', message.content):
            # Отправляем ответ
            await message.channel.send(
                f"Привет, я не тот **Робокотик**! Тебе нужен <@{Bots.robocat_srv}> =)"
            )

@bot.listen()
async def on_member_join(inter: disnake.Member):
    """
    Кидает случайное сообщение о входе юзера и выдаёт роли, если они были
    """
    if inter.guild.id == Guilds.main:
        channel = bot.get_channel(Channels.welcome)
        if channel is None:
            # cache-miss (частый после реконнекта) — пробуем дозапросить, иначе выходим,
            # а не падаем на channel.send(None), теряя welcome каждому входящему
            try:
                channel = await bot.fetch_channel(Channels.welcome)
            except (disnake.NotFound, disnake.Forbidden, disnake.HTTPException):
                logger.error("Канал приветствий %s недоступен — welcome для %s не отправлен", Channels.welcome, inter.id)
                return
        if await flags.getFlag(inter, "left"):
            await channel.send(random.choice(Messages.join_again).replace("%1", f"<@{inter.id}>"))
            await flags.removeFlag(inter,"left","Зашёл обратно")
        else:
            await channel.send(
                random.choice(Messages.join)
                .replace("%1", f"<@{inter.id}>")
                .replace("%2", str(Channels.about_server))
            )

@bot.listen()
async def on_raw_member_remove(payload: disnake.RawGuildMemberRemoveEvent):
    if payload.guild_id != Guilds.main:
        return
    if payload.user:
        await flags.setFlag(payload.user, "left", int(time.time()))
