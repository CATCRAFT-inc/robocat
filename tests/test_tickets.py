"""Тесты закрытия тикетов: /done, /decline и ЛС автору.

Регрессии, ради которых файл заведён:
- issue #42: `disnake.Colour.yellow` БЕЗ скобок — это bound method, а не цвет.
  `Embed` бросает на нём `TypeError`, который `except disnake.HTTPException`
  не ловит; тег «Исполнено» уже проставлен, а интеракция навсегда остаётся
  в состоянии «Робокотик думает...». Проверяем, что подтверждение доходит.
- issue #46: тред тикета удаляется при закрытии, поэтому в ЛС обязано быть
  ИМЯ тикета — иначе игрок с пачкой репортов не понимает, какой закрыли.
"""

from unittest.mock import AsyncMock, MagicMock

import disnake
import pytest

from bot.discord_config import Channels, Roles
from bot.handlers.tickets import engine as engine_module
from bot.handlers.tickets.engine import TicketEngine
from bot.utils import component_text


def _thread(parent_id: int, *, name="Ворота на спавне не открываются"):
    thread = MagicMock(spec=disnake.Thread)
    thread.id = 4242
    thread.name = name
    thread.parent_id = parent_id
    thread.applied_tags = []
    thread.add_tags = AsyncMock()
    thread.remove_tags = AsyncMock()
    thread.send = AsyncMock()
    thread.delete = AsyncMock()
    forum = MagicMock()
    forum.id = parent_id
    forum.get_tag_by_name = MagicMock(side_effect=lambda title: MagicMock(name=title))
    thread.parent = forum
    thread.owner = MagicMock()
    thread.owner.mention = "<@1>"
    return thread


def _interaction(thread):
    inter = MagicMock()
    inter.channel = thread
    inter.author.id = 777
    inter.author.mention = "<@777>"
    inter.response.defer = AsyncMock()
    inter.send = AsyncMock()
    inter.edit_original_response = AsyncMock()
    return inter


def _sent_texts(mock_send) -> str:
    """Весь текст, ушедший в send — из embed'ов и из Components V2."""
    parts = []
    for call in mock_send.await_args_list:
        embed = call.kwargs.get("embed")
        if embed is not None:
            parts.append(str(embed.title or ""))
        components = call.kwargs.get("components")
        if components is not None:
            # component_text уже умеет обходить дерево и read-side, и ui-классов.
            parts.append(
                component_text(components if isinstance(components, list) else [components])
            )
    return "\n".join(parts)


# --- issue #42: команда обязана ответить, а не зависнуть на «думает» ---------


@pytest.mark.parametrize(
    "forum_id, expected",
    [
        (Channels.requests, "Запрос отмечен"),
        (Channels.ideas, "Идея отмечена"),
    ],
)
async def test_done_confirms_to_admin_after_tagging(forum_id, expected):
    """Подтверждение обязано СНЯТЬ «Робокотик думает...», а не встать рядом.

    🛑 Проверяем `edit_original_response`, а не `inter.send`. После `defer`
    `inter.send` уходит в followup: сообщение появляется, плейсхолдер остаётся
    висеть — то есть прежняя версия теста зеленила ровно симптом issue #42
    (поймало мульти-ревью).
    """
    cog = TicketEngine(MagicMock())
    thread = _thread(forum_id)
    inter = _interaction(thread)

    await TicketEngine.doneCommand.callback(cog, inter)

    thread.add_tags.assert_awaited_once()
    thread.send.assert_awaited_once()
    inter.send.assert_not_awaited()
    confirmations = [c.args[0] for c in inter.edit_original_response.await_args_list if c.args]
    assert any(expected in text for text in confirmations), confirmations


