# Robocat Full Rewrite — Design Document

> **Для нового чата:** Прочитай этот документ полностью перед тем как что-то писать.
> Запусти `superpowers:writing-plans` для создания плана реализации на основе этого диздока.

---

## Цель

Написать того же бота с нуля чистым кодом. Функциональность та же, код — новый, без багов оригинала, с нормальной архитектурой.

**Не меняем:** SQLite (Supabase недоступен без VPN), disnake, логику AI-провайдеров, FM-радио, функционал флагов.

**Меняем:** структуру файлов, паттерны, устраняем все архитектурные проблемы оригинала.

---

## Известные баги оригинала (не повторять)

1. `hasFlag` — `float(None)` краш на постоянных флагах (NULL `expires_at`)
2. `flag_commands.py` — `getAllFlags` вызывался с двумя аргументами вместо одного
3. `flag_commands.py` — `FlagRow[0]` вместо `FlagRow.value`
4. `flag_commands.py` — `<t:None:R>` для постоянных флагов
5. `tickets/engine.py` — `get_member(str)` вместо `get_member(int)` в `/decline`
6. `tickets/bugs.py` — `FlagRow[0]` вместо `.value`
7. `ai/handler.py` — `message.reference.resolved.author` без проверки на None
8. `idiot_check.py` — `AIEngine()` без `load_ai()`, `client = None` → краш
9. `ai/engine.py` — trailing comma: `conversation = [...],` → tuple вместо list
10. `punishments.py` — `except HTTPException` после `except Exception` — мёртвый код
11. `utils.py` — `create_embed` всегда перезаписывает цвет дефолтным
12. `Flags()` — новый инстанс на каждый вызов в ticket engine вместо синглтона

---

## Стек

| Что | Чем |
|-----|-----|
| Discord | disnake >= 2.12 |
| База данных | SQLite + aiosqlite |
| AI | openai (OpenAI-compatible API) + google-genai (embeddings) |
| Конфиг | python-dotenv + PyYAML |
| Аудио (FM) | disnake[music] + tinytag |
| RCON | Кастомный asyncio-клиент (без mcrcon — он обрезает ответы) |

---

## Структура файлов

```
robocat/
├── main.py                        # точка входа: логирование, загрузка когов, bot.run()
├── requirements.txt
├── .env                           # токены, API ключи, RCON
│
├── bot/
│   ├── core/
│   │   ├── bot.py                 # класс RobocatBot(commands.Bot) с типизированными атрибутами
│   │   └── config.py              # загрузка env-переменных в датакласс Config
│   │
│   ├── flags/
│   │   ├── models.py              # FlagRow dataclass
│   │   ├── store.py               # FlagStore — вся логика флагов
│   │   └── cog.py                 # slash-команды управления флагами (admin only)
│   │
│   ├── ai/
│   │   ├── engine.py              # AIEngine cog: провайдеры, generateAnswer(), idiotCheck()
│   │   ├── handler.py             # on_message handler, лимиты, /aichat, /aiinfo
│   │   └── wiki.py                # WikiSearcher: embedding-поиск по индексу
│   │
│   ├── tickets/
│   │   ├── helpers.py             # create_ticket() — общая логика создания приватного треда
│   │   ├── admin.py               # AdminTicket modal + cog
│   │   ├── bugs.py                # BugHandler modal + cog
│   │   └── engine.py              # TicketEngine: on_dropdown, /done, /decline
│   │
│   ├── moderation/
│   │   ├── punishments.py         # /mute, !мут
│   │   └── rcon.py                # /rcon slash-команда
│   │
│   ├── music/
│   │   └── fm.py                  # CatcraftFM radio cog
│   │
│   ├── general/
│   │   ├── faq.py                 # FAQ prefix-команды
│   │   ├── roles.py               # RoleSelect on_dropdown
│   │   ├── events.py              # on_member_join, on_message (новости→тред)
│   │   └── admin.py               # /send_embed, /delete_until, /test_some_shit
│   │
│   ├── utils/
│   │   ├── time.py                # parse_duration(), duration_to_text()
│   │   ├── embeds.py              # create_embed(), create_container()
│   │   └── rcon_client.py         # низкоуровневый async RCON (protocol impl)
│   │
│   └── storage.py                 # константы: IDs каналов, ролей, эмодзи, тексты
│
└── data/
    ├── db.sqlite                  # runtime (создаётся при первом запуске)
    ├── db_init.py                 # скрипт создания схемы
    ├── ai_settings.yaml           # конфиг AI-провайдеров и системный промпт
    └── wiki_index.json            # предрассчитанные векторы для wiki-поиска
```

