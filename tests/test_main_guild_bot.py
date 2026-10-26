from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import disnake
import pytest
from disnake.ext import commands

from bot.discord_config import Guilds, Users


@pytest.mark.asyncio
async def test_dispatch_suppresses_foreign_guild_events(monkeypatch):
    from bot.bot import MainGuildBot

    dispatched = []
    monkeypatch.setattr(
        commands.Bot,
        "dispatch",
        lambda self, event_name, *args, **kwargs: dispatched.append(event_name),
    )
    test_bot = MainGuildBot(
        main_guild_id=Guilds.main,
        command_prefix="!",
        intents=disnake.Intents.none(),
    )

    test_bot.dispatch(
        "message",
        SimpleNamespace(guild=SimpleNamespace(id=99999999999999999)),
    )
    test_bot.dispatch(
        "raw_member_remove",
        SimpleNamespace(guild_id=99999999999999999),
    )
    test_bot.dispatch(
        "button_click",
        SimpleNamespace(guild_id=99999999999999999),
    )

    # Гостевому серверу (issue #41) достаётся ровно один канал — разбор
    # префиксной команды. Обычные листенеры (ИИ, ловушка, реакции на новости,
    # ВК-гейт) чужих событий не видят вообще.
    assert dispatched == ["guest_message"]


def test_the_exact_set_of_guest_visible_commands_is_pinned():
    """🛑 Список открыт по КОГАМ, значит растёт молча.

    Комментарий у `GUEST_COGS` защищает верное направление («новый ког по
    умолчанию закрыт») и молчит про обратное: каждая НОВАЯ команда внутри `FAQ`
    или `PingTab` уезжает наружу без единой правки и без единого теста. А
    `PingTab` когда-то был RCON-когом: `!тпс`, `!кто`, `!рестарт` легли бы туда
    следующими. Сейчас он отвечает только `:(`, но правило остаётся.

    Поэтому пиним поимённо. Тест падает не на ошибке, а на РЕШЕНИИ: добавил
    команду в гостевой ког — впиши её сюда осознанно или унеси в другой ког.
    """
    from bot.bot import _GUEST_ALIAS_DENY
    from bot.commands.faq import FAQ
    from bot.handlers.ping_tab import PingTab

    # Команды берём с КЛАССОВ: живой бот в тестах когов не грузит, и тест
    # молча уходил бы в skip — то есть не проверял бы ничего.
    visible = set()
    for cog_class in (FAQ, PingTab):
        for command in cog_class.__cog_commands__:
            visible.add(command.name)
            visible.update(getattr(command, "aliases", ()) or ())

    assert visible, "обход не нашёл ни одной команды — сломан он, а не код"

    # Ходовые имена, которые на чужом сервере — либо обычное слово, либо команда
    # хозяйского бота. Прогон показал, что гостям уезжали `ping`, `rules`,
    # `map`, `yes`, `no`, `да`, `нет` (нашли два угла ревью).
    collisions = {"ping", "ip", "map", "rules", "laws", "guide", "wiki", "yes", "no", "да", "нет"}
    assert collisions <= visible, "тест смотрит не туда — этих имён в когах уже нет"
    assert collisions <= _GUEST_ALIAS_DENY, (
        f"эти имена открыты гостям: {sorted(collisions - _GUEST_ALIAS_DENY)}"
    )


@pytest.mark.asyncio
async def test_generic_aliases_are_refused_outside_home_but_work_at_home():
    """Отказ по имени — только вне дома: дома `!ping` и `!да` остаются как были."""
    from bot.bot import _guest_guild_allows_only_info_cogs
    from bot.commands.faq import FAQ

    guest = _ctx(FAQ.__new__(FAQ))
    guest.invoked_with = "ping"
    assert not await _guest_guild_allows_only_info_cogs(guest)

    guest_ru = _ctx(FAQ.__new__(FAQ))
    guest_ru.invoked_with = "да"
    assert not await _guest_guild_allows_only_info_cogs(guest_ru)

    # Полное русское имя гостю по-прежнему доступно — ради него всё и делалось.
    ok = _ctx(FAQ.__new__(FAQ))
    ok.invoked_with = "вики"
    assert await _guest_guild_allows_only_info_cogs(ok)

    home = _ctx(FAQ.__new__(FAQ), guild_id=Guilds.main)
    home.invoked_with = "ping"
    assert await _guest_guild_allows_only_info_cogs(home)


