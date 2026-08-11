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
from bot.utils import answer_broken_interaction, create_embed, decorate_news
from bot.storage import Messages

os.environ["PYTHONIOENCODING"] = "UTF-8"

logger = logging.getLogger("robocat.bot")

# 🛑 `InteractionTimedOut` НЕ наследник `HTTPException` (он `ClientException`),
# а именно им disnake заворачивает 10062 на протухшей интеракции. Один
# `except disnake.HTTPException` пропускает его наружу, и падает уже сам
# обработчик ошибок — в логе виноватым выглядит он, а не упавшая команда.
# Сам disnake в таких местах перечисляет оба имени.
_INTERACTION_GONE = (
    disnake.HTTPException,
    disnake.InteractionTimedOut,
    # 🛑 «Уже отвечена» — тоже не `HTTPException`, а `InteractionException`,
    # сосед `InteractionTimedOut`. Проверка на подкоманде или `CheckFailure`,
    # поднятый после `send_message`, роняли сам обработчик ошибок — тот самый
    # класс, который в этой строке и лечили.
    disnake.InteractionResponded,
)

intents = disnake.Intents.all()


def _event_guild_id(args) -> int | None:
    """ID гильдии события, либо `_UNKNOWN_GUILD`, либо None для настоящей лички.

    🛑 Неопознанность определяется по СВОЙСТВУ аргумента, а не по имени события.
    Прежняя форма спрашивала «событие называется `message`?» — и `message_edit`,
    `message_delete`, `typing` с не доехавшей в кэш гильдией уходили по
    ДОМАШНЕМУ пути со всеми листенерами. Живого отказа не было только потому,
    что таких листенеров в проекте пока нет; первый же вышел бы за барьер молча.
    """
    for arg in args:
        guild_id = getattr(arg, "guild_id", None)
        if guild_id is not None:
            return guild_id
        guild = getattr(arg, "guild", None)
        if guild is not None:
            return getattr(guild, "id", None)
        # У `disnake.Message` атрибута `guild_id` нет вовсе, а `message.guild`
        # пуст и в личке, и когда гильдия не доехала. Различает их только канал:
        # личка — `DMChannel`, всё прочее без гильдии — неопознанная гильдия.
        channel = getattr(arg, "channel", None)
        if channel is not None:
            return None if isinstance(channel, DMChannel) else _UNKNOWN_GUILD
    return None


# Коги, которые Робокотик отдаёт ЧУЖИМ серверам (issue #41). Только
# информативное: статичные ответы FAQ и `!пинг`. RCON-команды, админка, тикеты,
# новости, модерация и весь ИИ-контур остаются кошкокрафтовскими.
# Список по КОГАМ, а не по именам команд: новая команда в админском коге не
# протечёт к гостям по недосмотру, а новый ког по умолчанию закрыт.
GUEST_COGS = frozenset({"FAQ", "PingTab"})

# 🛑 Ког открывается целиком, а имена команд выбирались для ДОМАШНЕГО чата, где
# префикс `!` принадлежит нам. На чужом сервере эти же имена — либо обычные
# слова («да», «нет»), либо самая частая команда любого бота («ping», «rules»,
# «map»). Итог без этого списка: Робокотик отвечает на нормальный разговор и
# спорит с хозяйским ботом, а выключить его владелец сервера не может — `!help`
# гостям закрыт. Гостю остаются полные русские имена (`!пинг`, `!вики`,
# `!айпи`, `!правила`), которых в чужом чате не набирают случайно.
_GUEST_ALIAS_DENY = frozenset({
    "ping", "ip", "map", "rules", "laws", "guide", "history", "wiki",
    "yes", "no", "donate", "helpme", "nometa", "readwiki",
    "да", "нет", "тут", "тут?",
})

# Гостевые серверы делят с нами RCON, CPU на рендер картинки и лимиты Discord,
# а модерации у нас там нет. Потолок общий на ВСЕ гостевые команды: у кога FAQ
# собственного кулдауна нет ни одного, и без этого он был бы безлимитным.
_GUEST_RATE = commands.CooldownMapping.from_cooldown(5, 60.0, commands.BucketType.guild)
# Отдельный счётчик на ОТВЕТ об отказе. Сам отказ обязан быть дешевле
# разрешённой команды, иначе защита работает на спамера: реакция — это REST, и
# без этой границы каждое сообщение сверх потолка стоило бы бота одного вызова
# Discord. Одна ⏳ за окно: человеку хватает, чтобы понять, что его услышали.
_GUEST_NOTICE = commands.CooldownMapping.from_cooldown(1, 60.0, commands.BucketType.guild)