@pytest.mark.parametrize(
    "forum_id, expected",
    [
        (Channels.requests, "Запрос отклонён"),
        (Channels.ideas, "Идея отклонена"),
    ],
)
async def test_decline_confirms_to_admin_after_tagging(forum_id, expected):
    cog = TicketEngine(MagicMock())
    thread = _thread(forum_id)
    inter = _interaction(thread)

    await TicketEngine.declineCommand.callback(cog, inter)

    thread.add_tags.assert_awaited_once()
    inter.send.assert_not_awaited()
    confirmations = [c.args[0] for c in inter.edit_original_response.await_args_list if c.args]
    assert any(expected in text for text in confirmations), confirmations


def test_no_branch_answers_with_send_after_defer():
    """🛑 Тринадцать веток — тринадцать шансов вернуть issue #42.

    Поведенческие тесты закрывают happy-path у Идей и Запросов; мутация
    `edit_original_response` → `inter.send` в любой ветке-ошибке («тег не
    найден», «нужен комментарий», `case _`) не роняла НИ ОДНОГО теста. После
    `defer` любой `inter.send` уходит во followup: сообщение появляется, а
    «Робокотик думает...» продолжает висеть рядом.

    Здесь сторож на сам идиом, а не на каждую ветку: он дешевле тринадцати
    тестов и не даст завести четырнадцатую ветку по старому образцу.
    """
    import inspect

    for command in (TicketEngine.doneCommand, TicketEngine.declineCommand):
        source = inspect.getsource(command.callback)
        assert "defer(" in source, "тест смотрит не туда — defer из команды исчез"
        after_defer = source[source.index("defer("):]
        # 🛑 Оба способа, а не один. `inter.followup.send(` не содержит подстроки
        # `inter.send(`, поэтому прежний сторож пропускал его насквозь — а
        # поведение то же самое: сообщение появляется, плейсхолдер висит.
        # Мутация именно этой формы проходила незамеченной (нашёл контрольный
        # угол ревью).
        for forbidden in ("inter.send(", "followup.send"):
            assert forbidden not in after_defer, (
                f"{command.callback.__name__}: после defer отвечать надо "
                f"edit_original_response, а не {forbidden}"
            )


async def test_done_embed_keeps_the_intended_yellow():
    """Прямая причина #42 — `Colour.yellow` БЕЗ скобок.

    🛑 Сравнение именно с жёлтым, а не `isinstance(..., Colour)`: после того как
    `_as_colour` научился глотать любой вход, проверка на тип стала тавтологией
    и зеленела при возврате бага (поймало мульти-ревью 10.08.2026). Регресс
    теперь виден как подмена цвета на дефолтный.
    """
    cog = TicketEngine(MagicMock())
    thread = _thread(Channels.requests)
    inter = _interaction(thread)

    await TicketEngine.doneCommand.callback(cog, inter, comment="починили")

    embed = thread.send.await_args.kwargs["embed"]
    assert embed.colour == disnake.Colour.yellow()


# --- issue #46: в ЛС видно, КАКОЙ тикет закрыли -----------------------------


@pytest.fixture
def closing(monkeypatch):
    """Общая обвязка закрытия: архив, флаги и индекс багов замокан."""
    monkeypatch.setattr(engine_module, "archive_ticket", AsyncMock(return_value=""))
    monkeypatch.setattr(engine_module, "remove_bug_from_index", AsyncMock())
    monkeypatch.setattr(engine_module.flags, "removeFlag", AsyncMock())
    monkeypatch.setattr(
        engine_module.flags, "getFlag", AsyncMock(return_value=MagicMock(value=1))
    )
    member = MagicMock()
    member.send = AsyncMock()
    return member


@pytest.mark.parametrize("parent_id", [Channels.bugs, Channels.support])
async def test_finish_ticket_dm_names_the_ticket(closing, parent_id):
    cog = TicketEngine(MagicMock())
    thread = _thread(parent_id, name="Лаги у портала в аду")
    inter = _interaction(thread)
    inter.guild.get_member.return_value = closing

    await cog._finishTicketInner(inter, thread, "поправили")

    assert "Лаги у портала в аду" in _sent_texts(closing.send)


