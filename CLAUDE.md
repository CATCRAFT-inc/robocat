# CLAUDE.md — robocat

Discord-бот сервера **Кошкокрафт** (CatCraft, Minecraft-сообщество, ~500 MAU). Python + disnake, SQLite (aiosqlite), LLM-слой с ротацией вендоров. Прод — VDS, systemd-сервис `robocat`, деплой автоматом с пуша в master.

## Команды

```bash
python main.py                  # запуск бота (из корня репо)
BOT_ENV=dev python main.py      # dev-режим: берёт DEV_DISCORD_TOKEN вместо DISCORD_TOKEN
pytest -q                       # тесты (asyncio_mode=auto, сеть и .env не нужны)
cd data && python db_init.py    # инициализация схемы SQLite (однократно)
```

Секреты — в `.env` (см. `.env.example`): токены Discord, ключи LLM-вендоров (`GEMINI`, `GROQ`, `OR`, `GHM`, `DS`), `RCON_HOST`/`RCON_PASSWORD`, `FAILURE_WEBHOOK_URL`.

## 🛑 СТОП-ПРАВИЛА (катастрофические — не нарушать)

Текстовые правила соблюдаются ~70%, хуки — 100%, поэтому где можно — стоит предохранитель (`.claude/hooks/guards.py`). Сработавший guard — это фича, а не препятствие: НЕ ищи обходных путей.

1. **Секретность токена и ключей.** Содержимое `.env` никогда не попадает в код, логи, git или транскрипт (хук блокирует чтение `.env` через shell; структура — в `.env.example`). Прецедент 12.06.2026: коммит «НЕ ТОТ ТОКЕН».
2. **Push в master = мгновенный деплой на прод** (GitHub Actions → SSH на VDS → `systemctl restart robocat`). Пушить только с зелёным `pytest`. Коммиты/пуши — только по просьбе пользователя. Правки только доков/конфига — с `[ci skip]` в сообщении коммита. Force-push в master заблокирован хуком.
3. **Деструктивный git** (`reset --hard`, `clean -fdx`, `checkout -- .`) — только по явной просьбе пользователя в этом же разговоре. Хук блокирует.
4. **`data/db.sqlite` на VDS — живые данные сообщества** (флаги, тикеты). Схему менять только через `data/db_init.py`, продумав миграцию существующих данных.

## Архитектура

```
main.py                    # entry point: логгер, список extensions, bot.run()
bot/
  bot.py                   # инстанс Bot (prefix "!", intents all) + базовые listeners (реакции на новости, welcome)
  storage.py               # ЕДИНСТВЕННЫЙ источник Discord ID и констант — см. «Паттерны»
  utils.py                 # create_embed/create_container/create_button, parse_duration, время
  misc.py                  # мелочи, !test
  flag_system/
    flag_system.py         # СИНГЛТОН `flags` — async SQLite key-value на Discord-сущностях, ленивый expiry
    flag_commands.py       # слэш-команды управления флагами
  ai/                      # весь ИИ; домен описан в .claude/rules/ai.md — прочитай его перед правками тут
    llm.py                 # единая точка LLM API: ротация вендоров, кулдауны, utility-модель, strip_thoughts
    engine.py              # AIEngine: generateAnswer, тулзы, генерация картинок (в т.ч. через codex), idiotCheck
    handler.py             # AIMessageHandler: AI-чаты в тредах, кнопка «Завершить», авто-удаление неактивных
    embeddings.py          # gemini-embedding-001 (768d), ленивый клиент; дедуп багов, поиск по вики
    wiki_search.py         # поиск по data/wiki_index.json (9.5 MB, коммитится)
  handlers/
    tickets/               # admin_ticket, bugs, engine — тикеты: транскрипты, AI-саммари, дедуп багов по эмбеддингам
    punishments.py         # /mute с пресетами и русским парсингом длительности
    catcraft_fm.py         # радио CatCraft FM
    digest.py              # /digest — выжимка новостей
    honeypot.py            # ловушка для ботов (мут + кнопки модерации)
    idiot_check.py rcon.py role_select.py search_player.py get_a_job.py
  slash_commands/admin.py  # /send_embed, /delete_until (admin-only)
  commands/                # префикс-команды: faq, general
data/
  db.sqlite                # SQLite (gitignored); схема — db_init.py
  ai_settings.yaml         # промпты и вендоры LLM (коммитится, БЕЗ ключей — ключи в .env)
tests/                     # pytest; conftest.py критичен — см. .claude/rules/tests.md
```

Новый ког регистрируется в списке `extensions` в `main.py`; каждому нужен `setup(bot)`.

## Паттерны

- **Константы**: `bot/storage.py` — единственный источник Discord ID (`Channels`, `Roles`, `Guilds`, `Messages`, `Embeds`, `Buttons`, `FAQStorage`, `LinksStorage`, `ColorStorage`). Никогда не хардкодь ID в хендлерах.
- **Флаги**: импортируй синглтон — `from bot.flag_system.flag_system import flags`. Не создавай `Flags()` заново.
- **UI**: Components V2 (`disnake.ui.Container`, `TextDisplay`, `Separator`), обёртка `create_container()` из `utils.py`, а не голые embeds.
- **🛑 Циклический импорт** `bot.utils ↔ bot.bot ↔ bot.storage`: первым всегда импортируется `bot.bot` (как в `main.py` и `tests/conftest.py`). Импорт `bot.utils`/`bot.storage` раньше `bot.bot` роняет процесс с `ImportError: partially initialized module`. Подробности — в докстринге `tests/conftest.py`.
- Все пользовательские строки — на русском.

## Path-scoped правила

Автозагружаются только при Read матчащего файла; при работе по домену через grep/git show — прочитай правило сам:

- `.claude/rules/ai.md` ← `bot/ai/**`, `data/ai_settings.yaml*`
- `.claude/rules/tests.md` ← `tests/**`, `conftest.py`, `pytest.ini`

Новый домен с гочами → новый файл в `.claude/rules/`, а не раздувание CLAUDE.md.

## Тесты и верификация

- `pytest -q` — быстрые, без сети (conftest подставляет фейковые env). Перед любым push в master — обязательно.
- Правка shared-кода (`utils`, `llm`, `flags`) → полный прогон, не точечный файл.
- Нетривиальная фича/фикс → адверсарная проверка сабагентом `verifier` (`.claude/agents/verifier.md`) перед коммитом; для второго мнения другой моделью — глобальный `/multi-code-review`.

## Деплой и прод

- Push в master → GitHub Actions (`.github/workflows/deploy.yml`) → SSH на VDS → `git pull` + `pip3 install` + `systemctl restart robocat`.
- Прод живёт в `/root/robocat` на VDS; его БД — там же (в git не попадает).
- Логи: локально `logs/bot.log` (ротация), на VDS — `journalctl -u robocat`.
- `notify_failure.py` (untracked, копия с VDS) — Discord-вебхук при падении сервиса.
- Коммит только доков/конфига — добавляй `[ci skip]`, чтобы не перезапускать прод впустую.

## Язык и коммиты

- Код, идентификаторы — English; докстринги, комментарии, коммиты, доки, STATUS.md — русский.
- Стиль коммитов как в истории: `тип: краткое описание по-русски` (`feat:`, `фикс:`/`fix:`, `chore:`, `рефакторинг:`, `тесты:`, `документация:`).

## STATUS.md

`STATUS.md` в корне (untracked, авто-инъекция хуком на старте сессии) — текущее состояние «объясняю коллеге за кофе», ≤35 строк. Обновляй на каждой точке ожидания: перед возвратом хода пользователю и перед запуском долгих задач.
