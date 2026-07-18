"""Смоук-тест: каждое расширение из main.py импортируется без ошибок.

Деплой идёт автопушем в master, а pytest раньше не грузил коги — синтаксическая
ошибка или битый импорт в любом расширении проходили CI зелёными и падали только
после рестарта на проде. Этот тест ловит их до деплоя.

Список расширений парсится из main.py (main.py нельзя импортировать — он на
уровне модуля вызывает bot.run), поэтому не разъедется с реальным списком.
"""

import ast
import importlib
import pathlib

import pytest

import bot.bot  # noqa: F401 — bot.bot первым, против циклического импорта


def _extension_names() -> list[str]:
    src = pathlib.Path(__file__).resolve().parents[1] / "main.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "extensions" for t in node.targets
        ):
            return [el.value for el in node.value.elts if isinstance(el, ast.Constant)]
    raise AssertionError("Не нашёл список extensions в main.py")


@pytest.mark.parametrize("ext", _extension_names())
def test_extension_module_imports(ext):
    importlib.import_module(f"bot.{ext}")