async def test_ticket_name_cannot_smuggle_markdown_into_the_dm(closing):
    """🛑 Имя тикета — первые слова текста, который набрал ИГРОК.

    В имени треда разметка инертна, в теле сообщения — нет: описание,
    начинающееся с `##`, разваливает заголовок, а `[клик](…)` становится
    ссылкой от имени бота. Нашли два угла ревью независимо.
    """
    cog = TicketEngine(MagicMock())
    thread = _thread(Channels.bugs, name="**жирный** [клик](http://evil.example) `код`")
    inter = _interaction(thread)
    inter.guild.get_member.return_value = closing

    await cog._finishTicketInner(inter, thread, "поправили")

    sent = _sent_texts(closing.send)
    # Активная разметка обезврежена: жирный не жирный, ссылка не ссылка.
    # Проверяем именно ЭКРАНИРОВАНИЕ: голая подстрока `[клик](` содержится и в
    # уже безопасном `\[клик](`, поэтому проверка «нет подстроки» тут лжёт.
    assert r"\*\*жирный\*\*" in sent, sent
    assert r"\[клик]" in sent, sent
    assert r"\`код\`" in sent, sent


# --- выжимка автору бага (и её границы) -------------------------------------


@pytest.fixture
def closing_with_summary(monkeypatch, closing):
    """Та же обвязка, но архив вернул настоящую выжимку."""
    monkeypatch.setattr(
        engine_module,
        "archive_ticket",
        AsyncMock(return_value="Игрок не мог открыть ворота, поправили редстоун."),
    )
    return closing


async def test_bug_close_dm_carries_the_ai_summary(closing_with_summary):
    cog = TicketEngine(MagicMock())
    thread = _thread(Channels.bugs)
    inter = _interaction(thread)
    inter.guild.get_member.return_value = closing_with_summary

    await cog._finishTicketInner(inter, thread, "поправили")

    assert "поправили редстоун" in _sent_texts(closing_with_summary.send)


async def test_summary_cannot_smuggle_markdown_into_the_dm(monkeypatch, closing):
    """🛑 Выжимка построена по тексту треда, а писать в тред бага может ЛЮБОЙ.

    Значит через неё в ЛС автору уезжает ссылка от имени бота. Экранируем ровно
    как имя тикета (`_safe_name`) — тот же класс, третий call-site.
    """
    monkeypatch.setattr(
        engine_module,
        "archive_ticket",
        AsyncMock(return_value="Чинили: [клик](http://evil.example) и **жирный**"),
    )
    cog = TicketEngine(MagicMock())
    thread = _thread(Channels.bugs)
    inter = _interaction(thread)
    inter.guild.get_member.return_value = closing

    await cog._finishTicketInner(inter, thread, "поправили")

    sent = _sent_texts(closing.send)
    # Проверяем ЭКРАНИРОВАНИЕ, а не отсутствие подстроки: голое `[клик](`
    # содержится и в уже безопасном `\[клик](`, поэтому «нет подстроки» тут лжёт.
    assert r"\[клик]" in sent, sent
    assert r"\*\*жирный\*\*" in sent, sent


async def test_admin_ticket_close_dm_has_no_summary(closing_with_summary):
    """🛑 Тред админ-тикета — внутреннее обсуждение: наказания, чужие игроки.

    Пересказать его автору — утечка, а не забота. Выжимка уходит ТОЛЬКО
    по багам, где переписка про сам баг.
    """
    cog = TicketEngine(MagicMock())
    thread = _thread(Channels.support)
    inter = _interaction(thread)
    inter.guild.get_member.return_value = closing_with_summary

    await cog._finishTicketInner(inter, thread, "разобрались")

    assert "редстоун" not in _sent_texts(closing_with_summary.send)


