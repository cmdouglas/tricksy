"""The game-kind discriminator and the stored seat count (DESIGN.md §11).

Neither one does anything yet - there is exactly one engine and nothing dispatches on ``kind``.
What these tests protect is that both are *written*, that both are read back tolerantly, and that
the lobby's notion of a full table comes from the item rather than from ``len(Seat)``. That is the
whole point of adding them before anything is deployed: a discriminator missing from an immutable
event log is a backfill, not an edit.
"""

from __future__ import annotations

from random import Random
from typing import Any

from mypy_boto3_dynamodb.service_resource import Table

from t42.engine import GAME_KIND
from t42.engine.house_rules import HouseRules
from t42.engine.state import Seat
from t42.storage.lobby import (
    Lobby,
    SeatAssignment,
    Visibility,
    create_pending_game,
    get_lobby,
    join_seat,
    list_games_for_player,
    list_open_games,
)
from t42.storage.repository import GameStatus, get_state
from t42.storage.rule_sets import create_rule_set, get_rule_set

_OTHERS = {Seat.EAST: "east", Seat.SOUTH: "south", Seat.WEST: "west"}


def _open_lobby(table: Table, game_id: str = "g1") -> None:
    create_pending_game(table, game_id, "north", "north", Seat.NORTH, HouseRules())


def _fill(table: Table, game_id: str = "g1") -> None:
    for seat, player_id in _OTHERS.items():
        join_seat(table, game_id, player_id, player_id, seat, rng=Random(0))


def _meta(table: Table, game_id: str = "g1") -> dict[str, Any]:
    item = table.get_item(Key={"PK": f"GAME#{game_id}", "SK": "META"})["Item"]
    return dict(item)


# ------------------------------------------------------------------------------ what gets written


def test_meta_carries_kind_and_seat_count(table: Table) -> None:
    _open_lobby(table)

    item = _meta(table)

    assert item["kind"] == GAME_KIND
    assert item["seat_count"] == len(Seat)


def test_lobby_decodes_kind_and_seat_count(table: Table) -> None:
    _open_lobby(table)

    lobby = get_lobby(table, "g1")

    assert lobby.kind == GAME_KIND
    assert lobby.seat_count == len(Seat)


def test_player_game_item_carries_kind(table: Table) -> None:
    _open_lobby(table)

    item = table.get_item(Key={"PK": "PLAYER#north", "SK": "GAME#g1"})["Item"]

    assert item["kind"] == GAME_KIND


def test_game_summary_decodes_kind(table: Table) -> None:
    _open_lobby(table)

    (summary,) = list_games_for_player(table, "north")

    assert summary.kind == GAME_KIND


def test_state_item_carries_kind(table: Table) -> None:
    _open_lobby(table)
    _fill(table)

    item = table.get_item(Key={"PK": "GAME#g1", "SK": "STATE"})["Item"]

    assert item["kind"] == GAME_KIND
    assert get_state(table, "g1").kind == GAME_KIND


def test_rule_set_carries_kind(table: Table) -> None:
    created = create_rule_set(table, "north", "weeknights", HouseRules())

    assert created.kind == GAME_KIND
    assert get_rule_set(table, "north", created.rule_set_id).kind == GAME_KIND


# --------------------------------------------------------------------------- read back tolerantly


def test_meta_written_before_kind_existed_still_decodes(table: Table) -> None:
    """A missing ``kind`` can only mean the one game there was, and a missing ``seat_count`` the
    one table shape there was - so both default rather than raising."""
    _open_lobby(table)
    table.update_item(
        Key={"PK": "GAME#g1", "SK": "META"},
        UpdateExpression="REMOVE #k, seat_count",
        ExpressionAttributeNames={"#k": "kind"},
    )

    lobby = get_lobby(table, "g1")

    assert lobby.kind == GAME_KIND
    assert lobby.seat_count == len(Seat)


def test_state_written_before_kind_existed_still_decodes(table: Table) -> None:
    _open_lobby(table)
    _fill(table)
    table.update_item(
        Key={"PK": "GAME#g1", "SK": "STATE"},
        UpdateExpression="REMOVE #k",
        ExpressionAttributeNames={"#k": "kind"},
    )

    assert get_state(table, "g1").kind == GAME_KIND


def test_rule_set_written_before_kind_existed_still_decodes(table: Table) -> None:
    created = create_rule_set(table, "north", "weeknights", HouseRules())
    table.update_item(
        Key={"PK": "PLAYER#north", "SK": f"RULESET#{created.rule_set_id}"},
        UpdateExpression="REMOVE #k",
        ExpressionAttributeNames={"#k": "kind"},
    )

    assert get_rule_set(table, "north", created.rule_set_id).kind == GAME_KIND


def test_player_game_item_written_before_kind_existed_still_decodes(table: Table) -> None:
    _open_lobby(table)
    table.update_item(
        Key={"PK": "PLAYER#north", "SK": "GAME#g1"},
        UpdateExpression="REMOVE #k",
        ExpressionAttributeNames={"#k": "kind"},
    )

    (summary,) = list_games_for_player(table, "north")

    assert summary.kind == GAME_KIND


# ------------------------------------------------------------- the seat count runs off the item


def _lobby_with(seat_count: int, filled: int) -> Lobby:
    return Lobby(
        game_id="g1",
        kind=GAME_KIND,
        status=GameStatus.WAITING,
        visibility=Visibility.PUBLIC,
        config=HouseRules(),
        seats={
            seat: SeatAssignment(player_id=f"p{seat}", username=f"p{seat}")
            for seat in range(filled)
        },
        seat_count=seat_count,
        created_at="2026-01-01T00:00:00+00:00",
        last_activity_at="2026-01-01T00:00:00+00:00",
    )


def test_is_full_follows_the_stored_seat_count_not_the_engines() -> None:
    """The property a game with a different number of seats depends on: a two-seat table is full
    at two, even though ``len(Seat)`` is four."""
    assert _lobby_with(seat_count=2, filled=2).is_full is True
    assert _lobby_with(seat_count=2, filled=1).is_full is False
    assert _lobby_with(seat_count=6, filled=4).is_full is False


def test_open_seats_follows_the_stored_seat_count() -> None:
    assert _lobby_with(seat_count=2, filled=1).open_seats == (1,)
    assert _lobby_with(seat_count=6, filled=4).open_seats == (4, 5)
    assert _lobby_with(seat_count=4, filled=4).open_seats == ()


# ------------------------------------------------------------------------- the open-games browse


def test_open_games_is_scoped_to_one_kind(table: Table) -> None:
    """The ``OpenGames`` GSI partition is ``OPEN#<kind>``, so browsing never mixes games."""
    _open_lobby(table, "g1")
    foreign = _meta(table, "g1")
    foreign["PK"] = "GAME#g2"
    foreign["kind"] = "spades"
    foreign["GSI1PK"] = "OPEN#spades"
    table.put_item(Item=foreign)

    assert [lobby.game_id for lobby in list_open_games(table)] == ["g1"]
    assert [lobby.game_id for lobby in list_open_games(table, kind="spades")] == ["g2"]
