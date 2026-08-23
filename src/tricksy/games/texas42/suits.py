"""Suit membership and ranking (DESIGN.md §5, "Domino & suit logic").

A tile's suit is not a property of the tile alone: it depends on the trump for the hand and on the
per-game ``doubles_are_own_suit`` variant, so every function here takes both.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Final, Literal

from .dominoes import Domino
from .house_rules import HouseRules


class Suit(IntEnum):
    """The seven number suits, plus doubles as a suit of its own under that variant."""

    BLANKS = 0
    ACES = 1
    DEUCES = 2
    TREYS = 3
    FOURS = 4
    FIVES = 5
    SIXES = 6
    DOUBLES = 7


NUMBER_SUITS: Final[tuple[Suit, ...]] = tuple(Suit(pips) for pips in range(7))

#: Trump for the hand: a suit, or ``None`` for no-trump contracts such as nello and sevens.
type Trump = Suit | None

#: Rank of a double within its own number suit - above every non-double in that suit.
_DOUBLE_RANK: Final = 7

#: Whether a double ranks above or below the rest of its number suit. Only nello_low uses "low" -
#: every trump-based contract and the default nello use the "high" default.
type DoublesRank = Literal["high", "low"]


def is_trump(domino: Domino, trump: Trump, config: HouseRules) -> bool:
    """Whether ``domino`` is a trump tile.

    Under ``doubles_are_own_suit`` a double belongs to the doubles suit only, so ``5-5`` is not
    trump when fives are trump - it is trump only when doubles themselves are trump.
    """
    if trump is None:
        return False
    if trump is Suit.DOUBLES:
        if not config.doubles_are_own_suit:
            raise ValueError("doubles cannot be trump unless doubles_are_own_suit is enabled")
        return domino.is_double
    if config.doubles_are_own_suit and domino.is_double:
        return False
    return trump in domino.ends


def led_suit(domino: Domino, trump: Trump, config: HouseRules) -> Suit:
    """The suit ``domino`` establishes when it is led.

    A non-trump tile belongs to both of its ends; led, it calls for its higher end.
    """
    if is_trump(domino, trump, config):
        assert trump is not None  # is_trump is False for no-trump
        return trump
    if config.doubles_are_own_suit and domino.is_double:
        return Suit.DOUBLES
    return Suit(domino.high)


def declarable_suits(domino: Domino, trump: Trump, config: HouseRules) -> tuple[Suit, ...]:
    """The non-default suit(s) ``domino`` may be declared as when leading it (DESIGN.md §5.2).

    Empty for a double or a trump tile - both offer no choice. Otherwise a single suit: the low
    end. The default (no declaration) already reads as the high end via :func:`led_suit`, so this
    is the one *additional* option a leader has, not a third alternative alongside it.
    """
    if domino.is_double or is_trump(domino, trump, config):
        return ()
    return (Suit(domino.low),)


def belongs_to(domino: Domino, suit: Suit, config: HouseRules) -> bool:
    """Whether ``domino`` is a member of ``suit``, ignoring trump."""
    if suit is Suit.DOUBLES:
        return config.doubles_are_own_suit and domino.is_double
    if config.doubles_are_own_suit and domino.is_double:
        return False
    return suit in domino.ends


def follows(domino: Domino, suit: Suit, trump: Trump, config: HouseRules) -> bool:
    """Whether playing ``domino`` follows a lead of ``suit``.

    Trump tiles follow trump and nothing else, even though they carry a second end.
    """
    if is_trump(domino, trump, config):
        return suit == trump
    return belongs_to(domino, suit, config)


def rank_in_suit(
    domino: Domino, suit: Suit, config: HouseRules, *, doubles_rank: DoublesRank = "high"
) -> int:
    """Rank of ``domino`` within ``suit``; higher beats lower. Only meaningful within one suit.

    Within a number suit the double ranks per ``doubles_rank`` (highest, by default) and the rest
    rank by their off end. Within the doubles suit, ``6-6`` is highest down to ``0-0`` regardless
    - ``doubles_rank`` only matters when doubles share a suit with non-doubles.
    """
    if not belongs_to(domino, suit, config):
        raise ValueError(f"{domino} is not in suit {suit.name}")
    if suit is Suit.DOUBLES:
        return domino.high
    if domino.is_double:
        return _DOUBLE_RANK if doubles_rank == "high" else -1
    return domino.low if domino.high == suit else domino.high