@pytest.mark.asyncio
async def test_dispatch_lets_command_error_through_from_guest_guilds(monkeypatch):
    """🛑 Отказ, неотличимый от пустого результата (нашёл Codex).

    `command_error` — не листенер чужого сервера, а единственный путь, которым
    сбой УЖЕ РАЗРЕШЁННОЙ гостю команды доходит до лога. Пока диспетчер гасил и
    его, упавший `!вики` (нет прав на reply, 5xx от Discord) выглядел ровно как
    «такой команды нет»: ни ответа человеку, ни строки оператору.
    """
    from bot.bot import MainGuildBot

    dispatched = []
    monkeypatch.setattr(
        commands.Bot,
        "dispatch",
        lambda self, event_name, *args, **kwargs: dispatched.append(event_name),
    )
    test_bot = MainGuildBot(
        main_guild_id=Guilds.main,
        command_prefix="!",
        intents=disnake.Intents.none(),
    )

    guest_ctx = SimpleNamespace(guild=SimpleNamespace(id=99999999999999999))
    test_bot.dispatch("command_error", guest_ctx, commands.CommandError("бум"))

    assert dispatched == ["command_error"]


_next_guild_id = iter(range(700000000000000000, 700000000000001000))


@pytest.fixture(autouse=True)
def _fresh_cooldowns():
    """🛑 `_GUEST_RATE`/`_GUEST_NOTICE` — модульные глобалы, живущие весь прогон.

    Без сброса тесты влияют друг на друга, и набор держится на том, что ID
    подобраны руками неперекрывающимися. Первый же повтор ID (или другой порядок
    тестов) даёт мигание там, где логика цела.
    """
    import bot.bot as bot_module

    for mapping in (bot_module._GUEST_RATE, bot_module._GUEST_NOTICE):
        mapping._cache.clear()
    yield
    for mapping in (bot_module._GUEST_RATE, bot_module._GUEST_NOTICE):
        mapping._cache.clear()


def _ctx(cog, guild_id=None):
    """Контекст команды. Каждый вызов — своя гильдия: гостевой потолок общий."""
    guild = None if guild_id == "dm" else SimpleNamespace(
        id=guild_id if guild_id is not None else next(_next_guild_id)
    )
    # 🛑 Канал строим НАСТОЯЩИМ `DMChannel` только для лички: проверка барьера
    # различает «личка» и «гильдия не в кэше» именно по типу канала, и
    # SimpleNamespace сделал бы тест слепым к этому различию.
    if guild_id == "dm":
        channel = MagicMock(spec=disnake.DMChannel)
        channel.id = next(_next_guild_id)
    else:
        channel = MagicMock(spec=disnake.TextChannel)
        channel.id = next(_next_guild_id)
    # В ЛС `BucketType.guild` падает на `msg.author` — потолок становится
    # на человека, что для лички и правильно.
    author = SimpleNamespace(id=next(_next_guild_id))
    return SimpleNamespace(
        guild=guild,
        cog=cog,
        channel=channel,
        invoked_with="вики",
        message=SimpleNamespace(
            guild=guild, channel=channel, author=author, add_reaction=AsyncMock()
        ),
        author=author,
    )


@pytest.mark.asyncio
async def test_guest_check_passes_only_info_cogs():
    from bot.bot import GUEST_COGS, _guest_guild_allows_only_info_cogs, bot as live_bot
    from bot.commands.faq import FAQ
    from bot.handlers.ping_tab import PingTab
    from bot.handlers.tickets.engine import TicketEngine

    # Проверка обязана быть НАВЕШЕНА, иначе тест ниже зеленеет впустую.
    assert _guest_guild_allows_only_info_cogs in live_bot._checks
    # Опечатка в имени кога молча отключила бы гостям всё разрешённое.
    assert GUEST_COGS == {FAQ.__name__, PingTab.__name__}

    assert await _guest_guild_allows_only_info_cogs(_ctx(FAQ.__new__(FAQ)))
    assert await _guest_guild_allows_only_info_cogs(_ctx(PingTab.__new__(PingTab)))
    # Тикеты, админка, ИИ — на чужом сервере не существуют.
    assert not await _guest_guild_allows_only_info_cogs(
        _ctx(TicketEngine.__new__(TicketEngine))
    )
    assert not await _guest_guild_allows_only_info_cogs(_ctx(None))


