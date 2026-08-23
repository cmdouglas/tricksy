"""The waiting room: games from creation until the deal (ROADMAP.md 2.2, DESIGN.md §4.1).

DESIGN.md §6 creates a game and then has players join seats one at a time, but the rules engine
has no notion of a partially seated game - ``new_game`` takes four seats and deals immediately.
Rather than teach it one, which would put lobby bookkeeping inside the pure engine and make
``GameState.players`` optional for every module downstream, the whole lifecycle lives here:

``WAITING``
    ``META`` exists with a partial ``seats`` map and the house rules. No ``STATE`` item yet, so
    :func:`tricksy.storage.repository.get_state` raises ``GameNotFound`` for it - correctly, since
    there is no game state to read until cards are on the table.
``ACTIVE``
    The join that filled the fourth seat called :func:`tricksy.storage.repository.start_game`, which
    dealt the first hand and flipped the status in one conditional transaction.
``COMPLETE``
    ``append`` set it when the final move produced ``Phase.GAME_OVER``.

**Why the seat claim is a conditional update rather than a read-then-write.** Four people invited
to the same game will click join at the same time. Each claim is a single ``UpdateItem``
conditioned on that seat being absent from the map, so two players cannot land on one seat no
matter how the requests interleave. The lobby read that precedes it exists only to produce a
useful error message, never to decide whether the write is safe.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from random import Random
from typing import Any, Final

from botocore.exceptions import ClientError
from mypy_boto3_dynamodb.service_resource import Table

from tricksy.games.texas42 import GAME_KIND
from tricksy.games.texas42.contracts import validate_house_rules
from tricksy.games.texas42.house_rules import HouseRules
from tricksy.games.texas42.state import GameId, GameState, PlayerId, Seat

from ._dynamo import as_text, from_dynamo
from .codec import decode_house_rules, encode_house_rules
from .errors import (
    AlreadySeated,
    GameAlreadyExists,
    GameAlreadyStarted,
    GameNotFound,
    GameNotJoinable,
    NotInvited,
    SeatTaken,
)
from .invites import find_invite, revoke_invite
from .repository import GameStatus, start_game

#: Game ids double as join codes (DESIGN.md §4.1), so they get read aloud and typed from a phone
#: screen. ``I``/``L``/``O``/``U``/``0``/``1`` are omitted: the first four are confusable in most
#: fonts, and dropping ``U`` keeps the generator from spelling anything unfortunate.
_CODE_ALPHABET: Final = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
_CODE_LENGTH: Final = 6


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _game_pk(game_id: GameId) -> str:
    return f"GAME#{game_id}"


def _player_pk(player_id: PlayerId) -> str:
    return f"PLAYER#{player_id}"


def _game_sk(game_id: GameId) -> str:
    return f"GAME#{game_id}"


def _open_gsi_pk(kind: str) -> str:
    """The ``OpenGames`` partition for one game kind.

    Namespaced rather than a bare ``"OPEN"`` so browsing open tables is per-kind: a GSI key format
    is settled by the data already written under it, so this is cheap now and a migration later.
    """
    return f"OPEN#{kind}"


def new_game_code() -> GameId:
    """A fresh join code. ~730 million possibilities, and a collision is caught by
    :func:`create_pending_game`'s conditional put rather than by checking first."""
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))


class Visibility(StrEnum):
    """Who may take a seat at a table (DESIGN.md §6.2). Lives here rather than next to
    ``GameStatus`` in ``repository.py`` because it is purely a lobby-lifecycle concept - nothing
    about it survives into ``GameState`` or the engine."""

    PUBLIC = "public"
    INVITE_ONLY = "invite_only"


@dataclass(frozen=True, slots=True)
class SeatAssignment:
    """Who is in a seat. Carries the username alongside the id so rendering a lobby or a game
    view needs no fan-out of profile reads - the cost is that a later rename would not propagate
    into games already in progress."""

    player_id: PlayerId
    username: str


