"""Листенеры `on_message` в ЛС: право писать боту даёт ОБЩАЯ гильдия.

🛑 Бота пускают на любой сервер (issue #41). Поэтому любой участник любого
гостевого сервера получает право на личку с ботом — а диспетчер режет только
события ЧУЖИХ ГИЛЬДИЙ, личку он пропускает целиком. Гейт по когам тут тоже не
работает: он проверяет команды, а не листенеры.

Мульти-ревью 10.08.2026 пробило барьер именно здесь: `idiot_check` жёг вызов
LLM на каждое сообщение постороннего в ЛС, а автоответчик FAQ отвечал без
единого лимита.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import disnake
import pytest

from bot.discord_config import Guilds


def _dm(content):
    message = MagicMock()
    message.guild = None
    message.content = content
    message.author.bot = False
    message.mentions = []
    message.channel.send = AsyncMock()
    message.reply = AsyncMock()
    message.delete = AsyncMock()
    message.author.send = AsyncMock()
    return message


def _guest(content):
    message = _dm(content)
    message.guild = SimpleNamespace(id=99999999999999999)
    return message


def _home(content):
    message = _dm(content)
    message.guild = SimpleNamespace(id=Guilds.main)
    return message


@pytest.mark.parametrize("factory", [_dm, _guest])
async def test_underage_check_never_burns_llm_outside_home(factory):
    from bot.handlers.idiot_check import IdiotCheck

    bot = MagicMock()
    bot.ai_engine.idiotCheck = AsyncMock(return_value=True)
    cog = IdiotCheck(bot)

    await cog.checkUnderage(factory("мне 5 лет"))

    bot.ai_engine.idiotCheck.assert_not_awaited()


async def test_underage_check_still_works_at_home():
    """Guard не должен убить саму фичу."""
    from bot.handlers.idiot_check import IdiotCheck

    bot = MagicMock()
    bot.ai_engine.idiotCheck = AsyncMock(return_value=False)
    cog = IdiotCheck(bot)

    await cog.checkUnderage(_home("мне 5 лет"))

    bot.ai_engine.idiotCheck.assert_awaited_once()


@pytest.mark.parametrize("factory", [_dm, _guest])
async def test_faq_autoresponder_stays_home(factory):
    from bot.commands.faq import FAQ

    bot = MagicMock()
    bot.user = object()
    cog = FAQ(bot)
    message = factory("когда вайп")

    await cog.findFAQInMessages(message)

    message.reply.assert_not_awaited()


async def test_faq_autoresponder_still_answers_at_home():
    from bot.commands.faq import FAQ

    bot = MagicMock()
    bot.user = object()
    cog = FAQ(bot)
    message = _home("а когда вайп будет")

    await cog.findFAQInMessages(message)

    message.reply.assert_awaited_once()


def test_the_guarded_listeners_are_actually_registered():
    """🛑 Иначе все проверки выше — про мёртвый код.

    Тесты зовут `checkUnderage`/`findFAQInMessages` как обычные методы. Сними с
    них декоратор `@commands.Cog.listener()` — Discord перестанет их вызывать
    вовсе, а весь файл останется зелёным: и «не сработало вне дома», и
    «сработало дома» одинаково выполнятся при прямом вызове. Поймал Codex.
    """
    from bot.commands.faq import FAQ
    from bot.handlers.idiot_check import IdiotCheck

    for cog_class, method in ((IdiotCheck, "checkUnderage"), (FAQ, "findFAQInMessages")):
        listeners = cog_class.__cog_listeners__
        assert (
            "on_message",
            method,
        ) in listeners, f"{cog_class.__name__}.{method} не зарегистрирован как on_message"


def _all_on_message_listeners():
    """Каждый `on_message`-листенер проекта — обходом, а не списком руками."""
    from disnake.ext import commands as dcommands

    from project_scan import descendants, import_all_bot_modules

    import_all_bot_modules()

    found = []
    for cog_class in descendants(dcommands.Cog):
        if not cog_class.__module__.startswith("bot."):
            continue
        for event, method in getattr(cog_class, "__cog_listeners__", ()):
            if event == "on_message":
                found.append((cog_class, method))
    return found


async def test_no_on_message_listener_answers_a_stranger_in_dm():
    """🛑 Список руками устаревает молча — а это ЕДИНСТВЕННАЯ граница в ЛС.

    Диспетчер режет чужие ГИЛЬДИИ, личку он пропускает целиком, и гейт по когам
    тут не работает (он про команды). Значит каждый `on_message`-листенер несёт
    собственный guard, и «не забыть его добавить» — не защита: седьмой листенер
    (антиспам, релей поддержки, ИИ в ЛС) появился бы без красного теста, пока
    сторож помнил ровно двоих.

    Здесь не разбор исходника, а ВЫЗОВ каждого листенера личкой постороннего.
    """
    listeners = _all_on_message_listeners()
    assert len(listeners) >= 5, f"обход нашёл лишь {len(listeners)} листенеров — сломан он"

    leaked, exploded = [], []
    for cog_class, method in listeners:
        cog = cog_class.__new__(cog_class)  # без __init__: он лезет в env и сеть
        cog.bot = MagicMock()
        cog.bot.user = object()
        # 🛑 Весь ИИ-движок замокан ЦЕЛИКОМ, а не один метод. Иначе листенер,
        # дёрнувший неподготовленный async-атрибут, падал на «MagicMock can't be
        # awaited» — тест краснел, но по ложной причине, а настоящую трату LLM
        # он при этом не видел вовсе (нашёл контрольный угол ревью).
        for ai_method in ("idiotCheck", "generateAnswer", "generateImage"):
            setattr(cog.bot.ai_engine, ai_method, AsyncMock(return_value=True))

        message = _dm("мне 5 лет, когда вайп")
        message.channel = MagicMock(spec=disnake.DMChannel)
        message.channel.id = 111222333444555666
        message.channel.send = AsyncMock()
        message.author.id = 999888777666555444

        try:
            await getattr(cog, method)(message)
        except Exception as exc:  # noqa: BLE001
            # 🛑 Падение НЕ считаем успехом: листенер, свалившийся на отсутствующем
            # атрибуте, ничего не отправил — и проверка стала бы вакуумной.
            exploded.append(f"{cog_class.__name__}.{method}: {exc!r}")
            continue

        watched = [
            ("reply", message.reply),
            ("channel.send", message.channel.send),
            ("author.send", message.author.send),
            ("delete", message.delete),
        ]
        # 🛑 Вред от постороннего в ЛС — не только исходящее сообщение. Ради
        # ЭТОГО файл и заведён: `idiot_check` жёг вызов LLM на каждое сообщение,
        # ничего никому не отправляя. Сторож, глядящий только на send/reply,
        # такой листенер пропускал (нашёл контрольный угол ревью).
        watched += [
            (f"ai_engine.{name}", getattr(cog.bot.ai_engine, name))
            for name in ("idiotCheck", "generateAnswer", "generateImage")
        ]
        for name, mock in watched:
            if mock.await_count:
                leaked.append(f"{cog_class.__name__}.{method} → {name}")

    assert not exploded, f"листенер не доехал до конца, проверка вакуумна: {exploded}"
    assert not leaked, f"личка постороннего получила ответ: {leaked}"


async def test_dm_digit_hint_is_closed_to_strangers(monkeypatch):
    """🛑 Дыра в барьере, найденная Codex: ветка вообще не проходила ни гейта.

    Подсказка «я не тот Робокотик» отвечает на любые четыре цифры в ЛС. Личку
    боту открывает ЛЮБАЯ общая гильдия — в том числе гостевая, — а ЛС проходят
    диспетчер как домашнее событие. То есть посторонний получал по исходящему
    сообщению Discord на каждое своё, мимо `GUEST_COGS` и мимо потолка: ни одна
    из двух заявленных границ этой ветки не касалась.
    """
    import bot.bot as bot_module

    monkeypatch.setattr(bot_module.asyncio, "sleep", AsyncMock())
    message = _dm("1234")
    message.channel = MagicMock(spec=disnake.DMChannel)
    message.channel.id = 555000111222333444
    message.channel.send = AsyncMock()
    message.author.id = 424242424242424242

    home = MagicMock()
    home.get_member.return_value = None  # автор не с Кошкокрафта
    monkeypatch.setattr(bot_module.bot, "get_guild", MagicMock(return_value=home))

    await bot_module.on_message(message)

    message.channel.send.assert_not_awaited()


async def test_dm_digit_hint_still_helps_a_catcraft_player(monkeypatch):
    """Гейт не должен убить саму подсказку — ради неё ветка и живёт."""
    import bot.bot as bot_module

    monkeypatch.setattr(bot_module.asyncio, "sleep", AsyncMock())
    message = _dm("1234")
    message.channel = MagicMock(spec=disnake.DMChannel)
    message.channel.id = 555000111222333445
    message.channel.send = AsyncMock()
    message.author.id = 434343434343434343

    home = MagicMock()
    home.get_member.return_value = MagicMock()  # свой
    monkeypatch.setattr(bot_module.bot, "get_guild", MagicMock(return_value=home))

    await bot_module.on_message(message)

    message.channel.send.assert_awaited_once()


async def test_dm_digit_hint_is_capped_even_for_our_own_player(monkeypatch):
    """Свой — не значит безлимитный: ветка делит общий гостевой потолок."""
    import bot.bot as bot_module

    monkeypatch.setattr(bot_module.asyncio, "sleep", AsyncMock())
    home = MagicMock()
    home.get_member.return_value = MagicMock()
    monkeypatch.setattr(bot_module.bot, "get_guild", MagicMock(return_value=home))

    sent = 0
    for _ in range(12):
        message = _dm("1234")
        message.channel = MagicMock(spec=disnake.DMChannel)
        message.channel.id = 555000111222333446
        message.channel.send = AsyncMock()
        message.author.id = 444444444444444444  # один и тот же человек
        await bot_module.on_message(message)
        sent += message.channel.send.await_count

    assert sent == 5, f"потолок не сработал: ушло {sent} сообщений из 12"


async def test_dm_digit_hint_stays_silent_when_the_guild_is_not_cached(monkeypatch):
    """Кэш не доехал → молчим. Цена ошибки — потерянная подсказка, не открытый канал."""
    import bot.bot as bot_module

    monkeypatch.setattr(bot_module.asyncio, "sleep", AsyncMock())
    message = _dm("1234")
    message.channel = MagicMock(spec=disnake.DMChannel)
    message.channel.id = 555000111222333447
    message.channel.send = AsyncMock()
    message.author.id = 454545454545454545

    monkeypatch.setattr(bot_module.bot, "get_guild", MagicMock(return_value=None))

    await bot_module.on_message(message)

    message.channel.send.assert_not_awaited()


