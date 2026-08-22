"""The move endpoint (ROADMAP.md 2.5, DESIGN.md §6).

Includes the four-case matrix DESIGN.md §10 asks for per mutating endpoint - valid move, invalid
move, out of turn, stale version - plus idempotency and the hidden-information sweep.

Every move goes to ``POST /games/{id}/moves``, discriminated on ``kind``, so ``_helpers.submit``
posts a projected legal move back with no translation at all - see
:func:`test_a_projected_legal_move_is_a_valid_request_body_verbatim`.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from t42.storage.repository import StoredGame, get_state

from ._helpers import Client, play_until, seated_game, submit, whose_turn


@pytest.fixture
def players(alice: Client, bob: Client, carol: Client, dave: Client) -> list[Client]:
    return [alice, bob, carol, dave]


@pytest.fixture
def game(players: list[Client]) -> str:
    return seated_game(players)


def _other(players: list[Client], player: Client) -> Client:
    return next(p for p in players if p.player_id != player.player_id)


# ------------------------------------------------------------------------------- the valid case


def test_a_legal_bid_is_accepted_and_advances_the_turn(players: list[Client], game: str) -> None:
    player, view = whose_turn(players, game)
    before = view["to_act"]

    response = submit(player, game, view["legal_moves"][0])

    assert response.status_code == 200
    assert response.json()["view"]["to_act"] != before


def test_a_projected_legal_move_is_a_valid_request_body_verbatim(
    players: list[Client], game: str
) -> None:
    """What collapsing the three move endpoints into one buys: ``legal_moves`` entries are already
    request bodies, so a client needs no table mapping move kinds to paths (DESIGN.md §6, §11).

    Posted with the ``None``-valued keys left in, exactly as ``project`` emits them, since that is
    what a client that does no filtering would send.
    """
    player, view = whose_turn(players, game)

    for move in view["legal_moves"]:
        assert set(move) >= {"kind"}
        # A dry run against a *different* seat: rejected for being out of turn, which proves the
        # body parsed and reached the engine rather than being refused as malformed.
        response = _other(players, player).post(f"/games/{game}/moves", move)
        assert response.status_code == 409, move
        assert response.json()["error"]["code"] == "OUT_OF_TURN"

    assert submit(player, game, view["legal_moves"][0]).status_code == 200


def test_passing_is_accepted(players: list[Client], game: str) -> None:
    player, view = whose_turn(players, game)
    passes = [m for m in view["legal_moves"] if m["kind"] == "PASS"]
    assert passes, "the first bidder can always pass"

    assert submit(player, game, passes[0]).status_code == 200


def test_a_full_game_runs_to_completion_over_http(players: list[Client]) -> None:
    """The Phase 2 milestone: signup to ``GAME_OVER`` without touching anything but the API."""
    game = seated_game(players, marks_to_win=1)

    body = play_until(players, game)

    assert body["view"]["phase"] == "GAME_OVER"
    assert body["status"] == "COMPLETE"
    marks = body["view"]["marks"]
    assert max(marks["north_south"], marks["east_west"]) >= 1


# ----------------------------------------------------------------------------- the invalid cases


def test_an_illegal_bid_is_400(players: list[Client], game: str) -> None:
    player, _ = whose_turn(players, game)

    response = player.post(f"/games/{game}/moves", {"kind": "BID", "points": 41, "marks": 3})

    assert response.status_code == 400
    assert response.json()["error"]["code"] in {"ILLEGAL_MOVE", "RULES_ERROR"}


def test_playing_a_domino_during_the_auction_is_400(players: list[Client], game: str) -> None:
    player, _ = whose_turn(players, game)

    response = player.post(f"/games/{game}/moves", {"kind": "PLAY_DOMINO", "domino": "6-6"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "ILLEGAL_MOVE"


def test_declaring_trump_during_the_auction_is_400(players: list[Client], game: str) -> None:
    player, _ = whose_turn(players, game)

    response = player.post(f"/games/{game}/moves", {"kind": "DECLARE_CONTRACT", "trump": 3})

    assert response.status_code == 400


def test_an_unparseable_domino_is_400(players: list[Client], game: str) -> None:
    player, _ = whose_turn(players, game)

    response = player.post(f"/games/{game}/moves", {"kind": "PLAY_DOMINO", "domino": "9-9"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_a_malformed_body_is_422(players: list[Client], game: str) -> None:
    """Distinct from the 400s above: this is the wrong shape, not a rejected move."""
    player, _ = whose_turn(players, game)

    assert player.post(f"/games/{game}/moves", {"kind": "NOT_A_KIND"}).status_code == 422
    assert player.post(f"/games/{game}/moves", {"kind": "PLAY_DOMINO"}).status_code == 422


# --------------------------------------------------------------------------------- out of turn


def test_bidding_out_of_turn_is_409(players: list[Client], game: str) -> None:
    on_the_clock, view = whose_turn(players, game)
    interloper = _other(players, on_the_clock)

    response = submit(interloper, game, view["legal_moves"][0])

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "OUT_OF_TURN"


def test_a_player_not_in_the_game_gets_403_not_409(
    players: list[Client], game: str, client: TestClient
) -> None:
    """Ordering matters: seating is checked before the rules, so an outsider learns nothing about
    whose turn it is."""
    eve = Client.register(client, "eve")

    response = eve.post(f"/games/{game}/moves", {"kind": "PASS"})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "NOT_A_PLAYER"


def test_moving_in_a_game_that_has_not_been_dealt_is_409(alice: Client, bob: Client) -> None:
    game = alice.create_game(seat=0)
    bob.join(game, 1)

    response = alice.post(f"/games/{game}/moves", {"kind": "PASS"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "GAME_NOT_STARTED"


# ------------------------------------------------------------------------------- stale version


def _pretend_a_write_landed_first(monkeypatch: MonkeyPatch) -> None:
    """Make the handler read a version one behind the stored one - exactly what it would see had
    another write slipped in between its read and its conditional write."""

    def stale(table: Any, game_id: str) -> StoredGame:
        stored = get_state(table, game_id)
        return StoredGame(state=stored.state, version=stored.version - 1)

    monkeypatch.setattr("t42.api.app.get_state", stale)


def test_a_stale_version_is_409(players: list[Client], game: str, monkeypatch: MonkeyPatch) -> None:
    """There is no retry by design (DESIGN.md §6), so the client is told to re-read rather than
    having its move applied to a state it never saw."""
    player, view = whose_turn(players, game)

    _pretend_a_write_landed_first(monkeypatch)
    response = submit(player, game, view["legal_moves"][0])

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VERSION_CONFLICT"


def test_a_conflicted_move_leaves_the_game_untouched(
    players: list[Client], game: str, monkeypatch: MonkeyPatch
) -> None:
    """A rejected write is all-or-nothing: no event appended, no turn advanced."""
    player, view = whose_turn(players, game)
    before = player.view(game)

    _pretend_a_write_landed_first(monkeypatch)
    submit(player, game, view["legal_moves"][0])
    monkeypatch.undo()

    assert player.view(game) == before


# ---------------------------------------------------------------------------------- idempotency


def test_the_same_idempotency_key_applies_a_move_once(players: list[Client], game: str) -> None:
    player, view = whose_turn(players, game)

    first = submit(player, game, view["legal_moves"][0], idempotency_key="req-1")
    second = submit(player, game, view["legal_moves"][0], idempotency_key="req-1")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()


def test_a_retry_after_the_turn_moved_on_is_still_a_no_op(players: list[Client], game: str) -> None:
    """The realistic retry: the first attempt landed, its response was lost, and by the time the
    client retries the auction has moved past them. Without the idempotency marker this would be
    a 409; with it, the client gets the result its move actually produced."""
    player, view = whose_turn(players, game)
    move = view["legal_moves"][0]

    first = submit(player, game, move, idempotency_key="req-1")
    next_player, next_view = whose_turn(players, game)
    submit(next_player, game, next_view["legal_moves"][0])

    retry = submit(player, game, move, idempotency_key="req-1")

    assert retry.status_code == 200
    assert first.json()["view"]["hand"] == retry.json()["view"]["hand"]


def test_different_keys_apply_different_moves(players: list[Client], game: str) -> None:
    player, view = whose_turn(players, game)
    submit(player, game, view["legal_moves"][0], idempotency_key="req-1")

    next_player, next_view = whose_turn(players, game)
    response = submit(next_player, game, next_view["legal_moves"][0], idempotency_key="req-2")

    assert response.status_code == 200


# ------------------------------------------------------------- hidden information at the wire


def _tiles_in(value: Any) -> set[str]:
    """Every domino-looking string anywhere in a response, however deeply nested."""
    found: set[str] = set()
    if isinstance(value, str):
        parts = value.split("-")
        if len(parts) == 2 and all(p.isdigit() and len(p) == 1 for p in parts):
            found.add(value)
    elif isinstance(value, dict):
        for item in value.values():
            found |= _tiles_in(item)
    elif isinstance(value, list):
        for item in value:
            found |= _tiles_in(item)
    return found


def test_no_response_ever_contains_another_seats_tiles(players: list[Client]) -> None:
    """The invariant-5 guarantee, checked at the wire rather than at ``project()``.

    1.5 proved ``project`` does not leak. This proves that what actually reaches a client does
    not either - that no handler, response model or serializer put anything back in. Every
    response every player receives across a whole game is swept.
    """
    game = seated_game(players, marks_to_win=1)
    seat_of = {p.player_id: i for i, p in enumerate(players)}

    def check(player: Client, body: dict[str, Any]) -> None:
        view = body["view"]
        if view is None:
            return
        own = set(view["hand"] or ())
        # Tiles that have been played are public, so exclude them from the comparison.
        public = _tiles_in(view["current_trick"]) | _tiles_in(view["completed_tricks"])
        visible = _tiles_in(body) - own - public
        assert not visible, (
            f"{player.username} (seat {seat_of[player.player_id]}) can see {sorted(visible)}, "
            "which is neither their own hand nor already on the table"
        )

    play_until(players, game, on_response=check)

    for player in players:
        check(player, player.view(game))
