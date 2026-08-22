"""Game creation, joining and reading over HTTP (ROADMAP.md 2.5, DESIGN.md §6)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ._helpers import Client, seated_game


def test_creating_a_game_returns_a_waiting_lobby(alice: Client) -> None:
    response = alice.post("/games", {"seat": 0})

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "WAITING"
    assert body["seats"] == [{"seat": 0, "player_id": alice.player_id, "username": "alice"}]
    assert body["view"] is None, "no hand has been dealt, so there is nothing to project"


def test_a_game_code_is_short_and_typable(alice: Client) -> None:
    """It gets read aloud and typed from a phone screen (DESIGN.md §4.1)."""
    game_id = alice.create_game()

    assert len(game_id) == 6
    assert not set(game_id) & set("ILOU01")


def test_the_fourth_join_deals_the_hand(
    alice: Client, bob: Client, carol: Client, dave: Client
) -> None:
    game_id = alice.create_game(seat=0)

    for seat, player in ((1, bob), (2, carol)):
        assert player.join(game_id, seat).json()["view"] is None

    body = dave.join(game_id, 3).json()

    assert body["status"] == "ACTIVE"
    assert body["view"] is not None
    assert body["view"]["phase"] == "BIDDING"
    assert len(body["view"]["hand"]) == 7


def test_a_taken_seat_is_a_conflict(alice: Client, bob: Client, carol: Client) -> None:
    game_id = alice.create_game(seat=0)
    bob.join(game_id, 1)

    response = carol.join(game_id, 1)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SEAT_TAKEN"


def test_one_player_cannot_take_two_seats(alice: Client, bob: Client) -> None:
    game_id = alice.create_game(seat=0)
    bob.join(game_id, 1)

    response = bob.join(game_id, 2)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ALREADY_SEATED"


def test_rejoining_your_own_seat_succeeds(alice: Client, bob: Client) -> None:
    """A retried join must not fail, or a dropped response leaves a client unable to tell
    whether it is seated."""
    game_id = alice.create_game(seat=0)
    bob.join(game_id, 1)

    assert bob.join(game_id, 1).status_code == 200


def test_a_latecomer_cannot_join_a_dealt_game(
    alice: Client, bob: Client, carol: Client, dave: Client, client: TestClient
) -> None:
    game_id = seated_game([alice, bob, carol, dave])
    eve = Client.register(client, "eve")

    response = eve.join(game_id, 0)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "GAME_NOT_JOINABLE"


def test_joining_an_unknown_game_is_404(alice: Client) -> None:
    response = alice.join("ZZZZZZ", 1)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "GAME_NOT_FOUND"


def test_a_non_player_cannot_read_a_game(
    alice: Client, bob: Client, carol: Client, dave: Client, client: TestClient
) -> None:
    game_id = seated_game([alice, bob, carol, dave])
    eve = Client.register(client, "eve")

    response = eve.get(f"/games/{game_id}")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "NOT_A_PLAYER"


def test_reading_a_game_needs_a_token(alice: Client) -> None:
    game_id = alice.create_game()

    assert alice.http.get(f"/games/{game_id}").status_code == 401


def test_house_rules_round_trip_and_reach_the_dealt_game(
    alice: Client, bob: Client, carol: Client, dave: Client
) -> None:
    game_id = seated_game(
        [alice, bob, carol, dave],
        enabled_contracts=["standard", "nello", "plunge", "splash"],
        contract_options={"plunge": {"minimum_doubles": 5}},
        doubles_are_own_suit=True,
        allow_declared_lead="always",
        marks_to_win=3,
    )

    rules = alice.view(game_id)["house_rules"]

    assert rules["marks_to_win"] == 3
    assert rules["doubles_are_own_suit"] is True
    assert rules["allow_declared_lead"] == "always"
    assert rules["contract_options"] == {"plunge": {"minimum_doubles": 5}}


@pytest.mark.parametrize(
    ("rules", "code"),
    [
        pytest.param(
            {"enabled_contracts": ["standard", "no-such-contract"]},
            "UNKNOWN_CONTRACT",
            id="unregistered contract",
        ),
        pytest.param(
            {"enabled_contracts": ["nello"]},
            "INVALID_REQUEST",
            id="standard disabled",
        ),
        pytest.param(
            {"contract_options": {"nello": {"no_such_option": 1}}},
            "UNKNOWN_CONTRACT",
            id="undeclared option key",
        ),
    ],
)
def test_an_invalid_rule_set_is_rejected_at_creation(
    alice: Client, rules: dict[str, object], code: str
) -> None:
    """Rejected now rather than three joins later, when the deal would first run the rules."""
    response = alice.post("/games", {"seat": 0, "house_rules": rules})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == code


def test_my_games_flags_whose_turn_it_is(
    alice: Client, bob: Client, carol: Client, dave: Client
) -> None:
    players = [alice, bob, carol, dave]
    game_id = seated_game(players)

    flags = {}
    for player in players:
        (summary,) = player.get("/players/me/games").json()["games"]
        assert summary["game_id"] == game_id
        assert summary["status"] == "ACTIVE"
        flags[player.username] = summary["is_my_turn"]

    assert sum(flags.values()) == 1, "exactly one player is on the clock"


def test_my_games_includes_waiting_games(alice: Client) -> None:
    game_id = alice.create_game()

    (summary,) = alice.get("/players/me/games").json()["games"]

    assert summary == {
        "game_id": game_id,
        "kind": "texas42",
        "status": "WAITING",
        "seat": 0,
        "is_my_turn": False,
    }


def test_my_games_is_empty_for_a_new_player(alice: Client) -> None:
    assert alice.get("/players/me/games").json()["games"] == []
