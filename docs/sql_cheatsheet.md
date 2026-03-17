# Шпаргалка: SQLite + aiosqlite

---

## Содержание

1. [SQL - базовый синтаксис](#sql-базовый-синтаксис)
2. [sqlite3 (синхронный)](#sqlite3-синхронный)
3. [aiosqlite (асинхронный)](#aiosqlite-асинхронный)
4. [Паттерны и лайфхаки](#паттерны-и-лайфхаки)

---

## SQL - базовый синтаксис

### DDL - структура таблиц

```sql
-- Создать таблицу
CREATE TABLE IF NOT EXISTS users (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT    NOT NULL,
    age      INTEGER,
    email    TEXT    UNIQUE,
    created  TEXT    DEFAULT (datetime('now'))
);

-- Удалить таблицу
DROP TABLE IF EXISTS users;

-- Изменить таблицу (добавить колонку)
ALTER TABLE users ADD COLUMN phone TEXT;
```

### DML - работа с данными

```sql
-- Вставка
INSERT INTO users (name, age, email) VALUES ('Серёжа', 21, 'test@mail.ru');

-- Вставка нескольких строк
INSERT INTO users (name, age) VALUES
    ('Вася', 25),
    ('Петя', 30);

-- Вставка с игнором конфликта (если email уже есть - пропустить)
INSERT OR IGNORE INTO users (name, email) VALUES ('Дима', 'test@mail.ru');

-- Вставка с заменой при конфликте (UPSERT)
INSERT OR REPLACE INTO users (name, email) VALUES ('Дима', 'test@mail.ru');

-- Выборка
SELECT * FROM users;
SELECT id, name FROM users WHERE age > 18;
SELECT * FROM users WHERE name LIKE 'С%';        -- начинается на С
SELECT * FROM users ORDER BY age DESC LIMIT 10;
SELECT * FROM users WHERE age BETWEEN 18 AND 30;
SELECT * FROM users WHERE email IS NOT NULL;

-- Обновление
UPDATE users SET age = 22 WHERE name = 'Серёжа';
UPDATE users SET age = age + 1 WHERE id = 1;

-- Удаление
DELETE FROM users WHERE id = 5;
DELETE FROM users WHERE age < 18;
```

### Агрегация

```sql
SELECT COUNT(*) FROM users;
SELECT AVG(age), MAX(age), MIN(age) FROM users;
SELECT name, COUNT(*) as cnt FROM users GROUP BY name;
SELECT name, COUNT(*) as cnt FROM users GROUP BY name HAVING cnt > 1;
```

### Joins

```sql
-- Есть таблица orders: id, user_id, product, amount

-- INNER JOIN - только совпадающие строки
SELECT u.name, o.product
FROM users u
INNER JOIN orders o ON u.id = o.user_id;

-- LEFT JOIN - все из левой + совпадения из правой
SELECT u.name, o.product
FROM users u
LEFT JOIN orders o ON u.id = o.user_id;
-- Если у юзера нет заказов - o.product будет NULL
```

### Полезные SQLite функции

```sql
-- Дата/время
SELECT datetime('now');               -- текущее время UTC
SELECT datetime('now', 'localtime'); -- локальное время

-- Строки
SELECT upper(name), lower(name), length(name) FROM users;
SELECT substr(name, 1, 3) FROM users;   -- первые 3 символа
SELECT name || ' ' || email FROM users; -- конкатенация

-- Числа
SELECT round(3.14159, 2);   -- 3.14
SELECT abs(-5);              -- 5
```

---

## sqlite3 (синхронный)

Встроенный модуль, не нужно устанавливать.

```python
import sqlite3

# Подключение
conn = sqlite3.connect("database.db")
# conn = sqlite3.connect(":memory:")  # БД только в RAM

# row_factory - чтобы результаты были dict-like, а не tuple
conn.row_factory = sqlite3.Row

cursor = conn.cursor()
```

### CRUD

```python
# Создание таблицы
cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id    INTEGER PRIMARY KEY AUTOINCREMENT,
        name  TEXT NOT NULL,
        age   INTEGER
    )
""")
conn.commit()

# INSERT - параметры через ? (никогда не через f-string!)
cursor.execute("INSERT INTO users (name, age) VALUES (?, ?)", ("Серёжа", 21))
conn.commit()
last_id = cursor.lastrowid  # id только что вставленной записи

# INSERT много строк
data = [("Вася", 25), ("Петя", 30)]
cursor.executemany("INSERT INTO users (name, age) VALUES (?, ?)", data)
conn.commit()

# SELECT
cursor.execute("SELECT * FROM users WHERE age > ?", (18,))
rows = cursor.fetchall()    # все строки
row  = cursor.fetchone()    # первая строка
# rows10 = cursor.fetchmany(10)  # N строк

# Если row_factory = sqlite3.Row - можно обращаться по имени
for row in rows:
    print(row["name"], row["age"])
    # или: dict(row)  -> {"id": 1, "name": "...", ...}

# UPDATE
cursor.execute("UPDATE users SET age = ? WHERE name = ?", (22, "Серёжа"))
conn.commit()
print(cursor.rowcount)  # сколько строк затронуто

# DELETE
cursor.execute("DELETE FROM users WHERE id = ?", (1,))
conn.commit()
```

### Context manager (лучший вариант)

```python
# conn как контекст-менеджер делает commit/rollback автоматом
# НО: соединение всё равно нужно закрыть вручную

with sqlite3.connect("database.db") as conn:
    conn.row_factory = sqlite3.Row
    conn.execute("INSERT INTO users (name, age) VALUES (?, ?)", ("Серёжа", 21))
    # commit происходит при выходе из блока
    # rollback - если было исключение
```

### Транзакции

```python
try:
    conn.execute("BEGIN")
    conn.execute("UPDATE accounts SET balance = balance - 100 WHERE id = 1")
    conn.execute("UPDATE accounts SET balance = balance + 100 WHERE id = 2")
    conn.commit()
except Exception as e:
    conn.rollback()
    print(f"Ошибка, откат: {e}")
```

---

## aiosqlite (асинхронный)

```bash
pip install aiosqlite
```

API почти идентично sqlite3, но всё через `async/await`.

### Подключение

```python
import aiosqlite

# Вариант 1: async with (рекомендуется)
async def main():
    async with aiosqlite.connect("database.db") as db:
        db.row_factory = aiosqlite.Row
        # работа с БД...

# Вариант 2: вручную
db = await aiosqlite.connect("database.db")
db.row_factory = aiosqlite.Row
# ...
await db.close()
```

### CRUD

```python
async with aiosqlite.connect("database.db") as db:
    db.row_factory = aiosqlite.Row

    # CREATE TABLE
    await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            age  INTEGER
        )
    """)
    await db.commit()

    # INSERT
    cursor = await db.execute(
        "INSERT INTO users (name, age) VALUES (?, ?)", ("Серёжа", 21)
    )
    await db.commit()
    print(cursor.lastrowid)

    # INSERT many
    data = [("Вася", 25), ("Петя", 30)]
    await db.executemany("INSERT INTO users (name, age) VALUES (?, ?)", data)
    await db.commit()

    # SELECT - fetchall
    cursor = await db.execute("SELECT * FROM users WHERE age > ?", (18,))
    rows = await cursor.fetchall()
    for row in rows:
        print(dict(row))

    # SELECT - fetchone
    cursor = await db.execute("SELECT * FROM users WHERE id = ?", (1,))
    row = await cursor.fetchone()
    if row:
        print(dict(row))

    # Итерация напрямую (память не жрёт всё сразу)
    async with db.execute("SELECT * FROM users") as cursor:
        async for row in cursor:
            print(dict(row))

    # UPDATE
    await db.execute("UPDATE users SET age = ? WHERE id = ?", (22, 1))
    await db.commit()

    # DELETE
    await db.execute("DELETE FROM users WHERE id = ?", (1,))
    await db.commit()
```

### Вспомогательные методы

```python
# execute_insert - возвращает lastrowid напрямую
last_id = await db.execute_insert(
    "INSERT INTO users (name) VALUES (?)", ("Вася",)
)

# execute_fetchall - без отдельного cursor
rows = await db.execute_fetchall("SELECT * FROM users WHERE age > ?", (18,))
```

---

## Паттерны и лайфхаки

### Singleton-подключение для бота (aiogram / disnake)

```python
# db.py
import aiosqlite

_db: aiosqlite.Connection | None = None

async def get_db() -> aiosqlite.Connection:
    global _db
    if _db is None:
        _db = await aiosqlite.connect("bot.db")
        _db.row_factory = aiosqlite.Row
    return _db

async def close_db():
    global _db
    if _db:
        await _db.close()
        _db = None
```

```python
# Использование в хендлере
from db import get_db

@bot.command()
async def info(ctx):
    db = await get_db()
    row = await db.execute_fetchall(
        "SELECT * FROM users WHERE discord_id = ?", (ctx.author.id,)
    )
```

### Параметры: ? vs :name

```python
# Позиционные (?)
await db.execute("SELECT * FROM users WHERE name = ? AND age = ?", ("Серёжа", 21))

# Именованные (:name) - читаемее при большом числе параметров
await db.execute(
    "SELECT * FROM users WHERE name = :name AND age = :age",
    {"name": "Серёжа", "age": 21}
)
```

### Безопасность - SQL Injection

```python
# ❌ НИКОГДА ТАК - SQL Injection
name = input()
cursor.execute(f"SELECT * FROM users WHERE name = '{name}'")

# ✅ Всегда через параметры
cursor.execute("SELECT * FROM users WHERE name = ?", (name,))
```

### Проверка существования записи

```python
cursor = await db.execute("SELECT 1 FROM users WHERE id = ?", (user_id,))
exists = await cursor.fetchone() is not None
```

### WAL mode - быстрее при параллельных записях

```python
async with aiosqlite.connect("database.db") as db:
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")  # включить внешние ключи
```

### Индексы - для быстрого поиска

```sql
-- Создать индекс по колонке, по которой часто фильтруешь
CREATE INDEX IF NOT EXISTS idx_users_discord_id ON users(discord_id);

-- Составной индекс
CREATE INDEX IF NOT EXISTS idx_users_name_age ON users(name, age);
```

---

## Типы данных SQLite

| Тип | Python | Описание |
|-----|--------|----------|
| `INTEGER` | `int` | Целое число |
| `REAL` | `float` | Дробное число |
| `TEXT` | `str` | Строка |
| `BLOB` | `bytes` | Бинарные данные |
| `NULL` | `None` | Отсутствие значения |

> SQLite хранит `bool` как `0`/`1` (INTEGER). Python-драйвер автоматически конвертирует.
