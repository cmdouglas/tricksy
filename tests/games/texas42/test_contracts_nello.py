from __future__ import annotations

from tricksy.games.texas42.contracts import get
from tricksy.games.texas42.dominoes import Domino
from tricksy.games.texas42.house_rules import HouseRules
from tricksy.games.texas42.state import (
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

NELLO = get("nello")


def _trick(winner: Seat, *dominoes: Domino) -> Trick:
    return Trick(plays=tuple(PlayedDomino(seat=winner, domino=d) for d in dominoes), winner=winner)


def _state_with_tricks(tricks: tuple[Trick, ...]) -> GameState:
    hand = HandState(
        dealer=Seat.WEST,
        hands=deal(1),
        bids=(Bid(bidder=Seat.NORTH, contract="nello", marks=2),),
        declarer=Seat.NORTH,
        contract="nello",
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


def test_no_declaration_no_confirmation_partner_sits_out_declarer_leads() -> None:
    assert not NELLO.requires_partner_confirmation()
    assert not NELLO.requires_declaration()
    state = _state_with_tricks(())
    assert NELLO.opening_leader(state) is Seat.NORTH
    assert NELLO.sits_out(state) is partner_of(Seat.NORTH)


def test_taking_zero_tricks_makes_the_bid() -> None:
    tricks = (
        _trick(Seat.EAST, Domino(6, 4)),
        _trick(Seat.WEST, Domino(5, 0)),
    )
    state = _state_with_tricks(tricks)
    assert NELLO.score_hand(state) == {Team.NORTH_SOUTH: 2}


def test_taking_a_single_trick_sets_the_bid() -> None:
    tricks = (
        _trick(Seat.NORTH, Domino(6, 4)),  # declarer's team took one - bid is set
        _trick(Seat.WEST, Domino(5, 0)),
    )
    state = _state_with_tricks(tricks)
    assert NELLO.score_hand(state) == {Team.EAST_WEST: 2}


def test_doubles_form_their_own_suit_regardless_of_the_table_config() -> None:
    # Even with the game's doubles_are_own_suit turned off, nello still treats a led double as
    # establishing the doubles suit, ranked 6-6 high to 0-0 low - intrinsic to the contract, not
    # driven by HouseRules.
    off_config = HouseRules(doubles_are_own_suit=False)
    trick = Trick(
        plays=(
            PlayedDomino(seat=Seat.NORTH, domino=Domino(5, 5)),
            PlayedDomino(seat=Seat.EAST, domino=Domino(6, 6)),
        )
    )
    assert NELLO.trick_winner(trick, None, off_config) is Seat.EAST


def test_a_double_does_not_follow_a_non_double_led_suit() -> None:
    # 5-5 belongs only to the doubles suit under nello, so it cannot win (or even follow) a trick
    # led by a plain 5-3 - it would only appear here if the player had no fives to follow with.
    trick = Trick(
        plays=(
            PlayedDomino(seat=Seat.NORTH, domino=Domino(5, 3)),
            PlayedDomino(seat=Seat.EAST, domino=Domino(5, 5)),
        )
    )
    assert NELLO.trick_winner(trick, None, HouseRules()) is Seat.NORTH
