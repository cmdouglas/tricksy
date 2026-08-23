"""Phase 1.5 (ROADMAP.md): project() is the only gate for hidden information (invariant 5), so
this suite's centerpiece is the leakage test - walking the projected structure of real, played
games and asserting no tile held by another seat ever appears in it.
"""

from __future__ import annotations

from collections.abc import Iterator
from random import Random
from typing import Any

import pytest

from tricksy.games.texas42.dominoes import parse
from tricksy.games.texas42.game import apply_move, legal_moves, new_game
from tricksy.games.texas42.house_rules import HouseRules
from tricksy.games.texas42.projection import project
from tricksy.games.texas42.state import GameState, Phase, Seat

from ._helpers import PLAYERS, drive_to_game_over, first_option, player_of, prefer_contract

CONFIG = HouseRules(
    enabled_contracts=frozenset({"standard", "nello", "plunge", "sevens"}), marks_to_win=1
)


def _walk_strings(value: Any) -> Iterator[str]:
    """Every leaf string in a nested dict/list structure - the shape project() returns."""
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_strings(item)
    elif isinstance(value, str):
        yield value


def _hidden_tiles(state: GameState, seat: Seat) -> set[str]:
    hand = state.hand
    assert hand is not None
    return {
        str(domino)
        for other_seat, tiles in hand.hands.items()
        if other_seat != seat
        for domino in tiles
    }


def _snapshots(contract_name: str, seed: int) -> list[GameState]:
    rng = Random(seed)
    state = new_game(f"g-{contract_name}", PLAYERS, CONFIG, rng=rng)
    snapshots: list[GameState] = [state]
    drive_to_game_over(
        state, prefer_contract(contract_name), rng, on_state=snapshots.append, max_moves=200
    )
    return snapshots


def _drive_until_playing(state: GameState, contract_name: str, rng: Random) -> GameState:
    choose = prefer_contract(contract_name)
    for _ in range(200):
        if state.phase is Phase.PLAYING:
            return state
        seat = state.to_act
        assert seat is not None
        move = choose(state, legal_moves(state, player_of(seat)))
        state = apply_move(state, move, rng=rng)
    raise AssertionError(f"never reached PLAYING while bidding for {contract_name}")


@pytest.mark.parametrize("contract_name", ["standard", "nello"])
def test_no_foreign_tile_ever_appears_in_a_projection(contract_name: str) -> None:
    for state in _snapshots(contract_name, seed=1):
        if state.hand is None:
            continue
        for seat in Seat:
            hidden = _hidden_tiles(state, seat)
            projected = project(state, player_of(seat))
            leaked = hidden & set(_walk_strings(projected))
            assert not leaked, f"{contract_name}, seat {seat}: leaked {leaked}"


def test_own_hand_matches_state() -> None:
    rng = Random(2)
    state = new_game("g-own-hand", PLAYERS, CONFIG, rng=rng)
    assert state.hand is not None
    for seat in Seat:
        projected = project(state, player_of(seat))
        assert projected["hand"] == [str(domino) for domino in state.hand.hands[seat]]


def test_hand_notation_is_parseable() -> None:
    rng = Random(8)
    state = new_game("g-notation", PLAYERS, CONFIG, rng=rng)
    assert state.hand is not None
    projected = project(state, player_of(Seat.NORTH))
    parsed = {parse(text) for text in projected["hand"]}
    assert parsed == set(state.hand.hands[Seat.NORTH])


def test_legal_moves_empty_unless_it_is_the_callers_turn() -> None:
    rng = Random(3)
    state = new_game("g-legal-moves", PLAYERS, CONFIG, rng=rng)
    assert state.to_act is not None
    for seat in Seat:
        projected = project(state, player_of(seat))
        if seat == state.to_act:
            assert projected["legal_moves"], "the seat to act should see at least one legal move"
        else:
            assert projected["legal_moves"] == []


def test_legal_moves_match_the_engines_own_enumeration() -> None:
    rng = Random(4)
    state = new_game("g-legal-moves-match", PLAYERS, CONFIG, rng=rng)
    assert state.to_act is not None
    actor = player_of(state.to_act)
    projected = project(state, actor)

    expected_kinds = [move.kind for move in legal_moves(state, actor)]
    assert [move["kind"] for move in projected["legal_moves"]] == expected_kinds


def test_nello_hand_shows_contract_and_declarer_with_no_trump() -> None:
    rng = Random(5)
    state = new_game("g-nello", PLAYERS, CONFIG, rng=rng)
    state = _drive_until_playing(state, "nello", rng)
    assert state.hand is not None and state.hand.declarer is not None

    projected = project(state, player_of(state.hand.declarer))
    assert projected["contract"] == "nello"
    assert projected["declarer"] == state.hand.declarer.value
    assert projected["trump"] is None


def test_game_over_projection_has_no_active_hand_fields() -> None:
    rng = Random(6)
    state = new_game("g-over", PLAYERS, HouseRules(marks_to_win=1), rng=rng)
    final = drive_to_game_over(state, first_option, rng)
    assert final.phase is Phase.GAME_OVER

    for seat in Seat:
        projected = project(final, player_of(seat))
        assert projected["phase"] == "GAME_OVER"
        assert projected["hand"] is None
        assert projected["dealer"] is None
        assert projected["declarer"] is None
        assert projected["contract"] is None
        assert projected["trump"] is None
        assert projected["current_trick"] is None
        assert projected["completed_tricks"] == []
        assert projected["to_act"] is None
        assert projected["legal_moves"] == []
        assert sum(projected["marks"].values()) == sum(final.marks.values())


def test_unseated_player_raises_key_error() -> None:
    rng = Random(7)
    state = new_game("g-unseated", PLAYERS, CONFIG, rng=rng)
    with pytest.raises(KeyError):
        project(state, "not-a-player")
