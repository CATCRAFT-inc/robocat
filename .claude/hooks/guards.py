#!/usr/bin/env python3
"""Предохранители СТОП-ПРАВИЛ (PreToolUse на Bash). Блокировка = exit 2.

Это tripwire от СЛУЧАЙНОСТЕЙ, не security boundary — останавливает ошибку,
а не злоумышленника. Покрывает:
  (1) force-push в main/master или без явной ветки;
  (2) чтение секретов .env через shell (cat/head/grep/...);
  (3) деструктивный git: clean -f..x.., reset --hard, checkout -- .

Правила описаны в CLAUDE.md → «СТОП-ПРАВИЛА». Ошибка парсинга = allow
(fail-open), кроме payload, похожего на force-push.
"""
import json
import re
import sys


def block(msg: str):
    sys.stderr.write(
        f"BLOCKED по СТОП-ПРАВИЛУ (CLAUDE.md): {msg} "
        "Если это явно нужно пользователю — пусть выполнит сам.\n"
    )
    sys.exit(2)


data = sys.stdin.read()
try:
    payload = json.loads(data.lstrip("﻿").strip())
    cmd = (payload.get("tool_input") or {}).get("command") or ""
except Exception:
    if re.search(r"\bpush\b", data) and re.search(r"(--force|\s-f\s)", data):
        block("нечитаемый payload хука с упоминанием force-push.")
    sys.exit(0)

# Каждый shell-стейтмент проверяется отдельно, чтобы `...; git status`
# не маскировал совпадение.
for raw in re.split(r";|&&|\|\||\||\r?\n", cmd):
    stmt = raw.strip()
    if not stmt:
        continue
    words = [w.strip("'\"") for w in stmt.split()]

    # --- (1) force-push в main/master ---
    if re.search(r"\bgit\b[\s\S]*\bpush\b", stmt):
        force = any(
            w == "-f" or re.fullmatch(r"--force(-with-lease(=.+)?)?", w)
            for w in words
        )
        if force and "push" in words:
            after = words[words.index("push") + 1:]
            branch_args = [
                w for w in after
                if not w.startswith("-") and w.lower() not in ("origin", "upstream")
            ]
            protected = any(
                re.fullmatch(r"(main|master)", w) or re.fullmatch(r"\S+:(main|master)", w)
                for w in branch_args
            )
            if protected or not branch_args:
                block(
                    "force-push в main/master (или без явной ветки) запрещён — "
                    "master автодеплоится на прод. Пушь фиче-ветку."
                )

    # --- (2) чтение .env (разрешён .env.example) ---
    if re.search(r"\b(cat|type|more|less|head|tail|bat|strings|xxd|grep|rg|sed|awk|findstr|source)\b", stmt, re.I):
        reads_env = any(
            re.search(r"(^|[\\/])\.env(\.[\w.-]+)?$", w, re.I)
            and not re.search(r"\.env\.example$", w, re.I)
            for w in words
        )
        if reads_env:
            block(
                "чтение секретов .env в транскрипт запрещено (секретность токена). "
                "Структура переменных — в .env.example."
            )

    # --- (3) деструктивный git ---
    if re.search(r"\bgit\b", stmt):
        if "clean" in words:
            # -f и -x/-X могут прийти отдельными токенами (`clean -f -x`) или
            # слитно (`clean -fdx`) — проверяем флаги независимо по всем токенам
            force = any(
                w == "--force" or (re.fullmatch(r"-[a-zA-Z]+", w) and "f" in w[1:])
                for w in words
            )
            removes_ignored = any(
                re.fullmatch(r"-[a-zA-Z]+", w) and "x" in w[1:].lower()
                for w in words
            )
            if force and removes_ignored:
                block(
                    "`git clean` с -f и -x удаляет gitignored-файлы (.env, база, STATUS.md). "
                    "Только по явной просьбе пользователя."
                )
        if "reset" in words and "--hard" in words:
            block(
                "`git reset --hard` уничтожает незакоммиченную работу. "
                "Только по явной просьбе пользователя."
            )
        if "checkout" in words:
            i = words.index("checkout")
            if len(words) > i + 2 and words[i + 1] == "--" and words[i + 2] == ".":
                block(
                    "`git checkout -- .` сбрасывает все незакоммиченные правки. "
                    "Только по явной просьбе пользователя."
                )

sys.exit(0)