@pytest.mark.asyncio
async def test_guest_check_lets_everything_through_at_home():
    from bot.bot import _guest_guild_allows_only_info_cogs
    from bot.handlers.tickets.engine import TicketEngine

    home = _ctx(TicketEngine.__new__(TicketEngine), guild_id=Guilds.main)

    assert await _guest_guild_allows_only_info_cogs(home)


@pytest.mark.asyncio
async def test_dm_from_our_own_player_is_home_not_guest(monkeypatch):
    """🛑 Личка НАШЕГО игрока — дом, а не гостевая территория.

    Первая версия урезала личку всем подряд, и участник Кошкокрафта, годами
    писавший боту в ЛС, получал на `!help` и `!очередь` полное молчание — то
    есть поведение «бот сдох» (нашёл угол конечного пользователя). Различаем по
    членству, а не по факту лички.
    """
    import bot.bot as bot_module
    from bot.bot import _guest_guild_allows_only_info_cogs
    from bot.handlers.catcraft_fm import CatcraftFM

    home_guild = MagicMock()
    home_guild.get_member.return_value = MagicMock()
    monkeypatch.setattr(bot_module.bot, "get_guild", MagicMock(return_value=home_guild))

    assert await _guest_guild_allows_only_info_cogs(
        _ctx(CatcraftFM.__new__(CatcraftFM), guild_id="dm")
    )


@pytest.mark.asyncio
async def test_dm_falls_back_to_guest_when_the_guild_is_not_cached(monkeypatch):
    """Кэш не доехал → считаем чужим. Цена ошибки несимметрична."""
    import bot.bot as bot_module
    from bot.bot import _guest_guild_allows_only_info_cogs
    from bot.handlers.catcraft_fm import CatcraftFM

    monkeypatch.setattr(bot_module.bot, "get_guild", MagicMock(return_value=None))

    assert not await _guest_guild_allows_only_info_cogs(
        _ctx(CatcraftFM.__new__(CatcraftFM), guild_id="dm")
    )


@pytest.mark.asyncio
async def test_guest_check_closes_dm_too():
    """ЛС — гостевая территория: право писать боту даёт общая гильдия.

    Прежняя форма (`ctx.guild is None → True`) открывала в личке ВЕСЬ префиксный
    набор любому участнику гостевого сервера: `!очередь` отдавал плейлист радио,
    `!help` — список админских команд.
    """
    from bot.bot import _guest_guild_allows_only_info_cogs
    from bot.commands.faq import FAQ
    from bot.handlers.catcraft_fm import CatcraftFM

    assert not await _guest_guild_allows_only_info_cogs(
        _ctx(CatcraftFM.__new__(CatcraftFM), guild_id="dm")
    )
    # help живёт вне кога — тоже закрыт.
    assert not await _guest_guild_allows_only_info_cogs(_ctx(None, guild_id="dm"))
    # А информативное в личке по-прежнему работает.
    assert await _guest_guild_allows_only_info_cogs(_ctx(FAQ.__new__(FAQ), guild_id="dm"))


@pytest.mark.asyncio
async def test_guest_rate_limit_caps_allowed_commands():
    """У кога FAQ нет ни одного кулдауна — потолок обязан быть общим."""
    from bot.bot import _guest_guild_allows_only_info_cogs
    from bot.commands.faq import FAQ

    guild_id = next(_next_guild_id)
    allowed = [
        await _guest_guild_allows_only_info_cogs(
            _ctx(FAQ.__new__(FAQ), guild_id=guild_id)
        )
        for _ in range(8)
    ]

    assert allowed[:5] == [True] * 5, "первые пять обязаны пройти"
    assert allowed[5:] == [False] * 3, "дальше гостевой потолок обязан отсекать"


