"""Тесты редактора новостей (issue #4): контейнер, парсинг, черновики, публикация.

Discord мокается целиком; контейнер собирается и разбирается на ui-классах
(в проде message.components отдаёт read-side классы с теми же атрибутами
children/content/items — парсер принимает оба).
"""

import asyncio
import datetime
import time
from unittest.mock import AsyncMock, MagicMock

import disnake
import pytest

from bot.handlers.news_editor import (
    _MAX_BLOCKS,
    _PREVIEW_OVERHEAD,
    NewsEditor,
    NewsModal,
    _body_blocks,
    _Draft,
    _news_container,
    _parse_container,
    _parse_publish_at,
)
from bot.news_schedule import ScheduledNews
from bot.storage import Channels, Roles


def _draft(**kw):
    base = dict(author_id=1, channel_id=Channels.announcements,
                title="Обнова", text="Завезли контент")
    base.update(kw)
    return _Draft(**base)


def _cog():
    inst = object.__new__(NewsEditor)
    inst.bot = MagicMock()
    inst.logger = MagicMock()
    inst.drafts = {}
    inst.schedule_store = MagicMock()
    inst.schedule_store.add = AsyncMock(return_value=17)
    inst.schedule_store.claim_due = AsyncMock(return_value=None)
    inst.schedule_store.complete = AsyncMock()
    inst._schedule_retry_at = 0.0
    inst._schedule_retry_delay = 60.0
    import itertools
    inst._seq = itertools.count(1)
    return inst


# --- время отложенной публикации ---

def test_parse_publish_at_treats_input_as_moscow_time():
    now = datetime.datetime(2026, 8, 31, 12, 0, tzinfo=datetime.UTC)

    timestamp = _parse_publish_at("31.08.2026 16:30", now=now)

    assert datetime.datetime.fromtimestamp(timestamp, tz=datetime.UTC) == datetime.datetime(
        2026, 8, 31, 13, 30, tzinfo=datetime.UTC
    )


@pytest.mark.parametrize("raw", ["2026-08-31 18:30", "31.08.26 18:30", "завтра"])
def test_parse_publish_at_rejects_ambiguous_format(raw):
    now = datetime.datetime(
        2026, 8, 31, 12, 0,
        tzinfo=datetime.timezone(datetime.timedelta(hours=3)),
    )

    with pytest.raises(ValueError, match="31.08.2026 18:30"):
        _parse_publish_at(raw, now=now)


def test_parse_publish_at_rejects_past_moscow_time():
    now = datetime.datetime(2026, 8, 31, 18, 31, tzinfo=datetime.UTC)

    with pytest.raises(ValueError, match="уже прошло"):
        _parse_publish_at("31.08.2026 18:30", now=now)


def test_news_has_no_schedule_slash_option():
    assert [option.name for option in NewsEditor.news.body.options] == ["channel"]


# --- контейнер ---

def test_news_container_minimal_structure():
    container = _news_container(_draft())
    children = list(container.children)
    assert children[0].content == "# Обнова"
    assert isinstance(children[1], disnake.ui.Separator)
    assert children[2].content == "Завезли контент"
    assert len(children) == 3


def test_news_container_with_image_and_ping():
    container = _news_container(_draft(text="Завезли\nhttps://x/img.png", ping_role_id=Roles.events))
    children = list(container.children)
    galleries = [c for c in children if isinstance(c, disnake.ui.MediaGallery)]
    assert len(galleries) == 1
    assert children[-1].content == f"<@&{Roles.events}>"


def test_parse_container_roundtrip():
    src = _draft(ping_role_id=Roles.events,
                 text="Первая строка\nВторая строка\nhttps://x/img.png")
    parsed = _parse_container(_news_container(src))
    assert parsed is not None
    assert parsed.title == src.title
    assert parsed.text == src.text
    assert parsed.ping_role_id == src.ping_role_id


def test_parse_container_rejects_foreign():
    foreign = disnake.ui.Container(disnake.ui.TextDisplay("просто текст без заголовка"))
    assert _parse_container(foreign) is None


