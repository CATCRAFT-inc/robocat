"""Общие пути к визуальным ассетам бота."""

from pathlib import Path


DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Основной шрифт для всех новых растровых интерфейсов.
DEFAULT_FONT_PATH = DATA_DIR / "fonts" / "MinecraftRus.ttf"