@pytest.mark.asyncio
async def test_guest_refusal_reacts_once_per_window_not_once_per_message():
    """🛑 Иначе защита работает НА спамера.

    Реакция — это REST-запрос. Пока ⏳ ставилась на каждое сообщение сверх
    потолка, отказ обходился боту ровно так же дорого, как разрешённая команда:
    поток из сотни сообщений давал сотню исходящих вызовов и жёг лимит Discord
    самого бота — уже ПОСЛЕ того, как защита сработала.
    """
    from bot.bot import _guest_guild_allows_only_info_cogs
    from bot.commands.faq import FAQ

    guild_id = next(_next_guild_id)
    contexts = [_ctx(FAQ.__new__(FAQ), guild_id=guild_id) for _ in range(25)]
    for ctx in contexts:
        await _guest_guild_allows_only_info_cogs(ctx)

    reacted = [c for c in contexts if c.message.add_reaction.await_count]
    assert len(reacted) == 1, f"на 20 отказов ушло {len(reacted)} сетевых вызовов"
    assert reacted[0].message.add_reaction.await_args.args == ("⏳",)


@pytest.mark.asyncio
async def test_guest_command_failure_is_logged_but_never_answered(caplog):
    """Событие пропустили — но наружу по-прежнему молчим.

    Ошибка гостевой команды обязана дать строку оператору и НИ ОДНОГО
    исходящего вызова: чужой канал не наш, чтобы в него писать.
    """
    import logging

    from bot.bot import on_command_error

    ctx = _ctx(None, guild_id=99999999999999999)
    ctx.command = None

    with caplog.at_level(logging.DEBUG, logger="robocat.bot"):
        await on_command_error(ctx, commands.CommandInvokeError(RuntimeError("бум")))

    ctx.message.add_reaction.assert_not_awaited()
    # Фильтр по имени: caplog собирает записи всех логгеров разом, и без него
    # проверка проходит за счёт посторонней строки.
    assert any(
        r.levelno >= logging.ERROR and r.name == "robocat.bot" for r in caplog.records
    ), [r.getMessage() for r in caplog.records]


@pytest.mark.asyncio
async def test_a_closed_cog_does_not_eat_the_budget_of_allowed_ones():
    """🛑 Иначе перебор закрытых команд выжигает лимит настоящим гостям.

    Чужой сервер гоняет `!мут`, `!очередь`, `!aiuserlock` — каждая закрытая
    попытка стоила бы бюджета FAQ и `!пинг`, то есть отказ снова обходился бы
    дороже разрешённой команды. Мутация «считать потолок до проверки кога» не
    роняла ни одного теста (нашла мутация ревью).
    """
    from bot.bot import _guest_guild_allows_only_info_cogs
    from bot.commands.faq import FAQ
    from bot.handlers.catcraft_fm import CatcraftFM

    guild_id = next(_next_guild_id)
    for _ in range(30):
        assert not await _guest_guild_allows_only_info_cogs(
            _ctx(CatcraftFM.__new__(CatcraftFM), guild_id=guild_id)
        )

    # Бюджет разрешённого кога обязан остаться нетронутым.
    allowed = [
        await _guest_guild_allows_only_info_cogs(_ctx(FAQ.__new__(FAQ), guild_id=guild_id))
        for _ in range(5)
    ]
    assert allowed == [True] * 5, "закрытые попытки съели бюджет разрешённых"


@pytest.mark.asyncio
async def test_guest_rate_limit_does_not_touch_home():
    from bot.bot import _guest_guild_allows_only_info_cogs
    from bot.commands.faq import FAQ

    home = [
        await _guest_guild_allows_only_info_cogs(
            _ctx(FAQ.__new__(FAQ), guild_id=Guilds.main)
        )
        for _ in range(20)
    ]

    assert all(home), "на Кошкокрафте потолка гостей быть не должно"


@pytest.mark.asyncio
async def test_dispatch_keeps_main_guild_and_dm_events(monkeypatch):
    from bot.bot import MainGuildBot

    dispatched = []
    monkeypatch.setattr(
        commands.Bot,
        "dispatch",
        lambda self, event_name, *args, **kwargs: dispatched.append(event_name),
    )
    test_bot = MainGuildBot(
        main_guild_id=Guilds.main,
        command_prefix="!",
        intents=disnake.Intents.none(),
    )

    dm = SimpleNamespace(guild=None, channel=MagicMock(spec=disnake.DMChannel))
    test_bot.dispatch(
        "message",
        SimpleNamespace(guild=SimpleNamespace(id=Guilds.main)),
    )
    test_bot.dispatch("message", dm)

    assert dispatched == ["message", "message"]


