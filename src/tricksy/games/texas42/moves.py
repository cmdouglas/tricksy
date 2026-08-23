"""The engine's input alphabet: what a player can propose.

A move is what a client submits (DESIGN.md §6); an :mod:`t42.engine.events` event is what the
engine appends once a move is accepted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .dominoes import Domino
from .state import PlayerId
from .suits import Suit, Trump


@dataclass(frozen=True, slots=True)
class PlaceBid:
    """A numeric bid (30-42) or a mark bid for ``contract``."""

    actor: PlayerId
    contract: str | None = None
    points: int | None = None
    marks: int | None = None
    kind: Literal["BID"] = "BID"


@dataclass(frozen=True, slots=True)
class Pass:
    actor: PlayerId
    kind: Literal["PASS"] = "PASS"


@dataclass(frozen=True, slots=True)
class ConfirmBid:
    """The partner's public accept/decline of a pending mark bid that needs confirmation (plunge).

    Generic rather than plunge-specific, so any future contract needing this step reuses it.
    """

    actor: PlayerId
    accept: bool
    kind: Literal["CONFIRM_BID"] = "CONFIRM_BID"


@dataclass(frozen=True, slots=True)
class DeclareContract:
    """Post-auction declaration: trump choice, nello partner sit-out, plunge trump pick."""

    actor: PlayerId
    trump: Trump = None
    kind: Literal["DECLARE_CONTRACT"] = "DECLARE_CONTRACT"


@dataclass(frozen=True, slots=True)
class PlayDomino:
    """Leading, ``declared_suit`` may name a non-default reading of a two-ended tile (DESIGN.md
    §5.2); never compulsory, and only meaningful when this is the trick's opening play."""

    actor: PlayerId
    domino: Domino
    declared_suit: Suit | None = None
    kind: Literal["PLAY_DOMINO"] = "PLAY_DOMINO"


type Move = PlaceBid | Pass | ConfirmBid | DeclareContract | PlayDomino