def _is_home_member(user) -> bool:
    """Игрок Кошкокрафта? Единственный способ отличить своего в ЛС.

    🛑 Fail-safe в сторону «чужой»: гильдия не в кэше (окно после рестарта,
    сбой на стороне Discord) — отвечаем «нет». Цена ошибки здесь несимметрична:
    лишний отказ своему — минутное неудобство, лишний доступ чужому — открытый
    наружу канал без потолка.
    """
    home = bot.get_guild(Guilds.main)
    if home is None:
        return False
    return home.get_member(getattr(user, "id", 0)) is not None

# Гильдию не опознали. Отдельное значение, а не None: None означает «личка».
_UNKNOWN_GUILD = -1


class MainGuildBot(commands.Bot):
    """Гостям — только префиксные команды из GUEST_COGS, остальное вне Кошкокрафта не существует."""

    def __init__(self, *args, main_guild_id: int, **kwargs):
        self.main_guild_id = main_guild_id
        super().__init__(*args, **kwargs)

    def dispatch(self, event_name, *args, **kwargs):
        # Пустая гильдия НЕ означает личку: она пуста и пока гильдия не доехала
        # в кэш (окно между READY и GUILD_CREATE, сбой гильдии). Неизвестность
        # обязана падать в сторону «чужой» — лишний отказ дешевле лишнего
        # доступа. Разбор — в `_event_guild_id`, чтобы правило действовало на
        # ВСЕ message-события, а не на одно по имени.
        guild_id = _event_guild_id(args)
        if guild_id is not None and guild_id != self.main_guild_id:
            # Гостевой сервер: из всего потока событий пропускаем ТОЛЬКО разбор
            # префиксных команд, и ни одного листенера. Поэтому новый
            # on_message-листенер (ИИ, ловушка, реакции на новости, ВК-гейт)
            # по умолчанию остаётся кошкокрафтовским — правку этого места
            # забыть нельзя, её просто не требуется делать.
            if event_name == "message" and args:
                return super().dispatch("guest_message", args[0])
            if event_name == "command_error":
                # 🛑 Исключение — и оно ровно одно. `command_error` не листенер
                # чужого сервера, а единственный путь, которым сбой УЖЕ
                # РАЗРЕШЁННОЙ гостю команды доходит до лога. Погасив его здесь,
                # мы делали настоящую поломку `!вики` неотличимой от «такой
                # команды нет»: ни ответа человеку, ни строки оператору.
                # Ответы наружу отсекает сам `on_command_error` — он молчит
                # везде, кроме Кошкокрафта.
                return super().dispatch(event_name, *args, **kwargs)
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
        except _INTERACTION_GONE:
            pass  # интеракция протухла/уже отвечена — отказ и так случился
        return
    logger.error("Ошибка слэш-команды %s", inter.application_command.name, exc_info=error)
    # Без ответа юзер остаётся с вечным «Робокотик думает...»: команда, упавшая
    # ПОСЛЕ defer, для него неотличима от зависшей (issue #42 — /done ставил тег
    # и падал на TypeError, а подтверждение не приходило никогда).
    await answer_broken_interaction(
        inter, "😿 Команда сломалась на полпути — админы уже видят это в логах. "
        "Проверь результат, прежде чем повторять."
    )


@bot.listen("on_guest_message")
async def on_guest_message(message: disnake.Message):
    """Единственное, что Робокотик делает на чужом сервере, — разбирает команду.

    Сюда попадают только сообщения из гильдий, не равных Кошкокрафту
    (см. `MainGuildBot.dispatch`). Какие команды реально выполнятся, решает
    глобальная проверка `_guest_guild_allows_only_info_cogs`.
    """
    await bot.process_commands(message)


