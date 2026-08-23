"""DynamoDB event log + materialized state (DESIGN.md §4.1, Phases 1 and 2).

``codec`` translates engine dataclasses to and from the plain attribute maps the storage layer
persists. ``events`` translates an accepted move (and the deal it may trigger) into the event that
gets appended to the log. ``replay`` rebuilds a ``GameState`` from that log. ``repository`` writes
games to DynamoDB - ``start_game``, ``get_state`` and ``append`` - with optimistic concurrency via
``STATE.version`` and idempotency via ``REQUEST#`` markers.

Phase 2 adds the two things the API needs underneath it: ``lobby`` covers a game's life from
creation until the deal, and ``accounts`` covers players, passwords and bearer tokens.
"""

from __future__ import annotations

from .accounts import (
    ContactChannel,
    Device,
    Player,
    authenticate,
    create_player,
    get_player,
    hash_token,
    issue_token,
    list_tokens,
    player_for_token,
    revoke_token,
)
from .codec import (
    decode_event,
    decode_game_state,
    decode_house_rules,
    encode_event,
    encode_game_state,
    encode_house_rules,
)
from .errors import (
    AlreadySeated,
    GameAlreadyExists,
    GameAlreadyStarted,
    GameNotFound,
    GameNotJoinable,
    InvalidCredentials,
    InvalidToken,
    SeatTaken,
    StorageError,
    UsernameTaken,
    VersionConflict,
)
from .events import event_for_move, events_for_move, hand_dealt_event
from .lobby import (
    GameSummary,
    Lobby,
    SeatAssignment,
    create_pending_game,
    get_lobby,
    join_seat,
    list_games_for_player,
    new_game_code,
)
from .replay import replay
from .repository import GameStatus, StoredGame, append, get_state, start_game

__all__ = [
    "AlreadySeated",
    "ContactChannel",
    "Device",
    "GameAlreadyExists",
    "GameAlreadyStarted",
    "GameNotFound",
    "GameNotJoinable",
    "GameStatus",
    "GameSummary",
    "InvalidCredentials",
    "InvalidToken",
    "Lobby",
    "Player",
    "SeatAssignment",
    "SeatTaken",
    "StorageError",
    "StoredGame",
    "UsernameTaken",
    "VersionConflict",
    "append",
    "authenticate",
    "create_pending_game",
    "create_player",
    "decode_event",
    "decode_game_state",
    "decode_house_rules",
    "encode_event",
    "encode_game_state",
    "encode_house_rules",
    "event_for_move",
    "events_for_move",
    "get_lobby",
    "get_player",
    "get_state",
    "hand_dealt_event",
    "hash_token",
    "issue_token",
    "join_seat",
    "list_games_for_player",
    "list_tokens",
    "new_game_code",
    "player_for_token",
    "replay",
    "revoke_token",
    "start_game",
]
