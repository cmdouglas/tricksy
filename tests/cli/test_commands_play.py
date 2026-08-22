from __future__ import annotations

import argparse

import pytest

from t42.cli import context
from t42.cli.command import Command
from t42.cli.commands import play

from ._helpers import fake_transport

_GAME = {
    "game_id": "ABC123",
    "status": "ACTIVE",
    "visibility": "public",
    "seats": [
        {"seat": 0, "player_id": "p1", "username": "alice"},
        {"seat": 1, "player_id": "p2", "username": "bob"},
        {"seat": 2, "player_id": "p3", "username": "carol"},
        {"seat": 3, "player_id": "p4", "username": "dave"},
    ],
    "house_rules": {
        "enabled_contracts": ["standard"],
        "contract_options": {},
        "doubles_are_own_suit": False,
        "allow_declared_lead": "never",
        "marks_to_win": 7,
    },
    "view": {
        "game_id": "ABC123",
        "phase": "auction",
        "seat": 0,
        "dealer": 3,
        "to_act": 0,
        "marks": {"north_south": 0, "east_west": 0},
        "declarer": None,
        "contract": None,
        "trump": None,
        "hand": ["6-6", "5-4"],
        "current_trick": {"plays": [], "declared_suit": None, "winner": None},
        "completed_tricks": [],
        "legal_moves": [{"kind": "PASS"}],
    },
}

_GAME_LIST = {"games": [{"game_id": "ABC123", "status": "ACTIVE", "seat": 0, "is_my_turn": True}]}


