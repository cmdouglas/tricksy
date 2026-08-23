"""Count dominoes and hand point totals (DESIGN.md §5, "Scoring").

Bid comparison, mark accounting and per-contract scoring live in the contract strategies; this
module holds only the fixed arithmetic of the deck.
"""

from __future__ import annotations

from typing import Final

from .dominoes import Domino

COUNT_VALUES: Final[dict[Domino, int]] = {
    Domino(5, 5): 10,
    Domino(6, 4): 10,
    Domino(5, 0): 5,
    Domino(4, 1): 5,
    Domino(3, 2): 5,
}

#: Points carried by the count dominoes in one hand.
HAND_COUNT_TOTAL: Final = 35

#: Each trick taken is worth one point on top of the count it contains.
POINT_PER_TRICK: Final = 1

TRICKS_PER_HAND: Final = 7

#: 35 count + 7 tricks.
MAX_HAND_POINTS: Final = HAND_COUNT_TOTAL + TRICKS_PER_HAND * POINT_PER_TRICK

MINIMUM_BID: Final = 30


def count_value(domino: Domino) -> int:
    """Count points carried by ``domino``; 0 for the 23 non-count tiles."""
    return COUNT_VALUES.get(domino, 0)


def count_of(dominoes: tuple[Domino, ...]) -> int:
    """Count points carried by a collection of tiles, e.g. the tiles in a won trick."""
    return sum(count_value(domino) for domino in dominoes)


def is_count(domino: Domino) -> bool:
    return domino in COUNT_VALUES
