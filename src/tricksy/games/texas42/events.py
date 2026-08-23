"""Immutable events appended to the game log (DESIGN.md §4.1).

Event shapes are the persistence contract: ``storage`` serializes these to ``EVENT#<seq>`` items
and replays them to rebuild state, so changing a field here is a data migration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .dominoes import Domino
from .state import PlayerId, Seat
from .suits import Suit, Trump


@dataclass(frozen=True, slots=True)
class HandDealt:
    """Records the deal so a hand can be replayed exactly."""

    hands: tuple[tuple[Seat, tuple[Domino, ...]], ...]
    dealer: Seat
    type: Literal["HAND_DEALT"] = "HAND_DEALT"


@dataclass(frozen=True, slots=True)
class BidPlaced:
    actor: PlayerId
    contract: str | None = None
    points: int | None = None
    marks: int | None = None
    type: Literal["BID"] = "BID"


@dataclass(frozen=True, slots=True)
class Passed:
    actor: PlayerId
    type: Literal["PASS"] = "PASS"


@dataclass(frozen=True, slots=True)
class BidConfirmed:
    """Public accept/decline of a pending bid (plunge). Logged unconditionally, never suppressed
    on decline - that a plunge was proposed and how the partner answered is table information."""

    actor: PlayerId
    accept: bool
    type: Literal["BID_CONFIRMED"] = "BID_CONFIRMED"


@dataclass(frozen=True, slots=True)
class ContractDeclared:
    actor: PlayerId
    contract: str
    trump: Trump = None
    type: Literal["CONTRACT_DECLARED"] = "CONTRACT_DECLARED"


@dataclass(frozen=True, slots=True)
class DominoPlayed:
    """Must carry ``declared_suit`` (DESIGN.md §5.2): once a declaration is possible, the led
    suit is a decision, not a function of the tile, so a log without it cannot be replayed."""

    actor: PlayerId
    domino: Domino
    declared_suit: Suit | None = None
    type: Literal["PLAY_DOMINO"] = "PLAY_DOMINO"


type Event = HandDealt | BidPlaced | Passed | BidConfirmed | ContractDeclared | DominoPlayed