def _command(name: str) -> Command:
    return next(c for c in play.COMMANDS if c.name == name)


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
def _config_home(tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> None:
    from t42.cli import config

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cfg = config.set_profile(config.Config(), "default", config.Profile("p1", "alice", "tok-1"))
    config.save(cfg)


# -- status --------------------------------------------------------------------------------


def test_status_fetches_game_and_renders_view(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    transport = fake_transport(_GAME)
    monkeypatch.setattr(context, "transport_factory", lambda url: transport)

    status = _command("status").handler(_parse("status", ["ABC123"]))

    assert status == 0
    assert transport.calls[0].method == "GET"
    assert transport.calls[0].url == "/games/ABC123"
    assert "phase:" in capsys.readouterr().out


def test_status_json_mode_prints_raw_body(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import json as jsonlib

    transport = fake_transport(_GAME)
    monkeypatch.setattr(context, "transport_factory", lambda url: transport)

    status = _command("status").handler(_parse("status", ["ABC123"], json=True))

    assert status == 0
    assert jsonlib.loads(capsys.readouterr().out) == _GAME


# -- games ---------------------------------------------------------------------------------


def test_games_lists_players_games(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    transport = fake_transport(_GAME_LIST)
    monkeypatch.setattr(context, "transport_factory", lambda url: transport)

    status = _command("games").handler(_parse("games", []))

    assert status == 0
    assert transport.calls[0].method == "GET"
    assert transport.calls[0].url == "/players/me/games"
    assert "ABC123" in capsys.readouterr().out


# -- bid -------------------------------------------------------------------------------------


def test_bid_points() -> None:
    body = play._parse_bid_body("32", None)
    assert body == {"kind": "BID", "points": 32}


def test_bid_pass() -> None:
    assert play._parse_bid_body("pass", None) == {"kind": "PASS"}


def test_bid_confirm_and_decline() -> None:
    assert play._parse_bid_body("confirm", None) == {"kind": "CONFIRM_BID", "accept": True}
    assert play._parse_bid_body("decline", None) == {"kind": "CONFIRM_BID", "accept": False}


def test_bid_marks_singular_unit_no_contract_key() -> None:
    assert play._parse_bid_body("1-mark", None) == {"kind": "BID", "marks": 1}


def test_bid_marks_plural_with_contract() -> None:
    body = play._parse_bid_body("2-marks", "nello")
    assert body == {"kind": "BID", "marks": 2, "contract": "nello"}


def test_bid_rejects_contract_with_points_pass_or_confirm() -> None:
    assert play._parse_bid_body("32", "nello") is None
    assert play._parse_bid_body("pass", "nello") is None
    assert play._parse_bid_body("confirm", "nello") is None


def test_bid_rejects_unparseable_token() -> None:
    assert play._parse_bid_body("nope", None) is None


def test_bid_command_sends_fresh_idempotency_key(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = fake_transport(_GAME)
    monkeypatch.setattr(context, "transport_factory", lambda url: transport)

    status = _command("bid").handler(_parse("bid", ["ABC123", "32"]))

    assert status == 0
    call = transport.calls[0]
    assert call.method == "POST"
    assert call.url == "/games/ABC123/moves"
    assert call.json == {"kind": "BID", "points": 32}
    assert "Idempotency-Key" in call.headers


def test_bid_marks_with_contract_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = fake_transport(_GAME)
    monkeypatch.setattr(context, "transport_factory", lambda url: transport)

    status = _command("bid").handler(_parse("bid", ["ABC123", "2-marks", "--contract", "nello"]))

    assert status == 0
    assert transport.calls[0].json == {"kind": "BID", "marks": 2, "contract": "nello"}


def test_bid_invalid_token_is_a_usage_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    transport = fake_transport(_GAME)
    monkeypatch.setattr(context, "transport_factory", lambda url: transport)

    status = _command("bid").handler(_parse("bid", ["ABC123", "nope"]))

    assert status == 2
    assert transport.calls == []
    assert "error" in capsys.readouterr().err


def test_bid_contract_with_pass_is_a_usage_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    transport = fake_transport(_GAME)
    monkeypatch.setattr(context, "transport_factory", lambda url: transport)

    status = _command("bid").handler(_parse("bid", ["ABC123", "pass", "--contract", "nello"]))

    assert status == 2
    assert transport.calls == []
    assert "error" in capsys.readouterr().err


# -- declare -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("token", "expected"),
    [("trump=fives", 5), ("trump=doubles", 7), ("trump=5", 5), ("trump=none", None)],
)
def test_declare_command_sends_resolved_trump(
    monkeypatch: pytest.MonkeyPatch, token: str, expected: int | None
) -> None:
    transport = fake_transport(_GAME)
    monkeypatch.setattr(context, "transport_factory", lambda url: transport)

    status = _command("declare").handler(_parse("declare", ["ABC123", token]))

    assert status == 0
    call = transport.calls[0]
    assert call.method == "POST"
    assert call.url == "/games/ABC123/moves"
    assert call.json == {"kind": "DECLARE_CONTRACT", "trump": expected}
    assert "Idempotency-Key" in call.headers


def test_declare_rejects_malformed_key() -> None:
    parser = argparse.ArgumentParser()
    play._configure_declare(parser)

    with pytest.raises(SystemExit):
        parser.parse_args(["ABC123", "suit=fives"])


def test_declare_rejects_unknown_suit_name() -> None:
    parser = argparse.ArgumentParser()
    play._configure_declare(parser)

    with pytest.raises(SystemExit):
        parser.parse_args(["ABC123", "trump=stripes"])


# -- play --------------------------------------------------------------------------------


def test_play_sends_domino_with_no_declared_suit_key(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = fake_transport(_GAME)
    monkeypatch.setattr(context, "transport_factory", lambda url: transport)

    status = _command("play").handler(_parse("play", ["ABC123", "4-1"]))

    assert status == 0
    call = transport.calls[0]
    assert call.method == "POST"
    assert call.url == "/games/ABC123/moves"
    assert call.json == {"kind": "PLAY_DOMINO", "domino": "4-1"}
    assert "Idempotency-Key" in call.headers


def test_play_with_declared_lead(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = fake_transport(_GAME)
    monkeypatch.setattr(context, "transport_factory", lambda url: transport)

    status = _command("play").handler(_parse("play", ["ABC123", "4-1", "--declare", "treys"]))

    assert status == 0
    assert transport.calls[0].json == {
        "kind": "PLAY_DOMINO",
        "domino": "4-1",
        "declared_suit": 3,
    }


def test_play_declare_does_not_accept_none() -> None:
    parser = argparse.ArgumentParser()
    play._configure_play(parser)

    with pytest.raises(SystemExit):
        parser.parse_args(["ABC123", "4-1", "--declare", "none"])


def test_play_rejects_bad_declare_suit_name() -> None:
    parser = argparse.ArgumentParser()
    play._configure_play(parser)

    with pytest.raises(SystemExit):
        parser.parse_args(["ABC123", "4-1", "--declare", "stripes"])


# -- parse_suit ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [("fives", 5), ("FIVES", 5), ("doubles", 7), ("blanks", 0), ("5", 5), ("0", 0), ("7", 7)],
)
def test_parse_suit_accepts_name_or_number(value: str, expected: int) -> None:
    assert play.parse_suit(value) == expected


@pytest.mark.parametrize("value", ["8", "-1", "stripes", "nope"])
def test_parse_suit_rejects_invalid_values(value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        play.parse_suit(value)