# --- разметка тела ---

def test_body_blocks_interleaves_text_and_images():
    blocks = _body_blocks(
        "Начало\nhttps://x/1.png\nСередина\n---\nКонец"
    )
    kinds = [type(b) for b in blocks]
    assert kinds == [
        disnake.ui.TextDisplay, disnake.ui.MediaGallery,
        disnake.ui.TextDisplay, disnake.ui.Separator, disnake.ui.TextDisplay,
    ]
    assert blocks[0].content == "Начало"
    assert blocks[2].content == "Середина"


def test_body_blocks_accepts_star_divider():
    # три звёздочки — привычная альтернатива дефисам, обе дают Separator
    assert [type(b) for b in _body_blocks("А\n***\nБ")] == [
        disnake.ui.TextDisplay, disnake.ui.Separator, disnake.ui.TextDisplay,
    ]


def test_body_blocks_groups_adjacent_images_into_one_gallery():
    # соседние ссылки — сетка в одной галерее; пустая строка её не рвёт
    blocks = _body_blocks("https://x/1.png\n\nhttps://x/2.png")
    assert len(blocks) == 1
    assert len(blocks[0].items) == 2


def test_body_blocks_splits_gallery_over_ten_images():
    blocks = _body_blocks("\n".join(f"https://x/{i}.png" for i in range(12)))
    assert [len(b.items) for b in blocks] == [10, 2]


def test_body_blocks_skips_empty_paragraphs():
    # пустой TextDisplay Discord отвергает — блоков из пустоты быть не должно
    assert _body_blocks("\n\n\n") == []
    blocks = _body_blocks("\n\nТекст\n\n")
    assert len(blocks) == 1 and blocks[0].content == "Текст"


def test_body_blocks_keeps_inline_url_as_text():
    # ссылка внутри предложения — не картинка, а обычный markdown-текст
    blocks = _body_blocks("Подробности тут https://x/page — заходите")
    assert len(blocks) == 1
    assert isinstance(blocks[0], disnake.ui.TextDisplay)


def test_body_blocks_keeps_non_image_link_as_text():
    # приглашение/соцсеть отдельной строкой — кликабельный текст, а не битая плитка
    blocks = _body_blocks("Заходи\nhttps://discord.gg/catcraftmc\nhttps://t.me/catcraftmc_tg")
    assert len(blocks) == 1
    assert isinstance(blocks[0], disnake.ui.TextDisplay)


def test_body_blocks_image_link_survives_query_string():
    # у ссылок из Discord после расширения идёт подпись — картинкой быть не мешает
    blocks = _body_blocks("https://cdn.discordapp.com/a/1.PNG?ex=68&is=67&hm=ab")
    assert isinstance(blocks[0], disnake.ui.MediaGallery)


def test_body_blocks_keeps_url_with_trailing_punctuation_as_text():
    blocks = _body_blocks("https://x/1.png.")
    assert isinstance(blocks[0], disnake.ui.TextDisplay)


def test_body_blocks_does_not_split_code_block():
    # разрезанный по строкам ``` не закроется — у читателя останутся backtick'и
    blocks = _body_blocks("```\nhttps://x/1.png\n---\n```")
    assert len(blocks) == 1
    assert blocks[0].content.count("```") == 2


def test_parse_container_roundtrip_keeps_layout():
    src = _draft(text="Начало\nhttps://x/1.png\n---\nКонец", ping_role_id=Roles.events)
    parsed = _parse_container(_news_container(src))
    assert parsed is not None
    # порядок блоков переживает публикацию → «Править» → повторную сборку
    assert parsed.text == src.text
    assert _parse_container(_news_container(parsed)).text == src.text


def test_parse_container_ignores_handwritten_role_mention():
    # тело, кончающееся упоминанием роли: пинг не заказан — и не должен появиться.
    # Иначе «Править» съедало бы две строки и подставляло роль в выпадашку,
    # а следом публикация уходила бы с разрешением пинговать
    src = _draft(text=f"Текст\n---\n<@&{Roles.events}>")
    parsed = _parse_container(_news_container(src))
    assert parsed is not None
    assert parsed.ping_role_id is None
    assert parsed.text == src.text


