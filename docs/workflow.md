# Воркфлоу проекта — robocat

> Инжектится в `/lets-work` на каждом входе в задачу. Факты и правила, не эссе.

## Что это за проект

Discord-бот сервера Кошкокрафт (Minecraft-сообщество, ~500 MAU): Python + disnake,
SQLite, LLM-слой с ротацией вендоров. Прод — VDS `/opt/robocat`, systemd-сервис
`robocat`, деплой автоматом с пуша в master.

## Что значит «работает»

- База: полный `pytest -q` зелёный. Запускать ТОЛЬКО виндовым venv:
  `.venv/Scripts/python.exe -m pytest -q` (репо на /mnt/f, venv Windows).
- Правка shared-кода (`utils`, `llm`, `flags`) → полный прогон, не точечный файл.
- Живой прогон на dev-боте (`BOT_ENV=dev python main.py`) — по запросу Серёжи,
  не дефолтный гейт; сценарии обычно гоняет сам Серёжа.
- Codex полный pytest не гоняет (FM-тесты с `asyncio.to_thread` зависают в его
  sandbox) — прогонять самому.
- После деплоя: workflow сам делает health-check; логи — `/opt/robocat/logs/bot.log`
  (у Серёжи `rclogs [N]`, `rcrestart`); в journalctl попадают только print'ы.

## Гочи проекта

- PR, выросший из issue, обязан его закрывать: `Closes #NN` (или Fixes/Resolves)
  в теле PR — GitHub закроет issue при мерже в master. `Refs #NN` issue НЕ
  закрывает (прецедент: PR #33 ушёл с Refs, issue #32 закрывали руками).

- `data/db_init.py` гоняется на КАЖДОМ деплое → DELETE/DROP в нём молча трёт
  живые данные (прецедент: возврат памяти требовал убрать старый DELETE).
- Пуш в master = мгновенный деплой. Docs/config-only коммиты — `[ci skip]`.
- Циклический импорт: первым всегда `import bot.bot`, иначе `ImportError:
  partially initialized` (детали — докстринг `tests/conftest.py`).
- Мердж может тихо откатить рефакторинг → после мерджа сверить
  `git diff <merged-parent> HEAD --stat` (прецедент news_editor 25.07).
- Залипший `.git/index.lock` тихо съедает `git checkout` → после создания
  ветки проверять `git rev-parse` (прецедент 25.07: ветка отрезалась не от
  того коммита).
- Недоверенный текст (юзер, веб, выжимки тредов) в system-роль — только через
  `bot.utils.neutralize_sections`.
- Discord-интеракция после `defer` обязана получить ответ на всех ветках;
  ловить шире `NotFound` — `Forbidden`/5xx тоже.
- Path-scoped правила читать перед правкой домена: `.claude/rules/ai.md`
  (bot/ai/**), `.claude/rules/tests.md` (tests/**).
- Хуки/rtk: guard блокирует литералы «.env» и «reset --hard» в bash; rtk
  ломает `case`/`;;` и `VAR=$(git ...)` (тихо пусто).

- **Обновление вики-индекса** (`scripts/build_wiki_index.py`) гоняется НА VDS:
  ключ `GEMINI` привязан к стране биллинга, с машины Серёжи — `403
  PERMISSION_DENIED`. Системный `python3` там без `openai`, интерпретатор —
  `/opt/robocat/.venv/bin/python`, сервис от юзера `bots`:
  `sudo -u bots .venv/bin/python scripts/build_wiki_index.py --out /tmp/idx.json`.
  `--out` обязателен (иначе бот подхватит непроверенный индекс сразу), а файл
  назначения создавать СРАЗУ от `bots` — иначе запись упадёт на правах уже
  после оплаченных запросов (прецедент 02.08.2026, 48 эмбеддингов в трубу).
- **Долгий прогон на VDS запускать через `setsid`**, а не только `nohup`:
  сессия агента рвётся, и процесс уходит вместе с ней.
- **Следить за фоновым процессом по PID самого интерпретатора.** `pgrep -f`
  матчит и bash-обёртку, и `sudo`, и собственную командную строку наблюдателя.
  Дважды за 02.08.2026: watcher висел вечно на себе самом, а «умерший» прогон
  оказался живым — второй запуск сжёг ~250 запросов квоты впустую.
  Проверять `ps -u bots -o pid,etime,time,cmd`, а живость — по росту CPU и
  установленным сокетам, не по буферизованному логу.
- **Файлы со СМЕШАННЫМИ окончаниями строк** (`storage.py`, `faq.py`,
  `data/ai_settings.yaml`): Edit нормализует их в CRLF и раздувает дифф на
  весь файл. Править побайтово скриптом из `playground/`.

## Стоп-зоны (сигналы Tier-2)

- **Деплой-контур: `.github/workflows/deploy.yml` + `data/db_init.py`** —
  исполняются на проде при каждом пуше; ошибка = потеря живых данных или
  мёртвый бот.
- СТОП-правила CLAUDE.md действуют всегда: секреты `.env`, push в master
  (= деплой), деструктивный git, данные `data/db.sqlite`.

## Деплой и окружения

- Прод: VDS `/opt/robocat`, systemd `robocat`. Push в master → GitHub Actions:
  pytest-гейт → SSH → pip по SHA → `git reset --hard SHA` → `db_init.py` →
  restart → health-check.
- Откат: revert-коммит в master (едет тем же конвейером). Руками: `rcrestart`,
  логи `rclogs [N]` — у Серёжи в ~/.local/bin.
- Dev: `BOT_ENV=dev python main.py` (DEV_DISCORD_TOKEN из `.env`).
- Прод-БД в git не попадает; схема — только через `db_init.py` с продуманной
  миграцией существующих данных.