@dataclass(frozen=True, slots=True)
class Lobby:
    """The ``META`` item, decoded. Everything about a game that is true before it is dealt, and
    stays true after.

    Seats are plain ``int`` here, not :class:`~tricksy.games.texas42.state.Seat`, and how many there
    are is read from the item rather than from ``len(Seat)``. Nothing this module does with a seat
    needs to know what the engine calls it - claiming one is a conditional update on a map key, and
    "is this table full" is a count. The one place the engine's own seat type is required is
    :func:`_deal`, which is the call into the engine. See DESIGN.md §11.
    """

    game_id: GameId
    kind: str
    status: GameStatus
    visibility: Visibility
    config: HouseRules
    seats: Mapping[int, SeatAssignment]
    seat_count: int
    created_at: str
    last_activity_at: str

    @property
    def is_full(self) -> bool:
        return len(self.seats) == self.seat_count

    @property
    def open_seats(self) -> tuple[int, ...]:
        return tuple(seat for seat in range(self.seat_count) if seat not in self.seats)

    def seat_of(self, player_id: PlayerId) -> int | None:
        for seat, assignment in self.seats.items():
            if assignment.player_id == player_id:
                return seat
        return None


@dataclass(frozen=True, slots=True)
class GameSummary:
    """One row of "my games" (DESIGN.md §6). Read straight off the ``PLAYER#`` item, so listing
    a player's games is a single query with no per-game reads."""

    game_id: GameId
    kind: str
    status: GameStatus
    seat: int
    is_my_turn: bool


def _encode_seats(seats: Mapping[int, SeatAssignment]) -> dict[str, Any]:
    return {
        str(seat): {"player_id": a.player_id, "username": a.username} for seat, a in seats.items()
    }


def _decode_seats(data: Mapping[str, Any]) -> dict[int, SeatAssignment]:
    return {
        int(key): SeatAssignment(player_id=value["player_id"], username=value["username"])
        for key, value in data.items()
    }


def create_pending_game(
    table: Table,
    game_id: GameId,
    creator: PlayerId,
    username: str,
    seat: int,
    config: HouseRules,
    *,
    visibility: Visibility = Visibility.PUBLIC,
    now: Callable[[], datetime] = _utcnow,
) -> Lobby:
    """Opens a lobby with its creator in ``seat`` and nobody else.

    Validates ``config`` here rather than leaving it to ``new_game``: the deal is three joins
    away, and an unusable rule set that only surfaced then would strand a lobby that three people
    had already joined.

    ``visibility`` defaults to :attr:`Visibility.PUBLIC`, which is exactly today's behaviour -
    anyone with the code may join (DESIGN.md §6.2).

    Raises :class:`~tricksy.storage.errors.GameAlreadyExists` if the code is in use.
    """
    validate_house_rules(config)
    timestamp = now().isoformat()
    seats = {seat: SeatAssignment(player_id=creator, username=username)}

    item: dict[str, Any] = {
        "PK": _game_pk(game_id),
        "SK": "META",
        "kind": GAME_KIND,
        "status": GameStatus.WAITING.value,
        "visibility": visibility.value,
        "seats": _encode_seats(seats),
        "seat_count": len(Seat),
        "config": encode_house_rules(config),
        "created_at": timestamp,
        "last_activity_at": timestamp,
    }
    if visibility is Visibility.PUBLIC:
        # The ``OpenGames`` GSI (DESIGN.md §4.1): sparse, so only a public game ever carries
        # these, and ``start_game`` removes them the moment the game leaves ``WAITING``.
        item["GSI1PK"] = _open_gsi_pk(GAME_KIND)
        item["GSI1SK"] = timestamp

    try:
        table.put_item(Item=item, ConditionExpression="attribute_not_exists(PK)")
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise GameAlreadyExists(game_id) from exc
        raise

    _put_player_game_item(table, game_id, creator, seat, GameStatus.WAITING)
    return Lobby(
        game_id=game_id,
        kind=GAME_KIND,
        status=GameStatus.WAITING,
        visibility=visibility,
        config=config,
        seats=seats,
        seat_count=len(Seat),
        created_at=timestamp,
        last_activity_at=timestamp,
    )


