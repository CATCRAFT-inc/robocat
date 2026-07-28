"""Диагностический хвост в on_error: возраст интеракции и разбор кода отказа."""

import datetime
from unittest.mock import Mock

import disnake

import bot.bot as bot_module  # noqa: F401 — bot.bot первым, против циклического импорта


class _Refused(Exception):
    """Подделка disnake.HTTPException: важен только атрибут code."""

    def __init__(self, code):
        super().__init__(str(code))
        self.code = code


def _interaction(age_seconds: float):
    inter = Mock(spec=disnake.Interaction)
    inter.id = 123
    inter.created_at = disnake.utils.utcnow() - datetime.timedelta(seconds=age_seconds)
    return inter


def _hint_for(code, age_seconds):
    try:
        raise _Refused(code)
    except _Refused:
        return bot_module._interaction_hint((_interaction(age_seconds),))


def test_hint_empty_without_interaction_in_args():
    assert bot_module._interaction_hint(("сообщение", 42)) == ""


def test_hint_reports_interaction_age():
    assert "возраст 5.0" in _hint_for(10062, 5.0)


def test_hint_blames_second_instance_on_already_acknowledged():
    assert "инстанс" in _hint_for(40060, 0.5)


def test_hint_blames_second_instance_on_fast_unknown_interaction():
    assert "инстанс" in _hint_for(10062, 0.5)


def test_hint_keeps_silent_about_instance_when_deadline_really_missed():
    assert "инстанс" not in _hint_for(10062, 4.0)