async def test_player_dm_never_carries_the_transcript_file(closing_with_summary):
    """Транскрипт остаётся в админском логе. В ЛС — только выжимка."""
    cog = TicketEngine(MagicMock())
    thread = _thread(Channels.bugs)
    inter = _interaction(thread)
    inter.guild.get_member.return_value = closing_with_summary

    await cog._finishTicketInner(inter, thread, "поправили")

    for call in closing_with_summary.send.await_args_list:
        assert "file" not in call.kwargs, call.kwargs
        assert "files" not in call.kwargs, call.kwargs


@pytest.mark.parametrize(
    "method, arg",
    [("_finishTicketInner", "поправили"), ("_rejectTicketInner", "не баг")],
)
@pytest.mark.parametrize("parent_id", [Channels.bugs, Channels.support])
async def test_ticket_dm_starts_with_a_heading(closing, parent_id, method, arg):
    """Первая строка ЛС обязана быть заголовком `##`, как в приветствии бага.

    Без `##` контейнер печатает её обычным текстом, и сообщение о закрытии
    выглядит куском абзаца.
    """
    cog = TicketEngine(MagicMock())
    thread = _thread(parent_id)
    inter = _interaction(thread)
    inter.guild.get_member.return_value = closing

    await getattr(cog, method)(inter, thread, arg)

    assert _sent_texts(closing.send).lstrip().startswith("## ")


async def test_empty_summary_is_not_read_as_a_failed_archive(closing):
    """🛑 Сторож формы проверки: `archive_ticket` теперь возвращает СТРОКУ.

    Удавшийся архив без AI даёт пустую строку. Проверка «если не архив» вместо
    «если None» прочитала бы её как сбой: игрок не получил бы ЛС, а тред остался
    бы висеть навсегда — и ни один прежний тест этого не заметил бы.
    """
    cog = TicketEngine(MagicMock())
    thread = _thread(Channels.bugs)
    inter = _interaction(thread)
    inter.guild.get_member.return_value = closing

    await cog._finishTicketInner(inter, thread, "поправили")

    closing.send.assert_awaited_once()
    thread.delete.assert_awaited_once()


@pytest.mark.parametrize("parent_id", [Channels.bugs, Channels.support])
async def test_reject_ticket_dm_names_the_ticket(closing, parent_id):
    cog = TicketEngine(MagicMock())
    thread = _thread(parent_id, name="Хочу летать в спавне")
    inter = _interaction(thread)
    inter.guild.get_member.return_value = closing

    await cog._rejectTicketInner(inter, thread, "не баг")

    assert "Хочу летать в спавне" in _sent_texts(closing.send)


# --- модалки обязаны отвечать при падении (мульти-ревью 10.08.2026) ---------


def _boom_modal():
    from bot.utils import AnsweringModal

    class Boom(AnsweringModal):
        def __init__(self):
            super().__init__(
                title="тест",
                components=[disnake.ui.TextInput(label="поле", custom_id="c")],
            )

    return Boom()


def _inter(*, done=True, shown=False):
    """`shown=False` — висит пустой defer; `shown=True` — команда уже ответила."""
    inter = MagicMock()
    inter.response.is_done.return_value = done
    original = MagicMock()
    original.content = "Баг-репорт создан! Перейди в него" if shown else ""
    original.embeds = []
    original.components = []
    inter.original_response = AsyncMock(return_value=original)
    inter.edit_original_response = AsyncMock()
    inter.followup.send = AsyncMock()
    inter.response.send_message = AsyncMock()
    return inter


async def test_modal_edits_deferred_placeholder_when_callback_fails():
    """Пустой defer — единственный случай, когда ответ можно переписать.

    Дефолтный `Modal.on_error` печатает трейсбек в stderr, а логгер проекта
    пишет только в файл — падение не видел ни игрок, ни лог, и «Робокотик
    думает...» висел вечно.
    """
    inter = _inter(shown=False)

    await _boom_modal().on_error(RuntimeError("бум"), inter)

    inter.edit_original_response.assert_awaited_once()
    said = inter.edit_original_response.await_args.kwargs["content"]
    # Плейсхолдер снят — и текст просит ПРОВЕРИТЬ, а не повторить: модалка
    # успевает создать тред до сбоя следующего шага, и «попробуй ещё раз»
    # плодит дубли.
    assert "проверь результат" in said.lower(), said


