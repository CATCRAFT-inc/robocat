"""Редактор вакансий: Container, кэш, публикация и приватные отклики."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import disnake
import pytest

from bot.discord_config import Channels, Roles
import bot.handlers.get_a_job as jobs


def _vacancy(**overrides):
    values = {
        "id": "vacancy-1",
        "title": "Пивовар",
        "description": "Варить напитки для таверны.",
        "requirements": "Знать рецепты.",
        "tasks": "Следить за запасами.",
        "payment": "100 мурок за смену.",
        "updated_at": 1_700_000_000,
    }
    values.update(overrides)
    return jobs._Vacancy(**values)


def test_vacancy_container_renders_five_sections_and_apply_state():
    published = jobs._vacancy_container(_vacancy(), apply_enabled=True)
    preview = jobs._vacancy_container(_vacancy(), apply_enabled=False)

    text = "\n".join(
        child.content for child in published.children if hasattr(child, "content")
    )
    assert "# Пивовар" in text
    assert "Варить напитки для таверны." in text
    assert "## Требования\nЗнать рецепты." in text
    assert "## Задачи\nСледить за запасами." in text
    assert "## Оплата\n100 мурок за смену." in text

    published_button = published.children[-1].children[0]
    preview_button = preview.children[-1].children[0]
    assert published_button.label == "Откликнуться"
    assert published_button.custom_id == "VACANCY_APPLY"
    assert published_button.disabled is False
    assert preview_button.disabled is True


def _draft(**overrides):
    values = {
        "author_id": 1,
        "title": "Пивовар",
        "description": "Варить напитки для таверны.",
        "requirements": "Знать рецепты.",
        "tasks": "Следить за запасами.",
        "payment": "100 мурок за смену.",
        "template_id": None,
    }
    values.update(overrides)
    return jobs._Draft(**values)


def _cog():
    bot = MagicMock()
    return jobs.GetAJob(bot)


def _interaction(custom_id: str, *, admin: bool = True):
    inter = MagicMock()
    inter.component.custom_id = custom_id
    inter.author.id = 1
    inter.author.display_name = "Барсик"
    inter.author.mention = "<@1>"
    role = MagicMock()
    role.id = Roles.admin if admin else 12345
    inter.author.roles = [role]
    inter.response.send_message = AsyncMock()
    inter.response.send_modal = AsyncMock()
    inter.response.edit_message = AsyncMock()
    inter.response.defer = AsyncMock()
    inter.edit_original_response = AsyncMock()
    inter.send = AsyncMock()
    return inter


def test_vacancy_cache_roundtrip_and_corruption_is_not_empty_cache():
    encoded = jobs._encode_vacancies([_vacancy()])

    assert jobs._decode_vacancies(encoded) == [_vacancy()]
    with pytest.raises(ValueError, match="кэш вакансий"):
        jobs._decode_vacancies("not-json")


async def test_vacancy_without_argument_opens_new_modal():
    cog = _cog()
    inter = _interaction("")

    await jobs.GetAJob.vacancy.callback(cog, inter, template_id=None)

    inter.response.send_modal.assert_awaited_once()
    modal = inter.response.send_modal.await_args.args[0]
    assert isinstance(modal, jobs.VacancyModal)
    assert cog.drafts[modal.draft_id].title == ""


async def test_vacancy_with_cached_argument_opens_preview():
    cog = _cog()
    cog._load_vacancies = AsyncMock(return_value=[_vacancy()])
    inter = _interaction("")

    await jobs.GetAJob.vacancy.callback(cog, inter, template_id="vacancy-1")

    inter.response.send_message.assert_awaited_once()
    kwargs = inter.response.send_message.await_args.kwargs
    assert kwargs["ephemeral"] is True
    preview = kwargs["components"]
    assert preview[1].children[0].content == "# Пивовар"
    assert cog.drafts


async def test_vacancy_autocomplete_filters_titles_and_returns_stable_ids():
    cog = _cog()
    cog._load_vacancies = AsyncMock(
        return_value=[_vacancy(), _vacancy(id="vacancy-2", title="Шахтёр")]
    )

    choices = await cog.vacancyAutocomplete(MagicMock(), "пив")

    assert list(choices.values()) == ["vacancy-1"]
    assert "Пивовар" in next(iter(choices))
    assert "vacanc" in next(iter(choices))


async def test_vacancy_autocomplete_labels_fit_discord_limit():
    cog = _cog()
    cog._load_vacancies = AsyncMock(return_value=[_vacancy(title="Я" * 100)])

    choices = await cog.vacancyAutocomplete(MagicMock(), "")

    assert max(map(len, choices)) <= 100
    assert list(choices.values()) == ["vacancy-1"]


async def test_cache_upsert_updates_selected_template_without_duplicate(monkeypatch):
    stored = [_vacancy(), _vacancy(id="vacancy-2", title="Шахтёр")]
    fake_flags = SimpleNamespace(
        getFlag=AsyncMock(return_value=SimpleNamespace(value=jobs._encode_vacancies(stored))),
        setFlag=AsyncMock(return_value=True),
    )
    monkeypatch.setattr(jobs, "flags", fake_flags)
    cog = _cog()

    saved = await cog._cache_vacancy(
        _draft(template_id="vacancy-1", payment="150 мурок за смену.")
    )

    assert saved.id == "vacancy-1"
    encoded = fake_flags.setFlag.await_args.args[2]
    cached = jobs._decode_vacancies(encoded)
    assert [item.id for item in cached] == ["vacancy-1", "vacancy-2"]
    assert cached[0].payment == "150 мурок за смену."


async def test_modal_updates_all_fields_and_sends_ephemeral_preview():
    cog = _cog()
    cog.drafts[1] = _draft(
        title="", description="", requirements="", tasks="", payment=""
    )
    modal = jobs.VacancyModal(cog, 1)
    inter = MagicMock()
    inter.message = None
    inter.text_values = {
        "vacancy_title": " Пекарь ",
        "vacancy_description": " Печь хлеб. ",
        "vacancy_requirements": " Не бояться печи. ",
        "vacancy_tasks": " Следить за тестом. ",
        "vacancy_payment": " 80 мурок. ",
    }
    inter.response.send_message = AsyncMock()

    await modal.callback(inter)

    assert cog.drafts[1].title == "Пекарь"
    assert cog.drafts[1].payment == "80 мурок."
    kwargs = inter.response.send_message.await_args.kwargs
    assert kwargs["ephemeral"] is True
    assert kwargs["components"][1].children[0].content == "# Пекарь"


async def test_successful_publish_posts_before_upserting_cache():
    cog = _cog()
    cog.drafts[1] = _draft()
    events = []

    async def send(**kwargs):
        events.append("post")
        return SimpleNamespace(jump_url="https://discord.test/vacancy")

    async def cache(draft):
        events.append("cache")
        return _vacancy()

    channel = MagicMock()
    channel.send = AsyncMock(side_effect=send)
    cog.bot.get_channel.return_value = channel
    cog._cache_vacancy = AsyncMock(side_effect=cache)
    inter = _interaction("VACANCY_PUB:1")

    await cog.vacancyButtons(inter)

    assert events == ["post", "cache"]
    sent = channel.send.await_args.kwargs["components"][0]
    assert sent.children[-1].children[0].disabled is False
    assert 1 not in cog.drafts
    assert "Опубликовано" in inter.edit_original_response.await_args.kwargs["components"][0].content


async def test_failed_publish_restores_draft_and_does_not_touch_cache():
    cog = _cog()
    draft = _draft(template_id="vacancy-1")
    cog.drafts[1] = draft
    channel = MagicMock()
    channel.send = AsyncMock(
        side_effect=disnake.HTTPException(MagicMock(status=500), "boom")
    )
    cog.bot.get_channel.return_value = channel
    cog._cache_vacancy = AsyncMock()
    inter = _interaction("VACANCY_PUB:1")

    await cog.vacancyButtons(inter)

    assert cog.drafts[1] is draft
    cog._cache_vacancy.assert_not_awaited()
    assert any(
        isinstance(component, disnake.ui.ActionRow)
        for component in inter.edit_original_response.await_args.kwargs["components"]
    )


async def test_apply_creates_private_channel_thread_and_sends_ping_container():
    cog = _cog()
    thread = MagicMock()
    thread.id = 777
    thread.send = AsyncMock()
    thread.add_user = AsyncMock()
    channel = MagicMock()
    channel.create_thread = AsyncMock(return_value=thread)
    cog.bot.get_channel.return_value = channel
    inter = _interaction("VACANCY_APPLY", admin=False)
    inter.message.id = 555
    inter.message.jump_url = "https://discord.test/vacancy/555"
    inter.message.components = [jobs._vacancy_container(_vacancy(), apply_enabled=True)]

    await cog.vacancyButtons(inter)

    inter.response.defer.assert_awaited_once_with(ephemeral=True)
    kwargs = channel.create_thread.await_args.kwargs
    assert kwargs["type"] is disnake.ChannelType.private_thread
    assert "Пивовар" in kwargs["name"]
    sent = thread.send.await_args.kwargs
    text = "\n".join(
        child.content for child in sent["components"][0].children if hasattr(child, "content")
    )
    assert "<@1>" in text
    assert f"<@&{Roles.admin}>" in text
    assert f"<@&{Roles.st_admin}>" in text
    assert inter.message.jump_url in text
    assert [role.id for role in sent["allowed_mentions"].roles] == [Roles.admin, Roles.st_admin]
    thread.add_user.assert_not_awaited()
    assert "<#777>" in inter.edit_original_response.await_args.args[0]


async def test_apply_guard_rejects_parallel_double_click():
    cog = _cog()
    cog._applying.add((555, 1))
    inter = _interaction("VACANCY_APPLY", admin=False)
    inter.message.id = 555

    await cog.vacancyButtons(inter)

    inter.response.send_message.assert_awaited_once()
    cog.bot.get_channel.assert_not_called()


async def test_apply_success_is_not_rolled_back_when_final_ephemeral_ack_fails():
    cog = _cog()
    thread = MagicMock(id=777)
    thread.send = AsyncMock()
    thread.delete = AsyncMock()
    channel = MagicMock()
    channel.create_thread = AsyncMock(return_value=thread)
    cog.bot.get_channel.return_value = channel
    inter = _interaction("VACANCY_APPLY", admin=False)
    inter.message.id = 555
    inter.message.jump_url = "https://discord.test/vacancy/555"
    inter.message.components = [jobs._vacancy_container(_vacancy(), apply_enabled=True)]
    inter.edit_original_response.side_effect = [
        disnake.HTTPException(MagicMock(status=404), "Unknown interaction"),
        None,
    ]

    await cog.vacancyButtons(inter)

    thread.send.assert_awaited_once()
    thread.delete.assert_not_awaited()


async def test_failed_apply_defer_releases_double_click_guard():
    cog = _cog()
    inter = _interaction("VACANCY_APPLY", admin=False)
    inter.message.id = 555
    inter.response.defer.side_effect = disnake.HTTPException(
        MagicMock(status=404), "Unknown interaction"
    )

    with pytest.raises(disnake.HTTPException):
        await cog.vacancyButtons(inter)

    assert (555, 1) not in cog._applying


async def test_old_job_button_points_to_jobs_channel():
    cog = _cog()
    inter = _interaction(jobs.Buttons.GET_A_JOB.id, admin=False)

    await cog.getAJobButton(inter)

    inter.send.assert_awaited_once_with(
        f"Все актуальные вакансии: <#{Channels.jobs}>", ephemeral=True
    )
