"""Pure Texas 42 rules engine (DESIGN.md §5).

This package must stay free of I/O, AWS SDK calls and any dependency on ``t42.storage``,
``t42.api`` or ``t42.cli``: it takes a state plus a proposed move and returns a new state or
raises. That is what keeps the rules testable in isolation and reusable behind any client.
"""

from __future__ import annotations

from typing import Final

from .dominoes import FULL_SET, Domino, parse
from .errors import IllegalMove, OutOfTurn, RulesError, UnknownContract
from .game import apply_move, legal_moves, new_game
from .house_rules import DEFAULT_CONTRACTS, DEFAULT_MARKS_TO_WIN, HouseRules
from .projection import project
from .scoring import COUNT_VALUES, HAND_COUNT_TOTAL, MAX_HAND_POINTS, count_value
from .state import GameState, HandState, Phase, Seat, Team, Trick
from .suits import Suit, Trump, follows, is_trump, led_suit, rank_in_suit

#: What this engine is, as a value the storage and API layers can write down and hand back.
#:
#: Nothing here dispatches on it - there is exactly one engine, and this package is it. The point
#: is that every game persisted by ``t42.storage`` and every game named on the wire says which
#: rules produced it, so a second game would be a new value rather than a backfill over an
#: immutable event log (invariant 6). See DESIGN.md §11.
GAME_KIND: Final[str] = "texas42"

__all__ = [
    "COUNT_VALUES",
    "DEFAULT_CONTRACTS",
    "DEFAULT_MARKS_TO_WIN",
    "FULL_SET",
    "GAME_KIND",
    "HAND_COUNT_TOTAL",
    "MAX_HAND_POINTS",
    "Domino",
    "GameState",
    "HandState",
    "HouseRules",
    "IllegalMove",
    "OutOfTurn",
    "Phase",
    "RulesError",
    "Seat",
    "Suit",
    "Team",
    "Trick",
    "Trump",
    "UnknownContract",
    "apply_move",
    "count_value",
    "follows",
    "is_trump",
    "led_suit",
    "legal_moves",
    "new_game",
    "parse",
    "project",
    "rank_in_suit",
]
