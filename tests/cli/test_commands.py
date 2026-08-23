"""``main(argv)`` end to end against the real app (ROADMAP.md 3.7).

Every ``test_commands_*.py`` sibling drives a command handler directly with a ``FakeTransport`` -
fast, but it never proves the whole stack (argument parsing, the real HTTP round trip, the real
API, the real engine and repository, and rendering the real response) actually fits together.
``cli_app_client``/``_wire_transport`` below point ``context.transport_factory`` at a real
``TestClient(app)`` instead, the same seam ROADMAP.md 3.2 built the ``Transport`` protocol for.

Two things are covered here: one scripted walkthrough exercising every command's happy path, and
one small test per DESIGN.md §7.2 exit-code bucket, each earned through a real command rather than
a synthetic one. ``test_errors.py`` already exhaustively unit-tests the code -> exit-status mapping
table itself; this proves each *bucket* is actually reachable end to end.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from tricksy.cli import context
from tricksy.cli.main import main

from ..notifications._helpers import FakeSender
from ._helpers import play_full_game_via_cli, run_json

_PASSWORD = "correct-horse-battery"
_NEW_PASSWORD = "new-correct-horse-battery"


@pytest.fixture(autouse=True)
def _wire_transport(cli_app_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(context, "transport_factory", lambda url: cli_app_client)


def _register(profile: str, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["--profile", profile, "register", profile, "--password", _PASSWORD])
    capsys.readouterr()
    assert exit_code == 0


def test_full_command_walkthrough(
    capsys: pytest.CaptureFixture[str], fake_sender: FakeSender
) -> None:
    for profile in ("alice", "bob", "carol", "dave", "erin", "frank"):
        _register(profile, capsys)

    whoami = run_json(["--profile", "alice", "--json", "whoami"], capsys)
    assert whoami["username"] == "alice"

    rule_set = run_json(
        [
            "--profile",
            "alice",
            "--json",
            "rules",
            "save",
            "casual",
            "--contracts",
            "standard,nello",
            "--marks",
            "1",
        ],
        capsys,
    )
    rule_set_id = rule_set["rule_set_id"]

    rule_sets = run_json(["--profile", "alice", "--json", "rules", "list"], capsys)
    assert any(rs["rule_set_id"] == rule_set_id for rs in rule_sets["rule_sets"])

    shown = run_json(["--profile", "alice", "--json", "rules", "show", rule_set_id], capsys)
    assert shown["rule_set_id"] == rule_set_id

    replaced = run_json(
        [
            "--profile",
            "alice",
            "--json",
            "rules",
            "replace",
            rule_set_id,
            "casual2",
            "--marks",
            "1",
        ],
        capsys,
    )
    assert replaced["name"] == "casual2"

    exit_code = main(["--profile", "alice", "rules", "delete", rule_set_id])
    capsys.readouterr()
    assert exit_code == 0

    game = run_json(
        ["--profile", "alice", "--json", "create-game", "--seat", "0", "--marks", "1"], capsys
    )
    code = game["game_id"]

    # "open" only shows a game while it's still WAITING - call it before the last seat fills.
    open_games = run_json(["--profile", "bob", "--json", "open"], capsys)
    assert any(g["game_id"] == code for g in open_games["games"])

    exit_code = main(["--profile", "alice", "invite", code, "erin"])
    capsys.readouterr()
    assert exit_code == 0

    invited = run_json(["--profile", "alice", "--json", "invited", code], capsys)
    assert any(i["username"] == "erin" for i in invited["invites"])

    erin_invites = run_json(["--profile", "erin", "--json", "invites"], capsys)
    assert any(g["game_id"] == code for g in erin_invites["games"])

    exit_code = main(["--profile", "erin", "decline", code])
    capsys.readouterr()
    assert exit_code == 0

    exit_code = main(["--profile", "alice", "invite", code, "frank"])
    capsys.readouterr()
    assert exit_code == 0
    exit_code = main(["--profile", "alice", "uninvite", code, "frank"])
    capsys.readouterr()
    assert exit_code == 0

    for profile, seat in (("bob", "1"), ("carol", "2"), ("dave", "3")):
        exit_code = main(["--profile", profile, "join", code, "--seat", seat])
        capsys.readouterr()
        assert exit_code == 0

    games_list = run_json(["--profile", "alice", "--json", "games"], capsys)
    assert any(g["game_id"] == code for g in games_list["games"])

    status_exit = main(["--profile", "alice", "status", code])
    capsys.readouterr()
    assert status_exit == 0

    final = play_full_game_via_cli(["alice", "bob", "carol", "dave"], code, capsys)
    assert final["status"] == "COMPLETE" or final["view"]["phase"] == "GAME_OVER"

    # contacts and password reset (ROADMAP.md 4.6)
    exit_code = main(["--profile", "alice", "contact", "add", "alice@example.com"])
    capsys.readouterr()
    assert exit_code == 0

    contacts = run_json(["--profile", "alice", "--json", "contacts"], capsys)
    assert any(c["address"] == "alice@example.com" for c in contacts["contacts"])

    exit_code = main(["--profile", "alice", "contact", "verify", "alice@example.com"])
    capsys.readouterr()
    assert exit_code == 0

    verify_match = re.search(r"tricksy contact confirm (\S+)", fake_sender.sent[-1].body)
    assert verify_match is not None
    exit_code = main(["contact", "confirm", verify_match.group(1)])  # works signed out
    capsys.readouterr()
    assert exit_code == 0

    whoami = run_json(["--profile", "alice", "--json", "whoami"], capsys)
    assert any(c["address"] == "alice@example.com" and c["verified"] for c in whoami["contacts"])

    exit_code = main(["--profile", "alice", "contact", "mute", "alice@example.com"])
    capsys.readouterr()
    assert exit_code == 0
    exit_code = main(["--profile", "alice", "contact", "unmute", "alice@example.com"])
    capsys.readouterr()
    assert exit_code == 0

    exit_code = main(["forgot-password", "alice"])  # works signed out
    capsys.readouterr()
    assert exit_code == 0

    reset_match = re.search(r"tricksy reset-password (\S+)", fake_sender.sent[-1].body)
    assert reset_match is not None
    exit_code = main(["reset-password", reset_match.group(1), "--password", _NEW_PASSWORD])
    capsys.readouterr()
    assert exit_code == 0

    # the reset revoked every device, so this login mints a fresh token for the profile
    exit_code = main(["--profile", "alice", "login", "alice", "--password", _NEW_PASSWORD])
    capsys.readouterr()
    assert exit_code == 0

    exit_code = main(["--profile", "alice", "contact", "remove", "alice@example.com"])
    capsys.readouterr()
    assert exit_code == 0

    exit_code = main(["--profile", "alice", "logout"])
    capsys.readouterr()
    assert exit_code == 0


# -- DESIGN.md §7.2 exit-code buckets, each earned through a real command -----------------------


def test_usage_error_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    _register("alice", capsys)
    exit_code = main(
        ["--profile", "alice", "create-game", "--rule-set", "some-id", "--contracts", "nello"]
    )
    capsys.readouterr()
    assert exit_code == 2


def test_who_are_you_exits_3_on_wrong_password(capsys: pytest.CaptureFixture[str]) -> None:
    _register("alice", capsys)
    exit_code = main(["--profile", "alice", "login", "alice", "--password", "not-the-password"])
    capsys.readouterr()
    assert exit_code == 3


def test_rules_say_no_exits_4_on_an_illegal_bid(capsys: pytest.CaptureFixture[str]) -> None:
    for profile in ("alice", "bob", "carol", "dave"):
        _register(profile, capsys)
    game = run_json(
        ["--profile", "alice", "--json", "create-game", "--seat", "0", "--marks", "1"], capsys
    )
    code = game["game_id"]
    for profile, seat in (("bob", "1"), ("carol", "2"), ("dave", "3")):
        main(["--profile", profile, "join", code, "--seat", seat])
        capsys.readouterr()

    first_bidder = None
    for profile in ("alice", "bob", "carol", "dave"):
        view = run_json(["--profile", profile, "--json", "status", code], capsys)["view"]
        if view["legal_moves"]:
            first_bidder = profile
            break
    assert first_bidder is not None, "nobody has a legal bid after dealing"
    exit_code = main(["--profile", first_bidder, "bid", code, "30"])
    capsys.readouterr()
    assert exit_code == 0

    next_bidder = None
    for profile in ("alice", "bob", "carol", "dave"):
        if profile == first_bidder:
            continue
        view = run_json(["--profile", profile, "--json", "status", code], capsys)["view"]
        if view["legal_moves"]:
            next_bidder = profile
            break
    assert next_bidder is not None, "nobody has a legal bid after the opening bid"

    # A bid that doesn't beat the standing 30 is schema-valid but engine-illegal.
    exit_code = main(["--profile", next_bidder, "bid", code, "30"])
    capsys.readouterr()
    assert exit_code == 4


def test_world_moved_exits_5_on_a_taken_seat(capsys: pytest.CaptureFixture[str]) -> None:
    _register("alice", capsys)
    _register("bob", capsys)
    _register("carol", capsys)
    game = run_json(["--profile", "alice", "--json", "create-game", "--seat", "0"], capsys)
    code = game["game_id"]
    exit_code = main(["--profile", "bob", "join", code, "--seat", "1"])
    capsys.readouterr()
    assert exit_code == 0

    exit_code = main(["--profile", "carol", "join", code, "--seat", "1"])
    capsys.readouterr()
    assert exit_code == 5


def test_no_such_thing_exits_6_on_an_unknown_game(capsys: pytest.CaptureFixture[str]) -> None:
    _register("alice", capsys)
    exit_code = main(["--profile", "alice", "status", "NOSUCH"])
    capsys.readouterr()
    assert exit_code == 6


def test_not_allowed_exits_7_for_an_uninvited_stranger(capsys: pytest.CaptureFixture[str]) -> None:
    _register("alice", capsys)
    _register("erin", capsys)
    game = run_json(
        [
            "--profile",
            "alice",
            "--json",
            "create-game",
            "--seat",
            "0",
            "--visibility",
            "invite_only",
        ],
        capsys,
    )
    code = game["game_id"]

    exit_code = main(["--profile", "erin", "status", code])
    capsys.readouterr()
    assert exit_code == 7
