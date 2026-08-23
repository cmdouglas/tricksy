"""Shared scaffolding for storage tests. Not collected as a test module (no test_ prefix)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from random import Random

from mypy_boto3_dynamodb.service_resource import Table

from tricksy.games.texas42.house_rules import HouseRules
from tricksy.games.texas42.state import GameState, Seat
from tricksy.storage.lobby import create_pending_game, join_seat
from tricksy.storage.repository import get_state

from ..games.texas42._helpers import PLAYERS


def _utcnow() -> datetime:
    return datetime.now(UTC)


def started_game(
    table: Table,
    game_id: str,
    *,
    seed: int = 0,
    config: HouseRules | None = None,
    now: Callable[[], datetime] = _utcnow,
) -> GameState:
    """A game dealt and ready to bid, reached the way a real one is: a lobby, then three more
    players joining it (ROADMAP.md 2.2). The fourth join deals, so this returns the state that
    deal produced.

    Tests that only care about a game being dealt use this rather than reaching past the lobby,
    so the path they exercise is the one the API actually takes.
    """
    creator, *joiners = ((seat, PLAYERS[seat]) for seat in Seat)
    create_pending_game(
        table, game_id, creator[1], creator[1], creator[0], config or HouseRules(), now=now
    )
    for seat, player_id in joiners:
        join_seat(table, game_id, player_id, player_id, seat, rng=Random(seed), now=now)
    return get_state(table, game_id).state