---

## Компоненты

### 1. `bot/core/bot.py` — RobocatBot

Подкласс `commands.Bot` с явно типизированными атрибутами:

```python
class RobocatBot(commands.Bot):
    flags: FlagStore
    config: Config
    ai_engine: AIEngine | None = None  # устанавливается в AIEngine.cog_load()
```

`FlagStore` создаётся в `__init__` бота и передаётся в когды через `self.bot.flags`. Никаких глобальных синглтонов.

### 2. `bot/core/config.py` — Config

Датакласс, загружается из env один раз при старте:

```python
@dataclass
class Config:
    discord_token: str
    dev_token: str
    rcon_host: str
    rcon_port: int
    rcon_password: str
    failure_webhook: str

    @classmethod
    def from_env(cls) -> "Config": ...
```

### 3. `bot/flags/store.py` — FlagStore

Вся логика флагов. Без глобального состояния, без синглтона — инжектируется через `bot.flags`.

**Публичное API:**
```python
class FlagStore:
    def __init__(self, db_path: Path)

    async def get(self, entity, flag: str) -> FlagRow | None
    async def set(self, entity, flag: str, value=None, expires_at: int | str | None = None)
    async def has(self, entity, flag: str) -> bool
    async def get_all(self, entity) -> list[FlagRow]          # только живые, без истёкших
    async def get_all_with(self, flag: str) -> list[FlagRow]  # все entity с этим флагом
    async def remove(self, entity, flag: str, reason: str = "")
```

**Правила реализации:**
- `get()` — единственное место, где проверяется `is_expired`. Если истёк — удаляет и возвращает `None`.
- `has()` — вызывает `get()`, проверяет `is not None`. Никакой отдельной логики.
- `set()` с `+N`/`-N` строкой — арифметика через `int(value)` (Python правильно парсит `"+5"` → 5).
- UPSERT: `value = :value, expires_at = :expires_at` — без `COALESCE`, чтобы можно было обнулить.
- `expires_at` как строка (`"1д"`, `"8ч"`) → конвертируется в unix timestamp через `parse_duration`.

**Entity resolution:**

```python
def _resolve(entity) -> tuple[str, int]:
    # возвращает (entity_type_str, entity_id)
    # "abstract" → ("abstract", -1)
    # disnake.Member → ("member", member.id)
    # и т.д.
```

### 4. `bot/flags/models.py` — FlagRow

```python
@dataclass
class FlagRow:
    entity_type: str
    entity_id: int
    flag: str
    value: str | None
    expires_at: int | None

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and self.expires_at < int(time.time())
```

**Важно:** `value` всегда `str | None` — SQLite хранит TEXT. Вызывающий код делает `int(flag.value)` когда нужно число.

### 5. `bot/utils/rcon_client.py` — RCON

Кастомный asyncio-клиент. `mcrcon` не используется — он обрезает ответы сервера.

**Протокол:**
- Пакет: `[Length 4B LE][RequestID 4B LE signed][Type 4B LE][Payload UTF-8 \x00][\x00]`
- Типы: AUTH=3, EXECCOMMAND=2, RESPONSE=0
- Аутентификация: ID=-1 в ответе → неверный пароль

**Сбор многопакетных ответов (sentinel trick):**
1. Отправить команду (ID=2)
2. Отправить пустую команду-sentinel (ID=3)
3. Читать пакеты, накапливать payload с ID=2
4. Получили ID=3 → конец ответа

**Публичное API:**
```python
async def rcon_exec(
    host: str,
    port: int,
    password: str,
    command: str,
    timeout: float = 10.0
) -> str:
    """Полный ответ сервера, включая многопакетные."""
```

Никакого контекст-менеджера — просто функция. Каждый вызов открывает соединение, отправляет команду, закрывает.

### 6. `bot/tickets/helpers.py` — Shared ticket logic

В оригинале `admin.py` и `bugs.py` дублировали 40 строк создания тикета. Выносим в хелпер:

```python
async def create_ticket(
    channel: disnake.TextChannel | disnake.ForumChannel,
    author: disnake.Member,
    title: str,
    body_container: disnake.ui.Container,
    notify_roles: list[int],
    flag_store: FlagStore,
) -> disnake.Thread:
    """Создаёт приватный тред, пингует роли, ставит флаг created_by, возвращает тред."""
```

