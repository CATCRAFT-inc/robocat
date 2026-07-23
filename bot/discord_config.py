"""Reloadable Discord IDs.

`data/discord.local.yml` is deliberately untracked: production operators edit
it and apply the complete candidate with `/config-reload`.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

import yaml
from disnake.ext import commands

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_PATH = ROOT / "data" / "discord.example.yml"
LOCAL_PATH = ROOT / "data" / "discord.local.yml"

_DERIVED = {
    ("channels", "news_reaction_channels"): ("announcements", "newspaper"),
    (
        "channels",
        "digest_channels",
    ): ("announcements", "newspaper", "media_news", "informator"),
    (
        "roles",
        "premium_ai",
    ): ("owner", "st_admin", "admin", "developer", "moderator", "kotikplus", "booster"),
}


class ConfigError(RuntimeError):
    """The Discord ID file is missing, malformed, or unsafe to activate."""


@dataclass(frozen=True)
class DiscordSnapshot:
    sections: Mapping[str, Mapping[str, int]]
    source: Path


def _read_yaml(path: Path) -> dict:
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"Не удалось прочитать {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ConfigError(f"{path}: корнем YAML должен быть объект")
    return loaded


def _schema() -> dict[str, frozenset[str]]:
    data = _read_yaml(EXAMPLE_PATH)
    schema: dict[str, frozenset[str]] = {}
    for section, values in data.items():
        if not isinstance(section, str) or not isinstance(values, dict):
            raise ConfigError(f"{EXAMPLE_PATH}: некорректная секция {section!r}")
        schema[section] = frozenset(values)
    return schema


_SCHEMA = _schema()


def _validate(path: Path) -> DiscordSnapshot:
    data = _read_yaml(path)
    actual_sections = frozenset(data)
    expected_sections = frozenset(_SCHEMA)
    if actual_sections != expected_sections:
        missing = sorted(expected_sections - actual_sections)
        unknown = sorted(actual_sections - expected_sections)
        raise ConfigError(f"{path}: секции missing={missing}, unknown={unknown}")

    frozen: dict[str, Mapping[str, int]] = {}
    for section, expected_keys in _SCHEMA.items():
        values = data[section]
        if not isinstance(values, dict):
            raise ConfigError(f"{path}: секция {section!r} должна быть объектом")
        actual_keys = frozenset(values)
        if actual_keys != expected_keys:
            missing = sorted(expected_keys - actual_keys)
            unknown = sorted(actual_keys - expected_keys)
            raise ConfigError(
                f"{path}: секция {section!r}: missing={missing}, unknown={unknown}"
            )
        validated: dict[str, int] = {}
        for name, value in values.items():
            if isinstance(value, bool) or not isinstance(value, int):
                raise ConfigError(f"{path}: {section}.{name} должен быть целым Discord ID")
            if not 10**16 <= value < 10**20:
                raise ConfigError(f"{path}: {section}.{name} не похож на Discord ID")
            validated[name] = value
        frozen[section] = MappingProxyType(validated)
    return DiscordSnapshot(MappingProxyType(frozen), path)


def _initial_path() -> Path:
    if not LOCAL_PATH.exists():
        try:
            shutil.copyfile(EXAMPLE_PATH, LOCAL_PATH)
        except OSError as exc:
            raise ConfigError(f"Не удалось создать {LOCAL_PATH}: {exc}") from exc
    return LOCAL_PATH


_current = _validate(_initial_path())
_startup_main_guild_id = _current.sections["guilds"]["main"]


def snapshot() -> DiscordSnapshot:
    return _current


def reload_config(path: Path | None = None) -> DiscordSnapshot:
    """Validate a complete candidate, then atomically make it current."""

    global _current
    candidate = _validate(Path(path) if path is not None else LOCAL_PATH)
    if candidate.sections["guilds"]["main"] != _startup_main_guild_id:
        raise ConfigError("guilds.main нельзя менять без перезапуска бота")
    _current = candidate
    return candidate


class _Namespace:
    __slots__ = ("_section",)

    def __init__(self, section: str):
        self._section = section

    def __getattr__(self, name: str):
        derived = _DERIVED.get((self._section, name))
        if derived is not None:
            values = [getattr(self, item) for item in derived]
            return frozenset(values) if self._section == "roles" else tuple(values)
        try:
            return _current.sections[self._section][name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __dir__(self):
        names = set(_current.sections[self._section])
        names.update(name for section, name in _DERIVED if section == self._section)
        return sorted(names)


Guilds = _Namespace("guilds")
Channels = _Namespace("channels")
Roles = _Namespace("roles")
Users = _Namespace("users")
Bots = _Namespace("bots")
ForumTags = _Namespace("forum_tags")
Emojis = _Namespace("emojis")


def has_config_roles(*role_names: str):
    """A command check that resolves configured roles at invocation time."""

    unknown = set(role_names) - _SCHEMA["roles"]
    if unknown:
        raise ValueError(f"Неизвестные роли в конфиге: {sorted(unknown)}")

    async def predicate(ctx) -> bool:
        author = getattr(ctx, "author", None) or getattr(ctx, "user", None)
        configured = {getattr(Roles, name) for name in role_names}
        return any(getattr(role, "id", None) in configured for role in getattr(author, "roles", ()))

    return commands.check(predicate)
