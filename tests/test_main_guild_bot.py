from types import SimpleNamespace
from unittest.mock import AsyncMock

import disnake
import pytest
from disnake.ext import commands

from bot.discord_config import Guilds, Users


@pytest.mark.asyncio
async def test_dispatch_suppresses_foreign_guild_events(monkeypatch):
    from bot.bot import MainGuildBot

    dispatched = []
    monkeypatch.setattr(
        commands.Bot,
        "dispatch",
        lambda self, event_name, *args, **kwargs: dispatched.append(event_name),
    )
    test_bot = MainGuildBot(
        main_guild_id=Guilds.main,
        command_prefix="!",
        intents=disnake.Intents.none(),
    )

    test_bot.dispatch(
        "message",
        SimpleNamespace(guild=SimpleNamespace(id=99999999999999999)),
    )
    test_bot.dispatch(
        "raw_member_remove",
        SimpleNamespace(guild_id=99999999999999999),
    )

    assert dispatched == []


@pytest.mark.asyncio
async def test_dispatch_keeps_main_guild_and_dm_events(monkeypatch):
    from bot.bot import MainGuildBot

    dispatched = []
    monkeypatch.setattr(
        commands.Bot,
        "dispatch",
        lambda self, event_name, *args, **kwargs: dispatched.append(event_name),
    )
    test_bot = MainGuildBot(
        main_guild_id=Guilds.main,
        command_prefix="!",
        intents=disnake.Intents.none(),
    )

    test_bot.dispatch(
        "message",
        SimpleNamespace(guild=SimpleNamespace(id=Guilds.main)),
    )
    test_bot.dispatch("message", SimpleNamespace(guild=None))

    assert dispatched == ["message", "message"]


@pytest.mark.asyncio
async def test_config_reload_reports_invalid_candidate_without_owner_change(monkeypatch):
    from bot.discord_config import ConfigError
    from bot.slash_commands import admin

    fake_bot = SimpleNamespace(owner_id=Users.szarkan)
    cog = admin.AdminCommands(fake_bot)
    inter = SimpleNamespace(
        response=SimpleNamespace(defer=AsyncMock()),
        edit_original_response=AsyncMock(),
    )
    monkeypatch.setattr(
        admin,
        "reload_config",
        lambda: (_ for _ in ()).throw(ConfigError("битый конфиг")),
    )

    await admin.AdminCommands.configReload.callback(cog, inter)

    inter.response.defer.assert_awaited_once_with(ephemeral=True)
    assert fake_bot.owner_id == Users.szarkan
    assert "битый конфиг" in inter.edit_original_response.await_args.args[0]


@pytest.mark.asyncio
async def test_config_reload_updates_bot_owner(monkeypatch):
    from bot.slash_commands import admin

    fake_bot = SimpleNamespace(owner_id=0)
    cog = admin.AdminCommands(fake_bot)
    inter = SimpleNamespace(
        response=SimpleNamespace(defer=AsyncMock()),
        edit_original_response=AsyncMock(),
    )
    monkeypatch.setattr(admin, "reload_config", lambda: object())

    await admin.AdminCommands.configReload.callback(cog, inter)

    assert fake_bot.owner_id == Users.szarkan
    assert "перезагружен" in inter.edit_original_response.await_args.args[0]


@pytest.mark.asyncio
async def test_config_reload_restarts_fm_when_channel_changes(monkeypatch):
    from bot.slash_commands import admin

    channels = SimpleNamespace(catcraft_fm=1)
    fm = SimpleNamespace(restart_for_config_reload=AsyncMock())
    fake_bot = SimpleNamespace(
        owner_id=0,
        get_cog=lambda name: fm if name == "CatcraftFM" else None,
    )
    cog = admin.AdminCommands(fake_bot)
    inter = SimpleNamespace(
        response=SimpleNamespace(defer=AsyncMock()),
        edit_original_response=AsyncMock(),
    )

    def _reload():
        channels.catcraft_fm = 2

    monkeypatch.setattr(admin, "Channels", channels)
    monkeypatch.setattr(admin, "reload_config", _reload)

    await admin.AdminCommands.configReload.callback(cog, inter)

    fm.restart_for_config_reload.assert_awaited_once()