def _put_player_game_item(
    table: Table, game_id: GameId, player_id: PlayerId, seat: int, status: GameStatus
) -> None:
    table.put_item(
        Item={
            "PK": _player_pk(player_id),
            "SK": _game_sk(game_id),
            "game_id": game_id,
            "kind": GAME_KIND,
            "seat": seat,
            "status": status.value,
            "is_my_turn": False,
        }
    )


def get_lobby(table: Table, game_id: GameId) -> Lobby:
    """The ``META`` item for ``game_id``. Raises ``GameNotFound`` if there is none."""
    item = table.get_item(Key={"PK": _game_pk(game_id), "SK": "META"}).get("Item")
    if item is None:
        raise GameNotFound(game_id)
    return _lobby_from_item(item)


def _lobby_from_item(item: Mapping[str, Any]) -> Lobby:
    """Decodes a ``META`` item into a ``Lobby``. Takes the raw item rather than a ``game_id``
    argument so it works equally for a direct ``get_item`` and for a hit off the ``OpenGames``
    GSI, which carries every attribute (``Projection: ALL``) but no key the caller already holds -
    the game id has to come from the item's own ``PK``.

    ``kind`` and ``seat_count`` are read with a default rather than required, so an item written
    before either existed still decodes. A missing ``kind`` can only mean the one game there was.
    """
    normalized = from_dynamo(item)
    return Lobby(
        game_id=as_text(normalized["PK"]).removeprefix("GAME#"),
        kind=as_text(normalized.get("kind", GAME_KIND)),
        status=GameStatus(normalized["status"]),
        visibility=Visibility(normalized["visibility"]),
        config=decode_house_rules(normalized["config"]),
        seats=_decode_seats(normalized["seats"]),
        seat_count=int(from_dynamo(normalized.get("seat_count", len(Seat)))),
        created_at=as_text(normalized["created_at"]),
        last_activity_at=as_text(normalized["last_activity_at"]),
    )


def list_open_games(table: Table, *, kind: str = GAME_KIND, limit: int = 50) -> tuple[Lobby, ...]:
    """Public tables of one ``kind`` still ``WAITING``, newest first (DESIGN.md §4.1,
    ROADMAP.md 2.7.3).

    One query on the sparse ``OpenGames`` GSI - an invite-only game, or one that's left
    ``WAITING``, was never indexed in the first place, so there is nothing to filter here.
    Newest-first falls out of ``GSI1SK`` being an ISO-8601 timestamp, descending.
    """
    response = table.query(
        IndexName="OpenGames",
        KeyConditionExpression="GSI1PK = :open",
        ExpressionAttributeValues={":open": _open_gsi_pk(kind)},
        ScanIndexForward=False,
        Limit=limit,
    )
    return tuple(_lobby_from_item(item) for item in response.get("Items", ()))