def test_parse_container_reads_own_ping_by_id():
    src = _draft(text="Текст", ping_role_id=Roles.events)
    parsed = _parse_container(_news_container(src))
    assert parsed is not None
    assert parsed.ping_role_id == Roles.events
    assert parsed.text == "Текст"  # служебные блоки в тело не протекли


def test_parse_container_reads_legacy_post():
    # новости, опубликованные до разметки: картинка отдельным блоком снизу
    legacy = disnake.ui.Container(
        disnake.ui.TextDisplay("# Обнова"),
        disnake.ui.Separator(),
        disnake.ui.TextDisplay("Завезли контент"),
        disnake.ui.MediaGallery(disnake.MediaGalleryItem("https://x/img.png")),
    )
    parsed = _parse_container(legacy)
    assert parsed is not None
    assert parsed.text == "Завезли контент\nhttps://x/img.png"


# --- ссылка/ID сообщения ---

def test_parse_message_ref_link_overrides_channel():
    mid, cid = NewsEditor._parse_message_ref(
        "https://discord.com/channels/1/222/333", 999)
    assert (mid, cid) == (333, 222)


def test_parse_message_ref_plain_id_uses_fallback():
    assert NewsEditor._parse_message_ref("12345", 999) == (12345, 999)


def test_parse_message_ref_garbage():
    mid, _ = NewsEditor._parse_message_ref("не ссылка", 999)
    assert mid is None


async def test_news_edit_rejects_non_news_channel():
    # кривая ссылка на не-новостной канал не должна дать перезаписать чужой контейнер
    cog = _cog()
    inter = MagicMock()
    inter.response.send_message = AsyncMock()
    await NewsEditor.newsEdit.callback(
        cog, inter, message="https://discord.com/channels/1/999/333", channel="Объявления"
    )
    inter.response.send_message.assert_awaited_once()
    assert "новостном канале" in inter.response.send_message.await_args.args[0]


# --- модалка ---

def test_modal_constructs_with_prefill():
    cog = _cog()
    cog.drafts[1] = _draft(ping_role_id=Roles.events)
    modal = NewsModal(cog, 1)
    assert modal.custom_id == "news_modal:1"
    schedule = next(
        component.component
        for component in modal.components
        if isinstance(component, disnake.ui.Label)
        and component.component.custom_id == "news_publish_at"
    )
    assert schedule.required is False
    assert schedule.placeholder == "15.09.2026 18:30"


async def _run_modal(cog, text: str, *, fail_preview: bool = False):
    modal = NewsModal(cog, 1)
    inter = MagicMock()
    inter.message = None
    inter.text_values = {"news_title": "Обнова", "news_text": text}
    inter.resolved_values = {}
    inter.response.send_message = AsyncMock()
    if fail_preview:
        inter.response.send_message.side_effect = [
            disnake.HTTPException(MagicMock(status=400), "Invalid"), None,
        ]
    await modal.callback(inter)
    return inter.response.send_message.await_args.kwargs["components"]


async def test_modal_callback_rejects_oversized_post_and_keeps_text():
    # больше блоков, чем держит сообщение → понятный отказ вместо падения на публикации
    cog = _cog()
    cog.drafts[1] = _draft()

    components = await _run_modal(cog, "\n".join(f"строка {i}\n---" for i in range(30)))

    assert "Слишком много блоков" in components[0].content
    assert cog.drafts[1].text  # текст не потерян, кнопка «Править» вернёт его в модалку
    assert components[1].children[0].custom_id == "NEWS_EDIT:1"


