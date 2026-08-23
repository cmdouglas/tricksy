"""The ``OpenGames`` GSI against a real DynamoDB Local (ROADMAP.md 2.7.3), the integration
counterpart of ``test_lobby.py``'s moto-backed suite. A GSI is eventually consistent - a table
created a moment ago may not appear in the very next query - and moto, being strongly consistent,
can never reproduce that (DESIGN.md §4.1). So this is the one guarantee that needs polling rather
than an immediate assertion, and the one guarantee moto alone can't establish.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import pytest
from mypy_boto3_dynamodb.service_resource import Table

from tricksy.games.texas42.house_rules import HouseRules
from tricksy.games.texas42.state import Seat
from tricksy.storage.lobby import create_pending_game, list_open_games

pytestmark = pytest.mark.integration


def _wait_until(
    predicate: Callable[[], bool], *, timeout: float = 15.0, interval: float = 0.2
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_a_newly_created_public_game_eventually_appears_in_the_open_index(
    real_table: Table,
) -> None:
    create_pending_game(real_table, "g1", "north", "north", Seat.NORTH, HouseRules())

    appeared = _wait_until(
        lambda: any(lobby.game_id == "g1" for lobby in list_open_games(real_table))
    )

    assert appeared, "g1 never appeared in the OpenGames index"
