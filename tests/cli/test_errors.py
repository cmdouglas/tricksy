from __future__ import annotations

import pytest

from tricksy.cli.errors import exit_code_for

# DESIGN.md §7.2's table, transcribed here so a change to either drifts visibly.
_CASES: tuple[tuple[str, int], ...] = (
    ("NOT_AUTHENTICATED", 3),
    ("INVALID_TOKEN", 3),
    ("INVALID_CREDENTIALS", 3),
    ("ILLEGAL_MOVE", 4),
    ("RULES_ERROR", 4),
    ("UNKNOWN_CONTRACT", 4),
    ("INVALID_REQUEST", 4),
    ("OUT_OF_TURN", 5),
    ("VERSION_CONFLICT", 5),
    ("SEAT_TAKEN", 5),
    ("ALREADY_SEATED", 5),
    ("GAME_NOT_JOINABLE", 5),
    ("GAME_NOT_STARTED", 5),
    ("GAME_ALREADY_EXISTS", 5),
    ("USERNAME_TAKEN", 5),
    ("GAME_NOT_FOUND", 6),
    ("RULE_SET_NOT_FOUND", 6),
    ("PLAYER_NOT_FOUND", 6),
    ("NOT_A_PLAYER", 7),
    ("NOT_INVITED", 7),
)


@pytest.mark.parametrize(("code", "expected_exit"), _CASES)
def test_documented_code_maps_to_its_exit_status(code: str, expected_exit: int) -> None:
    assert exit_code_for(code) == expected_exit


def test_unrecognised_code_exits_one() -> None:
    assert exit_code_for("SOMETHING_NEW") == 1


def test_unknown_fallback_code_exits_one() -> None:
    assert exit_code_for("UNKNOWN") == 1
