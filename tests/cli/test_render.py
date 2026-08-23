from __future__ import annotations

import re
from random import Random
from typing import Any

import pytest

from tricksy.cli import render
from tricksy.games.texas42.game import new_game
from tricksy.games.texas42.house_rules import HouseRules
from tricksy.games.texas42.projection import project
from tricksy.games.texas42.state import GameState, Seat

from ..games.texas42._helpers import PLAYERS, drive_to_game_over, player_of, prefer_contract

# --- seat / suit name tables -------------------------------------------------

_SEAT_CASES: tuple[tuple[int | None, str], ...] = (
    (0, "north"),
    (1, "east"),
    (2, "south"),
    (3, "west"),
    (None, "-"),
)


@pytest.mark.parametrize(("seat", "expected"), _SEAT_CASES)
def test_seat_name(seat: int | None, expected: str) -> None:
    assert render._seat_name(seat) == expected


_SUIT_CASES: tuple[tuple[int | None, str], ...] = (
    (0, "blanks"),
    (1, "aces"),
    (2, "deuces"),
    (3, "treys"),
    (4, "fours"),
    (5, "fives"),
    (6, "sixes"),
    (7, "doubles"),
    (None, "-"),
)


@pytest.mark.parametrize(("suit", "expected"), _SUIT_CASES)
def test_suit_name(suit: int | None, expected: str) -> None:
    assert render._suit_name(suit) == expected


# --- legal moves as commands --------------------------------------------------

_MOVE_CASES: tuple[tuple[dict[str, Any], str], ...] = (
    (
        {"kind": "BID", "contract": None, "points": 32, "marks": None},
        "tricksy bid ABCD 32",
    ),
    (
        {"kind": "BID", "contract": "nello", "points": None, "marks": 2},
        "tricksy bid ABCD 2-marks --contract nello",
    ),
    (
        {"kind": "BID", "contract": None, "points": None, "marks": 1},
        "tricksy bid ABCD 1-mark",
    ),
    ({"kind": "PASS"}, "tricksy bid ABCD pass"),
    ({"kind": "CONFIRM_BID", "accept": True}, "tricksy bid ABCD confirm"),
    ({"kind": "CONFIRM_BID", "accept": False}, "tricksy bid ABCD decline"),
    (
        {"kind": "DECLARE_CONTRACT", "trump": 5},
        "tricksy declare ABCD trump=fives",
    ),
    (
        {"kind": "DECLARE_CONTRACT", "trump": None},
        "tricksy declare ABCD trump=none",
    ),
    (
        {"kind": "PLAY_DOMINO", "domino": "4-1", "declared_suit": None},
        "tricksy play ABCD 4-1",
    ),
    (
        {"kind": "PLAY_DOMINO", "domino": "6-3", "declared_suit": 3},
        "tricksy play ABCD 6-3 --declare treys",
    ),
)


@pytest.mark.parametrize(("move", "expected"), _MOVE_CASES)
def test_move_command(move: dict[str, Any], expected: str) -> None:
    assert render._render_move_command(move, "ABCD") == expected


def test_unknown_move_kind_raises() -> None:
    with pytest.raises(ValueError, match="unknown move kind"):
        render._render_move_command({"kind": "TELEPORT"}, "ABCD")


def test_empty_legal_moves_renders_placeholder() -> None:
    result = render.render_legal_moves([], "ABCD")
    assert "nothing to do" in result


def test_legal_moves_renders_one_line_per_move() -> None:
    moves: list[dict[str, Any]] = [
        {"kind": "PASS"},
        {"kind": "BID", "contract": None, "points": 30, "marks": None},
    ]
    result = render.render_legal_moves(moves, "ABCD")
    lines = result.splitlines()
    assert lines == ["  tricksy bid ABCD pass", "  tricksy bid ABCD 30"]


# --- render_game: lobby-only ---------------------------------------------------


def _lobby_game(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "game_id": "ABCD",
        "status": "WAITING",
        "visibility": "public",
        "seats": [{"seat": 0, "player_id": "p-alice", "username": "alice"}],
        "house_rules": {
            "enabled_contracts": ["standard"],
            "contract_options": {},
            "doubles_are_own_suit": False,
            "allow_declared_lead": "never",
            "marks_to_win": 7,
        },
        "view": None,
    }
    base.update(overrides)
    return base


