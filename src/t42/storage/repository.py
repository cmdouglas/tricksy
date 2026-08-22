"""Durable game state in DynamoDB (ROADMAP.md 1.3), per DESIGN.md §4.1's single-table shape:
``META``, ``EVENT#<seq>``, ``STATE``, ``PLAYER#`` and ``REQUEST#<requestId>`` items under one
partition per game.

Functions here take a boto3 ``Table`` resource as an explicit parameter rather than owning a
connection themselves - the same injected-dependency style as ``rng: Random`` throughout the
engine, and it keeps this module trivially testable against a moto-backed table.

The boto3 wrinkles this module works around - ``TransactWriteItems`` living only on the client,
and the resource API deserializing numbers to ``Decimal`` - are handled by
:mod:`t42.storage._dynamo`, which documents both.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from random import Random
from typing import Any, cast

from botocore.exceptions import ClientError
from mypy_boto3_dynamodb.service_resource import Table

from t42.engine import GAME_KIND
from t42.engine.events import Event
from t42.engine.game import new_game
from t42.engine.house_rules import HouseRules
from t42.engine.state import GameId, GameState, Phase, PlayerId, Seat, Team

from ._dynamo import from_dynamo, is_transaction_cancelled, transact_write
from .codec import decode_game_state, encode_event, encode_game_state
from .errors import GameAlreadyStarted, GameNotFound, VersionConflict
from .events import hand_dealt_event


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _game_pk(game_id: GameId) -> str:
    return f"GAME#{game_id}"


def _player_pk(player_id: PlayerId) -> str:
    return f"PLAYER#{player_id}"


def _game_sk(game_id: GameId) -> str:
    return f"GAME#{game_id}"


def _event_sk(seq: int) -> str:
    return f"EVENT#{seq:06d}"


def _request_sk(request_id: str) -> str:
    return f"REQUEST#{request_id}"


class GameStatus(StrEnum):
    """Where a game sits in its lifecycle (DESIGN.md §4.1). Lives on the ``META`` item and is
    denormalized onto each ``PLAYER#`` item so "my games" is one query.

    This is storage's business, not the engine's: ``GameState.phase`` describes a game that has
    been dealt, and has no way to say "still waiting for players" (invariant 1).
    """

    WAITING = "WAITING"
    ACTIVE = "ACTIVE"
    COMPLETE = "COMPLETE"


@dataclass(frozen=True, slots=True)
class StoredGame:
    """What a read returns: the decoded state plus the ``version`` ``append`` must be given back
    to write the next event. ``version`` lives here, not on ``GameState`` - optimistic-concurrency
    bookkeeping is a storage concern, not a rules concern (invariant 1).

    ``kind`` says which engine produced ``state``. It is carried on the ``STATE`` item itself, not
    only on ``META``, so a read that decodes state never has to fetch a second item to know what it
    is holding (DESIGN.md §11)."""

    state: GameState
    version: int
    kind: str = GAME_KIND


def start_game(
    table: Table,
    game_id: GameId,
    players: Mapping[Seat, PlayerId],
    config: HouseRules,
    rng: Random,
    *,
    now: Callable[[], datetime] = _utcnow,
) -> GameState:
    """Deals the first hand into a game whose lobby has just filled (ROADMAP.md 2.2).

    Writes the opening ``HAND_DEALT`` event and ``STATE`` (``version=1``), and flips ``META`` from
    ``WAITING`` to ``ACTIVE``, all in one transaction. The ``META`` update is *conditioned on the
    status still being* ``WAITING``, which is what makes the deal happen exactly once even if two
    attempts race: the loser's whole transaction is cancelled, so it writes no event and no state
    either, and sees :class:`~t42.storage.errors.GameAlreadyStarted`.

    The ``PLAYER#`` items already exist - :func:`t42.storage.lobby.join_seat` writes one per seat
    as players arrive - so this updates their turn and status rather than creating them.
    """
    state = new_game(game_id, players, config, rng=rng)
    assert state.hand is not None
    event = hand_dealt_event(state.hand)
    timestamp = now().isoformat()

    transact_items: list[dict[str, Any]] = [
        {
            "Update": {
                "TableName": table.name,
                "Key": {"PK": _game_pk(game_id), "SK": "META"},
                # ``REMOVE`` is what drops a public game out of the ``OpenGames`` GSI
                # (DESIGN.md §4.1): this is the only transition out of ``WAITING``, so it's the
                # only place that removal is needed. Removing an attribute an invite-only game
                # never had is a safe no-op.
                "UpdateExpression": (
                    "SET #status = :active, last_activity_at = :ts REMOVE GSI1PK, GSI1SK"
                ),
                "ConditionExpression": "#status = :waiting",
                "ExpressionAttributeNames": {"#status": "status"},
                "ExpressionAttributeValues": {
                    ":active": GameStatus.ACTIVE.value,
                    ":waiting": GameStatus.WAITING.value,
                    ":ts": timestamp,
                },
            }
        },
        {
            "Put": {
                "TableName": table.name,
                "Item": {
                    "PK": _game_pk(game_id),
                    "SK": _event_sk(0),
                    "seq": 0,
                    "timestamp": timestamp,
                    **encode_event(event),
                },
                "ConditionExpression": "attribute_not_exists(SK)",
            }
        },
        {
            "Put": {
                "TableName": table.name,
                "Item": {
                    "PK": _game_pk(game_id),
                    "SK": "STATE",
                    "kind": GAME_KIND,
                    "version": 1,
                    "state": encode_game_state(state),
                },
                "ConditionExpression": "attribute_not_exists(SK)",
            }
        },
        *(
            {
                "Update": {
                    "TableName": table.name,
                    "Key": {"PK": _player_pk(player_id), "SK": _game_sk(game_id)},
                    "UpdateExpression": "SET is_my_turn = :turn, #status = :active, version = :v",
                    "ExpressionAttributeNames": {"#status": "status"},
                    "ExpressionAttributeValues": {
                        ":turn": seat == state.to_act,
                        ":active": GameStatus.ACTIVE.value,
                        ":v": 1,
                    },
                }
            }
            for seat, player_id in players.items()
        ),
    ]

    try:
        transact_write(table, transact_items)
    except ClientError as exc:
        if is_transaction_cancelled(exc):
            raise GameAlreadyStarted(game_id) from exc
        raise
    return state


def get_state(table: Table, game_id: GameId) -> StoredGame:
    """Reads the materialized ``STATE`` item. Raises ``GameNotFound`` if none exists.

    ``kind`` is read with a default rather than required, so an item written before it existed
    still decodes - a missing one can only mean the single game there was.
    """
    response = table.get_item(Key={"PK": _game_pk(game_id), "SK": "STATE"})
    item = response.get("Item")
    if item is None:
        raise GameNotFound(game_id)
    normalized = from_dynamo(item)
    return StoredGame(
        state=decode_game_state(normalized["state"]),
        version=normalized["version"],
        kind=str(normalized.get("kind", GAME_KIND)),
    )


def find_request(table: Table, game_id: GameId, request_id: str) -> int | None:
    """The version a previous call with this ``request_id`` produced, or ``None`` if there was
    none (ROADMAP.md 1.4, 2.4).

    ``append`` already recognises a duplicate request and refuses to apply it twice, but by then
    the caller has re-read the state and re-run the move through the engine - and a retry that
    arrives after the turn has moved on is rejected as out-of-turn well before ``append`` gets a
    chance to notice the marker. So a handler that wants a retry to be a true no-op has to ask
    *first*, which is what this is for. ``append``'s own check still guards the narrower race
    where two identical requests are in flight at once.
    """
    item = table.get_item(Key={"PK": _game_pk(game_id), "SK": _request_sk(request_id)}).get("Item")
    if item is None:
        return None
    return int(from_dynamo(item)["new_version"])


def append(
    table: Table,
    game_id: GameId,
    events: Sequence[Event],
    new_state: GameState,
    expected_version: int,
    *,
    request_id: str | None = None,
    now: Callable[[], datetime] = _utcnow,
) -> int:
    """Writes ``events`` and the resulting ``new_state`` in one transaction, conditioned on
    ``expected_version`` still matching what's stored - the write optimistic concurrency depends
    on. Also updates ``META.last_activity_at`` (DESIGN.md §9) and every ``PLAYER#`` item's turn
    status and ``version`` - the same ``new_version`` written to ``STATE``, which is what lets the
    notification handler (ROADMAP.md 4.5) dedupe a redelivered stream record without a separate
    counter. On the move that ends the game, ``META`` also gains the final ``scores`` map, so that
    handler can render a game-over email without ever reading ``STATE``. Raises
    ``VersionConflict`` if the transaction is cancelled; returns the new version on success.

    ``request_id``, when given, makes this call idempotent (ROADMAP.md 1.4, DESIGN.md §6/§9): a
    ``REQUEST#<requestId>`` marker recording the resulting version is written in the same
    transaction, conditioned on its own absence. A duplicate call with the same ``request_id`` -
    even with a now-stale ``expected_version``, as a real client retry would send - finds that
    marker already present, and returns its recorded version as a no-op rather than raising
    ``VersionConflict`` or applying ``events`` twice."""
    timestamp = now().isoformat()
    new_version = expected_version + len(events)

    transact_items: list[dict[str, Any]] = [
        {
            "Put": {
                "TableName": table.name,
                "Item": {
                    "PK": _game_pk(game_id),
                    "SK": _event_sk(expected_version + i),
                    "seq": expected_version + i,
                    "timestamp": timestamp,
                    **encode_event(event),
                },
                "ConditionExpression": "attribute_not_exists(SK)",
            }
        }
        for i, event in enumerate(events)
    ]
    transact_items.append(
        {
            "Update": {
                "TableName": table.name,
                "Key": {"PK": _game_pk(game_id), "SK": "STATE"},
                "UpdateExpression": "SET #state = :state, #version = :new_version",
                "ConditionExpression": "#version = :expected_version",
                "ExpressionAttributeNames": {"#state": "state", "#version": "version"},
                "ExpressionAttributeValues": {
                    ":state": encode_game_state(new_state),
                    ":new_version": new_version,
                    ":expected_version": expected_version,
                },
            }
        }
    )
    # The move that ends the game retires it from every "my games" listing in the same
    # transaction that records it, so no listing can ever show a finished game as still waiting.
    game_over = new_state.phase is Phase.GAME_OVER
    status = (GameStatus.COMPLETE if game_over else GameStatus.ACTIVE).value
    meta_expression = "SET last_activity_at = :ts, #status = :status"
    meta_values: dict[str, Any] = {":ts": timestamp, ":status": status}
    if game_over:
        # Denormalized so the notification handler (ROADMAP.md 4.5) can render a game-over email
        # from META alone, the one item it's allowed to read - it never touches STATE.
        #
        # Written as an open ``{label: score}`` map keyed on the scoring side's own name, not as a
        # fixed pair of keys, so the handler that reads it needs no notion of partnerships - which
        # is what keeps ``t42.notifications`` free of any one game's scoring shape (DESIGN.md §11).
        # For 42 the labels are ``north_south``/``east_west``, exactly as before.
        meta_expression += ", scores = :scores"
        meta_values[":scores"] = {team.name.lower(): new_state.marks.get(team, 0) for team in Team}
    transact_items.append(
        {
            "Update": {
                "TableName": table.name,
                "Key": {"PK": _game_pk(game_id), "SK": "META"},
                "UpdateExpression": meta_expression,
                "ExpressionAttributeNames": {"#status": "status"},
                "ExpressionAttributeValues": meta_values,
            }
        }
    )
    transact_items.extend(
        {
            "Update": {
                "TableName": table.name,
                "Key": {"PK": _player_pk(player_id), "SK": _game_sk(game_id)},
                "UpdateExpression": "SET is_my_turn = :turn, #status = :status, version = :v",
                "ExpressionAttributeNames": {"#status": "status"},
                "ExpressionAttributeValues": {
                    ":turn": seat == new_state.to_act,
                    ":status": status,
                    ":v": new_version,
                },
            }
        }
        for seat, player_id in new_state.players.items()
    )
    if request_id is not None:
        transact_items.append(
            {
                "Put": {
                    "TableName": table.name,
                    "Item": {
                        "PK": _game_pk(game_id),
                        "SK": _request_sk(request_id),
                        "new_version": new_version,
                    },
                    "ConditionExpression": "attribute_not_exists(SK)",
                }
            }
        )

    try:
        transact_write(table, transact_items)
    except ClientError as exc:
        if is_transaction_cancelled(exc):
            if request_id is not None:
                existing = table.get_item(
                    Key={"PK": _game_pk(game_id), "SK": _request_sk(request_id)}
                ).get("Item")
                if existing is not None:
                    return cast(int, from_dynamo(existing)["new_version"])
            raise VersionConflict(game_id, expected_version) from exc
        raise
    return new_version
