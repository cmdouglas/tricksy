"""Contract strategies, keyed by name.

Importing this package registers every built-in contract. A game may still restrict which of them
are legal to bid through its :class:`~tricksy.games.texas42.house_rules.HouseRules`.
"""

from __future__ import annotations

from . import nello, nello_low, plunge, sevens, splash, standard  # noqa: F401  (registers these)
from .base import Contract
from .registry import available, get, get_enabled, register, validate_house_rules

__all__ = [
    "Contract",
    "available",
    "get",
    "get_enabled",
    "register",
    "validate_house_rules",
]