def test_lobby_only_shows_open_seats() -> None:
    result = render.render_game(_lobby_game())
    assert "north: alice" in result
    assert "east:  (open)" in result
    assert "south: (open)" in result
    assert "west:  (open)" in result
    assert "phase:" not in result
    assert "hand:" not in result


def test_lobby_only_active_status_still_lobby_only_when_not_seated() -> None:
    result = render.render_game(_lobby_game(status="ACTIVE"))
    assert "Game ABCD - ACTIVE - public" in result
    assert "phase:" not in result


def test_lobby_house_rules_render_contract_options() -> None:
    game = _lobby_game(
        house_rules={
            "enabled_contracts": ["plunge", "standard"],
            "contract_options": {"plunge": {"minimum_doubles": 5}},
            "doubles_are_own_suit": True,
            "allow_declared_lead": "first_trick",
            "marks_to_win": 7,
        }
    )
    result = render.render_game(game)
    assert "plunge.minimum_doubles=5" in result


# --- render_game: full view ---------------------------------------------------


def _view(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "game_id": "ABCD",
        "phase": "BIDDING",
        "seat": 2,
        "dealer": 3,
        "to_act": 2,
        "marks": {"north_south": 2, "east_west": 1},
        "declarer": None,
        "contract": None,
        "trump": None,
        "hand": ["4-1", "5-3", "6-6", "2-0", "3-3", "5-1", "0-0"],
        "current_trick": {"plays": [], "declared_suit": None, "winner": None},
        "completed_tricks": [],
        "legal_moves": [{"kind": "PASS"}],
    }
    base.update(overrides)
    return base


def _seated_game(view: dict[str, Any]) -> dict[str, Any]:
    return _lobby_game(
        status="ACTIVE",
        seats=[
            {"seat": 0, "player_id": "p-alice", "username": "alice"},
            {"seat": 1, "player_id": "p-bob", "username": "bob"},
            {"seat": 2, "player_id": "p-carol", "username": "carol"},
            {"seat": 3, "player_id": "p-dave", "username": "dave"},
        ],
        view=view,
    )


def test_seated_marks_the_callers_own_seat() -> None:
    result = render.render_game(_seated_game(_view()))
    assert "south: carol (you)" in result
    assert "north: alice" in result
    assert "alice (you)" not in result


def test_game_over_view_with_no_current_hand_does_not_crash() -> None:
    """Every response to the move that ends a game looks like this: ``phase`` flips to
    ``GAME_OVER`` and ``hand``/``current_trick``/``dealer``/``declarer``/``contract``/``trump``/
    ``to_act`` all go back to ``None`` (``tricksy.games.texas42.projection.project`` has no hand
    left to read them from) - a real, reachable shape every completed game hits, not an edge
    case."""
    view = _view(
        phase="GAME_OVER",
        dealer=None,
        to_act=None,
        declarer=None,
        contract=None,
        trump=None,
        hand=None,
        current_trick=None,
        completed_tricks=[],
        legal_moves=[],
    )
    result = render.render_game(_seated_game(view))
    assert "phase: GAME_OVER" in result


def test_bidding_phase_renders_legal_bids_as_commands() -> None:
    view = _view(
        legal_moves=[
            {"kind": "BID", "contract": None, "points": 30, "marks": None},
            {"kind": "BID", "contract": "nello", "points": None, "marks": 2},
            {"kind": "PASS"},
        ]
    )
    result = render.render_game(_seated_game(view))
    assert "tricksy bid ABCD 30" in result
    assert "tricksy bid ABCD 2-marks --contract nello" in result
    assert "tricksy bid ABCD pass" in result


def test_playing_phase_partial_trick_with_declared_suit() -> None:
    view = _view(
        phase="PLAYING",
        declarer=0,
        contract="standard",
        trump=5,
        to_act=1,
        current_trick={
            "plays": [{"seat": 0, "domino": "6-4"}, {"seat": 1, "domino": "3-3"}],
            "declared_suit": 5,
            "winner": None,
        },
        completed_tricks=[{"plays": [], "declared_suit": None, "winner": 2}] * 3,
        legal_moves=[{"kind": "PLAY_DOMINO", "domino": "4-1", "declared_suit": None}],
    )
    result = render.render_game(_seated_game(view))
    assert "north: 6-4  (led: fives)" in result
    assert "east: 3-3" in result
    assert "completed tricks: 3" in result
    assert "trump: fives" in result
    assert "declarer: north" in result
    assert "tricksy play ABCD 4-1" in result


