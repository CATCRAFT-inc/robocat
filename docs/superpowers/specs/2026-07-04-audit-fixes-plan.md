# План: фиксы аудита + новые фичи (2026-07-04)

Продовый Discord-бот сервера Кошкокрафт. **disnake 2.12.0** (исходники: `.venv/Lib/site-packages/disnake/`), Python 3.12, aiosqlite, OpenAI SDK ≥2.32. Все строки для пользователей — **на русском**, в стиле существующих («котики», дружелюбно).

## Правила для исполнителей

1. Трогай **только файлы своего пакета**. Нужно чужое — пиши в `cross_file_requests` отчёта, сам не редактируй.
2. disnake API **не вспоминай по памяти** — сверяйся со шпаргалкой (путь в промпте) и локальным исходником `.venv/Lib/site-packages/disnake/`. Память моделей про disnake系统атически врёт.
3. Никаких коммитов. Только правки рабочего дерева. В дереве уже есть незакоммиченные правки — строй поверх текущего состояния.
4. Никаких новых зависимостей.
5. Минимализм: кратчайшее работающее решение, без спекулятивных абстракций. UI — `disnake.ui.Container`/`TextDisplay` через `create_container()` из `bot/utils.py`, где уместно.
6. ID каналов/ролей — только из `bot/storage.py` (`Channels`, `Roles`, `Guilds`), не хардкодить.

## Уже сделано ПМ (не переделывать)

- `main.py`: запятая в `os.getenv("BOT_ENV", "prod")`, `os.makedirs("logs", exist_ok=True)`, зарегистрированы экстеншены `handlers.honeypot` и `handlers.digest`.
- `bot/storage.py`: `Guilds.main`, `Channels.honeypot`, `Channels.news_reaction_channels`, `Channels.digest_channels`.

## Контракты интерфейсов (для параллельной работы W3 ↔ W4/W5)

### `bot/ai/llm.py` — создаёт W3, используют W4 (саммари тикетов) и W5 (/digest)

```python
class AIUnavailable(Exception):
    """Все вендоры на кулдауне/недоступны."""

class LLM:
    async def ask(self, prompt: str, *, system: str | None = None,
                  max_tokens: int = 1024, use_utility: bool = False) -> str:
        """Одноразовый вопрос → текст. Кидает AIUnavailable."""
    async def parse(self, prompt: str, schema: type[pydantic.BaseModel], *,
                    system: str | None = None) -> pydantic.BaseModel:
        """Структурный вывод через utility-модель. Кидает AIUnavailable."""
    async def complete(self, messages: list[dict], *, tools: list | None = None,
                       max_tokens: int = 1024, temperature: float = 0.6,
                       top_p: float = 1) -> "ChatCompletion":
        """Низкий уровень для engine.py: полный ответ SDK, с ротацией вендоров."""

llm = LLM()  # модульный синглтон, ленивая инициализация при первом вызове
```

Семантика ротации: вендоры из `data/ai_settings.yaml` по порядку; на `RateLimitError` — кулдаун вендора 15 мин и переход к следующему; на `AuthenticationError` — кулдаун 6 ч; на `InternalServerError`/`APIConnectionError` — 1 ретрай, затем следующий вендор. Все на кулдауне → `AIUnavailable`. Учёт токенов: `flags.setFlag("abstract", "token_used", f"+{usage.total_tokens}")` после каждого успешного ответа.

### `bot/ai/embeddings.py` — создаёт W3, использует W4 (дедуп багов)

```python
async def embed(text: str) -> list[float]:   # 768-мерный вектор, gemini-embedding-001 через OpenAI SDK
def cosine(a: list[float], b: list[float]) -> float
```

Клиент — ленивый `AsyncOpenAI(base_url="https://generativelanguage.googleapis.com/v1beta/openai/", api_key=os.getenv("GEMINI"))`. **Никакой инициализации клиента на импорте модуля** (тесты импортируют без сети).

---

## W1 «core»: `bot/bot.py`, `bot/utils.py`, `bot/handlers/punishments.py`, `bot/misc.py`, `notify_failure.py`

