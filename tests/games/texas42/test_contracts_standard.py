from __future__ import annotations

import pytest

from t42.engine.contracts import get
from t42.engine.dominoes import Domino
from t42.engine.errors import IllegalMove
from t42.engine.house_rules import HouseRules
from t42.engine.state import Bid, GameState, HandState, Phase, PlayedDomino, Seat, Team, Trick
from t42.engine.suits import Suit

from ._helpers import PLAYERS, deal

STANDARD = get("standard")


def _trick(winner: Seat, *dominoes: Domino) -> Trick:
    return Trick(plays=tuple(PlayedDomino(seat=winner, domino=d) for d in dominoes), winner=winner)


def _state_with_tricks(bid_points: int, tricks: tuple[Trick, ...]) -> GameState:
    hand = HandState(
        dealer=Seat.WEST,
        hands=deal(1),
        bids=(Bid(bidder=Seat.NORTH, points=bid_points),),
        declarer=Seat.NORTH,
        contract="standard",
        trump=Suit.SIXES,
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


def test_validate_bid_rejects_a_marks_form_bid() -> None:
    with pytest.raises(IllegalMove):
        STANDARD.validate_bid(
            Bid(bidder=Seat.NORTH, contract="standard", marks=1), (), HouseRules()
        )


def test_no_confirmation_and_declarer_declares_and_leads() -> None:
    assert not STANDARD.requires_partner_confirmation()
    assert STANDARD.requires_declaration()
    assert STANDARD.sits_out(_state_with_tricks(30, ())) is None


def test_declaring_seat_and_opening_leader_are_the_bidder() -> None:
    state = _state_with_tricks(30, ())
    assert STANDARD.declaring_seat(state) is Seat.NORTH
    assert STANDARD.opening_leader(state) is Seat.NORTH


def test_bid_made_exactly_scores_the_declaring_team_one_mark() -> None:
    tricks = (
        _trick(Seat.NORTH, Domino(6, 4)),  # 10 count + 1 = 11
        _trick(Seat.NORTH, Domino(5, 0)),  # 5 + 1 = 6
        _trick(Seat.NORTH, Domino(4, 1)),  # 5 + 1 = 6
        _trick(Seat.NORTH, Domino(3, 2)),  # 5 + 1 = 6
        _trick(Seat.NORTH, Domino(1, 1)),  # 0 + 1 = 1
        # total: 30 exactly
    )
    state = _state_with_tricks(30, tricks)
    assert STANDARD.score_hand(state) == {Team.NORTH_SOUTH: 1}


def test_bid_set_by_one_scores_the_defending_team_one_mark() -> None:
    tricks = (
        _trick(Seat.NORTH, Domino(6, 4)),
        _trick(Seat.NORTH, Domino(5, 0)),
        _trick(Seat.NORTH, Domino(4, 1)),
        _trick(Seat.NORTH, Domino(3, 2)),
        # total: 29, one short of the 30 bid
    )
    state = _state_with_tricks(30, tricks)
    assert STANDARD.score_hand(state) == {Team.EAST_WEST: 1}
