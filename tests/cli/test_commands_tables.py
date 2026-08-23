from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from tricksy.cli import config, context
from tricksy.cli.command import Command
from tricksy.cli.commands import tables

from ._helpers import fake_transport

_GAME = {
    "game_id": "ABC123",
    "status": "WAITING",
    "visibility": "public",
    "seats": [{"seat": 0, "player_id": "p1", "username": "alice"}],
    "house_rules": {
        "enabled_contracts": ["standard"],
        "contract_options": {},
        "doubles_are_own_suit": False,
        "allow_declared_lead": "never",
        "marks_to_win": 7,
    },
    "view": None,
}


def _command(name: str) -> Command:
    return next(c for c in tables.COMMANDS if c.name == name)


def _parse(
    name: str, argv: list[str], *, profile: str | None = None, json: bool = False
) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    _command(name).configure(parser)
    args = parser.parse_args(argv)
    args.profile = profile
    args.json = json
    args.api_url = "http://x"
    return args


@pytest.fixture(autouse=True)
def _config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cfg = config.set_profile(config.Config(), "default", config.Profile("p1", "alice", "tok-1"))
    config.save(cfg)


def test_create_game_omits_house_rules_key_when_rule_set_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = fake_transport(_GAME)
    monkeypatch.setattr(context, "transport_factory", lambda url: transport)

    status = _command("create-game").handler(_parse("create-game", ["--rule-set", "rs1"]))

    assert status == 0
    call = transport.calls[0]
    assert call.method == "POST"
    assert call.url == "/games"
    assert call.json == {"seat": 0, "visibility": "public", "rule_set_id": "rs1"}


def test_create_game_rejects_rule_set_combined_with_house_rule_flag(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    transport = fake_transport(_GAME)
    monkeypatch.setattr(context, "transport_factory", lambda url: transport)

    status = _command("create-game").handler(
        _parse("create-game", ["--rule-set", "rs1", "--marks", "5"])
    )

    assert status == 2
    assert transport.calls == []
    assert "error" in capsys.readouterr().err


def test_create_game_sends_house_rules_body_when_no_rule_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = fake_transport(_GAME)
    monkeypatch.setattr(context, "transport_factory", lambda url: transport)

    _command("create-game").handler(_parse("create-game", ["--seat", "east", "--marks", "5"]))

    call = transport.calls[0]
    assert call.json == {
        "seat": 1,
        "visibility": "public",
        "house_rules": {"marks_to_win": 5},
    }


def test_join_sends_seat_as_integer(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = fake_transport(_GAME)
    monkeypatch.setattr(context, "transport_factory", lambda url: transport)

    status = _command("join").handler(_parse("join", ["ABC123", "--seat", "south"]))

    assert status == 0
    call = transport.calls[0]
    assert call.method == "POST"
    assert call.url == "/games/ABC123/join"
    assert call.json == {"seat": 2}


def test_open_lists_public_games(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    transport = fake_transport({"games": [_GAME]})
    monkeypatch.setattr(context, "transport_factory", lambda url: transport)

    status = _command("open").handler(_parse("open", []))

    assert status == 0
    assert transport.calls[0].method == "GET"
    assert transport.calls[0].url == "/games/open"
    assert "ABC123" in capsys.readouterr().out


def test_invite_prints_a_confirmation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    response = {"game_id": "ABC123", "player_id": "p2", "username": "bob"}
    transport = fake_transport(response)
    monkeypatch.setattr(context, "transport_factory", lambda url: transport)

    status = _command("invite").handler(_parse("invite", ["ABC123", "bob"]))

    assert status == 0
    call = transport.calls[0]
    assert call.method == "POST"
    assert call.url == "/games/ABC123/invites"
    assert call.json == {"username": "bob"}
    assert "bob" in capsys.readouterr().out


def test_invited_lists_pending_invites(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    response = {
        "invites": [{"player_id": "p2", "username": "bob", "created_at": "2026-01-01T00:00:00Z"}]
    }
    transport = fake_transport(response)
    monkeypatch.setattr(context, "transport_factory", lambda url: transport)

    status = _command("invited").handler(_parse("invited", ["ABC123"]))

    assert status == 0
    assert transport.calls[0].url == "/games/ABC123/invites"
    assert "bob" in capsys.readouterr().out


def test_uninvite_resolves_username_to_player_id(monkeypatch: pytest.MonkeyPatch) -> None:
    invites_response = {
        "invites": [{"player_id": "p2", "username": "bob", "created_at": "2026-01-01T00:00:00Z"}]
    }
    transport = fake_transport(invites_response, None, status_code=200)
    transport.responses[1].status_code = 204
    monkeypatch.setattr(context, "transport_factory", lambda url: transport)

    status = _command("uninvite").handler(_parse("uninvite", ["ABC123", "bob"]))

    assert status == 0
    assert transport.calls[0].method == "GET"
    assert transport.calls[0].url == "/games/ABC123/invites"
    assert transport.calls[1].method == "DELETE"
    assert transport.calls[1].url == "/games/ABC123/invites/p2"


def test_uninvite_raises_player_not_found_when_username_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tricksy.cli.api import ApiError

    transport = fake_transport({"invites": []})
    monkeypatch.setattr(context, "transport_factory", lambda url: transport)

    with pytest.raises(ApiError) as exc_info:
        _command("uninvite").handler(_parse("uninvite", ["ABC123", "bob"]))

    assert exc_info.value.code == "PLAYER_NOT_FOUND"
    assert len(transport.calls) == 1  # no DELETE was attempted


def test_decline_uses_the_profiles_own_player_id_with_no_extra_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = fake_transport(None, status_code=204)
    monkeypatch.setattr(context, "transport_factory", lambda url: transport)

    status = _command("decline").handler(_parse("decline", ["ABC123"]))

    assert status == 0
    assert len(transport.calls) == 1
    assert transport.calls[0].method == "DELETE"
    assert transport.calls[0].url == "/games/ABC123/invites/p1"


def test_invites_lists_own_pending_invites(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    transport = fake_transport({"games": [_GAME]})
    monkeypatch.setattr(context, "transport_factory", lambda url: transport)

    status = _command("invites").handler(_parse("invites", []))

    assert status == 0
    assert transport.calls[0].method == "GET"
    assert transport.calls[0].url == "/players/me/invites"
    assert "ABC123" in capsys.readouterr().out
