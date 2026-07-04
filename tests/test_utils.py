"""Тесты bot.utils.parse_duration / duration_to_text.

Ожидания сверены с ФАКТИЧЕСКОЙ реализацией bot/utils.py (проверено вручную
интерпретатором перед написанием тестов) — расхождений с планом
W1 не найдено.
"""

import pytest

from bot.utils import parse_duration, duration_to_text


# -------------------- parse_duration --------------------

@pytest.mark.parametrize("raw, expected_seconds", [
    ("5сек", 5),
    ("15мин", 900),
    ("5м", 300),            # 'м' — тоже минуты (регрессия: раньше был месяц)
    ("1мес", 2592000),      # месяц = 30 дней
    ("1ч", 3600),
    ("3д", 259200),
    ("1н", 604800),         # неделя = 7 дней
    ("1г", 31536000),       # год = 365 дней
])
def test_parse_duration_valid_units(raw, expected_seconds):
    assert parse_duration(raw) == expected_seconds


@pytest.mark.parametrize("raw", [
    "abc",       # нет цифр, неизвестная единица
    "",          # пусто
    "5x",        # неизвестная единица
    "мин",       # нет цифр
    "5",         # нет единицы
    None,        # не строка
    123,         # не строка
])
def test_parse_duration_invalid_returns_none(raw):
    assert parse_duration(raw) is None


# -------------------- duration_to_text --------------------

@pytest.mark.parametrize("raw, expected_text", [
    ("5сек", "5 секунд"),
    ("15мин", "15 минут"),
    ("1ч", "1 час"),
    ("3д", "3 дня"),
    ("28д", "28 дней"),
    ("2н", "2 недели"),
    ("1мес", "1 месяц"),
    ("1г", "1 год"),
    ("21д", "21 день"),     # 21 % 10 == 1 и 21 % 100 != 11 -> "день"
    ("11д", "11 дней"),     # исключение "-надцать" -> "дней", а не "день"
])
def test_duration_to_text_declensions(raw, expected_text):
    assert duration_to_text(raw) == expected_text


@pytest.mark.parametrize("raw", ["abc", "", "5x", "мин"])
def test_duration_to_text_invalid_returns_original_string(raw):
    assert duration_to_text(raw) == raw