@pytest.mark.asyncio
async def test_dispatch_treats_unknown_guild_as_guest(monkeypatch):
    """Пустая `message.guild` — это НЕ личка, пока канал не DMChannel.

    У `disnake.Message` нет атрибута `guild_id`, а `message.guild` пуст, пока
    гильдия не доехала в кэш. Прежняя форма пускала такое сообщение по
    домашнему пути со всеми листенерами — то есть барьер ошибался в сторону
    «пустить».
    """
    from bot.bot import MainGuildBot

    dispatched = []
    monkeypatch.setattr(
        commands.Bot,
        "dispatch",
        lambda self, event_name, *args, **kwargs: dispatched.append(event_name),
    )
    test_bot = MainGuildBot(
        main_guild_id=Guilds.main,
        command_prefix="!",
        intents=disnake.Intents.none(),
    )

    # Гильдия не опознана, канал — обычный текстовый: считаем чужим.
    test_bot.dispatch(
        "message",
        SimpleNamespace(guild=None, channel=MagicMock(spec=disnake.TextChannel)),
    )

    assert dispatched == ["guest_message"]


@pytest.mark.asyncio
async def test_config_reload_reports_invalid_candidate_without_owner_change(monkeypatch):
    from bot.discord_config import ConfigError
    from bot.slash_commands import admin

    fake_bot = SimpleNamespace(owner_id=Users.szarkan)
    cog = admin.AdminCommands(fake_bot)
    inter = SimpleNamespace(
        response=SimpleNamespace(defer=AsyncMock()),
        edit_original_response=AsyncMock(),
    )
    monkeypatch.setattr(
        admin,
        "reload_config",
        lambda: (_ for _ in ()).throw(ConfigError("битый конфиг")),
    )

    await admin.AdminCommands.configReload.callback(cog, inter)

    inter.response.defer.assert_awaited_once_with(ephemeral=True)
    assert fake_bot.owner_id == Users.szarkan
    assert "битый конфиг" in inter.edit_original_response.await_args.args[0]


@pytest.mark.asyncio
async def test_config_reload_updates_bot_owner(monkeypatch):
    from bot.slash_commands import admin

    fake_bot = SimpleNamespace(owner_id=0)
    cog = admin.AdminCommands(fake_bot)
    inter = SimpleNamespace(
        response=SimpleNamespace(defer=AsyncMock()),
        edit_original_response=AsyncMock(),
    )
    monkeypatch.setattr(admin, "reload_config", lambda: object())

    await admin.AdminCommands.configReload.callback(cog, inter)

    assert fake_bot.owner_id == Users.szarkan
    assert "перезагружен" in inter.edit_original_response.await_args.args[0]


@pytest.mark.asyncio
async def test_config_reload_restarts_fm_when_channel_changes(monkeypatch):
    from bot.slash_commands import admin

    channels = SimpleNamespace(catcraft_fm=1)
    fm = SimpleNamespace(restart_for_config_reload=AsyncMock())
    fake_bot = SimpleNamespace(
        owner_id=0,
        get_cog=lambda name: fm if name == "CatcraftFM" else None,
    )
    cog = admin.AdminCommands(fake_bot)
    inter = SimpleNamespace(
        response=SimpleNamespace(defer=AsyncMock()),
        edit_original_response=AsyncMock(),
    )

    def _reload():
        channels.catcraft_fm = 2

    monkeypatch.setattr(admin, "Channels", channels)
    monkeypatch.setattr(admin, "reload_config", _reload)

    await admin.AdminCommands.configReload.callback(cog, inter)

    fm.restart_for_config_reload.assert_awaited_once()


@pytest.mark.asyncio
async def test_guest_message_listener_is_wired_to_command_processing(monkeypatch):
    """Смычка dispatch → листенер. Разъедутся имена — гости замолчат целиком.

    `dispatch("guest_message", ...)` у disnake ищет листенер с именем
    `on_guest_message`; тест выше проверяет левую половину этой пары, этот —
    правую и то, что она действительно зовёт разбор команд.
    """
    from bot.bot import bot as live_bot

    listeners = live_bot.extra_events.get("on_guest_message") or []
    assert listeners, "dispatch('guest_message') некому услышать — гости молчат"

    processed = []
    monkeypatch.setattr(
        live_bot, "process_commands", AsyncMock(side_effect=processed.append)
    )
    message = SimpleNamespace(id=1)
    for listener in listeners:
        await listener(message)

    assert processed == [message]