async def test_modal_answers_when_nothing_was_deferred():
    inter = _inter(done=False)

    await _boom_modal().on_error(RuntimeError("бум"), inter)

    inter.response.send_message.assert_awaited_once()
    assert inter.response.send_message.await_args.kwargs["ephemeral"] is True


async def test_modal_error_handler_survives_dead_interaction():
    """Канал тикета удаляют раньше, чем приходит ответ.

    🛑 `Modal._scheduled_task` зовёт `on_error` ВНЕ try: исключение отсюда
    умирает в брошенной asyncio-таске — ни ответа человеку, ни строки в логе.
    Поэтому ловим широко, а не только Discord-ошибки.
    """
    inter = _inter(shown=False)
    inter.edit_original_response = AsyncMock(side_effect=disnake.NotFound(MagicMock(), "gone"))

    await _boom_modal().on_error(RuntimeError("бум"), inter)  # не бросает

    # И даже совсем не-Discord поломка не должна пробиться наружу.
    inter.original_response = AsyncMock(side_effect=OSError("сеть легла"))
    inter.followup.send = AsyncMock(side_effect=OSError("сеть легла"))
    await _boom_modal().on_error(RuntimeError("бум"), inter)


def test_every_modal_in_the_project_answers_on_failure():
    """Новая модалка, забывшая базу, обязана уронить этот тест.

    Прямой наследник `disnake.ui.Modal` из пакета `bot.` = модалка, чьё падение
    снова будет невидимым и для игрока, и для лога.
    """
    from bot.utils import AnsweringModal
    from project_scan import descendants, import_all_bot_modules

    # 🛑 Обход по файловой системе, а не `pkgutil.walk_packages`: тот пропускает
    # каталог без `__init__.py` молча, а таких в проекте три из шести — сторож
    # осматривал половину дерева и выглядел исчерпывающим (нашло ревью).
    import_all_bot_modules()

    # Транзитивно: модалка-наследник другой модалки проекта — тоже модалка.
    project_modals = [
        cls for cls in descendants(disnake.ui.Modal)
        if cls.__module__.startswith("bot.") and cls is not AnsweringModal
    ]
    assert project_modals, "обход не нашёл ни одной модалки — тест сломан, а не код"

    forgotten = [
        cls.__qualname__ for cls in project_modals if not issubclass(cls, AnsweringModal)
    ]

    assert not forgotten, f"эти модалки не наследуют AnsweringModal: {forgotten}"

    # 🛑 Наследование — не поведение. Модалка может унаследовать базу и тут же
    # переопределить `on_error` пустым телом: `issubclass` пройден, ответ
    # игроку потерян, тест зелёный (нашла мутация ревью). Проверяем, что до
    # человека доедет именно рабочая реализация.
    overridden = [
        cls.__qualname__
        for cls in project_modals
        if cls.on_error is not AnsweringModal.on_error
    ]
    assert not overridden, f"эти модалки перекрыли on_error своим: {overridden}"


async def test_modal_failure_leaves_a_line_in_the_log(caplog):
    """🛑 Вторая половина обещания: человек получил ответ И админ узнал.

    Удаление `logger.error` не роняло ни одного теста — то есть «падение видно
    в логе» держалось на честном слове (нашла мутация ревью). А видно оно должно
    быть именно там: дефолт disnake печатает трейсбек в stderr, куда логгер
    проекта не пишет, и на VDS это уходит в никуда.
    """
    import logging

    inter = _inter(shown=False)

    with caplog.at_level(logging.DEBUG, logger="robocat.utils"):
        await _boom_modal().on_error(RuntimeError("бум"), inter)

    errors = [
        r for r in caplog.records
        if r.levelno >= logging.ERROR and r.name == "robocat.utils"
    ]
    assert errors, [r.getMessage() for r in caplog.records]
    assert errors[0].exc_info is not None, "трейсбек не приложен — чинить будет нечего"


