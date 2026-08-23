"""Turning domain rejections into HTTP responses (ROADMAP.md 2.3, DESIGN.md §6).

The engine and the repository both signal rejection by raising, and neither knows what HTTP is.
This module is the single place those exceptions become status codes, so a handler never writes
``raise HTTPException(409, ...)`` inline and the mapping can be read in one sitting.

Every error response carries a machine-readable ``code`` alongside its human ``message``::

    {"error": {"code": "OUT_OF_TURN", "message": "west is not the player to move"}}

The ``code`` exists for the Phase 3 CLI: matching on a stable symbol beats matching on prose,
and it lets a client distinguish "your move was illegal" from "somebody moved first" without
parsing English.

**Why rules rejections are 400 and not 422.** FastAPI already returns 422 for its own request
validation, so reusing it would conflate "this JSON is the wrong shape" with "this is a
well-formed move that the rules forbid". Those need to be distinguishable: the first is a client
bug, the second is ordinary gameplay - trying to renege is a 400, not a malformed request.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Final

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from t42.engine.errors import IllegalMove, OutOfTurn, RulesError, UnknownContract
from t42.storage.errors import (
    AlreadySeated,
    ContactAlreadyExists,
    ContactNotFound,
    GameAlreadyExists,
    GameNotFound,
    GameNotJoinable,
    InvalidCredentials,
    InvalidResetToken,
    InvalidToken,
    InvalidVerificationToken,
    NotInvited,
    PlayerNotFound,
    RuleSetNotFound,
    SeatTaken,
    UsernameTaken,
    VersionConflict,
)


class ApiError(Exception):
    """A rejection raised by the API layer itself, for things no lower layer knows about - a
    caller who is not seated in the game they asked for, or a missing bearer token."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def not_authenticated(message: str = "a bearer token is required") -> ApiError:
    return ApiError(status.HTTP_401_UNAUTHORIZED, "NOT_AUTHENTICATED", message)


def not_a_player(game_id: str) -> ApiError:
    """403 rather than 404. Hiding a game's existence from a non-player would be security
    theatre: game codes are handed out precisely so people can join, and a 404 here would make a
    genuine typo indistinguishable from someone else's game."""
    return ApiError(
        status.HTTP_403_FORBIDDEN,
        "NOT_A_PLAYER",
        f"you are not seated in game {game_id!r}",
    )


def not_started(game_id: str) -> ApiError:
    return ApiError(
        status.HTTP_409_CONFLICT,
        "GAME_NOT_STARTED",
        f"game {game_id!r} is still waiting for players",
    )


def invalid_request(message: str) -> ApiError:
    """A well-formed body that the domain rejects on construction - notably
    ``HouseRules.__post_init__``'s structural checks (DESIGN.md §5.1 tier 1), which raise a plain
    ``ValueError``. Converted at the point of construction rather than by a blanket ``ValueError``
    handler, which would quietly turn any internal bug into a 400."""
    return ApiError(status.HTTP_400_BAD_REQUEST, "INVALID_REQUEST", message)


#: Grouped by status code for reading. Order does not affect dispatch: Starlette resolves a
#: handler by walking ``type(exc).__mro__``, so ``OutOfTurn`` finds its own entry before the
#: ``IllegalMove`` and ``RulesError`` entries it inherits from.
_MAPPING: Final[tuple[tuple[type[Exception], int, str], ...]] = (
    # 401 - who are you?
    (InvalidToken, status.HTTP_401_UNAUTHORIZED, "INVALID_TOKEN"),
    (InvalidCredentials, status.HTTP_401_UNAUTHORIZED, "INVALID_CREDENTIALS"),
    (InvalidVerificationToken, status.HTTP_401_UNAUTHORIZED, "INVALID_VERIFICATION_TOKEN"),
    (InvalidResetToken, status.HTTP_401_UNAUTHORIZED, "INVALID_RESET_TOKEN"),
    # 404 - no such thing
    (GameNotFound, status.HTTP_404_NOT_FOUND, "GAME_NOT_FOUND"),
    (RuleSetNotFound, status.HTTP_404_NOT_FOUND, "RULE_SET_NOT_FOUND"),
    (PlayerNotFound, status.HTTP_404_NOT_FOUND, "PLAYER_NOT_FOUND"),
    (ContactNotFound, status.HTTP_404_NOT_FOUND, "CONTACT_NOT_FOUND"),
    # 403 - well-formed, correctly authenticated, but not allowed
    (NotInvited, status.HTTP_403_FORBIDDEN, "NOT_INVITED"),
    # 409 - the world is not in the state this request assumed
    (OutOfTurn, status.HTTP_409_CONFLICT, "OUT_OF_TURN"),
    (VersionConflict, status.HTTP_409_CONFLICT, "VERSION_CONFLICT"),
    (SeatTaken, status.HTTP_409_CONFLICT, "SEAT_TAKEN"),
    (AlreadySeated, status.HTTP_409_CONFLICT, "ALREADY_SEATED"),
    (GameNotJoinable, status.HTTP_409_CONFLICT, "GAME_NOT_JOINABLE"),
    (GameAlreadyExists, status.HTTP_409_CONFLICT, "GAME_ALREADY_EXISTS"),
    (UsernameTaken, status.HTTP_409_CONFLICT, "USERNAME_TAKEN"),
    (ContactAlreadyExists, status.HTTP_409_CONFLICT, "CONTACT_ALREADY_EXISTS"),
    # 400 - well-formed, but the rules say no
    (UnknownContract, status.HTTP_400_BAD_REQUEST, "UNKNOWN_CONTRACT"),
    (IllegalMove, status.HTTP_400_BAD_REQUEST, "ILLEGAL_MOVE"),
    (RulesError, status.HTTP_400_BAD_REQUEST, "RULES_ERROR"),
)


def _response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code, content={"error": {"code": code, "message": message}}
    )


def install_error_handlers(app: FastAPI) -> None:
    """Wire every domain exception to its status code. Called once, from ``app.py``."""

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, ApiError)
        return _response(exc.status_code, exc.code, exc.message)

    def _handler(
        status_code: int, code: str
    ) -> Callable[[Request, Exception], Awaitable[JSONResponse]]:
        async def handle(_: Request, exc: Exception) -> JSONResponse:
            return _response(status_code, code, str(exc))

        return handle

    for exc_type, status_code, code in _MAPPING:
        app.add_exception_handler(exc_type, _handler(status_code, code))