@pytest.mark.asyncio
async def test_slash_error_edits_deferred_response_instead_of_stacking_one():
    """Упавшая после defer команда обязана СНЯТЬ «Робокотик думает...».

    followup добавлял сообщение рядом, а публичный плейсхолдер оставался висеть
    вечно — ровно тот симптом, ради которого guard и написан (issue #42).
    """
    from bot.bot import bot as live_bot, on_slash_command_error

    # Без регистрации guard мёртв, а тест ниже зеленел бы впустую.
    assert on_slash_command_error in live_bot.extra_events.get(
        "on_slash_command_error", []
    )

    inter = _slash_inter(shown=False)

    await on_slash_command_error(inter, commands.CommandError("бум"))

    inter.edit_original_response.assert_awaited_once()
    said = inter.edit_original_response.await_args.kwargs["content"]
    # 🛑 Плейсхолдер снят — но текст именно «проверь результат», а не «повтори».
    # Пустой ответ доказывает, что мы не ОТВЕТИЛИ, и НЕ доказывает, что команда
    # ничего не успела сделать: `/done` архивирует тикет, пишет автору в ЛС и
    # снимает флаги ДО возможного сбоя `thread.delete()`.
    assert "повторять" in said.lower(), said
    assert "ещё раз" not in said.lower(), said
    inter.followup.send.assert_not_awaited()


def _slash_inter(*, done=True, shown=False):
    """`shown=False` — висит пустой defer; `shown=True` — команда уже ответила."""
    inter = MagicMock()
    inter.response.is_done.return_value = done
    original = MagicMock()
    original.content = "✅ Готово" if shown else ""
    original.embeds = []
    original.components = []
    inter.original_response = AsyncMock(return_value=original)
    inter.edit_original_response = AsyncMock()
    inter.followup.send = AsyncMock()
    inter.response.send_message = AsyncMock()
    inter.application_command.name = "тест"
    return inter


@pytest.mark.asyncio
async def test_slash_error_answers_when_nothing_was_deferred():
    from bot.bot import on_slash_command_error

    inter = _slash_inter(done=False)

    await on_slash_command_error(inter, commands.CommandError("бум"))

    inter.response.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_slash_error_handler_survives_expired_interaction():
    """`InteractionTimedOut` — НЕ наследник `HTTPException`.

    Именно им disnake заворачивает 10062. Один `except HTTPException` пропускал
    его наружу, и в логе виноватым выглядел сам обработчик ошибок, а не упавшая
    команда.
    """
    from bot.bot import on_slash_command_error

    inter = MagicMock()
    inter.response.is_done.return_value = False
    inter.response.send_message = AsyncMock(
        side_effect=disnake.InteractionTimedOut(MagicMock())
    )
    inter.followup.send = AsyncMock(
        side_effect=disnake.InteractionTimedOut(MagicMock())
    )
    inter.application_command.name = "тест"

    # 🛑 Именно `CheckFailure`, а не `CommandError`. `_INTERACTION_GONE` стоит
    # ТОЛЬКО в ветке отказа по правам; на `CommandError` тест уходил в общий
    # хвост и зеленел за счёт широкого `except` внутри `answer_broken_interaction`
    # — то есть проверял не тот код, про который написан (нашла мутация ревью).
    await on_slash_command_error(inter, commands.CheckFailure("нет прав"))  # не бросает

    # «Уже отвечена» — тоже `InteractionException`, а не `HTTPException`.
    inter.response.send_message = AsyncMock(
        side_effect=disnake.InteractionResponded(MagicMock())
    )
    await on_slash_command_error(inter, commands.CheckFailure("нет прав"))


@pytest.mark.asyncio
async def test_check_failure_on_prefix_command_is_visible():
    """Отказ по правам обязан быть виден: молчание неотличимо от «бот умер»."""
    from bot.bot import on_command_error

    ctx = MagicMock()
    ctx.command.has_error_handler.return_value = False
    ctx.cog = None
    ctx.guild.id = Guilds.main
    ctx.message.add_reaction = AsyncMock()

    await on_command_error(ctx, commands.CheckFailure("нет прав"))

    ctx.message.add_reaction.assert_awaited_once_with("⛔")