def test_modal_callbacks_defer_privately():
    """🛑 Плейсхолдер `modal_submit` по умолчанию ПУБЛИЧЕН.

    В disnake `defer()` у модалки — это `with_message=True`, то есть отложенное
    сообщение В КАНАЛ. А `answer_broken_interaction` при падении редактирует
    именно его. Реальный путь: админ жмёт «Завершить», тикет уже закрыт и ЛС
    игроку ушло, `thread.delete()` падает — и игрок читает у себя в треде «😿 Не
    получилось… Попробуй ещё раз». Два противоречащих ответа за пять секунд, и
    второй прямо предлагает завести дубль (нашёл угол конечного пользователя).
    """
    import inspect

    from bot.utils import AnsweringModal
    from project_scan import descendants, import_all_bot_modules

    import_all_bot_modules()

    import re

    public = []
    for cls in descendants(AnsweringModal):
        if not cls.__module__.startswith("bot."):
            continue
        source = inspect.getsource(cls.callback)
        # 🛑 Смотрим на САМ вызов `defer(...)`, а не на весь текст колбэка.
        # Прежняя форма («есть где-нибудь `ephemeral=True`») гасилась любым
        # вторым эфемерным ответом ниже по телу — а это обычная эволюция кода:
        # публичный defer плюс явное `followup.send(..., ephemeral=True)`.
        # Обе такие мутации проходили незамеченными (нашёл контрольный угол).
        for call in re.findall(r"\.defer\(([^)]*)\)", source):
            if "ephemeral=True" not in call:
                public.append(f"{cls.__module__}.{cls.__qualname__}")

    assert not public, f"эти модалки откладывают ответ публично: {public}"


async def test_archive_log_also_escapes_the_ticket_name(monkeypatch):
    """Третий call-site того же класса: лог тикета в админском канале.

    Веер мутировал ТЕЛО `_safe_name`, а не каждый её вызов — и подмена
    `{_safe_name(thread)}` на `{thread.name}` здесь не роняла ничего (нашёл
    контрольный угол ревью). Разметка в админском канале активна так же.
    """
    channel = MagicMock()
    channel.send = AsyncMock()
    bot = MagicMock()
    bot.get_channel.return_value = channel
    monkeypatch.setattr(engine_module.flags, "getFlag", AsyncMock(return_value=None))

    thread = _thread(Channels.bugs, name="**жирный** [клик](http://evil.example)")
    thread.history = MagicMock(return_value=_aiter([]))

    # 🛑 БЕЗ фикстуры `closing`: она подменяет `archive_ticket` моком, и тест
    # проверял бы отсутствующий код — зеленел бы всегда.
    ok = await engine_module.archive_ticket(
        bot, thread, ticket_type="Баг", closer=MagicMock(), note=None
    )
    # 🛑 `is not None`, а не «истинность»: успех теперь возвращает выжимку, а без
    # живого AI она пустая — `assert ok` объявил бы удавшийся архив провалом.
    assert ok is not None, "архив не состоялся — тест не наблюдал бы ничего"
    assert channel.send.await_count == 1, "лог не отправлен — наблюдать нечего"

    sent = _sent_texts(channel.send)
    assert r"\*\*жирный\*\*" in sent, sent
    assert r"\[клик]" in sent, sent


async def _aiter(items):
    for item in items:
        yield item