def test_no_trump_contract_renders_prose_no_trump() -> None:
    view = _view(phase="DECLARING", declarer=2, contract="nello", trump=None)
    result = render.render_game(_seated_game(view))
    assert "trump: no trump" in result
    assert "trump: None" not in result


def test_none_dealer_and_declarer_render_as_dash_not_string_none() -> None:
    view = _view(dealer=None, declarer=None, to_act=None)
    result = render.render_game(_seated_game(view))
    assert "dealer: -" in result
    assert "declarer: -" in result
    assert "to_act: -" in result
    assert "None" not in result


def test_game_over_phase_with_no_legal_moves() -> None:
    view = _view(phase="GAME_OVER", to_act=None, legal_moves=[])
    result = render.render_game(_seated_game(view))
    assert "nothing to do" in result


def test_declare_contract_confirmation_only_legal_moves() -> None:
    view = _view(
        legal_moves=[
            {"kind": "CONFIRM_BID", "accept": True},
            {"kind": "CONFIRM_BID", "accept": False},
        ]
    )
    result = render.render_game(_seated_game(view))
    assert "tricksy bid ABCD confirm" in result
    assert "tricksy bid ABCD decline" in result


# --- games list -----------------------------------------------------------------


def test_games_list_marks_my_turn() -> None:
    response = {
        "games": [
            {"game_id": "AAAA", "status": "ACTIVE", "seat": 0, "is_my_turn": True},
            {"game_id": "BBBB", "status": "ACTIVE", "seat": 1, "is_my_turn": False},
        ]
    }
    result = render.render_games_list(response)
    lines = result.splitlines()
    assert lines[0].endswith("*")
    assert not lines[1].endswith("*")


def test_games_list_empty() -> None:
    assert render.render_games_list({"games": []}) == "(no games)"


# --- open games / invite list ----------------------------------------------------


def test_open_games_empty() -> None:
    assert render.render_open_games({"games": []}) == "(no open games)"


def test_open_games_multiple_rows_separated_by_blank_line() -> None:
    response = {"games": [_lobby_game(game_id="AAAA"), _lobby_game(game_id="BBBB")]}
    result = render.render_open_games(response)
    assert "\n\n" in result
    assert "Game AAAA" in result
    assert "Game BBBB" in result


def test_invite_list_empty() -> None:
    assert render.render_invite_list({"games": []}) == "(no pending invites)"


def test_invite_list_rows_have_no_view() -> None:
    response = {"games": [_lobby_game(game_id="AAAA")]}
    result = render.render_invite_list(response)
    assert "phase:" not in result


# --- game invites (host side) ---------------------------------------------------


def test_game_invites_empty() -> None:
    assert render.render_game_invites({"invites": []}) == "(no pending invites)"


def test_game_invites_lists_username_and_created_at() -> None:
    response = {
        "invites": [{"player_id": "p-1", "username": "erin", "created_at": "2026-08-01T00:00:00Z"}]
    }
    result = render.render_game_invites(response)
    assert "erin" in result
    assert "2026-08-01T00:00:00Z" in result


# --- rule sets --------------------------------------------------------------------


def _house_rules(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "enabled_contracts": ["standard"],
        "contract_options": {},
        "doubles_are_own_suit": False,
        "allow_declared_lead": "never",
        "marks_to_win": 7,
    }
    base.update(overrides)
    return base


def test_render_rule_set_round_trips_set_flag_spelling() -> None:
    rule_set = {
        "rule_set_id": "rs-1",
        "name": "high-stakes",
        "created_at": "2026-08-01T00:00:00Z",
        "house_rules": _house_rules(
            enabled_contracts=["plunge", "standard"],
            contract_options={"plunge": {"minimum_doubles": 5}},
        ),
    }
    result = render.render_rule_set(rule_set)
    assert "high-stakes (rs-1)" in result
    assert "plunge.minimum_doubles=5" in result


def test_rule_set_list_empty() -> None:
    assert render.render_rule_set_list({"rule_sets": []}) == "(no saved rule sets)"


