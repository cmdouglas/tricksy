from __future__ import annotations

import pytest

from tricksy.games.texas42.bidding import apply_bid, apply_confirmation, legal_bids
from tricksy.games.texas42.dominoes import Domino
from tricksy.games.texas42.errors import IllegalMove, OutOfTurn
from tricksy.games.texas42.house_rules import HouseRules
from tricksy.games.texas42.moves import ConfirmBid, Pass, PlaceBid
from tricksy.games.texas42.state import Bid, Phase, Seat

from ._helpers import custom_deal, deal, make_game, player_of

DOUBLES = tuple(Domino(n, n) for n in range(7))


def test_legal_bids_from_a_fresh_auction_span_thirty_to_forty_two_plus_pass() -> None:
    state = make_game(deal(1))
    bids = legal_bids(state)
    assert Bid(bidder=Seat.EAST, points=30) in bids
    assert Bid(bidder=Seat.EAST, points=42) in bids
    assert Bid(bidder=Seat.EAST) in bids  # pass
    assert Bid(bidder=Seat.EAST, points=29) not in bids
    assert Bid(bidder=Seat.EAST, points=43) not in bids


def test_a_bid_must_strictly_exceed_the_current_high() -> None:
    state = make_game(deal(1))
    state = apply_bid(state, PlaceBid(actor=player_of(Seat.EAST), points=31))
    bids = legal_bids(state)
    assert all(b.is_pass or (b.points or 0) > 31 or b.marks is not None for b in bids)
    assert Bid(bidder=Seat.SOUTH, points=31) not in bids
    assert Bid(bidder=Seat.SOUTH, points=32) in bids


def test_out_of_turn_bid_is_rejected() -> None:
    state = make_game(deal(1))
    with pytest.raises(OutOfTurn):
        apply_bid(state, PlaceBid(actor=player_of(Seat.SOUTH), points=30))


def test_illegal_bid_value_is_rejected() -> None:
    state = make_game(deal(1))
    with pytest.raises(IllegalMove):
        apply_bid(state, PlaceBid(actor=player_of(Seat.EAST), points=29))


def test_bidding_a_disabled_contract_is_rejected() -> None:
    state = make_game(deal(1))  # splash is off by default
    with pytest.raises(IllegalMove):
        apply_bid(state, PlaceBid(actor=player_of(Seat.EAST), contract="splash", marks=2))


def test_plunge_without_enough_doubles_is_rejected() -> None:
    hands = custom_deal(
        north=(
            Domino(0, 0),
            Domino(1, 1),
            Domino(6, 5),
            Domino(6, 4),
            Domino(6, 3),
            Domino(6, 2),
            Domino(6, 1),
        )
    )
    state = make_game(hands, to_act=Seat.NORTH)
    assert Bid(bidder=Seat.NORTH, contract="plunge", marks=4) not in legal_bids(state)
    with pytest.raises(IllegalMove):
        apply_bid(state, PlaceBid(actor=player_of(Seat.NORTH), contract="plunge", marks=4))


def test_splash_with_enough_doubles_is_legal() -> None:
    hands = custom_deal(
        north=(*DOUBLES[:3], Domino(6, 5), Domino(6, 4), Domino(5, 4), Domino(4, 3))
    )
    config = HouseRules(enabled_contracts=frozenset({"standard", "nello", "plunge", "splash"}))
    state = make_game(hands, to_act=Seat.NORTH, config=config)
    assert Bid(bidder=Seat.NORTH, contract="splash", marks=2) in legal_bids(state)
    assert Bid(bidder=Seat.NORTH, contract="splash", marks=1) not in legal_bids(state)


def test_the_dealer_may_not_pass_after_three_passes() -> None:
    state = make_game(deal(1))
    state = apply_bid(state, Pass(actor=player_of(Seat.EAST)))
    state = apply_bid(state, Pass(actor=player_of(Seat.SOUTH)))
    state = apply_bid(state, Pass(actor=player_of(Seat.WEST)))
    assert state.to_act is Seat.NORTH
    assert Bid(bidder=Seat.NORTH) not in legal_bids(state)
    with pytest.raises(IllegalMove):
        apply_bid(state, Pass(actor=player_of(Seat.NORTH)))


def test_dealer_forced_bid_settles_the_auction() -> None:
    state = make_game(deal(1))
    state = apply_bid(state, Pass(actor=player_of(Seat.EAST)))
    state = apply_bid(state, Pass(actor=player_of(Seat.SOUTH)))
    state = apply_bid(state, Pass(actor=player_of(Seat.WEST)))
    state = apply_bid(state, PlaceBid(actor=player_of(Seat.NORTH), points=30))
    assert state.phase is Phase.DECLARING
    assert state.hand is not None and state.hand.declarer is Seat.NORTH


