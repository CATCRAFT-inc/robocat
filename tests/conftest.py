"""Общий conftest для тестов robocat.

ВАЖНАЯ ЛОВУШКА (преэкзистентная, не тестовый баг): в проекте есть циклический
импорт bot.utils <-> bot.bot <-> bot.storage:

    bot/utils.py   делает `from .bot import commands`
    bot/bot.py     делает `from bot import storage`
    bot/storage.py делает `from bot.utils import create_embed, create_button`

Если ЛЮБОЙ тест (или просто интерпретатор) первым импортирует bot.utils или
bot.storage — а не bot.bot, — Python словит частично инициализированный
модуль и упадёт с:

    ImportError: cannot import name 'create_embed' from partially
    initialized module 'bot.utils' (most likely due to a circular import)

Это НЕ проявляется в проде, потому что main.py первой строкой делает
`from bot.bot import bot`, и цикл "закрывается" удачно (к моменту, когда
bot.py доходит до `from bot import storage`, имя `commands` в bot.bot уже
определено — оно импортируется из disnake.ext ещё до этой строки).

Поэтому здесь мы обязаны повторить тот же порядок: bot.bot должен быть
первым импортом bot.* во всём тестовом прогоне. Именно для этого существует
этот conftest.py — pytest импортирует его раньше файлов тестов.
"""

import os
import sys
from pathlib import Path

# Корень репозитория — в sys.path, чтобы `import bot.xxx` работал независимо
# от того, откуда запущен pytest.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Фейковые env-переменные ДО любых импортов bot.* — токены/ключи не настоящие,
# тесты не должны стучаться в сеть. Используем setdefault, чтобы не затирать
# реальный .env, если он вдруг подхвачен окружением запуска.
_FAKE_ENV = {
    "DISCORD_TOKEN": "x",
    "DEV_DISCORD_TOKEN": "x",
    "GEMINI": "x",
    "GROQ": "x",
    "OR": "x",
    "GHM": "x",
    "DS": "x",
    "RCON_HOST": "",
    "RCON_PASSWORD": "",
}
for _key, _value in _FAKE_ENV.items():
    os.environ.setdefault(_key, _value)

# КРИТИЧНО: именно этот импорт первым — см. докстринг выше.
import bot.bot  # noqa: E402,F401
