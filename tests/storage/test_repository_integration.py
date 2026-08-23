"""Repository writes and reads against a real DynamoDB Local (ROADMAP.md 1.6), the integration
counterpart of test_repository.py's moto-backed suite. One test per bullet in ROADMAP.md's 1.6
description: create/append/read-back, concurrent conflicting writes, idempotent replay of a
duplicate request, and a full scripted game persisted move by move. No repository or engine
behavior differs here - the point is proving the existing functions work unmodified against real
infra, not moto's approximation of it.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from random import Random

import pytest
from mypy_boto3_dynamodb.service_resource import Table

from tricksy.games.texas42.events import Event
from tricksy.games.texas42.game import apply_move, legal_moves
from tricksy.games.texas42.house_rules import HouseRules
from tricksy.games.texas42.moves import Move
from tricksy.games.texas42.state import GameState
from tricksy.storage.errors import VersionConflict
from tricksy.storage.events import events_for_move, hand_dealt_event
from tricksy.storage.replay import replay
from tricksy.storage.repository import append, get_state

from ..games.texas42._helpers import PLAYERS, drive_to_game_over, prefer_contract
from ._helpers import started_game

pytestmark = pytest.mark.integration


def _event_count(table: Table, game_id: str) -> int:
    response = table.query(
        KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
        ExpressionAttributeValues={":pk": f"GAME#{game_id}", ":prefix": "EVENT#"},
    )
    return int(response["Count"])


def test_create_append_and_read_back(real_table: Table) -> None:
    state = started_game(real_table, "g1")
    seat = state.to_act
    assert seat is not None
    move = legal_moves(state, PLAYERS[seat])[0]
    new_state = apply_move(state, move, rng=Random(1))
    events = events_for_move(state, move, new_state)

    new_version = append(real_table, "g1", events, new_state, expected_version=1)

    assert new_version == 2
    stored = get_state(real_table, "g1")
    assert stored.version == 2
    assert stored.state == new_state


def test_concurrent_conflicting_writes_do_not_corrupt_state(real_table: Table) -> None:
    state = started_game(real_table, "g1")
    seat = state.to_act
    assert seat is not None
    move = legal_moves(state, PLAYERS[seat])[0]
    new_state = apply_move(state, move, rng=Random(1))
    events = events_for_move(state, move, new_state)

    attempts = 8
    with ThreadPoolExecutor(max_workers=attempts) as executor:
        futures = [
            executor.submit(append, real_table, "g1", events, new_state, expected_version=1)
            for _ in range(attempts)
        ]
        results = [future.exception() or future.result() for future in futures]

    successes = [r for r in results if isinstance(r, int)]
    conflicts = [r for r in results if isinstance(r, VersionConflict)]
    assert successes == [2]
    assert len(conflicts) == attempts - 1

    stored = get_state(real_table, "g1")
    assert stored.version == 2
    assert stored.state == new_state


def test_idempotent_replay_of_a_duplicate_request(real_table: Table) -> None:
    state = started_game(real_table, "g1")
    seat = state.to_act
    assert seat is not None
    move = legal_moves(state, PLAYERS[seat])[0]
    new_state = apply_move(state, move, rng=Random(1))
    events = events_for_move(state, move, new_state)

    first_version = append(real_table, "g1", events, new_state, expected_version=1, request_id="r1")
    count_after_first = _event_count(real_table, "g1")

    second_version = append(
        real_table, "g1", events, new_state, expected_version=1, request_id="r1"
    )

    assert second_version == first_version == 2
    assert _event_count(real_table, "g1") == count_after_first

    stored = get_state(real_table, "g1")
    assert stored.version == 2
    assert stored.state == new_state


def test_full_scripted_game_persisted_move_by_move(real_table: Table) -> None:
    config = HouseRules()  # default marks_to_win=7: several hands, several re-deals
    game_id = "full-game"
    rng = Random(7)

    state = started_game(real_table, game_id, config=config)
    assert state.hand is not None
    logged_events: list[Event] = [hand_dealt_event(state.hand)]
    version_deltas: list[int] = []

    def record(before: GameState, move: Move, after: GameState) -> None:
        move_events = events_for_move(before, move, after)
        logged_events.extend(move_events)
        stored = get_state(real_table, game_id)
        new_version = append(real_table, game_id, move_events, after, stored.version)
        version_deltas.append(new_version - stored.version)

    final = drive_to_game_over(
        state, prefer_contract("standard"), rng, max_moves=5000, on_transition=record
    )

    assert 2 in version_deltas, "expected at least one hand to close and re-deal mid-game"
    stored = get_state(real_table, game_id)
    assert stored.state == final

    assert replay(game_id, PLAYERS, config, logged_events) == final
