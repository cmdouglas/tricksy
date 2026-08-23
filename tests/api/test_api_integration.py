"""The API against real DynamoDB Local rather than moto (ROADMAP.md 2.6).

The moto-backed contract tests cover behaviour; these cover the things moto approximates. The
whole Phase 2 stack runs against a real database: conditional writes, transactions, and the
``TransactionCanceledException`` that every optimistic-concurrency guarantee in the repository is
built on. If moto's approximation of any of that diverged from DynamoDB, the suite would still be
green and the deployed API would still be wrong.

Marked ``integration`` and excluded from the default run, since they need Docker: run them with
``uv run pytest -m integration``.
"""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from mypy_boto3_dynamodb.service_resource import Table

from tricksy.api.app import app
from tricksy.api.deps import get_table

from ._helpers import Client, play_until, seated_game, submit, whose_turn

pytestmark = pytest.mark.integration


@pytest.fixture
def real_client(real_table: Table) -> Iterator[TestClient]:
    """The same app the contract tests drive, pointed at a real DynamoDB Local container."""
    app.dependency_overrides[get_table] = lambda: real_table
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def players(real_client: TestClient) -> list[Client]:
    return [Client.register(real_client, name) for name in ("alice", "bob", "carol", "dave")]


def test_a_full_game_runs_signup_to_game_over(players: list[Client]) -> None:
    """Phase 2's exit criterion, end to end against real infrastructure."""
    game = seated_game(players, marks_to_win=1)

    body = play_until(players, game)

    assert body["view"]["phase"] == "GAME_OVER"
    assert body["status"] == "COMPLETE"

    for player in players:
        (summary,) = player.get("/players/me/games").json()["games"]
        assert summary["status"] == "COMPLETE"
        assert summary["is_my_turn"] is False


def test_concurrent_joins_cannot_double_seat_or_double_deal(
    real_client: TestClient, players: list[Client]
) -> None:
    """The claim the conditional writes exist for, under genuine concurrency rather than moto's
    sequential approximation: four players racing for the same seat, then three racing to fill
    the last one. Exactly one seat claim may win, and the game may only be dealt once."""
    latecomers = [Client.register(real_client, f"racer{i}") for i in range(4)]
    game = players[0].create_game(seat=0)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda p: p.join(game, 1), latecomers))

    winners = [r for r in results if r.status_code == 200]
    assert len(winners) == 1, [r.json() for r in results]
    assert all(r.json()["error"]["code"] == "SEAT_TAKEN" for r in results if r.status_code != 200)

    # Fill seats 2 and 3 concurrently: the one that lands last must deal, exactly once.
    seated = winners[0].json()["seats"][1]["player_id"]
    remaining = [p for p in latecomers if p.player_id != seated][:2]
    with ThreadPoolExecutor(max_workers=2) as pool:
        final = list(
            pool.map(
                lambda sp: sp[0].join(game, sp[1]),
                zip(remaining, (2, 3), strict=True),
            )
        )

    assert all(r.status_code == 200 for r in final), [r.json() for r in final]
    view = players[0].view(game)
    assert view["status"] == "ACTIVE"
    assert len(view["seats"]) == 4
    assert len(view["view"]["hand"]) == 7


def test_concurrent_submissions_of_the_same_move_apply_it_once(players: list[Client]) -> None:
    """A client that retries in parallel - the narrow race ``append``'s own idempotency check
    guards, which needs a real database to exercise. One event, one version bump, whichever
    request the database happens to serialize first."""
    game = seated_game(players)
    player, view = whose_turn(players, game)
    move = view["legal_moves"][0]

    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(
            pool.map(lambda _: submit(player, game, move, idempotency_key="retry-1"), range(4))
        )

    assert all(r.status_code == 200 for r in responses), [r.json() for r in responses]
    bodies = [r.json()["view"]["to_act"] for r in responses]
    assert len(set(bodies)) == 1, "every response must describe the same resulting state"


def test_a_second_player_racing_the_turn_is_rejected(players: list[Client]) -> None:
    """Two different players submitting at once. Turn order means only one of them can be legal,
    so this must never produce a mixed state."""
    game = seated_game(players)
    on_the_clock, view = whose_turn(players, game)
    interloper = next(p for p in players if p.player_id != on_the_clock.player_id)
    move = view["legal_moves"][0]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda p: submit(p, game, move),
                (on_the_clock, interloper),
            )
        )

    assert [r.status_code for r in results] == [200, 409]
    assert results[1].json()["error"]["code"] in {"OUT_OF_TURN", "VERSION_CONFLICT"}
