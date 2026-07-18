from typing import Any
import disnake

import datetime
from .bot import commands

import logging
import re

logger = logging.getLogger("robocat.utils")

# def guild_check():
#     async def predicate(ctx):
#         if ctx.guild.id != 1138425078493753366:
#             await ctx.send("Эта команда разрешена только в [Дискорд сервере Кошкокрафта](<https://discord.gg/catcraftmc>)!")
#             return False
#         return True
#     return commands.check(predicate)

def create_embed(title: str = None,
                 description: str = None,
                 image: str = None,
                 footer: str = None,
                 color: str | int | disnake.Color = "#4f2dbe"):
    """
    Метод для быстрого создания эмбедов без других полей
    :param color: Цвет в HEX. Без указания ставится базовый
    :param title: Название
    :param description: Описание
    :param image: Картинка (опционально)
    :param footer: Футер (опционально)
    :return: Готовый эмбед
    """

    # Если ебанат поставил не HEX цвет - ставится дефолтный
    if isinstance(color, str):
        try:
            color = disnake.Colour.from_hex(color)
        except (ValueError, KeyError):
            logger.warning("Некорректный HEX-цвет %r в create_embed — использую дефолтный", color)
            color = disnake.Colour.from_hex("#4f2dbe")
            

    embed = disnake.Embed(
        title=title,
        description=description,
        color=color
    )
    if image is not None:
        embed.set_image(url=image)
    if footer is not None:
        embed.set_footer(text=footer)
    return embed

def create_button(label: str,
                  custom_id: str,
                  style: disnake.ButtonStyle,
                  emoji = None):
    """
    Метод для быстрого создания кнопок
    :param emoji: Эмоджии
    :param label: Текст на кнопке
    :param custom_id: Кастомный ID кнопки
    :param style: Стиль кнопки
    :return: Кнопку
    """
    button = disnake.ui.Button(
        label=label,
        custom_id=custom_id,
        style=style,
        emoji=emoji
    )
    return button

def getTime():
    # Время по МСК
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=3)))

# Множители единиц времени в секундах. ВНИМАНИЕ: 'м' = минуты (раньше был месяц —
# из-за этого /mute мутил на месяцы). Месяц — только 'мес'.
_TIME_MULTIPLIERS = {
    'сек': 1,           # секунда
    'мин': 60,          # минута
    'м': 60,            # минута (короткая форма)
    'ч': 3600,          # час
    'д': 86400,         # день
    'н': 86400 * 7,     # неделя
    'мес': 86400 * 30,  # месяц
    'г': 86400 * 365,   # год
}

# Формы склонения для каждой единицы: (1, 2-4, 5+)
_TIME_FORMS = {
    'сек': ('секунда', 'секунды', 'секунд'),
    'мин': ('минута', 'минуты', 'минут'),
    'м': ('минута', 'минуты', 'минут'),
    'ч': ('час', 'часа', 'часов'),
    'д': ('день', 'дня', 'дней'),
    'н': ('неделя', 'недели', 'недель'),
    'мес': ('месяц', 'месяца', 'месяцев'),
    'г': ('год', 'года', 'лет'),
}

def parse_duration(duration_str):
    """
    Парсит строку вида "15мин" в количество секунд.
    Единицы: сек, мин ('м' тоже минуты), ч, д, н, мес, г.
    Любой кривой ввод (пустые цифры/неизвестная единица) → None (не кидает).
    """
    if not isinstance(duration_str, str):
        return None

    digits = ''.join(filter(str.isdigit, duration_str))
    unit = ''.join(filter(str.isalpha, duration_str)).lower()

    if not digits or unit not in _TIME_MULTIPLIERS:
        return None

    try:
        # str.isdigit пропускает суперскрипты/circled digits ('⁵'), а int() на них падает
        return int(digits) * _TIME_MULTIPLIERS[unit]
    except ValueError:
        return None

def duration_to_text(dur_str):
    """
    Меняет строчки типа "1д", "2ч", "5мин" на "1 день", "2 часа", "5 минут" и т.п.
    Число и суффикс выделяются так же, как в parse_duration.
    Кривой ввод → возвращает исходную строку (не кидает).
    :param dur_str: Строчка
    :return: Красивая строчка
    """
    if not isinstance(dur_str, str):
        return dur_str

    digits = ''.join(filter(str.isdigit, dur_str))
    unit = ''.join(filter(str.isalpha, dur_str)).lower()

    if not digits or unit not in _TIME_FORMS:
        return dur_str

    try:
        nums = int(digits)
    except ValueError:  # суперскрипты/circled digits проходят isdigit, но не int()
        return dur_str
    one, few, many = _TIME_FORMS[unit]

    if nums % 10 == 1 and nums % 100 != 11:
        word = one
    elif nums % 10 in (2, 3, 4) and nums % 100 not in (12, 13, 14):
        word = few
    else:
        word = many

    return f'{nums} {word}'

def create_container(title: str, description: str, footer: str = None, color: str = "#4f2dbe"):
    """ По сути - замена create_embed, добавляет Title, separator, description и опционально маленький футер и цвет.

    Args:
        title (str): Большой текст, не включает в себя "#"
        description (str): Текст ниже
        footer (str, optional): Маленький текст, если не указать - его не будет. Defaults to None.
        color (str, optional): Цвет в HEX формате. Defaults to "#4f2dbe".

    Returns:
        _type_: disnake.ui.Container
    """
    components = [
        disnake.ui.TextDisplay(title),
        disnake.ui.Separator(),
        disnake.ui.TextDisplay(description),
    ]
    if footer:
        components.append(
            disnake.ui.TextDisplay(f"-# {footer}")
        )
    container = disnake.ui.Container(*components, accent_colour=disnake.Color.from_hex(color))
    return container


def component_text(components) -> str:
    """Весь текст TextDisplay-компонентов V2-сообщения, в порядке обхода дерева.

    Работает и с read-side классами (message.components), и с ui-классами из
    тестов: у обоих текстовые ноды несут .content, контейнеры — .children.
    """
    parts = []
    stack = list(components or [])
    while stack:
        c = stack.pop(0)
        content = getattr(c, "content", None)
        if isinstance(content, str) and content:
            parts.append(content)
        stack[:0] = list(getattr(c, "children", None) or [])
    return "\n".join(parts)


def setup(bot: commands.Bot):
    pass