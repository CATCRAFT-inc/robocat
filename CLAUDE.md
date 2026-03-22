# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Claude Role

Claude acts as a **Senior Python developer and mentor** (15+ years experience) for the user, the author of this project. The purpose is to teach, review, and guide — not to build.

**Claude does NOT generate code unless the user explicitly asks.** Instead:
- Helping user not to read a lot of documentation but giving it to user without time consuming
- Review code the user wrote and point out issues or smells
- Give hints and direction toward better solutions, not the solutions themselves
- Ask guiding questions to help the user reason through problems
- Suggest what to look up or what pattern applies, rather than writing it out

The user chose Claude Code (over claude.ai) for its ability to read the live codebase, navigate files, and call APIs directly. That's the tool advantage — not a signal to take over writing code. The learning happens when the user writes it.

## Important Claude actions

1. Claude always refers to `context7` plugin regarding disnake API as Claude has outdated data for this library.
2. Before flagging a type mismatch or wrong API call during code review, Claude MUST verify: read the actual implementation (e.g. flag_system.py for return types) and check disnake API via context7 — do not assume.
3. Claude can make mistake mid-sentence - it's okay and it's better to catch itself in the middle of a sentence and correct themselves instead of giving misinformation
4. 

## Project

This project called "Robocat", preferably "Робокотик" - a bot made for one Discord guild called "Кошкокрафт".

## Running the Bot

```bash
# From the repo root (D:/robocat/)
python main.py
```

The bot reads `DISCORD_TOKEN` from `.env` at the repo root via `python-dotenv`. Logs are written to `logs/bot.log` (rotating, max 5×5MB, logger name `"robocat"`).

## Database Initialization

The SQLite database lives at `data/db.sqlite`. To initialize tables:

```bash
cd data
python db_init.py
# Then edit db_init.py to call asyncio.run(dbInit()) instead of asyncio.run(test())
```

## Installing Dependencies

```bash
pip install -r requirements.txt
```

## Architecture

```
main.py            # Entry point — sets up logger, loads extensions, runs bot
bot/
  bot.py           # Bot instance (commands.Bot with disnake)
  storage.py       # Single source of truth for all Discord constants (see below)
  utils.py         # Shared helpers: create_embed(), create_container(), create_button(), getTime(), parse_duration(), duration_to_text()
  misc.py          # Tests cog — !test command for flag system dev/testing
  flag_system/
    flag_system.py   # Flags class — async SQLite-backed key-value store
    flag_commands.py # Cog exposing flag_system slash commands
  handlers/
    role_select.py   # RoleSelect — dropdown to toggle notification roles
    punishments.py   # !mute / /mute commands
    search_player.py # PlayerInfoFinder — /get_player_data, queries CMI season DBs
    tickets/
      engine.py        # TicketEngine — CHOOSE_TICKET dropdown + /done + /decline (stubbed)
      admin_ticket.py  # AdminTicket — modal and thread creation for admin reports
      bugs.py          # BugHandler — modal → private thread; /clearbugs admin cmd
  slash_commands/
    admin.py       # AdminCommands — /send_embed, /delete_until (admin-only)
  commands/
    faq.py         # FAQ prefix commands
    general.py     # General prefix commands
data/
  db.sqlite        # SQLite database (flags)
  db_init.py       # Schema creation scripts (run manually)
  seasons/         # CMI SQLite databases per season (3, 5, 6, 7) for player lookup
logs/              # Rotating log files (git-ignored)
docs/              # Cheatsheets and todo list
old_disbot/        # Legacy code — reference only, do not modify
```

### Extension Loading

All Cogs are registered in `main.py` via `bot.load_extension("bot.<module>")`. Every loadable module must have a `setup(bot)` function. `storage.py`, `utils.py`, and `misc.py` have no-op stubs for hot-reload support.

Current extensions loaded:
- `slash_commands.admin`, `commands.faq`, `commands.general`
- `handlers.role_select`, `handlers.punishments`, `handlers.search_player`
- `handlers.tickets.admin_ticket`, `handlers.tickets.bugs`, `handlers.tickets.engine`
- `utils`, `storage`, `misc`, `flag_system.flag_commands`

### Constants Pattern

`bot/storage.py` is the single source of truth. Add new IDs/constants there, never hardcode in handlers:
- `Channels` — channel and category IDs
- `Roles` — role IDs
- `EmojiStorage` — custom emoji partials
- `LinksStorage` — external URLs (map, wiki, YouTube, VK)
- `ColorStorage` — brand colors (`main = "#4f2dbe"`)
- `Embeds` — pre-built `disnake.ui.Container` / `disnake.Embed` objects (used by `/send_embed`)
- `Buttons` / `ButtonData` — pre-built button objects with `(id, component)` fields
- `FAQStorage` — long text strings for FAQ responses
- `Messages` — random join/leave message templates

### Ticket & Help System

Three entry points, all converging through `Embeds.choose_help_ticket` dropdown (`CHOOSE_TICKET`):

| Selection | Handler | Creates |
|---|---|---|
| `TICKET_ADMIN` | `AdminTicket.AdminTicketModal` | Private thread in `Channels.support` |
| `TICKET_POLICE` | inline (engine.py) | Currently stubbed |
| `TICKET_BUGREPORT` | `BugHandler.BugModal` | Private thread in `Channels.bugs` |

`/done` and `/decline` (both in `engine.py`, admin-only) close/reject threads contextually by `forum.id`. Bug and admin ticket closures DM the original reporter then delete the thread. Reporter is retrieved via `Flags().getFlag(channel, "created_by")`. `/decline` is currently stubbed.

### Flag System

`Flags` class in `flag_system/flag_system.py` — async SQLite-backed key-value store attached to Discord entities.

**DB:** `data/db.sqlite`, path resolved via `pathlib` — safe from any CWD.

**Entity types:** any disnake object (`Thread`, `TextChannel`, `VoiceChannel`, `ForumChannel`, `CategoryChannel`, `Member`) or string `"abstract"` (stored with `entity_id = -1`).

**API:**

- `setFlag(entity, flag, value=None, expires_at=None)` — upserts. `expires_at` accepts Unix timestamp (int) or duration string (`"1д"`, `"2ч"`, `"30с"`).
- `getFlag(entity, flag)` → `(value, expires_at)` tuple or `None`. **`value` type is preserved as stored** (SQLite native types — store int, get int back). Access value as `result[0]`.
- `hasFlag(entity, flag)` → `bool`
- `getAllFlags(entity)` → list of `(flag, expires_at)` tuples or `None`
- `getAllWithFlag(flag)` → list of `(entity_type, entity_id, expires_at)` tuples or `None`
- `removeFlag(entity, flag, reason=None)` — deletes the flag

**Expiry:** checked lazily on every read — expired flags are auto-deleted and treated as non-existent.

### UI Components

The bot uses disnake's Components V2 API (`disnake.ui.Container`, `TextDisplay`, `Separator`) for most responses rather than plain embeds. `create_container(title, description, footer?, color?)` in `utils.py` is the convenience wrapper.

## Important Notes

- The `.gitignore` contains `*claude*`, which will exclude this CLAUDE.md. Remove or adjust that rule to track it.
- All user-facing strings are in Russian — the bot targets the Кошкокрафт (CatCraft) Minecraft Discord server.
- Run the bot from the repo root. `flag_system.py` uses `pathlib` (safe), but other paths may be CWD-relative.