def test_rule_set_list_shows_each_set() -> None:
    response = {
        "rule_sets": [
            {
                "rule_set_id": "rs-1",
                "name": "casual",
                "created_at": "2026-08-01T00:00:00Z",
                "house_rules": _house_rules(),
            },
            {
                "rule_set_id": "rs-2",
                "name": "sharks",
                "created_at": "2026-08-01T00:00:00Z",
                "house_rules": _house_rules(enabled_contracts=["nello", "standard"]),
            },
        ]
    }
    result = render.render_rule_set_list(response)
    assert "rs-1  casual" in result
    assert "rs-2  sharks" in result


# --- profile -----------------------------------------------------------------------


def test_profile_renders_all_contacts_and_devices() -> None:
    player = {
        "player_id": "p-1",
        "username": "carol",
        "created_at": "2026-08-01T00:00:00Z",
        "contacts": [
            {"kind": "email", "address": "carol@example.com", "verified": True, "notify": True},
            {"kind": "sms", "address": "+15551234567", "verified": False, "notify": False},
        ],
        "devices": [
            {
                "token_hash": "abc",
                "label": "laptop",
                "created_at": "2026-08-01T00:00:00Z",
                "last_used_at": "2026-08-02T00:00:00Z",
            },
            {
                "token_hash": "def",
                "label": "phone",
                "created_at": "2026-08-01T00:00:00Z",
                "last_used_at": "2026-08-03T00:00:00Z",
            },
        ],
    }
    result = render.render_profile(player)
    assert "carol@example.com" in result
    assert "verified" in result
    assert "+15551234567" in result
    assert "unverified" in result
    assert "notifying" in result
    assert "muted" in result
    assert "laptop" in result
    assert "phone" in result


def test_profile_empty_contacts_and_devices() -> None:
    player = {
        "player_id": "p-1",
        "username": "carol",
        "created_at": "2026-08-01T00:00:00Z",
        "contacts": [],
        "devices": [],
    }
    result = render.render_profile(player)
    assert "contacts: (none)" in result
    assert "devices: (none)" in result


# --- contacts (ROADMAP.md 4.6) ------------------------------------------------------


def test_render_contact_shows_kind_address_verified_and_notify() -> None:
    contact = {"kind": "email", "address": "carol@example.com", "verified": True, "notify": False}
    result = render.render_contact(contact)
    assert "carol@example.com" in result
    assert "verified" in result
    assert "muted" in result


def test_render_contact_list_shows_every_contact() -> None:
    response = {
        "contacts": [
            {"kind": "email", "address": "a@example.com", "verified": True, "notify": True},
            {"kind": "email", "address": "b@example.com", "verified": False, "notify": True},
        ]
    }
    result = render.render_contact_list(response)
    assert "a@example.com" in result
    assert "b@example.com" in result


def test_render_contact_list_when_empty() -> None:
    assert render.render_contact_list({"contacts": []}) == "(no contact channels)"


# --- the third leakage proof (ROADMAP.md 3.7) -----------------------------------------------
#
# 1.5 proved project() doesn't leak another seat's tiles; 2.5 proved the wire doesn't either. This
# proves the same of what render_game actually prints - the only one a player looks at.

_CONFIG = HouseRules(
    enabled_contracts=frozenset({"standard", "nello", "plunge", "sevens"}), marks_to_win=1
)
_TILE_RE = re.compile(r"(?<!\d)[0-6]-[0-6](?!\d)")


def _hidden_tiles(state: GameState, seat: Seat) -> set[str]:
    hand = state.hand
    assert hand is not None
    return {
        str(domino)
        for other_seat, tiles in hand.hands.items()
        if other_seat != seat
        for domino in tiles
    }


@pytest.mark.parametrize("contract_name", ["standard", "nello"])
def test_no_foreign_tile_ever_appears_in_rendered_output(contract_name: str) -> None:
    rng = Random(1)
    state = new_game(f"g-{contract_name}", PLAYERS, _CONFIG, rng=rng)
    snapshots = [state]
    drive_to_game_over(
        state, prefer_contract(contract_name), rng, on_state=snapshots.append, max_moves=200
    )

    for snapshot in snapshots:
        if snapshot.hand is None:
            continue
        for seat in Seat:
            hidden = _hidden_tiles(snapshot, seat)
            view = project(snapshot, player_of(seat))
            rendered = render.render_game(_seated_game(view))
            leaked = hidden & set(_TILE_RE.findall(rendered))
            assert not leaked, f"{contract_name}, seat {seat}: leaked {leaked}"
