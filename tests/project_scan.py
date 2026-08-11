"""Обход ВСЕХ модулей пакета `bot` — основа сторожевых тестов.

🛑 Здесь не `pkgutil.walk_packages`, и это не вкусовщина. `pkgutil` пропускает
каталог без `__init__.py` МОЛЧА, а в проекте таких три из шести: `bot/ai`,
`bot/commands`, `bot/flag_system`. Сторож, построенный на нём, обходил половину
дерева и при этом выглядел исчерпывающим — модалка, добавленная в `bot/ai/`,
прошла бы мимо проверки «каждая модалка отвечает при падении», и issue #42
вернулась бы при зелёном прогоне.

Поэтому идём по файловой системе: она не знает про `__init__.py` и не умеет
пропускать. Нашли ноль модулей — падаем: пустой обход обязан быть провалом
теста, а не тихим успехом.
"""

import importlib
import pathlib

import bot as bot_package

_ROOT = pathlib.Path(bot_package.__file__).resolve().parent


def import_all_bot_modules() -> list[str]:
    """Импортирует каждый `.py` под `bot/` и возвращает список имён модулей."""
    names = []
    for path in sorted(_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(_ROOT).with_suffix("")
        parts = [p for p in relative.parts if p != "__init__"]
        name = ".".join(["bot", *parts])
        importlib.import_module(name)
        names.append(name)

    assert len(names) > 20, f"обход нашёл всего {len(names)} модулей — сломан он, а не код"
    # Именно те каталоги, которые молча терял pkgutil.
    for blind_spot in ("bot.ai.engine", "bot.commands.faq", "bot.flag_system.flag_system"):
        assert blind_spot in names, f"{blind_spot} не найден — обход снова слепнет на подпакетах"
    return names


def descendants(cls):
    """Все наследники класса, включая транзитивных."""
    for child in cls.__subclasses__():
        yield child
        yield from descendants(child)