async def test_modal_callback_counts_preview_overhead_not_just_post():
    # 36 блоков: публикуемый пост (37 компонентов) в лимит Discord влезает,
    # а превью с подписью и тремя кнопками — уже нет. Число намеренно жёсткое:
    # тест, считающий его от _MAX_BLOCKS, подстроится под сдвинутый порог и
    # перестанет ловить регрессию
    cog = _cog()
    cog.drafts[1] = _draft()

    components = await _run_modal(cog, "\n".join(["текст", "---"] * 17))

    assert len(_news_container(cog.drafts[1]).children) == 36
    assert "Слишком много блоков" in components[0].content


def test_max_blocks_leaves_room_for_preview():
    # порог считает превью (контейнер + подпись + строка из трёх кнопок), не пост
    assert _MAX_BLOCKS + _PREVIEW_OVERHEAD <= 40


async def test_modal_callback_rejects_over_text_budget():
    # 4000 символов в поле больше, чем текстовый бюджет сообщения V2
    cog = _cog()
    cog.drafts[1] = _draft()

    components = await _run_modal(cog, "я" * 3999)

    assert "Слишком много текста" in components[0].content


async def test_modal_callback_survives_rejected_preview():
    # Discord отверг превью по причине, которой нет в наших проверках:
    # интеракция всё равно должна получить ответ с выходом на «Править»
    cog = _cog()
    cog.drafts[1] = _draft()

    components = await _run_modal(cog, "Нормальный текст", fail_preview=True)

    assert "Править" in components[0].content
    assert cog.drafts[1].text


async def test_modal_callback_updates_draft_and_sends_preview():
    cog = _cog()
    cog.drafts[1] = _draft(title="", text="")
    modal = NewsModal(cog, 1)
    inter = MagicMock()
    inter.message = None
    inter.text_values = {"news_title": " Обнова ", "news_text": "Текст"}
    inter.resolved_values = {"news_ping": [str(Roles.events)]}
    inter.response.send_message = AsyncMock()

    await modal.callback(inter)

    draft = cog.drafts[1]
    assert draft.title == "Обнова"
    assert draft.ping_role_id == Roles.events
    assert draft.publish_at is None
    inter.response.send_message.assert_awaited_once()
    assert inter.response.send_message.await_args.kwargs.get("ephemeral") is True


async def test_modal_callback_sets_future_moscow_schedule():
    cog = _cog()
    cog.drafts[1] = _draft()
    modal = NewsModal(cog, 1)
    inter = MagicMock()
    inter.message = None
    inter.text_values = {
        "news_title": "Обнова",
        "news_text": "Текст",
        "news_publish_at": "31.12.2099 23:45",
    }
    inter.resolved_values = {}
    inter.response.send_message = AsyncMock()

    await modal.callback(inter)

    draft = cog.drafts[1]
    assert draft.publish_at == int(
        datetime.datetime(
            2099, 12, 31, 23, 45,
            tzinfo=datetime.timezone(datetime.timedelta(hours=3)),
        ).timestamp()
    )
    preview = inter.response.send_message.await_args.kwargs["components"]
    assert "31.12.2099 в 23:45 МСК" in preview[0].content


async def test_modal_callback_rejects_bad_schedule_and_keeps_input():
    cog = _cog()
    cog.drafts[1] = _draft()
    modal = NewsModal(cog, 1)
    inter = MagicMock()
    inter.message = None
    inter.text_values = {
        "news_title": "Обнова",
        "news_text": "Текст",
        "news_publish_at": "завтра вечером",
    }
    inter.resolved_values = {}
    inter.response.send_message = AsyncMock()

    await modal.callback(inter)

    draft = cog.drafts[1]
    assert draft.publish_at is None
    assert draft.publish_at_text == "завтра вечером"
    refusal = inter.response.send_message.await_args.kwargs["components"]
    assert "31.08.2026 18:30" in refusal[0].content

    reopened = NewsModal(cog, 1)
    schedule = next(
        component.component
        for component in reopened.components
        if isinstance(component, disnake.ui.Label)
        and component.component.custom_id == "news_publish_at"
    )
    assert schedule.value == "завтра вечером"


