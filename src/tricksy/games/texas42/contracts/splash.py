"""Splash: bid 2+ marks holding at least 3 of the 7 doubles; the bidder's partner names trump and
leads, same as plunge but with a lower bar and no confirmation step (DESIGN.md §12).
"""

from __future__ import annotations

from collections.abc import Mapping

from ..house_rules import OptionValue
from ._partner_declares_common import PartnerDeclaresBase
from .registry import register


class SplashContract(PartnerDeclaresBase):
    name = "splash"
    _option_defaults: Mapping[str, OptionValue] = {"minimum_doubles": 3, "minimum_marks": 2}
    _requires_confirmation = False


register(SplashContract())
