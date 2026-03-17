from typing import Any
import disnake

import datetime
from .bot import commands

import re

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
        if re.match(string=color, pattern="^(0x|0X|#)?[a-fA-F0-9]+$"):
            color = disnake.Colour.from_hex(color)
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
    if emoji is not None:
        emoji = emoji
    else:
        emoji = None
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

def parse_duration(duration_str):
    # Define the multipliers for each time unit
    time_multipliers = {
        'сек': 1,  # second
        'мин': 60,  # minute
        'ч': 3600,  # hour
        'д': 86400,  # day
        'н': 86400*7,  # week
        'м': 86400*30, # месяц
        'г': 86400*365
    }

    # Extract the number and the time unit from the string
    number = int(''.join(filter(str.isdigit, duration_str)))
    unit = ''.join(filter(str.isalpha, duration_str)).lower()

    # Calculate the duration in seconds
    if unit in time_multipliers:
        return number * time_multipliers[unit]
    else:
        return False

def duration_to_text(dur_str):
    """
    Меняет строчки типа "1д", "2ч", "5м" на "1 день", "2 часа", "5 минут" и т.п.
    :param dur_str: Строчка
    :return: Красивая строчка
    """
    dur = dur_str[-1]  # Последний символ строки
    try:
        nums = int(dur_str[:-1])
    except:
        raise IndexError("Некорректный формат строки")

    if dur == 'д':
        if nums % 100 in [11, 12, 13, 14]:
            return f'{nums} дней'
        elif nums % 10 == 1:
            return f'{nums} день'
        elif nums % 10 in [2, 3, 4]:
            return f'{nums} дня'
        else:
            return f'{nums} дней'
    elif dur == 'ч':
        if nums % 100 in [11, 12, 13, 14]:
            return f'{nums} часов'
        elif nums % 10 == 1:
            return f'{nums} час'
        elif nums % 10 in [2, 3, 4]:
            return f'{nums} часа'
        else:
            return f'{nums} часов'
    elif dur == 'м':
        if nums % 100 in [11, 12, 13, 14]:
            return f'{nums} минут'
        elif nums % 10 == 1:
            return f'{nums} минута'
        elif nums % 10 in [2, 3, 4]:
            return f'{nums} минуты'
        else:
            return f'{nums} минут'
    else:
        raise ValueError("Неизвестная единица измерения")

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

def setup(bot: commands.Bot):
    pass