### 7. `bot/ai/engine.py` — AIEngine

Cog. Основные правила:

- `cog_load`: загружает YAML, создаёт клиент, устанавливает `self.bot.ai_engine = self`
- `cog_unload`: закрывает клиент
- `generateAnswer` — async generator: `yield Status(...)`, `yield FinalAnswer(...)`
- `idiotCheck` — отдельный метод, использует тот же клиент
- Нет `_buildFinalMessage` (мёртвый метод)
- `conversation` в `idiotCheck` — список, без trailing comma

### 8. `bot/ai/handler.py` — AIMessageHandler

- `on_message`: проверка `resolved = message.reference.resolved if message.reference else None`
- Лимит запросов через `bot.flags` (не через `self.ai_engine.flags`)
- После `cog_load` устанавливает `bot.ai_engine = self.ai_engine`

---

## Схема БД

```sql
CREATE TABLE IF NOT EXISTS flags (
    entity_type TEXT    NOT NULL,
    entity_id   INTEGER NOT NULL,
    flag        TEXT    NOT NULL,
    value       TEXT,
    expires_at  INTEGER,
    PRIMARY KEY (entity_type, entity_id, flag)
);
```

Других таблиц в активном использовании нет. `stats` и `left_players` из оригинала нигде не используются — не создавать.

---

## Правила кода

**Запрещено:**
- `print()` — только `logger.xxx()`
- `except:` без типа — всегда `except SomeError as e:`
- `Flags()` или `FlagStore()` внутри когов — только `self.bot.flags`
- Голые `try/except Exception` там где можно поймать конкретную ошибку
- `except SpecificError` после `except Exception` — мёртвый код
- Нет комментариев объясняющих ЧТО — только ПОЧЕМУ (если неочевидно)

**Обязательно:**
- Аннотации типов на всех публичных методах
- `cog_load` / `cog_unload` для инициализации/очистки ресурсов
- Логгер в каждом cog: `self.logger = logging.getLogger("robocat.<name>")`
- `ephemeral=True` для всех ошибок пользователю

---

## Ветка

Работаем на новой ветке `rewrite` от `master` (не от `rework`):

```bash
git checkout master
git checkout -b rewrite
```

Ветка `rework` остаётся как референс — там все баги уже исправлены, можно подсматривать код.

---

## Что переносится без изменений

- `bot/music/fm.py` (CatcraftFM) — написана хорошо, только переносим в новое место
- `data/ai_settings.yaml` — структура не меняется
- `data/wiki_index.json` — не трогаем
- `bot/storage.py` — IDs каналов и ролей, только переносим
- Regex в `IdiotCheck` — правильный, переносим как есть

---

## Порядок реализации (для writing-plans)

Рекомендуемый порядок задач:

1. **Scaffold** — структура папок, `main.py`, `core/bot.py`, `core/config.py`, `storage.py`
2. **FlagStore** — `flags/models.py`, `flags/store.py`, `data/db_init.py`
3. **Utils** — `utils/time.py`, `utils/embeds.py`, `utils/rcon_client.py`
4. **Flag commands** — `flags/cog.py`
5. **Moderation** — `moderation/punishments.py`
6. **General** — `general/faq.py`, `general/roles.py`, `general/events.py`, `general/admin.py`
7. **Tickets** — `tickets/helpers.py`, `tickets/bugs.py`, `tickets/admin.py`, `tickets/engine.py`
8. **RCON** — `moderation/rcon.py` (использует `utils/rcon_client.py`)
9. **AI** — `ai/wiki.py`, `ai/engine.py`, `ai/handler.py`
10. **FM Radio** — `music/fm.py`
11. **Финальная проверка** — `main.py` загружает все коги, синтаксис, структура

Задачи 3-6 можно делать параллельно агентами (нет зависимостей между собой).
Задачи 7-10 параллельно после задач 2-3.

---

## Чеклист перед мержем

- [ ] Все 27 `.py` файлов проходят `python3 -m ast`
- [ ] Нет `print()` вне `main.py`
- [ ] Нет `Flags()` / `FlagStore()` внутри когов
- [ ] Нет `except:` без типа
- [ ] `bot.flags` инжектируется через `RobocatBot.__init__`
- [ ] `bot.ai_engine` устанавливается в `AIEngine.cog_load`
- [ ] Все slash-команды возвращают ответ (нет зависших interaction)
- [ ] RCON использует sentinel-trick для полных ответов
