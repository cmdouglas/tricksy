"""Nello (default doubles-own-suit variant): partner sits out, no trump.

Doubles form their own suit, ranked 6-6 high down to 0-0 low. See ``nello_low.py`` for the
doubles-rank-low variant and DESIGN.md §12 for why these are two contracts, not a flag.
"""

from __future__ import annotations

from ._nello_common import NelloBase
from .registry import register


class NelloContract(NelloBase):
    name = "nello"
    _doubles_are_own_suit = True
    _doubles_rank = "high"  # moot: doubles form their own suit, so number-suit rank never applies


register(NelloContract())
