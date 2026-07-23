from pathlib import Path

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