1. **utils.parse_duration**: при любом кривом вводе возвращать `None` (не кидать ValueError). Единицы: `сек`, `мин`, `ч`, `д`, `н`, `мес` (месяц, 30д), `г`. **`м` = минуты** (раньше был месяц — источник бага /mute). Пустые цифры/неизвестная единица → `None`.
2. **utils.duration_to_text**: переписать — выделять число и суффикс так же, как parse_duration (не «последний символ»); поддержать склонения для всех единиц: сек/мин/ч/д/н/мес/г («5 секунд», «15 минут», «1 час», «3 дня», «2 недели», «1 месяц», «1 год»...). Кривой ввод → вернуть исходную строку (не кидать).
3. **punishments.py**: убрать мёртвые ветки под старую семантику; `parse_duration` теперь возвращает `None` — существующие проверки заработают. Проверить, что choices `/mute` (`5сек`,`15мин`,`1ч`,`3д`,`28д`) проходят весь путь мут→ответ→ЛС. `mute_member.send` обернуть в try/except `disnake.Forbidden` (закрытые ЛС не должны ронять ответ). В prefix-команде `!мут`: `ref_message.author` может быть не Member (вебхук) — guard.
4. **bot.py `on_ready`**: `get_channel` может вернуть `None` → guard `if channel:`.
5. **bot.py `on_message` (реакции+тред в новостных)**: список каналов заменить на `Channels.news_reaction_channels` (архивный канал уже выброшен). Пропускать ботов (`message.author.bot`) и не-дефолтные типы сообщений (`message.type != disnake.MessageType.default`). `create_thread` обернуть в try/except `disnake.HTTPException`.
6. **bot.py `on_raw_member_remove`**: фильтр `payload.guild_id != Guilds.main → return`.
7. **bot.py `on_member_join`**: сравнение с гильдией через `Guilds.main` (не литерал).
8. **misc.py**: `restrictArtem123zzz` — guard `if message.guild is None: return`, `delete()` в try/except (`Forbidden`, `NotFound`). `!mess` — добавить `@commands.is_owner()` и `@commands.guild_only()`; убрать протухший `MediaGalleryItem` (подписанная CDN-ссылка истекла) — оставить текст без галереи.
9. **notify_failure.py**: если `FAILURE_WEBHOOK_URL` пуст — `sys.exit(1)` с print в stderr; `urlopen` в try/except с печатью ошибки.

Приёмка: `parse_duration("5сек")==5`, `parse_duration("15мин")==900`, `parse_duration("5м")==300`, `parse_duration("1мес")==2592000`, `parse_duration("abc") is None`, `parse_duration("") is None`; `duration_to_text("15мин")=="15 минут"`, `duration_to_text("28д")=="28 дней"`.

## W2 «flags»: `bot/flag_system/flag_system.py`, `bot/flag_system/flag_commands.py`

1. **Инкремент `"+N"/"-N"` не должен стирать `expires_at`** и должен быть атомарным: выполнять в одном соединении/транзакции (`BEGIN IMMEDIATE` или один UPSERT с `COALESCE(:expires_at, flags.expires_at)` для expires_at). Нечисловое текущее значение → warning + `False`, без записи.
2. **`setFlag` возвращает `bool`** (True — записано). Все существующие вызовы игнорируют результат — сигнатура обратно совместима.
3. **`_defineEntityType`**: добавить `disnake.User` → `"member"` (проверять вместе с Member). Причина: `on_raw_member_remove` отдаёт User для незакешированных.
4. **flag_commands**: `expires_at` от админа валидировать ДО setFlag (через `parse_duration`, который теперь возвращает None) → при кривом вводе ephemeral-ответ «Неверный формат времени. Примеры: 30сек, 15мин, 8ч, 1д, 2н, 1мес». Если `setFlag` вернул False → честный ответ «Не удалось установить флаг (неподдерживаемый тип канала или нечисловое значение для +N)». `_format_flag_list`: усекать вывод до ~3500 символов с припиской «-# …и ещё N».

Приёмка: параллельные 20×`setFlag(x,"c","+1")` (`asyncio.gather`) дают value=20; `setFlag(user,"a",1,expires_at="8ч")` затем `setFlag(user,"a","+1")` — expires_at сохранён; `getFlag` по User-объекту работает.

