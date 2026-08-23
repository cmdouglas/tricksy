"""Invites: permission to join a specific invite-only table (ROADMAP.md 2.7.2, DESIGN.md §6.2).

An invite is a **permission grant, not a seat reservation** - it records that a player may take
any open seat at a game, not which one. Any seated player may invite; there is no host role and
no field recording who sent it.

Two items, in the same single table as everything else (DESIGN.md §4.1), written and deleted
together in one transaction so a half-invite - visible from one side but not the other - is never
reachable (the same shape as the ``USERNAME#``/``PLAYER#`` pair in ``accounts.create_player``):

``GAME#<gameId> / INVITE#<playerId>``
    Read as a single ``GetItem`` by ``lobby.join_seat``'s gate.
``PLAYER#<playerId> / INVITE#<gameId>``
    The same fact from the invitee's side, so "my pending invites" is one ``Query``.

**This module knows nothing about lobbies.** Whether a game is still ``WAITING`` and whether the
invitee already holds a seat are both questions that need a ``Lobby``, and ``t42.storage.lobby``
already needs *this* module (to gate ``join_seat`` and to revoke a consumed invite) - so importing
``Lobby`` here would be circular. ``t42.api.app``'s invite handler, which fetches the ``Lobby`` to
check the caller is seated anyway, does that validation and calls straight through to
``invite_player`` once it has, the same "dumb storage, smart handler" split ROADMAP.md 2.7.2 already
prescribes for enriching "my pending invites".
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from mypy_boto3_dynamodb.service_resource import Table

from t42.engine.state import GameId, PlayerId

from ._dynamo import as_text, from_dynamo, transact_write


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _game_pk(game_id: GameId) -> str:
    return f"GAME#{game_id}"


def _player_pk(player_id: PlayerId) -> str:
    return f"PLAYER#{player_id}"


def _game_invite_sk(player_id: PlayerId) -> str:
    return f"INVITE#{player_id}"


def _player_invite_sk(game_id: GameId) -> str:
    return f"INVITE#{game_id}"


@dataclass(frozen=True, slots=True)
class PendingInvite:
    """One row of "my pending invites" - just enough to look the game up. The API handler
    enriches this with a ``get_lobby`` read rather than this module duplicating lobby fields."""

    game_id: GameId
    created_at: str


@dataclass(frozen=True, slots=True)
class InvitedPlayer:
    """One row of "who is invited to this table" - unlike :class:`PendingInvite`, this is already
    a complete row: the ``GAME#/INVITE#`` item carries ``username`` alongside ``player_id``, so no
    handler-side enrichment is needed to render it."""

    player_id: PlayerId
    username: str
    created_at: str


def invite_player(
    table: Table,
    game_id: GameId,
    invitee_id: PlayerId,
    invitee_username: str,
    *,
    inviter_username: str,
    now: Callable[[], datetime] = _utcnow,
) -> None:
    """Grants ``invitee_id`` permission to join ``game_id``.

    Idempotent: re-inviting the same player overwrites silently rather than raising, since a
    client that didn't see its own response will retry (ROADMAP.md 2.7.2). Does no validation of
    its own - see the module docstring for why that lives in the handler instead.

    ``inviter_username`` is denormalized onto the invitee's own item as ``invited_by``, the same
    trade-off ``lobby.SeatAssignment`` already makes for seat usernames: the notification handler
    (ROADMAP.md 4.5) needs a display string, not an id, and keyword-only so it can't be silently
    transposed with the adjacent ``invitee_username``.
    """
    timestamp = now().isoformat()
    transact_write(
        table,
        [
            {
                "Put": {
                    "TableName": table.name,
                    "Item": {
                        "PK": _game_pk(game_id),
                        "SK": _game_invite_sk(invitee_id),
                        "player_id": invitee_id,
                        "username": invitee_username,
                        "created_at": timestamp,
                    },
                }
            },
            {
                "Put": {
                    "TableName": table.name,
                    "Item": {
                        "PK": _player_pk(invitee_id),
                        "SK": _player_invite_sk(game_id),
                        "game_id": game_id,
                        "invited_by": inviter_username,
                        "created_at": timestamp,
                    },
                }
            },
        ],
    )


def find_invite(table: Table, game_id: GameId, player_id: PlayerId) -> bool:
    """Whether ``player_id`` currently holds an invite to ``game_id`` - the gate
    ``lobby.join_seat`` checks on an ``invite_only`` table."""
    item = table.get_item(Key={"PK": _game_pk(game_id), "SK": _game_invite_sk(player_id)}).get(
        "Item"
    )
    return item is not None


def list_invites_for_player(table: Table, player_id: PlayerId) -> tuple[PendingInvite, ...]:
    """Every game ``player_id`` has been invited to and hasn't joined or declined, newest first."""
    response = table.query(
        KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
        ExpressionAttributeValues={":pk": _player_pk(player_id), ":prefix": "INVITE#"},
    )
    invites = [_pending_invite_from_item(item) for item in response.get("Items", ())]
    return tuple(sorted(invites, key=lambda i: i.created_at, reverse=True))


def _pending_invite_from_item(item: dict[str, Any]) -> PendingInvite:
    normalized = from_dynamo(item)
    return PendingInvite(
        game_id=as_text(normalized["game_id"]), created_at=as_text(normalized["created_at"])
    )


def list_invites_for_game(table: Table, game_id: GameId) -> tuple[InvitedPlayer, ...]:
    """Everyone currently invited to ``game_id``, newest first - the host-side counterpart to
    ``list_invites_for_player`` (DESIGN.md §6.2)."""
    response = table.query(
        KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
        ExpressionAttributeValues={":pk": _game_pk(game_id), ":prefix": "INVITE#"},
    )
    invites = [_invited_player_from_item(item) for item in response.get("Items", ())]
    return tuple(sorted(invites, key=lambda i: i.created_at, reverse=True))


def _invited_player_from_item(item: dict[str, Any]) -> InvitedPlayer:
    normalized = from_dynamo(item)
    return InvitedPlayer(
        player_id=as_text(normalized["player_id"]),
        username=as_text(normalized["username"]),
        created_at=as_text(normalized["created_at"]),
    )


def revoke_invite(table: Table, game_id: GameId, player_id: PlayerId) -> None:
    """Deletes both sides of the invite. Idempotent, like ``accounts.revoke_token``: used both for
    a seated player revoking it and the invitee declining it, and calling it on an invite that
    doesn't exist - e.g. every join to a public game - is a no-op rather than an error."""
    transact_write(
        table,
        [
            {
                "Delete": {
                    "TableName": table.name,
                    "Key": {"PK": _game_pk(game_id), "SK": _game_invite_sk(player_id)},
                }
            },
            {
                "Delete": {
                    "TableName": table.name,
                    "Key": {"PK": _player_pk(player_id), "SK": _player_invite_sk(game_id)},
                }
            },
        ],
    )