async def test_error_handler_that_fails_itself_still_leaves_a_trace(caplog):
    """Последняя линия: если и ответ не ушёл, должна остаться хотя бы строка.

    Мутация «убрать `logger.exception`» не роняла ничего (нашёл контрольный
    угол ревью), а без неё сбой обработчика ошибок не оставляет ВООБЩЕ ничего:
    `on_error` живёт в брошенной asyncio-таске, и её исключение никуда не идёт.
    """
    import logging

    inter = _inter(shown=True)
    inter.edit_original_response = AsyncMock(side_effect=OSError("сеть легла"))

    with caplog.at_level(logging.DEBUG, logger="robocat.utils"):
        await _boom_modal().on_error(RuntimeError("бум"), inter)  # не бросает

    # 🛑 Ищем ИМЕННО эту строку, а не «любой ERROR от robocat.utils»: соседний
    # `logger.error("Модалка %s упала")` пишется тем же логгером и удовлетворял
    # проверке даже с удалённой последней линией (поймано веером мутаций).
    assert [
        r for r in caplog.records if "сообщить о сбое" in r.getMessage()
    ], [r.getMessage() for r in caplog.records]


async def test_news_request_decision_log_escapes_the_request_name(monkeypatch):
    """Четвёртый call-site того же класса — чат репортёров.

    Имя треда заявки собирается из первых слов текста, который набрал репортёр,
    ровно как у багов. В `engine.py` экранирование есть, сюда развёртка по
    классу не доехала (нашёл контрольный угол ревью).
    """
    from bot.handlers.tickets import news_request as nr

    channel = MagicMock()
    channel.send = AsyncMock()
    cog = nr.NewsRequestHandler.__new__(nr.NewsRequestHandler)
    cog.bot = MagicMock()
    cog.bot.get_channel.return_value = channel

    thread = MagicMock()
    thread.id = 77
    thread.name = "**жирный** [клик](http://evil.example)"

    await cog._notifyReportersChat(
        thread, MagicMock(), author_id=1, note=None, published=True
    )

    assert channel.send.await_count == 1, "лог не отправлен — наблюдать нечего"
    sent = _sent_texts(channel.send)
    assert r"\*\*жирный\*\*" in sent, sent
    assert r"\[клик]" in sent, sent


def _button_interaction(thread, *role_ids: int, custom_id="TICKET_DONE"):
    inter = MagicMock()
    inter.component.custom_id = custom_id
    inter.channel = thread
    inter.author.roles = [MagicMock(id=role_id) for role_id in role_ids]
    inter.response.send_message = AsyncMock()
    inter.response.send_modal = AsyncMock()
    return inter


@pytest.mark.parametrize("role_id", [Roles.admin, Roles.st_admin, Roles.moderator])
@pytest.mark.parametrize("parent_id", [Channels.bugs, Channels.support])
@pytest.mark.parametrize("custom_id", ["TICKET_DONE", "TICKET_DECLINE"])
async def test_ticket_buttons_open_the_modal_for_staff(role_id, parent_id, custom_id):
    cog = TicketEngine.__new__(TicketEngine)
    inter = _button_interaction(_thread(parent_id), role_id, custom_id=custom_id)

    await cog.ticketButtons(inter)

    assert inter.response.send_modal.await_count == 1
    assert inter.response.send_message.await_count == 0


@pytest.mark.parametrize("parent_id", [Channels.bugs, Channels.support])
@pytest.mark.parametrize("custom_id", ["TICKET_DONE", "TICKET_DECLINE"])
async def test_ticket_buttons_refuse_a_player_without_roles(parent_id, custom_id):
    """Обратная половина: расширение списка ролей не должно пускать всех подряд.

    `Roles.reporter` — роль без отношения к тикетам; вместе с пустым набором
    ролей она ловит замену проверки на «есть хоть какая-нибудь роль».
    """
    cog = TicketEngine.__new__(TicketEngine)
    inter = _button_interaction(_thread(parent_id), Roles.reporter, custom_id=custom_id)

    await cog.ticketButtons(inter)

    assert inter.response.send_modal.await_count == 0
    assert inter.response.send_message.await_count == 1