## W3 «ai»: `bot/ai/llm.py` (новый), `bot/ai/embeddings.py` (новый), `bot/ai/engine.py`, `bot/ai/handler.py`, `bot/ai/wiki_search.py`, `data/ai_settings.yaml`, `data/ai_settings.yaml.example`, `requirements.txt`, `playground/embed_compare.py` (новый)

1. **llm.py** и **embeddings.py** — строго по контрактам выше. В `ai_settings.yaml` добавить блок:
   ```yaml
   utility_model:
     model: "gemini-3.1-flash-lite"
     base_url: https://generativelanguage.googleapis.com/v1beta/openai/
     env: GEMINI
     extra_body:            # опционально, пробрасывается в запрос как есть
       extra_body:
         google:
           thinking_config: {thinking_level: low, include_thoughts: false}
   ```
2. **engine.py** — остаётся чат-ботом (персона, тулзы, событийный стрим Status/FinalAnswer/AIError), но:
   - все вызовы API через `llm.complete` (ротация теперь там); `_getNewClient`/`self.client` выпилить;
   - `idiotCheck` → `llm.parse` (модель больше не захардкожена);
   - `buildConverstaion`: картинку кодировать **только у последнего** сообщения списка (это текущее сообщение юзера) и только если у вендора `has_vision` (спросить у `llm` текущий вендор) — иначе текстовая пометка; `content_type` может быть `None` — guard; починить мёртвую ветку index;
   - `_executeTool`: если тул не вернул `_ToolDone` — подставить `"[[ Tool returned nothing ]]"` вместо креша на `result.content`;
   - `FinalAnswer.attachments` — всегда список;
   - системный промпт: подстановка даты через `.replace("{date}", ...)`, в yaml заменить `{}` на `{date}` (защита от фигурных скобок в промпте);
   - Каомодзи/строки персоны не менять.
3. **handler.py**:
   - **AI-треды (фича)**: `/aichat` (роли как сейчас) ставит на созданный тред флаги `ai_chat=1` и `created_by=<id>`; приветствие без костыля «ответь на моё сообщение». В листенере: сообщение в треде с флагом `ai_chat` (и не бот, и не префикс `!`) → отвечать **без пинга**. Контекст: `[system] + [саммари из флага ai_summary, если есть] + последние сообщения треда` (`thread.history`, до 40 сообщений, старые→новые), обрезка старых сверх бюджета **16000 символов**. После ответа: если обрезанная часть превысила 6000 символов — `asyncio.create_task` фонового сжатия: `llm.ask(use_utility=True)` сжимает [старое саммари + обрезанные сообщения] в ≤1500 символов → `flags.setFlag(thread, "ai_summary", текст)`. Ошибка фоновой задачи — только в лог.
   - Обычный режим (пинг/реплай в каналах) остаётся как есть, но: обход reply-цепочки должен переживать `DeletedReferencedMessage` (у него нет `.author` — проверить тип, при нём прекращать подъём);
   - лимитер как есть (треды премиумные — и так без лимита);
   - `/reloadai`: порядок — сначала `_loadAIData`, потом пересоздание клиента (теперь через `llm`); `/aiinfo` — показать текущий вендор + кулдауны.
4. **wiki_search.py**: убрать google-genai; `search()` → `async`, эмбеддинг запроса через `embeddings.embed`; вызов в `engine._executeTool` — `await`. Формат `data/wiki_index.json` не менять.
5. **playground/embed_compare.py**: скрипт сравнения качества поиска: 5 фиксированных запросов («как попасть на сервер», «пивоварение», «что такое АРы», «кланы», «запрещённые моды»), топ-5 URL старым путём (google-genai, он ещё в .venv) vs новым (OpenAI SDK); печать пересечения топов. Запускается вручную, в бота не грузится.
6. **requirements.txt**: удалить `requests`, `asyncpg`, `google-genai` (нигде не импортируются после миграции). `dave.py` и `pynacl` НЕ трогать (нужны голосу/E2EE).

Приёмка: `python -c "import bot.ai.llm, bot.ai.embeddings"` без сети/ключей не падает; ротация: замоканный клиент с RateLimitError на 1-м вендоре → ответ со 2-го; `grep -r "google.genai" bot/` пусто.

## W4 «tickets»: `bot/handlers/tickets/engine.py`, `bot/handlers/tickets/bugs.py`