def join_seat(
    table: Table,
    game_id: GameId,
    player_id: PlayerId,
    username: str,
    seat: int,
    *,
    rng: Random | None = None,
    now: Callable[[], datetime] = _utcnow,
) -> Lobby:
    """Claims ``seat`` for ``player_id``, dealing the first hand if that fills the game.

    Idempotent in the way a retried request needs: re-joining a seat you already hold returns the
    current lobby rather than raising, whether or not the game has since been dealt.

    Raises :class:`~tricksy.storage.errors.SeatTaken` if somebody else got there first,
    :class:`~tricksy.storage.errors.AlreadySeated` if you already hold a different seat,
    :class:`~tricksy.storage.errors.GameNotJoinable` once the game is past its lobby, and
    :class:`~tricksy.storage.errors.NotInvited` if the table is ``invite_only`` and ``player_id``
    holds no invite to it (DESIGN.md §6.2).

    **The invite check is a read-then-write, deliberately** - the same trade-off the rest of this
    function already makes. Its one window is an invite revoked in the seconds between the read
    above and the claim below, which lets one join through that should have been refused. Closing
    it would mean promoting the claim to a transaction with a ``ConditionCheck`` on the invite
    item, which costs the precise ``ConditionalCheckFailedException`` attribution the claim
    depends on to tell the caller which way they lost - seat taken, or game already dealt. Against
    the threat model in DESIGN.md §6.1 that is a bad trade: the failure is one unwanted player at a
    casual game, and the remedy is to not invite them again.
    """
    lobby = get_lobby(table, game_id)
    held = lobby.seat_of(player_id)
    if held == seat:
        return lobby
    if held is not None:
        raise AlreadySeated(game_id, held)
    if lobby.status is not GameStatus.WAITING:
        raise GameNotJoinable(game_id, lobby.status.value)
    if seat in lobby.seats:
        raise SeatTaken(game_id, seat)
    if lobby.visibility is Visibility.INVITE_ONLY and not find_invite(table, game_id, player_id):
        raise NotInvited(game_id)

    try:
        table.update_item(
            Key={"PK": _game_pk(game_id), "SK": "META"},
            UpdateExpression="SET seats.#seat = :assignment, last_activity_at = :ts",
            ConditionExpression=("#status = :waiting AND attribute_not_exists(seats.#seat)"),
            ExpressionAttributeNames={"#seat": str(seat), "#status": "status"},
            ExpressionAttributeValues={
                ":assignment": {"player_id": player_id, "username": username},
                ":waiting": GameStatus.WAITING.value,
                ":ts": now().isoformat(),
            },
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        # Lost a race since the read above. Re-read to say which way we lost.
        current = get_lobby(table, game_id)
        if current.status is not GameStatus.WAITING:
            raise GameNotJoinable(game_id, current.status.value) from exc
        raise SeatTaken(game_id, seat) from exc

    _put_player_game_item(table, game_id, player_id, seat, GameStatus.WAITING)
    # A no-op for a public game, which never had one. Unconditional rather than gated on
    # ``visibility`` so a stray invite to a public table (nothing forbids one) is cleaned up too.
    revoke_invite(table, game_id, player_id)

    lobby = get_lobby(table, game_id)
    if lobby.is_full:
        _deal(table, lobby, rng=rng or Random(), now=now)
        lobby = get_lobby(table, game_id)
    return lobby


def _deal(
    table: Table, lobby: Lobby, *, rng: Random, now: Callable[[], datetime]
) -> GameState | None:
    """Deals a full lobby. Returns ``None`` if somebody else dealt it first, which is a benign
    outcome rather than an error: the caller wanted a dealt game and there is one.

    The one place this module converts its plain ``int`` seats back into the engine's own
    :class:`~tricksy.games.texas42.state.Seat`, because this is the call into the engine.
    """
    players = {Seat(seat): assignment.player_id for seat, assignment in lobby.seats.items()}
    try:
        return start_game(table, lobby.game_id, players, lobby.config, rng, now=now)
    except GameAlreadyStarted:
        return None


def list_games_for_player(table: Table, player_id: PlayerId) -> tuple[GameSummary, ...]:
    """Every game ``player_id`` is in, with whose-turn flags (DESIGN.md §6).

    One query against the ``PLAYER#`` partition, reading fields denormalized onto those items by
    ``join_seat`` and ``append`` - no per-game reads, so the cost does not grow with how many
    games somebody has going.
    """
    response = table.query(
        KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
        ExpressionAttributeValues={":pk": _player_pk(player_id), ":prefix": "GAME#"},
    )
    summaries = [
        GameSummary(
            game_id=as_text(item["game_id"]),
            kind=as_text(item.get("kind", GAME_KIND)),
            status=GameStatus(as_text(item["status"])),
            seat=int(from_dynamo(item["seat"])),
            is_my_turn=bool(item["is_my_turn"]),
        )
        for item in response.get("Items", ())
    ]
    return tuple(sorted(summaries, key=lambda s: s.game_id))
