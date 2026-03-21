# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Bot

```bash
# From the repo root (D:/robocat/)
python main.py
```

The bot reads `DISCORD_TOKEN` from `.env` at the repo root via `python-dotenv`.

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
main.py            # Entry point — loads all extensions, runs the bot
bot/
  bot.py           # Bot instance (commands.Bot with disnake)
  storage.py       # Single source of truth for all Discord constants (see below)
  utils.py         # Shared helpers: create_embed(), create_container(), create_button(), getTime(), parse_duration(), duration_to_text()
  flag_system/
    flag_system.py   # Flags class — async SQLite-backed key-value store for Discord entities
    flag_commands.py # Cog that exposes flag_system slash commands
  handlers/        # Cogs mixing button/dropdown listeners with their related slash commands
    bugs.py        # BugHandler — button click → modal → private thread; /clearbugs admin cmd
    role_select.py # RoleSelect — dropdown to toggle notification roles
    punishments.py # !mute / /mute commands
    get_help/
      engine.py      # TicketEngine — CHOOSE_TICKET dropdown dispatcher + /done command
      admin_ticket.py # AdminTicket — modal and thread creation for admin reports
  slash_commands/
    admin.py       # AdminCommands — /send_embed, /delete_until (admin-only)
  commands/
    faq.py         # FAQ prefix commands
    general.py     # General prefix commands
data/
  db.sqlite        # SQLite database
  db_init.py       # Schema creation scripts (run manually)
docs/              # Cheatsheets and todo list
old_disbot/        # Legacy bot code — do not modify, kept for reference only
```

### Extension Loading

All Cogs are registered in `main.py` via `bot.load_extension("bot.<module>")`. Every loadable module must have a `setup(bot)` function. `storage.py` and `utils.py` have no-op `setup()` stubs for hot-reload support.

Current extensions loaded:
- `slash_commands.admin`, `commands.faq`, `commands.general`
- `handlers.bugs`, `handlers.role_select`, `handlers.punishments`
- `handlers.get_help.admin_ticket`, `handlers.get_help.engine`
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

`/done` (in `engine.py`) closes threads contextually based on `forum.id` — handles ideas, requests, bugs, and admin tickets differently. Bug and admin ticket closures notify the original reporter via DM, then delete the thread. The reporter is looked up via `Flags.getFlag(channel, channel_id, "created_by")`.

### Flag System

`Flags` class in `flag_system/flag_system.py` provides async `setFlag`, `getFlag`, `hasFlag`, `getAllFlags`, `getAllWithFlag`, `removeFlag`. The DB path is resolved via `pathlib` relative to the file itself — safe to call from any CWD. Currently used to store `"created_by"` on bug and admin ticket threads.

### UI Components

The bot uses disnake's Components V2 API (`disnake.ui.Container`, `TextDisplay`, `Separator`) for most responses rather than plain embeds. `create_container(title, description, footer?, color?)` in `utils.py` is the convenience wrapper.

## Important Notes

- The `.gitignore` contains `*claude*`, which will exclude this CLAUDE.md. Remove or adjust that rule to track it.
- All user-facing strings are in Russian — the bot targets the Кошкокрафт (CatCraft) Minecraft Discord server.
- Run the bot from the repo root. `flag_system.py` uses `pathlib` (safe), but other paths may be CWD-relative.