async def test_news_opens_modal_without_schedule_argument():
    cog = _cog()
    inter = MagicMock()
    inter.author.id = 42
    inter.response.send_modal = AsyncMock()

    await NewsEditor.news.callback(cog, inter, channel="Газета")

    draft = next(iter(cog.drafts.values()))
    assert draft.publish_at is None
    assert draft.channel_id == Channels.newspaper
    inter.response.send_modal.assert_awaited_once()


def test_scheduled_preview_names_moscow_time_and_action():
    cog = _cog()
    publish_at = int(
        datetime.datetime(
            2026, 9, 1, 18, 30,
            tzinfo=datetime.timezone(datetime.timedelta(hours=3)),
        ).timestamp()
    )

    components = cog._preview(_draft(publish_at=publish_at), 1)

    assert "01.09.2026 в 18:30 МСК" in components[0].content
    assert components[2].children[0].label == "Запланировать"


# --- кнопки и публикация ---

def _button_inter(custom_id: str, *, admin: bool = True, author_id: int = 1):
    inter = MagicMock()
    inter.component.custom_id = custom_id
    role = MagicMock()
    role.id = Roles.admin if admin else 12345
    inter.author.roles = [role]
    inter.author.id = author_id  # черновики в тестах создаются с author_id=1
    inter.response.edit_message = AsyncMock()
    inter.response.send_message = AsyncMock()
    inter.response.send_modal = AsyncMock()
    inter.edit_original_response = AsyncMock()
    return inter


async def test_buttons_ignore_foreign_custom_id():
    cog = _cog()
    inter = _button_inter("HONEYPOT_BAN:1")
    await cog.newsButtons(inter)
    inter.response.edit_message.assert_not_awaited()
    inter.response.send_message.assert_not_awaited()


async def test_buttons_reject_non_admin():
    cog = _cog()
    cog.drafts[1] = _draft()
    inter = _button_inter("NEWS_PUB:1", admin=False)
    await cog.newsButtons(inter)
    inter.response.send_message.assert_awaited_once()


async def test_lost_draft_reports_and_no_publish():
    cog = _cog()
    inter = _button_inter("NEWS_PUB:7")
    await cog.newsButtons(inter)
    inter.response.edit_message.assert_awaited_once()


async def test_buttons_unicode_digit_reports_lost_draft():
    # Unicode-цифра проходит isdigit(), но не должна ронять int().
    cog = _cog()
    inter = _button_inter("NEWS_PUB:²")

    await cog.newsButtons(inter)

    inter.response.edit_message.assert_awaited_once()


async def test_failed_publish_ack_restores_draft():
    # Упавший ack «⏳ Публикую...» не должен безвозвратно съедать черновик
    # (инцидент 26.07.2026: интеракции перехватывал второй инстанс бота).
    cog = _cog()
    draft = _draft()
    cog.drafts[1] = draft
    inter = _button_inter("NEWS_PUB:1")
    inter.response.edit_message.side_effect = disnake.HTTPException(MagicMock(status=404), "Unknown interaction")

    with pytest.raises(disnake.HTTPException):
        await cog.newsButtons(inter)

    assert cog.drafts[1] is draft


async def test_cancel_deletes_draft():
    cog = _cog()
    cog.drafts[1] = _draft()
    inter = _button_inter("NEWS_CANCEL:1")
    await cog.newsButtons(inter)
    assert 1 not in cog.drafts


