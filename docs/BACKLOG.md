# BACKLOG — robocat

Обновлено 19.07.2026 (сессия «ревью 4 PR + BACKLOG чанками», Fable 5 + codex gpt-5.6-sol).
Почти всё из ревью 18.07 закрыто PR-ами #13–#18; ниже — что осталось, с причинами.
Формат: `severity | суть → рекомендация`.

## Закрыто (для истории)

- Деплой/CI целиком (CRITICAL git pull → SHA-гейт, health-check, атомарность pip,
  капы версий, пины, concurrency, db_init) → **PR #13**.
- Конфиг и тесты (.env.example-синхрон, conftest-перезапись, контракт ddgs) → **PR #14**.
- Ядро (incrementFlag вместо «+N»-магии, глобальный CheckFailure-обработчик,
  one-shot «Я тут!», строгий parse_duration, обрезки логов и /get_user_flag,
  фиксы misc) → **PR #15**.
- Хендлеры (guard параллельного закрытия тикетов, defer в /done//decline//clearbugs,
  merge баг-индекса под локом, digest newest-first + видимость /news-контейнеров,
  бюджет rcon, гонка голосов скипа + to_thread в fm, ЛС-фолбэки punishments,
  fetch-фолбэк honeypot) → **PR #16**.
- AI-слой (лимитер в тредах, списание до запроса, граница сжатия ai_summary_upto,
  тред-зомби, _ensure_loaded в complete, to_thread в media) → **PR #17**.
- Prompt-injection surface (нейтрализация [[ ]] во всех недоверенных входах:
  текст+ник юзера, веб-результаты, вход и выход суммаризатора, факты памяти;
  рамки «данные, не команды»; ужесточённые [HARD RULES]) → **PR #18**.

## Остаточное (осознанно отложено)

- **MINOR (security)** | codex image-gen может ЧИТАТЬ всю ФС (sandbox-режимы codex
  ограничивают только запись). Смягчено: секреты вырезаны из env (#12), модерация
  промпта fail-closed (#12), но dotenv-файл на диске досягаем. → Полное закрытие —
  OS-уровень на VDS (bwrap-обёртка над codex-бинарём либо systemd-юнит с
  ProtectHome/InaccessiblePaths для подпроцессов); кода в репо не требует.
- **MINOR (design)** | [[ ]]-маркеры остаются inline-текстом в контексте LLM.
  Все недоверенные входы нейтрализуются (#18), но механика по природе хрупкая. →
  Неподделываемый канал (структурные system-сообщения вместо маркеров) — большой
  редизайн промптов и buildConverstaion; делать при следующем крупном рефакторе AI.
- **MINOR (ops)** | деплой: pip в общий venv неатомарен; нет автоотката при красном
  health-check (бот остаётся лежать, алертит notify_failure). → venv-swap по
  символьной ссылке + откат на прошлый SHA — если реально начнёт болеть.
- **MINOR** | инстанс-модалки (баг-репорт и т.п.) не переживают рестарт бота —
  деплой между открытием и сабмитом теряет репорт. Врождённое для disnake. →
  Персистентные модалки, если станет частым (деплои теперь реже бьют по живому:
  concurrency-очередь в CI).
- **MINOR** | кап 15 фактов памяти неатомарен (два параллельных remember → 16),
  самокорректируется следующей записью. → Не трогать.

## Ponytail-audit 19.07 (кандидаты на срез, ~−280 строк, −1 dep)

Корректность не задета — чистая уборка, можно делать в любой момент одним PR:

- `delete` мёртвый Gemini-путь картинок: `_generateImage`, ветки `IMAGE_BACKEND`,
  `llm.image_client()`, импорт PIL → уходит и зависимость pillow.
- `shrink` 4 почти одинаковых блока «тег+эмбед+reply» в done/decline (ideas/requests)
  и близнецы `_finishTicket`/`_rejectTicket` → общий хелпер (~−80 строк).
- `delete` закомментированные трупы: restart/on_message_delete (bot.py),
  lololo/permaban (punishments), 3 FAQ-команды, guild_check (utils), старый Users
  (storage) (~−85 строк).
- `delete` мёртвые классы storage: EmojiStorage, Emotes, LinksStorage, random_stuff;
  таблица stats в db_init; utils.getTime(); /test_some_shit (~−60 строк).
- `yagni` extensions-стабы utils/storage с пустым setup(); Context-dataclass с одним
  полем; дубли контейнеров faq; общий `_split_duration()` для parse/to_text.