@bot.check
async def _guest_guild_allows_only_info_cogs(ctx: commands.Context) -> bool:
    """Полный набор команд — только на Кошкокрафте, всё остальное урезано.

    🛑 Условие намеренно «дом ИЛИ урезать», а не «чужая гильдия → урезать».
    Прежняя форма выпускала ЛС в полный набор, а право писать боту в личку даёт
    сам факт общей гильдии — в том числе гостевой. Через это открывались
    `!очередь` (плейлист радио) и `!help` со списком админских команд, а `!пинг`
    считался по домашнему потолку. Сюда же попадает неопознанная гильдия.

    🛑 Но личка НАШЕГО игрока — это дом, а не гостевая территория. Первая версия
    урезала личку всем подряд, и участник Кошкокрафта, годами писавший боту в
    ЛС, получал на `!help` и `!очередь` полное молчание — поведение «бот сдох». Различаем по членству в главной гильдии: кэш не доехал —
    считаем чужим, лишний отказ дешевле лишнего доступа.

    🛑 И проверка на `DMChannel` тут ОБЯЗАТЕЛЬНА, а не для красоты. `guild is
    None` означает личку ИЛИ не доехавшую в кэш гильдию — ровно та двузначность,
    ради которой в `dispatch` заведён `_UNKNOWN_GUILD`. Без канала условие
    fail-open: участник Кошкокрафта в ЧУЖОМ канале при промахе кэша получал
    полный домашний набор — `!help`, `!очередь`, всё, — мимо allowlist, запрета
    алиасов и потолка. Тот же класс, что чинили этажом ниже, внесённый заново
    этажом выше.
    """
    if ctx.guild is not None and ctx.guild.id == Guilds.main:
        return True
    if isinstance(ctx.channel, DMChannel) and _is_home_member(ctx.author):
        return True
    if ctx.cog is None or type(ctx.cog).__name__ not in GUEST_COGS:
        return False
    if (ctx.invoked_with or "").lower() in _GUEST_ALIAS_DENY:
        return False  # слишком общее имя для чужого чата — см. `_GUEST_ALIAS_DENY`
    # Потолок считается ПОСЛЕ разрешения кога: чужие попытки достучаться до
    # закрытого не должны съедать бюджет тех команд, что гостям разрешены.
    if not _GUEST_RATE.update_rate_limit(ctx.message):
        return True
    # 🛑 Отвечаем один раз за окно, а не на каждый отказ. Реакция — сетевой
    # вызов; без второго счётчика спамер, УЖЕ упёршийся в потолок, продолжал
    # генерировать по REST-запросу на сообщение, то есть защита обходилась боту
    # дороже, чем разрешённая команда, и жгла его же лимит Discord.
    # Реакция, а не текст: чужой канал не наш, чтобы в него писать. Запрещённый
    # ког не получает даже её — подтверждать каждую чужую команду значит
    # приглашать перебирать их.
    if not _GUEST_NOTICE.update_rate_limit(ctx.message):
        try:
            await ctx.message.add_reaction("⏳")
        except Exception:
            # 🛑 Ловим ШИРЕ `HTTPException`. Мы внутри глобального чека, а его
            # зовёт `Command.prepare` — там перехватывается только
            # `CommandError`. Обрыв сети (`OSError`, таймаут aiohttp) улетал бы
            # мимо, давая полный трейсбек в лог на КАЖДЫЙ отказ: то есть отказ
            # снова стоил бы дороже разрешённой команды, ровно наоборот тому,
            # ради чего написан `_GUEST_NOTICE`.
            logger.debug("Не удалось отметить отказ гостю", exc_info=True)
    return False


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    """Тишина на «такой команды нет», видимый отказ по правам, лог на поломки.

    Дефолтный обработчик disnake печатает трейсбек в stderr на ЛЮБУЮ ошибку,
    включая «такой команды нет», а логгер проекта пишет только в файл.

    🛑 На гостевых серверах он ВЫЗЫВАЕТСЯ, но наружу молчит. `command_error` —
    единственное событие чужой гильдии, которое диспетчер пропускает: иначе сбой
    уже разрешённой гостю команды (`!вики` упал на `fetch`) не давал ни ответа
    человеку, ни строки оператору и был неотличим от «такой команды нет».
    Ответы отсекаются ниже по одному признаку — `ctx.guild.id != Guilds.main`.
    """
    # Своя обработка у команды или кога — не мешаем ей (так же делает дефолт).
    if ctx.command is not None and ctx.command.has_error_handler():
        return
    if ctx.cog is not None and ctx.cog.has_error_handler():
        return
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.CheckFailure):
        # 🛑 Реакция — ТОЛЬКО дома. Там отказ означает «не хватило прав», и
        # молчание неотличимо от «бот умер». В ЛС и на гостевых серверах это
        # означает «команды тут нет», а подтверждать каждую чужую попытку —
        # приглашение перебирать их: посторонний из своего сервера гоняет в
        # личке закрытые команды по кругу и тратит наш лимит Discord на реакции.
        if ctx.guild is None or ctx.guild.id != Guilds.main:
            return
        logger.info("Отказ по правам: %s у %s", ctx.command, getattr(ctx.author, "id", "?"))
        try:
            await ctx.message.add_reaction("⛔")
        except disnake.HTTPException:
            pass  # нет права на реакции — отказ и так состоялся
        return
    # Гильдию называем обязательно: после того как диспетчер стал пропускать
    # `command_error` с чужих серверов, «Ошибка команды вики / Forbidden» без
    # неё неотличима от поломки ДОМА — и ночью первым чинят прод.
    logger.error(
        "Ошибка команды %s (гильдия %s)",
        ctx.command,
        getattr(ctx.guild, "id", "ЛС"),
        exc_info=error,
    )