async def test_publish_sends_container_reactions_and_thread(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    cog = _cog()
    cog.drafts[1] = _draft(ping_role_id=Roles.events)
    sent_msg = MagicMock()
    sent_msg.jump_url = "https://discord.com/x"
    sent_msg.add_reaction = AsyncMock()
    sent_msg.create_thread = AsyncMock()
    channel = MagicMock()
    channel.send = AsyncMock(return_value=sent_msg)
    cog.bot.get_channel.return_value = channel
    inter = _button_inter("NEWS_PUB:1")

    await cog.newsButtons(inter)

    channel.send.assert_awaited_once()
    mentions = channel.send.await_args.kwargs["allowed_mentions"]
    assert mentions.roles and mentions.roles[0].id == Roles.events
    assert sent_msg.add_reaction.await_count == 3
    sent_msg.create_thread.assert_awaited_once()
    assert 1 not in cog.drafts


async def test_second_publish_click_does_not_double_post(monkeypatch):
    # два клика «Опубликовать» по одному черновику → публикация ровно одна
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    cog = _cog()
    cog.drafts[1] = _draft(channel_id=Channels.informator)
    sent_msg = MagicMock()
    sent_msg.jump_url = "url"
    sent_msg.add_reaction = AsyncMock()
    sent_msg.create_thread = AsyncMock()
    channel = MagicMock()
    channel.send = AsyncMock(return_value=sent_msg)
    cog.bot.get_channel.return_value = channel

    await cog.newsButtons(_button_inter("NEWS_PUB:1"))
    await cog.newsButtons(_button_inter("NEWS_PUB:1"))  # повторный клик

    channel.send.assert_awaited_once()  # не два раза
    assert 1 not in cog.drafts


async def test_scheduled_publish_is_saved_without_sending_to_discord():
    cog = _cog()
    publish_at = int(
        datetime.datetime(
            2026, 9, 1, 18, 30,
            tzinfo=datetime.timezone(datetime.timedelta(hours=3)),
        ).timestamp()
    )
    cog.drafts[1] = _draft(publish_at=publish_at, ping_role_id=Roles.events)
    inter = _button_inter("NEWS_PUB:1")

    await cog.newsButtons(inter)

    cog.schedule_store.add.assert_awaited_once_with(
        channel_id=Channels.announcements,
        author_id=1,
        title="Обнова",
        body="Завезли контент",
        ping_role_id=Roles.events,
        publish_at=publish_at,
    )
    cog.bot.get_channel.assert_not_called()
    assert 1 not in cog.drafts
    result = inter.edit_original_response.await_args.kwargs["components"][0].content
    assert "01.09.2026 в 18:30 МСК" in result


async def test_scheduled_publish_storage_failure_restores_draft():
    cog = _cog()
    src = _draft(publish_at=1_800_000_000)
    cog.drafts[1] = src
    cog.schedule_store.add.side_effect = RuntimeError("db")
    inter = _button_inter("NEWS_PUB:1")

    await cog.newsButtons(inter)

    assert cog.drafts[1] is src
    components = inter.edit_original_response.await_args.kwargs["components"]
    assert any(isinstance(c, disnake.ui.ActionRow) for c in components)


async def test_scheduled_publisher_delivers_and_completes_row():
    cog = _cog()
    scheduled = ScheduledNews(
        id=7,
        channel_id=Channels.informator,
        author_id=42,
        title="По расписанию",
        body="Текст",
        ping_role_id=None,
        publish_at=1_800_000_000,
    )
    cog.schedule_store.claim_due.side_effect = [scheduled, None]
    msg = MagicMock(id=555)
    cog._deliver_news = AsyncMock(return_value=(msg, False))

    await NewsEditor.scheduledNewsPublisher.coro(cog)

    delivered = cog._deliver_news.await_args.args[0]
    assert delivered.title == "По расписанию"
    assert delivered.publish_at == scheduled.publish_at
    cog.schedule_store.complete.assert_awaited_once_with(7)


async def test_scheduled_publisher_keeps_failed_row_for_lease_retry():
    cog = _cog()
    scheduled = ScheduledNews(
        id=7,
        channel_id=Channels.informator,
        author_id=42,
        title="По расписанию",
        body="Текст",
        ping_role_id=None,
        publish_at=1_800_000_000,
    )
    cog.schedule_store.claim_due.side_effect = [scheduled, None]
    cog._deliver_news = AsyncMock(side_effect=RuntimeError("discord"))

    await NewsEditor.scheduledNewsPublisher.coro(cog)

    cog.schedule_store.complete.assert_not_awaited()
    assert cog.schedule_store.claim_due.await_count == 2


async def test_scheduled_publisher_backs_off_after_storage_failure():
    cog = _cog()
    cog.schedule_store.claim_due.side_effect = RuntimeError("db")

    await NewsEditor.scheduledNewsPublisher.coro(cog)
    await NewsEditor.scheduledNewsPublisher.coro(cog)

    cog.schedule_store.claim_due.assert_awaited_once()
    assert cog._schedule_retry_delay == 120
    assert cog._schedule_retry_at > time.monotonic()


async def test_publish_to_non_reaction_channel_skips_reactions(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    cog = _cog()
    cog.drafts[1] = _draft(channel_id=Channels.informator)
    sent_msg = MagicMock()
    sent_msg.jump_url = "url"
    sent_msg.add_reaction = AsyncMock()
    sent_msg.create_thread = AsyncMock()
    channel = MagicMock()
    channel.send = AsyncMock(return_value=sent_msg)
    cog.bot.get_channel.return_value = channel
    inter = _button_inter("NEWS_PUB:1")

    await cog.newsButtons(inter)

    sent_msg.add_reaction.assert_not_awaited()
    sent_msg.create_thread.assert_not_awaited()


async def test_edit_flow_edits_message_without_reactions(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    cog = _cog()
    cog.drafts[1] = _draft(edit_message_id=555)
    existing = MagicMock()
    existing.jump_url = "url"
    existing.edit = AsyncMock()
    channel = MagicMock()
    channel.fetch_message = AsyncMock(return_value=existing)
    channel.send = AsyncMock()
    cog.bot.get_channel.return_value = channel
    inter = _button_inter("NEWS_PUB:1")

    await cog.newsButtons(inter)

    existing.edit.assert_awaited_once()
    channel.send.assert_not_awaited()


# --- восстановление черновика и чужие кнопки ---


async def test_foreign_draft_button_denied():
    # коллизия draft_id после рестарта: чужая кнопка не трогает черновик
    cog = _cog()
    cog.drafts[1] = _draft(author_id=999)
    inter = _button_inter("NEWS_PUB:1", author_id=1)

    await cog.newsButtons(inter)

    assert 1 in cog.drafts  # черновик цел
    inter.response.send_message.assert_awaited_once()
    assert "не твой" in inter.response.send_message.await_args.args[0]


async def test_publish_failure_restores_draft(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    cog = _cog()
    src = _draft()
    cog.drafts[1] = src
    channel = MagicMock()
    channel.send = AsyncMock(side_effect=disnake.HTTPException(MagicMock(status=500), "boom"))
    cog.bot.get_channel.return_value = channel
    inter = _button_inter("NEWS_PUB:1")

    await cog.newsButtons(inter)

    # текст не потерян: черновик вернулся, превью с кнопками перерисовано
    assert cog.drafts.get(1) is src
    inter.edit_original_response.assert_awaited_once()
    components = inter.edit_original_response.await_args.kwargs["components"]
    assert any(isinstance(c, disnake.ui.ActionRow) for c in components)


async def test_publish_channel_missing_restores_draft():
    cog = _cog()
    src = _draft()
    cog.drafts[1] = src
    cog.bot.get_channel.return_value = None
    inter = _button_inter("NEWS_PUB:1")

    await cog.newsButtons(inter)

    assert cog.drafts.get(1) is src
    components = inter.response.edit_message.await_args.kwargs["components"]
    assert any(isinstance(c, disnake.ui.ActionRow) for c in components)


async def test_edit_passes_allowed_mentions(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    cog = _cog()
    cog.drafts[1] = _draft(edit_message_id=555, ping_role_id=Roles.events)
    existing = MagicMock()
    existing.jump_url = "url"
    existing.edit = AsyncMock()
    channel = MagicMock()
    channel.fetch_message = AsyncMock(return_value=existing)
    cog.bot.get_channel.return_value = channel

    await cog.newsButtons(_button_inter("NEWS_PUB:1"))

    assert "allowed_mentions" in existing.edit.await_args.kwargs
