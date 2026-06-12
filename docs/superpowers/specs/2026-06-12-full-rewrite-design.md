# Robocat — Bot Design Spec

Discord-бот для Minecraft-сервера (~500 MAU). Стек: **disnake**, **aiosqlite** (SQLite), **openai** (OpenAI-compatible API), **google-genai**, **PyYAML**, **python-dotenv**.

---

## 1. Flag System

Универсальное key-value хранилище, прикреплённое к любой сущности Discord.

**Entity types:** `member`, `channel`, `thread`, `category`, `forum`, `abstract` (не Discord-объект, ID=-1).

**Схема:**
```sql
CREATE TABLE flags (
    entity_type TEXT    NOT NULL,
    entity_id   INTEGER NOT NULL,
    flag        TEXT    NOT NULL,
    value       TEXT,
    expires_at  INTEGER,  -- unix timestamp, NULL = не истекает
    PRIMARY KEY (entity_type, entity_id, flag)
);
```

**Dataclass FlagRow:** поля `entity_type, entity_id, flag, value: str|None, expires_at: int|None`. Свойство `is_expired` → `expires_at is not None and expires_at < time()`.

**Класс FlagStore — публичный API:**
- `get(entity, flag)` → `FlagRow | None` — если флаг истёк, удаляет и возвращает None
- `set(entity, flag, value=None, expires_at=None)` — UPSERT. `value="+5"` / `value="-3"` → арифметика к текущему. `expires_at` принимает unix int ИЛИ строку (`"1д"`, `"8ч"`)
- `has(entity, flag)` → `bool` — просто вызывает `get() is not None`
- `get_all(entity)` → `list[FlagRow]` — только живые, без истёкших
- `get_all_with(flag)` → `list[FlagRow]` — все entity с этим флагом
- `remove(entity, flag)`

FlagStore создаётся один раз в `RobocatBot.__init__` и доступен как `bot.flags`.

**Slash-команды (admin-only, ephemeral):**
- `/flag_member`, `/flag_channel` — set/remove/info/list для member и channel
- info показывает value и `<t:{expires_at}:R>` (или "Никогда")
- list показывает все живые флаги entity

---

## 2. AI-система

### Engine

Cog. Загружает провайдеров из `data/ai_settings.yaml`:
```yaml
providers:
  - name: groq
    base_url: ...
    api_key_env: GROQ_API_KEY
    model: ...
  - name: openrouter
    ...
system_prompt: "..."
```

При rate limit (429) — ротация к следующему провайдеру.

**`generateAnswer(conversation: list[dict])` — async generator:**
- `yield StatusUpdate(text)` — промежуточные статусы (редактирует сообщение)
- `yield FinalAnswer(text)` — финальный ответ

`bot.ai_engine` устанавливается в `AIEngine.cog_load`.

### Handler (on_message)

Отвечает если: бот упомянут / ответ на сообщение бота / в AI-канале.

Лимит запросов через `bot.flags`: флаг `ai_requests` на member с expires_at = конец дня. Если `int(flag.value) >= MAX` → отказ.

Ответ: сначала "обрабатываю...", потом редактирует через статусы из генератора.

### Wiki Search

`WikiSearcher` — embedding-поиск по `data/wiki_index.json`. Используется в контексте ответа на вопросы о сервере.

### Idiot Check (on_message listener)

Regex pre-filter на возраст (паттерны "мне X лет", "X years old"). При совпадении → LLM structured output:
```python
class Idiot(BaseModel):
    isIdiot: bool
```
Если `isIdiot=True` → предупреждение в канале. Использует `bot.ai_engine`.

---

## 3. Ticket System

**Общая механика:** при открытии тикета создаётся приватный тред в нужном канале. Флаг `created_by` (entity=тред, value=str(member.id)) хранит автора.

**Типы тикетов:**

### Admin Ticket (обращение к администрации)
- Dropdown в спец. канале → Modal с полем "опишите проблему"
- После submit: создаётся приватный тред, пингуются роли `admin` + `st_admin`, кнопки "Закрыть" / "Отклонить"

### Bug Report
- Аналогично: dropdown → modal ("заголовок" + "описание" + "как воспроизвести")
- Тред в канале баг-репортов, пинг роли `dev`

**TicketEngine Cog (on_dropdown):**
- `/done` — закрывает тред (архивирует), доступно только ролям `admin`/`st_admin`/`dev`
- `/decline` — отклоняет тред, уведомляет автора (читает `created_by` флаг → `int(flag.value)` → `guild.get_member()`)

---

## 4. Moderation

### Mute (/mute и !мут)
- Slash: `/mute member duration reason`
- Prefix: `!мут @member 1ч причина`
- `parse_duration("1ч")` → секунды. Применяет `member.timeout(duration)`
- DM участнику с причиной и сроком

### RCON (/rcon)
Admin-only, ephemeral. Отправляет команду на Minecraft-сервер, возвращает полный ответ.

**Кастомный asyncio RCON-клиент** (без сторонних библиотек):

Протокол: `[Length 4B LE][RequestID 4B signed LE][Type 4B LE][Payload UTF-8 \x00\x00]`
Типы: AUTH=3, EXECCOMMAND=2, RESPONSE=0.

**Sentinel trick для полных ответов:**
1. Auth (type=3)
2. Отправить команду (ID=CMD_ID, type=2)
3. Отправить пустую команду-sentinel (ID=SENTINEL_ID, type=2)
4. Читать пакеты: накапливать payload где ID==CMD_ID, стоп когда пришёл ID==SENTINEL_ID
5. Вернуть конкатенированный ответ

```python
async def rcon_exec(host, port, password, command, timeout=10.0) -> str: ...
```

Env: `RCON_HOST`, `RCON_PORT` (default 25575), `RCON_PASSWORD`.

---

## 5. CatcraftFM Radio

Cog для воспроизведения интернет-радио в голосовом канале.

- `/fm play <url>` — подключиться и играть через `FFmpegPCMAudio`
- `/fm stop` — остановить и выйти
- Vote-skip: `/fm skip` — нужен порог голосов (например, >50% слушателей)
- Reconnect supervisor: при разрыве соединения — exponential backoff (1s, 2s, 4s... до 60s), потом переподключение

---

## 6. General

### FAQ
Prefix-команды: `!правила`, `!ip`, `!donate` и т.д. Возвращают embed с текстом из `storage.py`.

### Role Select (on_dropdown)
Dropdown с ролями (цвет ника, уведомления и т.д.) — toggle-логика: если роль есть → снять, нет → дать.

### Events
- `on_member_join` — приветственное DM
- `on_message` в канале новостей → автоматически создаёт тред к сообщению

### Admin Commands
- `/send_embed channel` — Modal для создания embed (title, description, color, image)
- `/delete_until message_id` — удалить сообщения в канале до указанного ID

---

## 7. Bot Structure

```python
class RobocatBot(commands.Bot):
    flags: FlagStore        # создаётся в __init__
    config: Config          # загружается из env
    ai_engine: AIEngine | None = None  # устанавливается в AIEngine.cog_load
```

Логгер в каждом cog: `logging.getLogger("robocat.<name>")`. Нет `print()`. Нет голых `except:`.

`storage.py` — все Discord ID (каналы, роли, эмодзи) как именованные константы.
