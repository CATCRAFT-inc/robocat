# Шпаргалка: venv + pip (Windows 11, Python 3.12, VSCode)

## Создание виртуального окружения

```powershell
# Создать venv в папке .venv (стандартное имя)
python -m venv .venv

# Если несколько версий питона - явно указать версию
py -3.12 -m venv .venv
```

> `.venv` - общепринятое имя. Добавь его в `.gitignore`!

---

## Активация / деактивация

```powershell
# Активация (PowerShell)
.venv\Scripts\Activate.ps1

# Активация (CMD)
.venv\Scripts\activate.bat

# Деактивация (универсально)
deactivate
```

### Если PowerShell ругается на политику выполнения скриптов:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### VSCode - автоматическая активация:
1. `Ctrl+Shift+P` -> **Python: Select Interpreter**
2. Выбрать интерпретатор из `.venv` (он должен появиться в списке)
3. Терминал в VSCode теперь сам активирует venv при открытии

---

## pip - установка пакетов

```powershell
# Установить пакет
pip install requests

# Установить конкретную версию
pip install requests==2.31.0

# Установить минимальную версию
pip install "requests>=2.28.0"

# Установить несколько пакетов
pip install flask sqlalchemy pydantic

# Обновить пакет
pip install --upgrade requests

# Удалить пакет
pip uninstall requests

# Удалить без подтверждения
pip uninstall -y requests
```

---

## requirements.txt

### Генерация

```powershell
# Сохранить все установленные пакеты (с точными версиями)
pip freeze > requirements.txt

# Посмотреть что внутри
cat requirements.txt
```

Пример содержимого:
```
flask==3.0.2
requests==2.31.0
sqlalchemy==2.0.29
```

### Установка из файла

```powershell
# Установить всё из requirements.txt
pip install -r requirements.txt

# Установить из файла в другом месте
pip install -r path\to\requirements.txt
```

### Тонкость: pip freeze vs ручной файл

`pip freeze` дампит **всё**, включая транзитивные зависимости (зависимости зависимостей).
Для небольших проектов это ок. Для крупных - лучше вести `requirements.txt` вручную,
указывая только прямые зависимости (без жёсткого пиннинга версий):

```
flask>=3.0
requests>=2.28
sqlalchemy>=2.0
```

---

## Просмотр установленного

```powershell
# Список всех пакетов
pip list

# Список в формате freeze (имя==версия)
pip freeze

# Инфо о конкретном пакете
pip show requests

# Найти устаревшие пакеты
pip list --outdated
```

---

## Проверка что venv активен

```powershell
# Должно показать путь внутри .venv
where python

# Должно показать pip из .venv
where pip

# Версия питона
python --version
```

Если в начале строки терминала есть `(.venv)` - всё активировано.

---

## Структура проекта (рекомендуемая)

```
my_project/
├── .venv/              <- виртуальное окружение (в .gitignore)
├── .gitignore
├── requirements.txt    <- зависимости
├── main.py
└── ...
```

`.gitignore` минимум:
```
.venv/
__pycache__/
*.pyc
```

---

## Быстрый старт нового проекта (весь флоу)

```powershell
mkdir my_project && cd my_project
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install flask requests         # нужные пакеты
pip freeze > requirements.txt
code .                             # открыть VSCode
```

---

## Восстановление проекта (клон/переустановка)

```powershell
git clone https://github.com/...
cd my_project
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

---

## Полезные мелочи

| Задача | Команда |
|---|---|
| Обновить сам pip | `python -m pip install --upgrade pip` |
| Установить в режиме разработки (editable) | `pip install -e .` |
| Кэш pip (очистить если глючит) | `pip cache purge` |
| Установить из git репозитория | `pip install git+https://github.com/user/repo.git` |
| Посмотреть зависимости пакета | `pip show -v requests` |
