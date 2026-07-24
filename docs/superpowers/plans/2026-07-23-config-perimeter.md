# Discord Configuration and Perimeter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Discord IDs reloadable without deployment, block foreign-guild use, and correct the donation link.

**Architecture:** A validated YAML loader atomically swaps an immutable ID snapshot. Lightweight namespace proxies and dynamic role checks keep all consumers current without extension reloads. A Bot dispatch boundary suppresses foreign-guild events centrally.

**Tech Stack:** Python 3.12+, PyYAML, disnake 2.12, pytest.

## Global Constraints

- Runtime configuration is `data/discord.local.yml`; the committed source is `data/discord.example.yml`.
- `/config-reload` is the only runtime mutation command.
- Main guild is fixed for a process lifetime.
- Foreign guilds are ignored silently; the bot does not leave them.
- No new dependency.

---

### Task 1: Validated atomic configuration

**Files:**
- Create: `bot/discord_config.py`
- Create: `data/discord.example.yml`
- Modify: `.gitignore`
- Test: `tests/test_discord_config.py`

**Interfaces:**
- Produces: `reload_config(path: Path | None = None) -> DiscordSnapshot`
- Produces: `Guilds`, `Channels`, `Roles`, `Users`, `Emojis`, `ForumTags`, `Bots`
- Produces: `has_config_roles(*role_names: str)`

- [ ] **Step 1: Write failing loader tests**

```python
def test_reload_swaps_only_after_full_validation(tmp_path):
    original = config.snapshot()
    bad = tmp_path / "discord.yml"
    bad.write_text("guilds: {main: nope}", encoding="utf-8")
    with pytest.raises(ConfigError):
        config.reload_config(bad)
    assert config.snapshot() is original
```

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/test_discord_config.py -q`
Expected: import or assertion failure because the loader does not exist.

- [ ] **Step 3: Implement the minimal loader and namespace proxies**

```python
@dataclass(frozen=True)
class DiscordSnapshot:
    sections: Mapping[str, Mapping[str, int]]

def reload_config(path: Path | None = None) -> DiscordSnapshot:
    candidate = _load_and_validate(path or LOCAL_PATH)
    if _current is not None and candidate.guilds["main"] != _current.guilds["main"]:
        raise ConfigError("guilds.main requires restart")
    _current = candidate
    return candidate
```

- [ ] **Step 4: Verify GREEN**

Run: `.venv/bin/python -m pytest tests/test_discord_config.py -q`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add .gitignore bot/discord_config.py data/discord.example.yml tests/test_discord_config.py
git commit -m "feat: добавить горячую конфигурацию Discord ID"
```

### Task 2: Replace source ID constants and static authorization

**Files:**
- Modify: `bot/storage.py`
- Modify: all `bot/**/*.py` files importing ID namespaces
- Modify: `bot/bot.py`
- Test: `tests/test_extensions_import.py`
- Test: `tests/test_discord_config.py`

**Interfaces:**
- Consumes: namespace proxies and `has_config_roles()` from Task 1.
- Produces: zero active 17–20 digit Discord IDs in Python files.

- [ ] **Step 1: Add failing static and dynamic-role tests**

```python
def test_python_sources_have_no_raw_discord_ids():
    offenders = scan_active_python_snowflakes()
    assert offenders == []

async def test_config_role_check_observes_reload(member_interaction, config_file):
    config.reload_config(config_file(admin=111))
    assert await role_predicate("admin")(member_interaction(author_role=111))
    config.reload_config(config_file(admin=222))
    assert await role_predicate("admin")(member_interaction(author_role=222))
```

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/test_discord_config.py tests/test_extensions_import.py -q`
Expected: failures list current hardcoded IDs and stale role checks.

- [ ] **Step 3: Move IDs and replace decorators**

```python
from bot.discord_config import Channels, Roles, has_config_roles

@has_config_roles("admin", "st_admin")
async def command(...):
    ...
```

Keep non-ID links, copy, and reusable UI in `storage.py`; move only Discord
configuration and remove import-time derived ID collections.

- [ ] **Step 4: Verify GREEN**

Run: `.venv/bin/python -m pytest tests/test_discord_config.py tests/test_extensions_import.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add bot data/discord.example.yml tests
git commit -m "refactor: вынести Discord ID из Python-кода"
```

### Task 3: Reload command, server boundary, and donation

**Files:**
- Modify: `bot/bot.py`
- Modify: `bot/slash_commands/admin.py`
- Modify: `bot/commands/faq.py`
- Test: `tests/test_discord_config.py`
- Test: `tests/test_faq.py`

**Interfaces:**
- Consumes: `reload_config()`.
- Produces: `/config-reload`.

- [ ] **Step 1: Write failing perimeter, reload, and URL tests**

```python
def test_dispatch_ignores_foreign_guild(bot):
    bot.dispatch("message", message(guild_id=999))
    assert bot._dispatch_mock.call_count == 0

async def test_config_reload_keeps_old_snapshot_on_invalid_file(cog, interaction):
    await cog.configReload.callback(cog, interaction)
    assert config.Channels.bugs == ORIGINAL_BUGS

def test_donate_uses_canonical_url():
    assert "https://donate.catcraft.ru" in donate_source()
```

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/test_discord_config.py tests/test_faq.py -q`
Expected: perimeter and canonical URL assertions fail.

- [ ] **Step 3: Implement central dispatch guard and command**

```python
class MainGuildBot(commands.Bot):
    def dispatch(self, event_name, *args, **kwargs):
        guild_id = event_guild_id(args)
        if guild_id is not None and guild_id != self.main_guild_id:
            return
        return super().dispatch(event_name, *args, **kwargs)
```

`/config-reload` defers ephemerally, calls `reload_config()`, updates
`bot.owner_id`, and reports success or the validation error.

- [ ] **Step 4: Verify GREEN and milestone**

Run: `.venv/bin/python -m pytest tests/test_discord_config.py tests/test_faq.py tests/test_extensions_import.py -q`
Expected: all pass.

Run: `.venv/bin/python -m pytest -q`
Expected: no new failures beyond the documented baseline.

- [ ] **Step 5: Commit**

```bash
git add bot tests
git commit -m "feat: ограничить бота основным сервером"
```
