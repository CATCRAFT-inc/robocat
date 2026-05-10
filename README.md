# Robocat

[![CI/CD](https://github.com/szarkans/robocat/actions/workflows/deploy.yml/badge.svg)](https://github.com/szarkans/robocat/actions)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE)

Production Discord bot serving an online gaming community (~500 MAU).
Built with `disnake`, `asyncio`, PostgreSQL, and a multi-provider LLM integration layer.
Full development → containerization → CI/CD → production lifecycle.

> Live deployment: [discord.gg/6f3FwFRJWC](https://discord.gg/6f3FwFRJWC)

---

## Tech Stack

| Layer            | Technology                                                        |
| ---------------- | ----------------------------------------------------------------- |
| Runtime          | Python 3.12, `asyncio`                                            |
| Discord client   | `disnake` (Components V2)                                         |
| Database         | PostgreSQL (Supabase) · `asyncpg`                                 |
| LLM integration  | Provider rotation: Groq, OpenRouter, Moonshot AI                  |
| Containerization | Docker                                                            |
| CI/CD            | GitHub Actions → SSH deploy                                       |

LLM models in rotation: Gemma 4 31B, Gemini Embeddings 004
Available but not used: Llama 3.1 405B, Llama 3.3 70B Versatile, GPT-OSS 120B, DeepSeek V3, DeepSeek R1, Kimi K2.6.

---

## Features

- **Flag system** — attach arbitrary metadata to any Discord object (channel, user, category, message or abstract).
- **Ticket system** — administration contact, bug reports, in-game moderation appeals.
- **AI Integration** — multi-provider rotation with fallback on rate limits. Chatbot with users + archivist for Minecraft server's wiki
- **FAQ, utility, role selection, moderation commands**.

---

## Quick Start (local)

```bash
git clone https://github.com/szarkans/robocat.git
cd robocat

cp .env.example .env # Insert discord token and AI API keys

python -m venv .venv
pip install -r requirements.txt
```

---

## Deployment

Production deploys are fully automated:

1. `git push` to `master`
2. GitHub Actions runner picks up the workflow in `.github/workflows/`
3. Workflow deploys updated files to the production server via SSH
4. Container is restarted with the new build

No manual steps. Mean time from commit to production: 2–3 minutes.

---

## Project Structure

```
robocat/
├── bot/                    # cogs, handlers, services
├── data/                   # schema, migrations, fixtures
├── playground/             # experimental features, prototypes
├── .github/workflows/      # CI/CD pipelines
├── main.py                 # entry point
├── requirements.txt
└── .env.example
```

---

## License

AGPL-3.0 — see [LICENSE](LICENSE).

Commercial use, modification, and distribution are permitted provided derivatives are released under the same license, including network-service deployments.