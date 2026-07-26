# Robocat

Discord bot of the CATCRAFT: Reborn Minecraft community — news, an AI assistant, 24/7 radio, tickets and moderation in one place.

English · [Русский](README.ru.md) · [中文](README.zh.md)

[![CI/CD](https://github.com/szarkans/robocat/actions/workflows/deploy.yml/badge.svg)](https://github.com/szarkans/robocat/actions)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE)

## What it does

**News**

- `/news` — news editor: modal form, ephemeral preview, publishing as rich (Components V2) posts; `/news_edit` reopens a published post for editing.
- `/digest` — LLM-written digest of recent news.
- VK repost — a manual gate: an editor reposts a published news item to the VK community with one click, media included.

**AI assistant**

- Answers when mentioned in channels; private AI threads via `/aichat` with rolling conversation compression.
- Searches the server wiki through embeddings, falls back to free web search (SearXNG or DuckDuckGo).
- Remembers facts about players, "watches" videos by sampling frames with ffmpeg, tracks token spend per source (`/aistats`).

**CatCraft FM**

- 24/7 radio in a voice channel with next/previous track voting and a supervisor that survives disconnects and restarts.

**Moderation & tickets**

- Admin tickets and bug reports with rate limiting.
- Punishments, a honeypot channel that auto-mutes spam bots, role selection, player lookup.

**Integrations**

- RCON commands to the Minecraft server.
- VK (news reposts), SearXNG/DuckDuckGo (web search).

## How it works

A single `disnake` bot process; every feature is a cog loaded in `main.py`.

- **Storage** — SQLite via `aiosqlite`. The core primitive is the flag system: arbitrary key/value flags attached to any Discord entity (user, channel, message, or abstract), used both for feature data and for runtime configuration.
- **LLM layer** — all model calls go through `bot/ai/llm.py`, the single access point. Providers are OpenAI-compatible endpoints (Gemini, Groq, OpenRouter, …) configured in `data/ai_settings.yaml`; the layer rotates vendors with per-vendor cooldowns on errors and accounts tokens.
- **Config** — Discord IDs (channels, roles, users) live in `data/discord.local.yml`, deliberately untracked: operators edit it on the server and apply with `/config-reload`, no restart needed.

## Deployment

Fully automated, no manual steps:

1. Push to `master`.
2. GitHub Actions runs the pytest gate.
3. On green, the workflow deploys over SSH to the VDS: installs dependencies, checks out the exact commit SHA, runs `data/db_init.py`.
4. The `robocat` systemd service is restarted, followed by a health check.

Rollback is a revert commit — it rides the same pipeline.

## Community

CATCRAFT: Reborn (Кошкокрафт) — a Russian-speaking Minecraft community.

- Discord: [discord.gg/catcraftmc](https://discord.gg/catcraftmc) (direct invite: [discord.gg/6f3FwFRJWC](https://discord.gg/6f3FwFRJWC))
- Telegram: [t.me/catcraftmc_tg](https://t.me/catcraftmc_tg)

## License

AGPL-3.0 — see [LICENSE](LICENSE). Derivatives, including network-service deployments, must be released under the same license.
