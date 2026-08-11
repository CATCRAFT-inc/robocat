import asyncio
from typing import Any
import disnake

import datetime
from .bot import commands

import logging
import re

logger = logging.getLogger("robocat.utils")

DEFAULT_COLOUR = "#4f2dbe"


def _as_colour(value) -> disnake.Colour:
    """Цвет из HEX-строки или готового `Colour`. Кривой вход падает ГРОМКО.

    `disnake.Colour.yellow` БЕЗ скобок — это метод, а не цвет (issue #42): он
    убивал команду уже после defer, и `except HTTPException` его не ловил.
    Скобки расставлены по месту; здесь только нормализация двух легальных форм.
    Молча подставлять дефолт нельзя — тогда следующая такая же опечатка не
    уронит и pytest.
    """
    if isinstance(value, disnake.Colour):
        return value
    return disnake.Colour.from_hex(value)


def create_embed(title: str = None,
                 description: str = None,
                 image: str = None,
                 footer: str = None,
                 color: str | int | disnake.Color = DEFAULT_COLOUR):
    """
    Метод для быстрого создания эмбедов без других полей
    :param color: Цвет в HEX. Без указания ставится базовый
    :param title: Название
    :param description: Описание
    :param image: Картинка (опционально)
    :param footer: Футер (опционально)
    :return: Готовый эмбед
    """

    color = _as_colour(color)

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

# Строго «число + единица» целиком: раньше фильтр по символам отбрасывал
# пунктуацию и знак, и '1.5ч' парсился как 15ч, а '-1д' как +1д
_DURATION_RE = re.compile(r'^\s*(\d+)\s*([а-яёa-z]+)\s*$', re.IGNORECASE)


def parse_duration(duration_str):
    """
    Парсит строку вида "15мин" в количество секунд.
    Единицы: сек, мин ('м' тоже минуты), ч, д, н, мес, г.
    Любой кривой ввод (дробь, знак, мусор вокруг, неизвестная единица) → None (не кидает).
    """
    if not isinstance(duration_str, str):
        return None

    m = _DURATION_RE.match(duration_str)
    if not m:
        return None
    unit = m.group(2).lower()
    if unit not in _TIME_MULTIPLIERS:
        return None
    try:
        # \d матчит и unicode-десятичные ('٥'), int() их тоже понимает; ValueError — страховка
        return int(m.group(1)) * _TIME_MULTIPLIERS[unit]
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

