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

# Коги, выключенные сознательно. Код и тесты остаются, но в main.py они
# закомментированы: сервер закрыт, live-функции не должны загружаться.
DISABLED_EXTENSIONS = {
    "handlers.rcon",
    "handlers.get_a_job",
}


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


def _cog_modules() -> list[str]:
    """Модули с `def setup(bot)` — то есть готовые к загрузке коги."""
    root = pathlib.Path(__file__).resolve().parents[1] / "bot"
    found = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(isinstance(n, ast.FunctionDef) and n.name == "setup" for n in tree.body):
            found.append(path.relative_to(root).with_suffix("").as_posix().replace("/", "."))
    return sorted(found)


@pytest.mark.parametrize("module", _cog_modules())
def test_cog_module_registered_in_main(module):
    """Ког, которого нет в extensions, молча мёртв: слушатели не подписаны,
    кнопки не отвечают вообще. Так `handlers.get_a_job` пролежал нерабочим
    с момента написания — CI этого не видел.

    Исключение — осознанно выключенные live-коги: они перечислены в
    `DISABLED_EXTENSIONS`, чтобы случайное забывание не пряталось за ним."""
    assert module in _extension_names() or module in DISABLED_EXTENSIONS, (
        f"ког bot/{module.replace('.', '/')}.py не в списке extensions в main.py — "
        "он не загрузится, а его кнопки и команды будут молчать"
    )
