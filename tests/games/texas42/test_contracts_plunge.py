from __future__ import annotations

import pytest

from t42.engine.contracts import get
from t42.engine.dominoes import Domino
from t42.engine.errors import IllegalMove
from t42.engine.house_rules import HouseRules
from t42.engine.state import (
    Bid,
    GameState,
    HandState,
    Phase,
    PlayedDomino,
    Seat,
    Team,
    Trick,
    partner_of,
)

from ._helpers import PLAYERS, deal

PLUNGE = get("plunge")
DOUBLES = tuple(Domino(n, n) for n in range(7))


def _trick(winner: Seat, *dominoes: Domino) -> Trick:
    return Trick(plays=tuple(PlayedDomino(seat=winner, domino=d) for d in dominoes), winner=winner)


def _state_with_tricks(tricks: tuple[Trick, ...]) -> GameState:
    hand = HandState(
        dealer=Seat.WEST,
        hands=deal(1),
        bids=(Bid(bidder=Seat.NORTH, contract="plunge", marks=4),),
        declarer=Seat.NORTH,
        contract="plunge",
        trump=None,
        completed_tricks=tricks,
    )
    return GameState(
        game_id="g",
        config=HouseRules(),
        players=dict(PLAYERS),
        phase=Phase.HAND_COMPLETE,
        marks={Team.NORTH_SOUTH: 0, Team.EAST_WEST: 0},
        hand=hand,
    )


def test_requires_four_marks() -> None:
    with pytest.raises(IllegalMove, match="marks"):
        PLUNGE.validate_bid(
            Bid(bidder=Seat.NORTH, contract="plunge", marks=3), DOUBLES, HouseRules()
        )


def test_requires_at_least_four_doubles() -> None:
    weak_hand = (*DOUBLES[:3], Domino(6, 5), Domino(6, 4), Domino(5, 4), Domino(4, 3))
    with pytest.raises(IllegalMove, match="doubles"):
        PLUNGE.validate_bid(
            Bid(bidder=Seat.NORTH, contract="plunge", marks=4), weak_hand, HouseRules()
        )


def test_four_doubles_and_four_marks_is_legal() -> None:
    PLUNGE.validate_bid(Bid(bidder=Seat.NORTH, contract="plunge", marks=4), DOUBLES, HouseRules())


def test_a_house_rule_override_raises_the_marks_minimum() -> None:
    strict = HouseRules(contract_options={"plunge": {"minimum_marks": 6}})
    with pytest.raises(IllegalMove, match="marks"):
        PLUNGE.validate_bid(Bid(bidder=Seat.NORTH, contract="plunge", marks=5), DOUBLES, strict)
    PLUNGE.validate_bid(Bid(bidder=Seat.NORTH, contract="plunge", marks=6), DOUBLES, strict)


def test_requires_partner_confirmation() -> None:
    assert PLUNGE.requires_partner_confirmation()


def test_partner_declares_and_leads_not_the_bidder() -> None:
    state = _state_with_tricks(())
    assert PLUNGE.requires_declaration()
    assert PLUNGE.declaring_seat(state) is partner_of(Seat.NORTH)
    assert PLUNGE.opening_leader(state) is partner_of(Seat.NORTH)
    assert PLUNGE.sits_out(state) is None


def test_taking_every_point_makes_the_bid() -> None:
    tricks = (
        _trick(Seat.NORTH, Domino(6, 4)),  # 10 + 1
        _trick(Seat.SOUTH, Domino(5, 5)),  # 10 + 1
        _trick(Seat.NORTH, Domino(5, 0)),  # 5 + 1
        _trick(Seat.SOUTH, Domino(4, 1)),  # 5 + 1
        _trick(Seat.NORTH, Domino(3, 2)),  # 5 + 1
        _trick(Seat.SOUTH, Domino(1, 1)),  # 0 + 1
        _trick(Seat.NORTH, Domino(2, 2)),  # 0 + 1
        # total: 35 count + 7 tricks = 42
    )
    state = _state_with_tricks(tricks)
    assert PLUNGE.score_hand(state) == {Team.NORTH_SOUTH: 4}


def test_conceding_a_single_trick_sets_the_bid() -> None:
    tricks = (
        _trick(Seat.NORTH, Domino(6, 4)),
        _trick(Seat.SOUTH, Domino(5, 5)),
        _trick(Seat.NORTH, Domino(5, 0)),
        _trick(Seat.SOUTH, Domino(4, 1)),
        _trick(Seat.NORTH, Domino(3, 2)),
        _trick(Seat.SOUTH, Domino(1, 1)),
        _trick(Seat.EAST, Domino(2, 2)),  # opponents took this one
    )
    state = _state_with_tricks(tricks)
    assert PLUNGE.score_hand(state) == {Team.EAST_WEST: 4}
