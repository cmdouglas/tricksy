"""Local credentials file (ROADMAP.md 3.1, DESIGN.md §7.1).

A signed-in device holds its bearer token in ``~/.config/tricksy/config.json`` (honouring
``XDG_CONFIG_HOME``) rather than an environment variable, because a token minted by
``tricksy login`` has to outlive the shell that ran it. The file holds a map of named **profiles**,
each one player - ``--profile``/``TRICKSY_PROFILE`` selects one at invocation time. Profiles are
what let one person play all four seats of a game from one machine, which is this phase's own
dogfooding milestone (ROADMAP.md 3.7).

Nothing in this module talks to the network or reads ``--profile``/``TRICKSY_PROFILE``. It is pure
file I/O over a JSON shape, reused as-is starting in ROADMAP.md 3.4 by every command that needs
credentials.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Final

DEFAULT_API_URL: Final = "http://127.0.0.1:8000"  # DESIGN.md §7.3


class ConfigError(Exception):
    """The config file exists but is not valid JSON in the shape this module writes."""


@dataclass(frozen=True, slots=True)
class Profile:
    player_id: str
    username: str
    token: str


@dataclass(frozen=True, slots=True)
class Config:
    api_url: str = DEFAULT_API_URL
    profiles: Mapping[str, Profile] = field(default_factory=dict)


def config_path() -> Path:
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg_config_home) if xdg_config_home else Path.home() / ".config"
    return base / "tricksy" / "config.json"


def load(path: Path | None = None) -> Config:
    resolved = path if path is not None else config_path()
    try:
        raw = resolved.read_text()
    except FileNotFoundError:
        return Config()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{resolved} is not valid JSON: {exc}") from exc

    return _config_from_dict(data, resolved)


def save(config: Config, path: Path | None = None) -> None:
    resolved = path if path is not None else config_path()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(resolved.parent, 0o700)

    data = _config_to_dict(config)
    fd, tmp_name = tempfile.mkstemp(dir=resolved.parent, prefix=".config-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, resolved)
    except BaseException:
        os.unlink(tmp_name)
        raise


def get_profile(config: Config, name: str) -> Profile | None:
    return config.profiles.get(name)


def set_profile(config: Config, name: str, profile: Profile) -> Config:
    profiles = dict(config.profiles)
    profiles[name] = profile
    return replace(config, profiles=profiles)


def remove_profile(config: Config, name: str) -> Config:
    if name not in config.profiles:
        return config
    profiles = dict(config.profiles)
    del profiles[name]
    return replace(config, profiles=profiles)


def _config_from_dict(data: Any, source: Path) -> Config:
    try:
        api_url = data.get("api_url", DEFAULT_API_URL)
        raw_profiles = data.get("profiles", {})
        profiles = {
            name: Profile(
                player_id=p["player_id"],
                username=p["username"],
                token=p["token"],
            )
            for name, p in raw_profiles.items()
        }
    except (AttributeError, KeyError, TypeError) as exc:
        raise ConfigError(f"{source} is not a valid tricksy config file: {exc}") from exc

    return Config(api_url=api_url, profiles=profiles)


def _config_to_dict(config: Config) -> dict[str, Any]:
    return {
        "api_url": config.api_url,
        "profiles": {
            name: {"player_id": p.player_id, "username": p.username, "token": p.token}
            for name, p in config.profiles.items()
        },
    }
