"""Старые кнопки вакансий после закрытия сервера отвечают только `:(`."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.misc import Tests as MiscCog
from bot.storage import Buttons


@pytest.mark.parametrize("custom_id", [
    Buttons.GET_A_JOB.id,
    "VACANCY_APPLY",
    "VACANCY_ACCEPT:123",
])
async def test_closed_vacancy_buttons_reply_sad_face(monkeypatch, custom_id):
    monkeypatch.setattr("bot.misc.SERVER_CLOSED", True)
    inter = MagicMock()
    inter.component.custom_id = custom_id
    inter.response.send_message = AsyncMock()

    await MiscCog.closedVacancyButtons(MiscCog(MagicMock()), inter)

    inter.response.send_message.assert_awaited_once_with(":(", ephemeral=True)


async def test_vacancy_buttons_stay_silent_when_server_is_open(monkeypatch):
    monkeypatch.setattr("bot.misc.SERVER_CLOSED", False)
    inter = MagicMock()
    inter.component.custom_id = Buttons.GET_A_JOB.id
    inter.response.send_message = AsyncMock()

    await MiscCog.closedVacancyButtons(MiscCog(MagicMock()), inter)

    inter.response.send_message.assert_not_awaited()