@pytest.mark.asyncio
async def test_check_failure_outside_home_is_silent():
    """Подтверждать каждую чужую попытку — приглашение перебирать команды.

    Посторонний из своего сервера гонял бы в ЛС закрытые команды по кругу, и
    каждая стоила бы нам вызова Discord на реакцию плюс строки в логе.
    """
    from bot.bot import on_command_error

    for guild in (None, SimpleNamespace(id=99999999999999999)):
        ctx = MagicMock()
        ctx.command.has_error_handler.return_value = False
        ctx.cog = None
        ctx.guild = guild
        ctx.message.add_reaction = AsyncMock()

        await on_command_error(ctx, commands.CheckFailure("не тут"))

        ctx.message.add_reaction.assert_not_awaited()


def test_news_reaction_listener_is_registered():
    """Тесты зовут `on_message` напрямую — без этого снятие декоратора незаметно."""
    from bot.bot import bot as live_bot, on_message

    assert on_message in live_bot.extra_events.get("on_message", [])


@pytest.mark.asyncio
async def test_guest_hitting_the_rate_limit_gets_an_answer():
    """Упереться в потолок не должно быть неотличимо от «бот сломался».

    Отказ проверки уходит в `command_error`, а тот для чужой гильдии гасится
    диспетчером — значит ответить можно только прямо в проверке.
    """
    from bot.bot import _guest_guild_allows_only_info_cogs
    from bot.commands.faq import FAQ

    guild_id = next(_next_guild_id)
    last = None
    for _ in range(6):
        last = _ctx(FAQ.__new__(FAQ), guild_id=guild_id)
        await _guest_guild_allows_only_info_cogs(last)

    last.message.add_reaction.assert_awaited_once_with("⏳")


@pytest.mark.asyncio
async def test_guest_hitting_a_closed_cog_gets_no_answer():
    """Закрытый ког реакции не получает: подтверждать каждую чужую команду —
    приглашение перебирать их."""
    from bot.bot import _guest_guild_allows_only_info_cogs
    from bot.handlers.tickets.engine import TicketEngine

    ctx = _ctx(TicketEngine.__new__(TicketEngine))

    assert not await _guest_guild_allows_only_info_cogs(ctx)
    ctx.message.add_reaction.assert_not_awaited()


def test_slash_commands_stay_scoped_to_the_main_guild():
    """Единственное, что держит слэш-команды вне гостевых серверов.

    Гейт по когам (`bot.check`) на слэш-команды НЕ распространяется — у них своя
    цепочка проверок. Снимут `test_guilds` ради «мы же теперь на нескольких
    серверах» — все 27 команд станут глобальными, и гостевой набор молча
    перестанет быть набором.
    """
    from bot.bot import bot as live_bot

    assert tuple(live_bot._test_guilds or ()) == (Guilds.main,)


@pytest.mark.asyncio
async def test_home_member_in_a_foreign_channel_is_still_a_guest(monkeypatch):
    """🛑 Fail-open, внесённый поверх уже починенного fail-open.

    `guild is None` означает личку ИЛИ не доехавшую в кэш гильдию — ровно та
    двузначность, ради которой в `dispatch` заведён `_UNKNOWN_GUILD`. Проверка
    членства без проверки КАНАЛА выпускала участника Кошкокрафта в ЧУЖОМ канале
    на полный домашний набор (`!help`, `!очередь`) мимо allowlist, запрета
    алиасов и потолка. Нашёл Codex на седьмом круге.
    """
    import bot.bot as bot_module
    from bot.bot import _guest_guild_allows_only_info_cogs
    from bot.handlers.catcraft_fm import CatcraftFM

    home_guild = MagicMock()
    home_guild.get_member.return_value = MagicMock()  # автор — свой
    monkeypatch.setattr(bot_module.bot, "get_guild", MagicMock(return_value=home_guild))

    # Гильдия не опознана, канал обычный текстовый — это НЕ личка.
    ctx = _ctx(CatcraftFM.__new__(CatcraftFM))
    ctx.guild = None

    assert not await _guest_guild_allows_only_info_cogs(ctx)


