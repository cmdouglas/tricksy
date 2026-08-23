"""Error code to exit status (ROADMAP.md 3.2, DESIGN.md §7.2).

One mapping, transcribed from DESIGN.md §7.2's table. An unrecognised code - including the
``ApiClient``'s own ``"UNKNOWN"`` fallback for a response that did not carry the
``{"error": {"code", "message"}}`` envelope - exits 1, so the server can add a code, or a caller can
hit an endpoint that doesn't use the envelope, without the client crashing.
"""

from __future__ import annotations

from typing import Final

_EXIT_CODES: Final[dict[str, int]] = {
    # 3 - who are you
    "NOT_AUTHENTICATED": 3,
    "INVALID_TOKEN": 3,
    "INVALID_CREDENTIALS": 3,
    # 4 - the rules say no
    "ILLEGAL_MOVE": 4,
    "RULES_ERROR": 4,
    "UNKNOWN_CONTRACT": 4,
    "INVALID_REQUEST": 4,
    # 5 - the world moved
    "OUT_OF_TURN": 5,
    "VERSION_CONFLICT": 5,
    "SEAT_TAKEN": 5,
    "ALREADY_SEATED": 5,
    "GAME_NOT_JOINABLE": 5,
    "GAME_NOT_STARTED": 5,
    "GAME_ALREADY_EXISTS": 5,
    "USERNAME_TAKEN": 5,
    # 6 - no such thing
    "GAME_NOT_FOUND": 6,
    "RULE_SET_NOT_FOUND": 6,
    "PLAYER_NOT_FOUND": 6,
    # 7 - not allowed
    "NOT_A_PLAYER": 7,
    "NOT_INVITED": 7,
}


def exit_code_for(code: str) -> int:
    return _EXIT_CODES.get(code, 1)