@bot.event
async def on_error(event, *args, **kwargs):
    """Пишет необработанные исключения из listeners в bot.log (дефолт disnake печатает только в stderr).

    К записи добавляется возраст интеракции и разбор кодов 40060/10062 — без них
    инцидент со вторым инстансом (25-28.07.2026) читался как «затык event loop»
    и лечился не в том слое."""
    try:
        hint = _interaction_hint(args)
    except Exception:  # диагностика не имеет права съесть саму запись об ошибке
        hint = ""
    logger.exception("Необработанная ошибка в событии %s%s", event, hint)


def _interaction_hint(args) -> str:
    """Диагностический хвост к записи об ошибке: возраст интеракции и код отказа.

    Возраст меряется по снежинке (время создания интеракции у Discord) — он
    отделяет «мы отвечали слишком долго» от «ответ отверг Discord сразу»:
    - 10062 при возрасте <3с и 40060 в любом виде = интеракцию подтвердили
      раньше нас, наш собственный дедлайн ни при чём;
    - 10062 при возрасте >3с = мы действительно не уложились в дедлайн.

    `send_message` заворачивает 10062 в `InteractionTimedOut` и теряет `code`,
    а `send_modal`/`edit_message` бросают голый `NotFound` — ловим оба вида,
    иначе половина отказов молча теряет диагноз.
    """
    inter = next((a for a in args if isinstance(a, disnake.Interaction)), None)
    if inter is None:
        return ""
    age = (disnake.utils.utcnow() - inter.created_at).total_seconds()
    tail = f" (интеракция {inter.id}, возраст {age:.2f}с)"
    error = sys.exc_info()[1]
    code = 10062 if isinstance(error, disnake.InteractionTimedOut) else getattr(error, "code", None)
    if code == 40060 or (code == 10062 and age < 3):
        tail += (
            " — подтвердили не мы. Кандидата два: второй процесс с боевым"
            " токеном или два листенера на один custom_id внутри процесса;"
            " как различить — docs/tasks/28.07-buttons/notes.md"
        )
    return tail

@bot.listen()
async def on_message(message: disnake.Message):
    """
    Функция на добавление реакций и создания треда под каждым постом в заданных каналах
    + пишет, если игрок ошибся при написании Робокотику, а не Робокотику SRV
    """
    if message.channel.id in Channels.news_reaction_channels:
        # Пост через вебхук — тоже новость, хотя author.bot у него True (issue #39).
        # Свои посты бот шлёт токеном, а не вебхуком, поэтому под условие не попадает:
        # реакции под /news вешает news_editor сам, дубля не будет.
        if (message.author.bot and not message.webhook_id) or message.type not in (disnake.MessageType.default, disnake.MessageType.reply):
            return
        # Реакции и тред — одна реализация на оба источника новостей (см.
        # `decorate_news`): раньше это была копипаста, и фикс «сбой одной
        # реакции не уносит остальные и тред» лёг только сюда, а вторая копия
        # в `news_editor` продолжала их терять.
        await decorate_news(message, logger)
    elif isinstance(message.channel, DMChannel):
        # Подсказка для игрока, перепутавшего двух Робокотиков: код привязки
        # с сервера — ровно 4 цифры.
        #
        # 🛑 Гейт на членство обязателен, и он тут единственный. Личку боту
        # открывает ЛЮБАЯ общая гильдия — в том числе гостевая (issue #41), а
        # ЛС проходят диспетчер как домашнее событие. Без проверки посторонний
        # получал по исходящему сообщению Discord на каждое своё: ни барьера,
        # ни потолка на этой ветке не было вовсе. Кэша не хватило — молчим:
        # цена ошибки тут потерянная подсказка, а не открытый наружу канал.
        if not re.fullmatch(r'\d{4}', message.content):
            return
        if not _is_home_member(message.author):
            return
        if _GUEST_RATE.update_rate_limit(message):
            return  # свой, но частит — потолок общий с остальными ЛС
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
