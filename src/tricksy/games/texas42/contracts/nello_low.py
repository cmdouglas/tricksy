"""Nello, doubles-low variant: partner sits out, no trump.

Doubles stay in their number suit but rank lowest there, instead of forming their own suit. Off
by default; enable per game like splash. See DESIGN.md §12.
"""

from __future__ import annotations

from ._nello_common import NelloBase
from .registry import register


class NelloLowContract(NelloBase):
    name = "nello_low"
    _doubles_are_own_suit = False
    _doubles_rank = "low"


register(NelloLowContract())
