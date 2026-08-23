from __future__ import annotations

import pytest

from t42.engine.contracts import get
from t42.engine.dominoes import Domino
from t42.engine.errors import IllegalMove
from t42.engine.house_rules import HouseRules
from t42.engine.state import Bid, Seat

SPLASH = get("splash")
DOUBLES = tuple(Domino(n, n) for n in range(7))


def test_requires_two_marks() -> None:
    with pytest.raises(IllegalMove, match="marks"):
        SPLASH.validate_bid(
            Bid(bidder=Seat.NORTH, contract="splash", marks=1), DOUBLES[:3], HouseRules()
        )


def test_requires_at_least_three_doubles() -> None:
    weak_hand = (*DOUBLES[:2], Domino(6, 5), Domino(6, 4), Domino(5, 4), Domino(4, 3), Domino(3, 1))
    with pytest.raises(IllegalMove, match="doubles"):
        SPLASH.validate_bid(
            Bid(bidder=Seat.NORTH, contract="splash", marks=2), weak_hand, HouseRules()
        )


def test_three_doubles_and_two_marks_is_legal() -> None:
    hand = (*DOUBLES[:3], Domino(6, 5), Domino(6, 4), Domino(5, 4), Domino(4, 3))
    SPLASH.validate_bid(Bid(bidder=Seat.NORTH, contract="splash", marks=2), hand, HouseRules())


def test_no_partner_confirmation_needed_unlike_plunge() -> None:
    assert not SPLASH.requires_partner_confirmation()


def test_a_house_rule_override_raises_the_doubles_minimum() -> None:
    hand = (*DOUBLES[:3], Domino(6, 5), Domino(6, 4), Domino(5, 4), Domino(4, 3))
    strict = HouseRules(contract_options={"splash": {"minimum_doubles": 4}})
    with pytest.raises(IllegalMove, match="doubles"):
        SPLASH.validate_bid(Bid(bidder=Seat.NORTH, contract="splash", marks=2), hand, strict)
    SPLASH.validate_bid(
        Bid(bidder=Seat.NORTH, contract="splash", marks=2), (*DOUBLES[:4], Domino(6, 5)), strict
    )