1. **bugs.py: `self.bot.flags` → `flags`** (модульный импорт уже есть) — сейчас кнопка баг-репорта падает с AttributeError. КРИТИЧЕСКИЙ фикс.
2. **engine.py: `/done` и `/decline`** — первой строкой проверять `isinstance(inter.channel, disnake.Thread)`, иначе ephemeral-ответ (сейчас `.parent` крашится в обычных каналах). `get_tag_by_name` может вернуть `None` → guard (тег переименовали — честное сообщение, не креш).
3. **Транскрипт + AI-саммари при закрытии (фича)**: в ветках `Channels.bugs` и `Channels.support` (`/done` и `/decline`) перед `channel.delete()`:
   - собрать историю треда (`thread.history(limit=None, oldest_first=True)`);
   - транскрипт: `[YYYY-MM-DD HH:MM] Автор: текст` (+ URL вложений), в `disnake.File` из BytesIO, имя `ticket-<thread_id>.txt`;
   - выжимка: `llm.ask` («Сожми тикет: кто создал, суть проблемы, что решили», ≤600 токенов); при `AIUnavailable`/ошибке — «⚠️ Выжимка недоступна», транскрипт постим всё равно;
   - пост в `Channels.ticket_log`: контейнер (название треда, тип, кто закрыл, комментарий/причина, выжимка) + файл;
   - только потом ЛС юзеру и удаление треда.
4. **Дедуп баг-репортов (фича)** в bugs.py:
   - индекс `data/bug_index.json`: `{thread_id: {"text": str, "url": str, "vector": [float]}}`; загрузка/сохранение — маленькие sync-функции, запись под `asyncio.Lock`;
   - в `BugModal.callback` после создания треда: `await embed(описание)` (из `bot.ai.embeddings`, контракт выше); при ошибке — молча пропустить (лог). Похожесть `cosine ≥ 0.72` с существующими → пост в тред: «🔍 Возможно, этот баг уже репортили:» + до 3 ссылок; затем добавить новый баг в индекс;
   - удалять запись из индекса при закрытии треда бага в `/done`/`/decline` (cross-file: это engine.py — твой же пакет) и в `/clearbugs`;
   - `/rebuild_bug_index` (роли admin/st_admin): пройти активные треды канала багов, взять первый пост бота (описание), перестроить индекс; ответ с количеством.
5. **Напоминалка о зависших тикетах (фича)** в engine.py: `tasks.loop(time=datetime.time(hour=9, tzinfo=timezone(timedelta(hours=3))))` (12:00 МСК): активные треды `Channels.bugs` и `Channels.support` с флагом `created_by`, у которых `disnake.utils.snowflake_time(thread.last_message_id or thread.id)` старше 5 дней → один контейнер со списком ссылок в `Channels.secret`. Пусто → не постить. Запуск лупа в `cog_load`, остановка в `cog_unload`.

Приёмка: кнопка баг-репорта не обращается к `bot.flags`; `/done` в текстовом канале отвечает, а не крашится; в `/done` бага порядок: транскрипт+лог → ЛС → delete.

## W5 «handlers»: `bot/handlers/honeypot.py` (новый), `bot/handlers/digest.py` (новый), `bot/commands/faq.py`, `bot/handlers/get_a_job.py`, `bot/handlers/role_select.py`, `bot/slash_commands/admin.py`, `bot/handlers/search_player.py`

1. **honeypot.py (фича, новый ког)**: сообщение в `Channels.honeypot` (чат войса «Для Ботов») от не-бота:
   - удалить сообщение (try/except);
   - `author.timeout(duration=timedelta(days=28), reason="Ловушка: сообщение в канале-приманке")` — 28д максимум Discord; `Forbidden` (админ) → только лог;
   - лог-пост в `Channels.discord_logs`: пинг `<@&{Roles.moderator}>`, кто (mention + id), текст сообщения (усечь до 1000), кнопки `🔨 Забанить` (`custom_id=f"HONEYPOT_BAN:{user_id}"`, danger) и `😇 Помиловать` (`HONEYPOT_PARDON:{user_id}`, green);
   - `on_button_click` по префиксу custom_id: право — любая из ролей admin/st_admin/moderator, иначе ephemeral-отказ. BAN → `guild.ban(user, reason=..., clean_history_duration=timedelta(days=1))` (сверить точный параметр с disnake 2.12!); PARDON → снять таймаут (`member.timeout(duration=None)`); после действия — отредактировать лог-пост (кнопки убрать, дописать итог и кто нажал). Юзер мог уже выйти → `guild.ban` работает по объекту юзера/Object, guard на NotFound.
