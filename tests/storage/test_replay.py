"""replay(events) reproduces the state a real game reached (ROADMAP.md 1.2): drive a game through
new_game/apply_move, record the events those calls would produce, then replay them and compare.
"""

from __future__ import annotations

from random import Random

import pytest

from tricksy.games.texas42.contracts import available
from tricksy.games.texas42.events import Event, Passed
from tricksy.games.texas42.game import new_game
from tricksy.games.texas42.house_rules import HouseRules
from tricksy.games.texas42.moves import Move
from tricksy.games.texas42.state import GameState
from tricksy.storage.events import events_for_move, hand_dealt_event
from tricksy.storage.replay import replay

from ..games.texas42._helpers import PLAYERS, Chooser, drive_to_game_over, prefer_contract


def _play_full_game(
    game_id: str, config: HouseRules, choose: Chooser, rng: Random
) -> tuple[list[Event], GameState]:
    state = new_game(game_id, PLAYERS, config, rng=rng)
    assert state.hand is not None
    events: list[Event] = [hand_dealt_event(state.hand)]

    def record(before: GameState, move: Move, after: GameState) -> None:
        events.extend(events_for_move(before, move, after))

    final = drive_to_game_over(state, choose, rng, max_moves=5000, on_transition=record)
    return events, final


@pytest.mark.parametrize("contract_name", sorted(available()))
def test_replay_reproduces_a_full_game(contract_name: str) -> None:
    config = HouseRules(
        enabled_contracts=frozenset(available()), marks_to_win=1, allow_declared_lead="always"
    )
    game_id = f"replay-{contract_name}"
    rng = Random(hash(contract_name) & 0xFFFF)

    events, final = _play_full_game(game_id, config, prefer_contract(contract_name), rng)

    assert replay(game_id, PLAYERS, config, events) == final


def test_replay_reproduces_a_random_multi_hand_game() -> None:
    config = HouseRules()  # default marks_to_win=7: several hands, several re-deals
    game_id = "replay-random"
    rng = Random(99)

    def choose(state: GameState, options: tuple[Move, ...]) -> Move:
        return rng.choice(options)

    events, final = _play_full_game(game_id, config, choose, rng)

    assert replay(game_id, PLAYERS, config, events) == final


def test_replay_rejects_a_log_not_opening_with_hand_dealt() -> None:
    with pytest.raises(ValueError, match="HAND_DEALT"):
        replay("g", PLAYERS, HouseRules(), [Passed(actor="north")])


def test_replay_rejects_an_empty_log() -> None:
    with pytest.raises(ValueError, match="HAND_DEALT"):
        replay("g", PLAYERS, HouseRules(), [])
