# Discord configuration and server perimeter

## Scope

This milestone resolves #20, adds the requested main-server-only boundary, and
fixes the donation URL from #23.

## Configuration

Discord IDs move out of Python source into `data/discord.example.yml`. At
runtime the bot reads `data/discord.local.yml`; when it is missing, the loader
copies the committed example once. The local file is ignored by git, so the
production `git reset --hard` deploy does not overwrite live changes.

`bot/discord_config.py` owns parsing, validation, and the current immutable
snapshot. Existing call sites keep readable namespaces such as
`Channels.bugs`, `Roles.admin`, and `Users.szarkan`, but those namespace
objects resolve values from the current snapshot on every access.

`/config-reload` is the only runtime command. It reads the whole file into a
new snapshot, validates every required key and Discord snowflake, and swaps the
snapshot only after validation succeeds. Invalid files leave the previous
configuration active. The main guild ID is fixed for the lifetime of the
process because application-command registration is scoped at startup; reload
rejects an attempted main-guild change.

Role decorators use a dynamic `has_config_roles()` check so changed role IDs
take effect without extension reloads. Derived lists and sets are properties,
not import-time snapshots.

## Server boundary

The bot silently suppresses guild events and command processing when their
guild ID differs from the startup main guild. Direct messages retain their
existing behavior. The bot does not leave foreign guilds.

## Other changes

All active raw Discord IDs outside the YAML configuration are replaced by
named configuration entries. `!донатик` points to
`https://donate.catcraft.ru`.

## Failure behavior and tests

- A malformed or incomplete local file fails startup with a readable error.
- A failed reload keeps the last valid snapshot.
- Foreign-guild messages, interactions, and raw guild events are not
  dispatched.
- Reloaded channel and role IDs are observed without a restart.
- Donation command output contains the canonical URL.