2. **digest.py (фича, новый ког)**: `/digest дней: int 1..14 = 7` (описание на русском):
   - кулдаун: флаг `digest_cd` 6ч, `Roles.premium_ai` — без кулдауна; на кулдауне → ephemeral с `<t:...:R>`;
   - `inter.response.defer(ephemeral=True)`;
   - по `Channels.digest_channels`: `channel.history(after=datetime.now(timezone.utc)-timedelta(days=N), limit=200, oldest_first=True)`, собрать `[#канал] автор: текст` (пропуская пустые/системные), общий бюджет 15000 символов — при переполнении отбрасывать самое старое;
   - нет сообщений → «За этот период новостей не было =(»;
   - `llm.ask(system="Ты — Робокотик...", prompt="Сделай выжимку новостей по темам, с маркерами...")`, ответ в контейнере ephemeral; `AIUnavailable` → вежливая ошибка.
3. **faq.py**: `send_faq` — `fetch_message` в try/except (`NotFound`, `HTTPException`) с фолбэком на `ctx.channel.send`; `findFAQInMessages` — выходить, если `message.author.bot` или бот упомянут в сообщении (иначе двойной ответ с AI).
4. **get_a_job.py**: кнопка отвечает ephemeral-заглушкой: «Пока вакансий нет — сезон ещё не начался! Загляни позже =)» (вместо мёртвого return).
5. **role_select.py**: `add_roles`/`remove_roles` в try/except `disnake.Forbidden` → ephemeral «У меня не хватает прав на эту роль — сообщи админам!».
6. **admin.py**: `/delete_until` — `await inter.channel.purge(after=message.created_at)` (bulk, сверить сигнатуру по disnake 2.12: limit=None означает «все»), ответ с количеством; старше 14 дней purge удаляет медленно поштучно — приемлемо. `/send_embed` — собрать словарь `{имя: объект}` из атрибутов `Embeds` без `_`-префиксов, параметр с `choices` из этого словаря;无 совпадения → ответ «нет такого».
7. **search_player.py**: довести до работающего минимума: `defer()`; `licenseInfo` возвращает `(False, None)` при не-200/не-found; `searchCmi` — пропускать отсутствующие файлы БД (`Path.exists`), убрать `print`; в конце — контейнер: лицензия да/нет, online/offline UUID, ники из CMI (или «не найден»). Мёртвую `searchPlayerData` удалить.

Приёмка: honeypot не банит сам (только мут+кнопки); у digest есть кулдаун и defer; `/send_embed` больше не принимает `__module__`.

## Тесты (пакет тестировщика)

Окружение: свежий venv в scratchpad, `pip install disnake aiosqlite pytest pytest-asyncio pyyaml pydantic openai pillow python-dotenv tinytag`. Тесты в `tests/` репозитория, `conftest.py` добавляет корень в `sys.path`.

1. `python -m py_compile` на все файлы проекта (main.py, bot/**, data/db_init.py, notify_failure.py).
2. Импорт-смоук: с фейковыми env (`DISCORD_TOKEN=x`, `GEMINI=x` и т.д.) импортировать каждый модуль `bot.*` — ни один не должен требовать сеть на импорте.
3. `tests/test_utils.py`: parse_duration (валидные все единицы, `м`=минуты, кривые→None), duration_to_text (склонения, `5сек`, `15мин`, `28д`).
4. `tests/test_flags.py` (tmp sqlite): set/get, expiry, `+1` сохраняет expires_at, 20 параллельных `+1` = 20, disnake.User-подобная сущность, нечисловой `+N` → False.
5. `tests/test_llm.py`: ротация с замоканным клиентом (1-й вендор кидает RateLimitError → ответ со 2-го; все в кулдауне → AIUnavailable).
6. `tests/test_chunking.py`: `_buildLongMessage` — длинный текст с код-блоком режется с переносом ```.

Тесты НЕ ходят в сеть и НЕ требуют реального токена.
