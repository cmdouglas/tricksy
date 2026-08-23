from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from tricksy.cli import config


def test_load_missing_file_returns_default_config(tmp_path: Path) -> None:
    loaded = config.load(tmp_path / "config.json")

    assert loaded == config.Config()


def test_config_path_honours_xdg_config_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    assert config.config_path() == tmp_path / "tricksy" / "config.json"


def test_config_path_falls_back_to_dot_config_when_xdg_unset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert config.config_path() == tmp_path / ".config" / "tricksy" / "config.json"


def test_save_creates_missing_parent_directory(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "config.json"

    config.save(config.Config(), path)

    assert path.exists()
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_save_then_load_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    original = config.Config(
        api_url="http://example.test",
        profiles={"north": config.Profile(player_id="p1", username="alice", token="tok1")},
    )

    config.save(original, path)
    loaded = config.load(path)

    assert loaded == original


def test_save_writes_mode_0600(tmp_path: Path) -> None:
    path = tmp_path / "config.json"

    config.save(config.Config(), path)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_save_writes_via_a_same_directory_temp_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "config.json"
    recorded: dict[str, Path] = {}
    real_replace = os.replace

    def fake_replace(src: str, dst: str) -> None:
        recorded["src"] = Path(src)
        recorded["dst"] = Path(dst)
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", fake_replace)

    config.save(config.Config(), path)

    assert recorded["src"] != recorded["dst"]
    assert recorded["src"].parent == recorded["dst"].parent


def test_save_leaves_destination_untouched_if_write_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "config.json"
    config.save(config.Config(api_url="http://original"), path)
    original_bytes = path.read_bytes()

    def broken_dump(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated write failure")

    monkeypatch.setattr(json, "dump", broken_dump)

    with pytest.raises(RuntimeError):
        config.save(config.Config(api_url="http://new"), path)

    assert path.read_bytes() == original_bytes
    leftover = [p for p in tmp_path.iterdir() if p != path]
    assert leftover == []


def test_two_profiles_do_not_see_each_others_tokens(tmp_path: Path) -> None:
    cfg = config.Config()
    cfg = config.set_profile(cfg, "north", config.Profile("p1", "alice", "tok-north"))
    cfg = config.set_profile(cfg, "south", config.Profile("p2", "bob", "tok-south"))

    config.save(cfg, tmp_path / "config.json")
    loaded = config.load(tmp_path / "config.json")

    assert config.get_profile(loaded, "north") == config.Profile("p1", "alice", "tok-north")
    assert config.get_profile(loaded, "south") == config.Profile("p2", "bob", "tok-south")


def test_remove_profile_removes_only_that_one() -> None:
    cfg = config.Config()
    cfg = config.set_profile(cfg, "north", config.Profile("p1", "alice", "tok-north"))
    cfg = config.set_profile(cfg, "south", config.Profile("p2", "bob", "tok-south"))

    cfg = config.remove_profile(cfg, "north")

    assert config.get_profile(cfg, "north") is None
    assert config.get_profile(cfg, "south") == config.Profile("p2", "bob", "tok-south")


def test_remove_profile_missing_name_is_a_noop() -> None:
    cfg = config.set_profile(config.Config(), "north", config.Profile("p1", "alice", "tok-north"))

    result = config.remove_profile(cfg, "nobody")

    assert result == cfg


def test_get_profile_missing_name_returns_none() -> None:
    assert config.get_profile(config.Config(), "nobody") is None


def test_set_profile_does_not_mutate_the_original_config() -> None:
    original = config.Config()

    config.set_profile(original, "north", config.Profile("p1", "alice", "tok-north"))

    assert original.profiles == {}


def test_corrupt_json_raises_config_error(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("not json {")

    with pytest.raises(config.ConfigError):
        config.load(path)