@pytest.mark.asyncio
async def test_cog_with_own_handler_is_left_alone_but_others_are_not():
    """🛑 Обе ветки делегирования не наблюдал ни один тест.

    Инверсия условия не роняла ничего (нашёл контрольный угол ревью), а стоит
    она много в обе стороны:
    - убрать делегирование → `CommandOnCooldown` (наследник `CommandError`, НЕ
      `CheckFailure`) пойдёт и в коговый обработчик, и сюда, где его ждёт
      `logger.error(exc_info=...)` — полный трейсбек на каждое сообщение
      спамера, ровно «отказ дороже разрешённой команды»;
    - инвертировать → ког БЕЗ своего обработчика теряет ⛔ и строку в логе,
      то есть «бот выглядит мёртвым».
    """
    import logging

    from bot.bot import on_command_error

    # У кога есть свой обработчик — глобальный не вмешивается.
    own = _ctx(None, guild_id=Guilds.main)
    own.command = MagicMock()
    own.command.has_error_handler.return_value = False
    own.cog = MagicMock()
    own.cog.has_error_handler.return_value = True

    await on_command_error(own, commands.CheckFailure("нет прав"))
    own.message.add_reaction.assert_not_awaited()

    # У кога своего обработчика нет — отказ обязан стать видимым.
    plain = _ctx(None, guild_id=Guilds.main)
    plain.command = MagicMock()
    plain.command.has_error_handler.return_value = False
    plain.cog = MagicMock()
    plain.cog.has_error_handler.return_value = False

    await on_command_error(plain, commands.CheckFailure("нет прав"))
    plain.message.add_reaction.assert_awaited_once_with("⛔")


@pytest.mark.asyncio
async def test_slash_failure_leaves_a_traceback_in_the_log(caplog):
    """Зеркало модалочной проверки: без строки чинить будет нечего."""
    import logging

    from bot.bot import on_slash_command_error

    inter = _slash_inter(shown=False)

    with caplog.at_level(logging.DEBUG, logger="robocat.bot"):
        await on_slash_command_error(inter, commands.CommandError("бум"))

    # 🛑 Фильтр по ИМЕНИ логгера обязателен: `caplog.records` собирает записи
    # всех логгеров разом, поэтому «есть ERROR» проходило за счёт посторонней
    # строки из `robocat.utils` — и удаление проверяемой не роняло тест
    # (поймано веером мутаций).
    errors = [
        r for r in caplog.records
        if r.levelno >= logging.ERROR and r.name == "robocat.bot"
    ]
    assert errors, [r.getMessage() for r in caplog.records]
    assert errors[0].exc_info is not None, "трейсбек не приложен"


@pytest.mark.asyncio
async def test_permission_denial_at_home_is_logged_with_the_author(caplog):
    """ID нужен, чтобы понять, КОМУ не хватило прав, — без него строка бесполезна."""
    import logging

    from bot.bot import on_command_error

    ctx = _ctx(None, guild_id=Guilds.main)
    ctx.command = None

    with caplog.at_level(logging.DEBUG, logger="robocat.bot"):
        await on_command_error(ctx, commands.CheckFailure("нет прав"))

    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert str(ctx.author.id) in logged, logged


@pytest.mark.asyncio
async def test_guest_refusal_survives_a_transport_error(caplog):
    """🛑 Ширина `except` тут несущая, а не украшение.

    Мы внутри ГЛОБАЛЬНОГО чека, а его зовёт `Command.prepare`, где ловится
    только `CommandError`. Обрыв сети (`OSError`, таймаут aiohttp) улетал бы
    мимо и давал полный трейсбек в лог на КАЖДЫЙ отказ — то есть отказ снова
    стоил бы дороже разрешённой команды. Сужение до `HTTPException` не роняло
    ни одного теста (нашёл контрольный угол ревью).
    """
    import logging

    from bot.bot import _guest_guild_allows_only_info_cogs
    from bot.commands.faq import FAQ

    guild_id = next(_next_guild_id)
    contexts = [_ctx(FAQ.__new__(FAQ), guild_id=guild_id) for _ in range(7)]
    for ctx in contexts:
        ctx.message.add_reaction = AsyncMock(side_effect=OSError("сеть легла"))

    with caplog.at_level(logging.DEBUG, logger="robocat.bot"):
        for ctx in contexts:
            assert await _guest_guild_allows_only_info_cogs(ctx) in (True, False)

    assert not [r for r in caplog.records if r.levelno >= logging.ERROR], [
        r.getMessage() for r in caplog.records
    ]