def test_plunge_proposal_awaits_partner_confirmation() -> None:
    hands = custom_deal(north=DOUBLES)
    state = make_game(hands, to_act=Seat.NORTH)
    state = apply_bid(state, PlaceBid(actor=player_of(Seat.NORTH), contract="plunge", marks=4))
    assert state.hand is not None
    assert state.hand.pending_bid is not None
    assert state.hand.bids == ()  # not live yet
    assert state.to_act is Seat.SOUTH  # NORTH's partner


def test_declined_plunge_leaves_no_bid_and_the_proposer_acts_again() -> None:
    hands = custom_deal(north=DOUBLES)
    state = make_game(hands, to_act=Seat.NORTH)
    state = apply_bid(state, PlaceBid(actor=player_of(Seat.NORTH), contract="plunge", marks=4))
    state = apply_confirmation(state, ConfirmBid(actor=player_of(Seat.SOUTH), accept=False))
    assert state.hand is not None
    assert state.hand.pending_bid is None
    assert state.hand.bids == ()
    assert state.to_act is Seat.NORTH

    state = apply_bid(state, PlaceBid(actor=player_of(Seat.NORTH), points=31))
    assert state.hand is not None
    assert state.hand.bids[-1] == Bid(bidder=Seat.NORTH, points=31)
    assert state.to_act is Seat.EAST


def test_accepted_plunge_becomes_a_live_bid_and_the_auction_continues() -> None:
    hands = custom_deal(north=DOUBLES)
    state = make_game(hands, to_act=Seat.NORTH)
    state = apply_bid(state, PlaceBid(actor=player_of(Seat.NORTH), contract="plunge", marks=4))
    state = apply_confirmation(state, ConfirmBid(actor=player_of(Seat.SOUTH), accept=True))
    assert state.hand is not None
    assert state.hand.pending_bid is None
    assert state.hand.bids == (Bid(bidder=Seat.NORTH, contract="plunge", marks=4),)
    assert state.to_act is Seat.EAST


def test_confirmation_out_of_turn_is_rejected() -> None:
    hands = custom_deal(north=DOUBLES)
    state = make_game(hands, to_act=Seat.NORTH)
    state = apply_bid(state, PlaceBid(actor=player_of(Seat.NORTH), contract="plunge", marks=4))
    with pytest.raises(OutOfTurn):
        apply_confirmation(state, ConfirmBid(actor=player_of(Seat.EAST), accept=True))


def test_auction_resolves_to_standard_declaring_seat_is_the_bidder() -> None:
    state = make_game(deal(1))
    state = apply_bid(state, PlaceBid(actor=player_of(Seat.EAST), points=30))
    state = apply_bid(state, Pass(actor=player_of(Seat.SOUTH)))
    state = apply_bid(state, Pass(actor=player_of(Seat.WEST)))
    state = apply_bid(state, Pass(actor=player_of(Seat.NORTH)))
    assert state.phase is Phase.DECLARING
    assert state.hand is not None
    assert state.hand.declarer is Seat.EAST
    assert state.hand.contract == "standard"
    assert state.to_act is Seat.EAST  # bidder declares trump themself


def test_auction_resolves_to_plunge_declaring_seat_is_the_partner() -> None:
    hands = custom_deal(north=DOUBLES)
    state = make_game(hands, to_act=Seat.NORTH)
    state = apply_bid(state, PlaceBid(actor=player_of(Seat.NORTH), contract="plunge", marks=4))
    state = apply_confirmation(state, ConfirmBid(actor=player_of(Seat.SOUTH), accept=True))
    state = apply_bid(state, Pass(actor=player_of(Seat.EAST)))
    state = apply_bid(state, Pass(actor=player_of(Seat.SOUTH)))
    state = apply_bid(state, Pass(actor=player_of(Seat.WEST)))
    assert state.phase is Phase.DECLARING
    assert state.hand is not None and state.hand.declarer is Seat.NORTH
    assert state.to_act is Seat.SOUTH  # bidder's partner declares trump


def test_auction_resolves_to_nello_skips_declaring_and_opens_play() -> None:
    state = make_game(deal(1))
    state = apply_bid(state, PlaceBid(actor=player_of(Seat.EAST), contract="nello", marks=1))
    state = apply_bid(state, Pass(actor=player_of(Seat.SOUTH)))
    state = apply_bid(state, Pass(actor=player_of(Seat.WEST)))
    state = apply_bid(state, Pass(actor=player_of(Seat.NORTH)))
    assert state.phase is Phase.PLAYING
    assert state.hand is not None and state.hand.declarer is Seat.EAST
    assert state.to_act is Seat.EAST  # nello's declarer leads first
