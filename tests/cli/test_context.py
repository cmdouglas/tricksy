from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from tricksy.cli import config, context
from tricksy.cli.api import ApiError

from ._helpers import fake_transport


def _args(
    tmp_path: Path,
    *,
    profile: str | None = None,
    json: bool = False,
    api_url: str = "http://x",
) -> argparse.Namespace:
    return argparse.Namespace(profile=profile, json=json, api_url=api_url)


@pytest.fixture(autouse=True)
def _fake_transport_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(context, "transport_factory", lambda url: fake_transport({}))


def test_build_client_defaults_to_the_default_profile(tmp_path: Path) -> None:
    cfg = config.set_profile(
        config.Config(), "default", config.Profile("p1", "alice", "tok-default")
    )
    config.save(cfg)

    _, profile = context.build_client(_args(tmp_path))

    assert profile is not None
    assert profile.username == "alice"


def test_build_client_uses_named_profile(tmp_path: Path) -> None:
    cfg = config.set_profile(config.Config(), "north", config.Profile("p1", "alice", "tok-north"))
    config.save(cfg)

    _, profile = context.build_client(_args(tmp_path, profile="north"))

    assert profile is not None
    assert profile.username == "alice"


def test_build_client_raises_not_authenticated_when_profile_missing(tmp_path: Path) -> None:
    with pytest.raises(ApiError) as exc_info:
        context.build_client(_args(tmp_path))

    assert exc_info.value.code == "NOT_AUTHENTICATED"


def test_build_client_allows_missing_profile_when_auth_not_required(tmp_path: Path) -> None:
    client, profile = context.build_client(_args(tmp_path), require_auth=False)

    assert profile is None
    assert client is not None


def test_build_client_sends_the_profiles_token_on_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = config.set_profile(
        config.Config(), "default", config.Profile("p1", "alice", "tok-default")
    )
    config.save(cfg)
    transport = fake_transport({})
    monkeypatch.setattr(context, "transport_factory", lambda url: transport)

    client, _ = context.build_client(_args(tmp_path))
    client.request("GET", "/players/me")

    assert transport.calls[0].headers["Authorization"] == "Bearer tok-default"


def test_emit_json_mode_prints_body(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    context.emit(_args(tmp_path, json=True), {"a": 1})

    assert capsys.readouterr().out.strip() == '{"a": 1}'


def test_emit_json_mode_prints_nothing_for_none_body(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    context.emit(_args(tmp_path, json=True), None)

    assert capsys.readouterr().out == ""


def test_emit_text_mode_uses_render_fn(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    context.emit(_args(tmp_path), {"a": 1}, lambda r: f"got {r['a']}")

    assert capsys.readouterr().out.strip() == "got 1"


def test_emit_text_mode_uses_confirmation_when_no_render_fn(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    context.emit(_args(tmp_path), None, confirmation="done")

    assert capsys.readouterr().out.strip() == "done"


def test_emit_text_mode_prints_nothing_without_render_fn_or_confirmation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    context.emit(_args(tmp_path), None)

    assert capsys.readouterr().out == ""
