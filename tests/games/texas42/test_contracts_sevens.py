from __future__ import annotations

from t42.engine.contracts import get
from t42.engine.dominoes import Domino
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
)

from ._helpers import PLAYERS, deal

SEVENS = get("sevens")


def _trick(winner: Seat, *dominoes: Domino) -> Trick:
    return Trick(plays=tuple(PlayedDomino(seat=winner, domino=d) for d in dominoes), winner=winner)


def _state_with_tricks(tricks: tuple[Trick, ...]) -> GameState:
    hand = HandState(
        dealer=Seat.WEST,
        hands=deal(1),
        bids=(Bid(bidder=Seat.NORTH, contract="sevens", marks=1),),
        declarer=Seat.NORTH,
        contract="sevens",
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


def test_no_trump_and_all_four_seats_play() -> None:
    assert not SEVENS.requires_declaration()
    assert not SEVENS.requires_partner_confirmation()
    assert SEVENS.sits_out(_state_with_tricks(())) is None


def test_closest_to_seven_wins_and_earliest_play_wins_ties() -> None:
    trick = Trick(
        plays=(
            PlayedDomino(seat=Seat.NORTH, domino=Domino(3, 2)),  # sum 5, distance 2
            PlayedDomino(seat=Seat.EAST, domino=Domino(6, 1)),  # sum 7, distance 0 - takes lead
            PlayedDomino(seat=Seat.SOUTH, domino=Domino(5, 2)),  # sum 7, distance 0 - ties, no beat
            PlayedDomino(seat=Seat.WEST, domino=Domino(4, 0)),  # sum 4, distance 3
        )
    )
    assert SEVENS.trick_winner(trick, None, HouseRules()) is Seat.EAST


def test_sweeping_every_trick_makes_the_bid() -> None:
    tricks = (_trick(Seat.NORTH, Domino(6, 1)), _trick(Seat.SOUTH, Domino(5, 2)))
    state = _state_with_tricks(tricks)
    assert SEVENS.score_hand(state) == {Team.NORTH_SOUTH: 1}


def test_losing_a_single_trick_sets_the_bid() -> None:
    tricks = (_trick(Seat.NORTH, Domino(6, 1)), _trick(Seat.EAST, Domino(5, 2)))
    state = _state_with_tricks(tricks)
    assert SEVENS.score_hand(state) == {Team.EAST_WEST: 1}
