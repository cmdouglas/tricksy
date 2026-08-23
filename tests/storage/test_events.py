"""Unit tests for the write-direction move/deal -> event translation (ROADMAP.md 1.2)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from tricksy.games.texas42.events import (
    BidConfirmed,
    BidPlaced,
    ContractDeclared,
    DominoPlayed,
    Event,
    Passed,
)
from tricksy.games.texas42.moves import (
    ConfirmBid,
    DeclareContract,
    Move,
    Pass,
    PlaceBid,
    PlayDomino,
)
from tricksy.games.texas42.state import GameState, HandState, Phase, Seat
from tricksy.games.texas42.suits import Suit
from tricksy.storage.events import event_for_move, events_for_move, hand_dealt_event

from ..games.texas42._helpers import deal, make_game

_HANDS = deal(0)
_FIRST_DOMINO = _HANDS[Seat.NORTH][0]


def _state() -> GameState:
    return make_game(_HANDS, dealer=Seat.NORTH)


@pytest.mark.parametrize(
    ("move", "expected"),
    [
        (PlaceBid(actor="north", points=30), BidPlaced(actor="north", points=30)),
        (
            PlaceBid(actor="north", contract="plunge", marks=4),
            BidPlaced(actor="north", contract="plunge", marks=4),
        ),
        (Pass(actor="north"), Passed(actor="north")),
        (ConfirmBid(actor="north", accept=True), BidConfirmed(actor="north", accept=True)),
        (
            PlayDomino(actor="north", domino=_FIRST_DOMINO),
            DominoPlayed(actor="north", domino=_FIRST_DOMINO),
        ),
        (
            PlayDomino(actor="north", domino=_FIRST_DOMINO, declared_suit=Suit.SIXES),
            DominoPlayed(actor="north", domino=_FIRST_DOMINO, declared_suit=Suit.SIXES),
        ),
    ],
)
def test_trivial_moves_translate_field_for_field(move: Move, expected: Event) -> None:
    assert event_for_move(_state(), move) == expected


def test_declare_contract_reads_the_contract_name_off_state() -> None:
    state = _state()
    assert state.hand is not None
    state = replace(state, phase=Phase.DECLARING, hand=replace(state.hand, contract="standard"))
    move = DeclareContract(actor="north", trump=Suit.SIXES)
    assert event_for_move(state, move) == ContractDeclared(
        actor="north", contract="standard", trump=Suit.SIXES
    )


def test_hand_dealt_event_carries_every_seat_and_the_dealer() -> None:
    state = _state()
    assert state.hand is not None
    event = hand_dealt_event(state.hand)
    assert event.dealer == state.hand.dealer
    assert dict(event.hands) == dict(state.hand.hands)


def test_events_for_move_returns_one_event_when_the_hand_continues() -> None:
    before = _state()
    after = replace(before, phase=Phase.PLAYING)
    move = Pass(actor="north")
    assert events_for_move(before, move, after) == (Passed(actor="north"),)


def test_events_for_move_appends_a_hand_dealt_event_when_a_new_hand_starts() -> None:
    before = _state()
    next_hand = HandState(dealer=Seat.EAST, hands=deal(1))
    after = replace(before, hand=next_hand, phase=Phase.BIDDING)
    move = PlayDomino(actor="west", domino=_HANDS[Seat.WEST][0])

    events = events_for_move(before, move, after)

    assert len(events) == 2
    assert events[1] == hand_dealt_event(next_hand)


def test_events_for_move_appends_nothing_at_game_over() -> None:
    before = _state()
    after = replace(before, hand=None, phase=Phase.GAME_OVER, to_act=None)
    move = PlayDomino(actor="west", domino=_HANDS[Seat.WEST][0])

    events = events_for_move(before, move, after)

    assert len(events) == 1