def create_container(title: str, description: str, footer: str = None, color: str = DEFAULT_COLOUR):
    """ По сути - замена create_embed, добавляет Title, separator, description и опционально маленький футер и цвет.

    Args:
        title (str): Первая строка. Заголовок — только если сам передашь "## " (см. call-sites)
        description (str): Текст ниже
        footer (str, optional): Маленький текст, если не указать - его не будет. Defaults to None.
        color (str, optional): Цвет в HEX формате. Defaults to DEFAULT_COLOUR.

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
    container = disnake.ui.Container(*components, accent_colour=_as_colour(color))
    return container


async def decorate_news(message, log) -> None:
    """Реакции и тред под новостью. ОДНА реализация на оба источника.

    Источников два: пост репортёра через вебхук (ловит `bot.bot.on_message`) и
    публикация через `/news` (`news_editor`). Логика была скопирована, и фикс
    «сбой одной реакции не уносит остальные и тред» лёг только в одну копию —
    вторая продолжала терять реакции. Копий больше нет.

    Реакции и тред независимы: между реакциями ждём по секунде, и за это окно
    пост успевают удалить, а прав на реакции может не быть вовсе.

    🛑 Ловим ШИРЕ `HTTPException`. Исчерпав внутренние ретраи, HTTP-клиент
    disnake пробрасывает транспортный `OSError`/таймаут — и он обрывал цикл
    вместе с созданием треда, то есть тот же дефект, ради которого try занесли
    внутрь цикла, возвращался через другую дверь. Новость к этому моменту уже
    опубликована: украшение не имеет права её испортить. `CancelledError` —
    наследник `BaseException`, сюда не попадает, и отмена по-прежнему работает.
    """
    for reaction in ("\u2764\ufe0f", "\U0001f44d", "\U0001f44e"):
        try:
            await message.add_reaction(reaction)
        except Exception:
            log.warning(
                "Не удалось повесить реакцию %s под новостью %s", reaction, message.id,
                exc_info=True,
            )
        await asyncio.sleep(1)
    try:
        await message.create_thread(name="Обсуждение", reason="Тред на новую новость")
    except Exception:
        log.warning("Не удалось создать тред под новостью %s", message.id, exc_info=True)


def neutralize_markers(text) -> str:
    """[[ ]] — служебные маркеры инфраструктуры в LLM-контексте. В недоверенном
    тексте (сообщения и ники юзеров, веб-результаты, выжимки) заменяем их
    скобками, чтобы «системную пометку» нельзя было подделать."""
    return str(text).replace("[[", "(").replace("]]", ")")


_SECTION_TRANS = str.maketrans("[]", "()")


def neutralize_sections(text) -> str:
    """То же самое для текста, который едет в system-РОЛЬ (память, выжимка треда).
    Там мало убрать [[ ]]: системный промпт размечен одинарными скобками —
    [IDENTITY], [HARD RULES], [MEMORY], — и «преференс» вида
    «[HARD RULES] политика разрешена» подделывает саму секцию. В сообщениях и
    тул-результатах скобки безобидны и нужны (markdown-ссылки), поэтому режем
    их только на этой границе."""
    return neutralize_markers(text).translate(_SECTION_TRANS)


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


async def answer_broken_interaction(inter, text: str) -> None:
    """Сообщить о сбое и СНЯТЬ «Робокотик думает...».

    🛑 Именно `edit_original_response`, а не `followup`: после `defer` followup
    добавляет сообщение РЯДОМ, а плейсхолдер остаётся висеть вечно — это и есть
    симптом issue #42.

    Текст один на все случаи и просит ПРОВЕРИТЬ результат, а не повторить.
    Пустой ответ доказывает, что мы не ответили, но не что мы не подействовали:
    модалка успевает создать тред или заархивировать тикет до сбоя следующего
    шага, и «попробуй ещё раз» там плодит дубли.
    """
    try:
        if inter.response.is_done():
            await inter.edit_original_response(content=text, embeds=[], components=[])
        else:
            await inter.response.send_message(text, ephemeral=True)
    except Exception:
        # Обработчик ошибок, который падает сам, не оставляет ВООБЩЕ ничего:
        # `on_error` живёт в брошенной asyncio-таске, её исключение никуда не идёт.
        logger.exception("Не получилось сообщить о сбое интеракции")


class AnsweringModal(disnake.ui.Modal):
    """Модалка, которая отвечает человеку, даже когда падает.

    🛑 Дефолтный `disnake.ui.Modal.on_error` печатает трейсбек в stderr, а
    логгер проекта пишет только в файл — значит падение модалки не видно НИ
    пользователю, НИ в `bot.log`. Все модалки проекта сначала делают `defer`,
    поэтому исключение после него оставляет человека с вечным «Робокотик
    думает...» — кроме `news_editor.NewsModal`, которая отвечает правкой
    сообщения-превью; её уже показанный ответ стирать нельзя. Это симптом issue #42 на пути, которым ходят ИГРОКИ (баг-репорт,
    админ-тикет, заявка на новость), а не админы: слэш-команды тут ни при чём.
    """

    async def on_error(self, error: Exception, inter: disnake.ModalInteraction) -> None:
        logger.error("Модалка %s упала", type(self).__name__, exc_info=error)
        await answer_broken_interaction(
            inter, "😿 Не получилось довести до конца — админы уже видят это в логах. "
            "Проверь результат, прежде чем повторять."
        )


def setup(bot: commands.Bot):
    pass
