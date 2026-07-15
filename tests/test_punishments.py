"""Регресс-тесты префиксной команды !мут.

conftest.py уже импортирует bot.bot первым (закрывает циклический импорт),
поэтому здесь достаточно импортировать сам ког.
"""

import inspect

from bot.handlers.punishments import PunishmentsHanlder


def test_prefix_mute_reason_takes_rest_of_message():
    """`reason` должен быть keyword-only — тогда парсер disnake отдаёт ему весь
    остаток сообщения, а не одно слово (issue #3: `!мут 3д причина из слов`
    клала в reason только «причина»)."""
    sig = inspect.signature(PunishmentsHanlder.prefixMute.callback)
    reason = sig.parameters["reason"]
    assert reason.kind is inspect.Parameter.KEYWORD_ONLY


def test_prefix_mute_duration_still_positional():
    """duration остаётся первым позиционным аргументом — порядок `!мут <время>
    <причина...>` не меняется."""
    sig = inspect.signature(PunishmentsHanlder.prefixMute.callback)
    duration = sig.parameters["duration"]
    assert duration.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert duration.default == "3д"
