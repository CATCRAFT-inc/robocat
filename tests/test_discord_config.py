import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml


def _config_copy(tmp_path: Path, **changes: int) -> Path:
    from bot import discord_config

    data = yaml.safe_load(discord_config.EXAMPLE_PATH.read_text(encoding="utf-8"))
    for dotted_name, value in changes.items():
        section, name = dotted_name.split("__", 1)
        data[section][name] = value
    path = tmp_path / "discord.yml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def test_reload_swaps_only_after_full_validation(tmp_path):
    from bot import discord_config

    original = discord_config.snapshot()
    bad = tmp_path / "discord.yml"
    bad.write_text("guilds: {main: nope}", encoding="utf-8")

    with pytest.raises(discord_config.ConfigError):
        discord_config.reload_config(bad)

    assert discord_config.snapshot() is original


def test_namespace_proxy_observes_reload_without_reimport(tmp_path):
    from bot import discord_config

    original_value = discord_config.Channels.bugs
    changed_value = 99999999999999999
    path = _config_copy(tmp_path, channels__bugs=changed_value)

    try:
        discord_config.reload_config(path)
        assert discord_config.Channels.bugs == changed_value
    finally:
        discord_config.reload_config(discord_config.EXAMPLE_PATH)

    assert discord_config.Channels.bugs == original_value


def test_reload_rejects_main_guild_change(tmp_path):
    from bot import discord_config

    path = _config_copy(tmp_path, guilds__main=99999999999999999)

    with pytest.raises(discord_config.ConfigError, match="guilds.main"):
        discord_config.reload_config(path)


@pytest.mark.parametrize(
    "contents",
    [
        "{}",
        "guilds: {main: 1138425078493753366}",
        "guilds: {main: true}",
    ],
)
def test_reload_rejects_incomplete_or_non_integer_config(tmp_path, contents):
    from bot import discord_config

    path = tmp_path / "discord.yml"
    path.write_text(contents, encoding="utf-8")

    with pytest.raises(discord_config.ConfigError):
        discord_config.reload_config(path)


def test_python_sources_have_no_raw_discord_ids():
    root = Path(__file__).resolve().parents[1]
    sources = [root / "main.py", *(root / "bot").rglob("*.py")]
    offenders = []
    for source in sources:
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, int)
                and not isinstance(node.value, bool)
                and 10**16 <= node.value < 10**20
            ):
                offenders.append(f"{source.relative_to(root)}:{node.lineno}")

    assert offenders == []


@pytest.mark.asyncio
async def test_config_role_check_observes_reload(tmp_path):
    from bot import discord_config

    original_admin = discord_config.Roles.admin
    changed_admin = 99999999999999999
    path = _config_copy(tmp_path, roles__admin=changed_admin)
    old_ctx = SimpleNamespace(
        author=SimpleNamespace(roles=[SimpleNamespace(id=original_admin)])
    )
    new_ctx = SimpleNamespace(
        author=SimpleNamespace(roles=[SimpleNamespace(id=changed_admin)])
    )
    role_check = discord_config.has_config_roles("admin").predicate

    assert await role_check(old_ctx)
    try:
        discord_config.reload_config(path)
        assert not await role_check(old_ctx)
        assert await role_check(new_ctx)
    finally:
        discord_config.reload_config(discord_config.EXAMPLE_PATH)


def test_derived_collections_and_ui_observe_reload(tmp_path):
    from bot import discord_config
    from bot.handlers.news_editor import _news_channels
    from bot.storage import Embeds

    changed_channel = 88888888888888888
    changed_role = 77777777777777777
    path = _config_copy(
        tmp_path,
        channels__announcements=changed_channel,
        roles__server_updates=changed_role,
    )

    try:
        discord_config.reload_config(path)
        assert discord_config.Channels.news_reaction_channels[0] == changed_channel
        assert _news_channels()["Объявления"] == changed_channel
        role_select = Embeds.role_choose().children[-1].children[0]
        assert role_select.options[0].value == str(changed_role)
    finally:
        discord_config.reload_config(discord_config.EXAMPLE_PATH)
