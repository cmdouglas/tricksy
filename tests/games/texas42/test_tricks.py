from __future__ import annotations

import pytest

from t42.engine.contracts import get as get_contract
from t42.engine.dominoes import FULL_SET
from t42.engine.errors import IllegalMove, OutOfTurn
from t42.engine.house_rules import HouseRules
from t42.engine.moves import PlayDomino
from t42.engine.state import Bid, GameState, HandState, Phase, Seat, Team, partner_of
from t42.engine.suits import Suit
from t42.engine.tricks import legal_plays, play

from ._helpers import PLAYERS, deal, player_of


def _standard_playing_state(seed: int, *, declarer: Seat = Seat.NORTH) -> GameState:
    hands = deal(seed)
    hand = HandState(
        dealer=Seat.WEST,
        hands=hands,
        bids=(Bid(bidder=declarer, points=30),),
        declarer=declarer,
        contract="standard",
        trump=Suit.SIXES,
    )
    return GameState(
        game_id="test-game",
        config=HouseRules(),
        players=dict(PLAYERS),
        phase=Phase.PLAYING,
        marks={Team.NORTH_SOUTH: 0, Team.EAST_WEST: 0},
        hand=hand,
        to_act=declarer,
    )


def _nello_playing_state(seed: int, *, declarer: Seat = Seat.NORTH) -> GameState:
    hands = deal(seed)
    hand = HandState(
        dealer=Seat.WEST,
        hands=hands,
        bids=(Bid(bidder=declarer, contract="nello", marks=1),),
        declarer=declarer,
        contract="nello",
        trump=None,
    )
    return GameState(
        game_id="test-game",
        config=HouseRules(),
        players=dict(PLAYERS),
        phase=Phase.PLAYING,
        marks={Team.NORTH_SOUTH: 0, Team.EAST_WEST: 0},
        hand=hand,
        to_act=declarer,
    )


def _play_first_legal(state: GameState) -> GameState:
    seat = state.to_act
    assert seat is not None and state.hand is not None and state.hand.contract is not None
    contract = get_contract(state.hand.contract)
    options = legal_plays(
        state.hand.hands[seat], state.hand.current_trick, state.hand.trump, state.config, contract
    )
    return play(state, PlayDomino(actor=player_of(seat), domino=options[0]))


def _play_out_hand(state: GameState) -> tuple[GameState, list[int]]:
    """Play every trick to completion, returning the final state and each trick's play count."""
    trick_sizes: list[int] = []
    previous_completed = 0
    while state.phase is Phase.PLAYING:
        state = _play_first_legal(state)
        completed = len(state.hand.completed_tricks) if state.hand else 0
        if completed > previous_completed:
            trick_sizes.append(len(state.hand.completed_tricks[-1].plays))  # type: ignore[union-attr]
            previous_completed = completed
    return state, trick_sizes


def test_out_of_turn_play_is_rejected() -> None:
    state = _standard_playing_state(1, declarer=Seat.NORTH)
    other = Seat.EAST
    with pytest.raises(OutOfTurn):
        play(state, PlayDomino(actor=player_of(other), domino=state.hand.hands[other][0]))  # type: ignore[union-attr]


def test_playing_a_tile_not_in_hand_is_rejected() -> None:
    state = _standard_playing_state(1)
    seat = state.to_act
    assert seat is not None and state.hand is not None
    foreign = next(d for d in FULL_SET if d not in state.hand.hands[seat])
    with pytest.raises(IllegalMove):
        play(state, PlayDomino(actor=player_of(seat), domino=foreign))


def test_turn_advances_to_the_trick_winner() -> None:
    state = _standard_playing_state(1)
    for _ in range(4):
        state = _play_first_legal(state)
    assert state.hand is not None
    assert len(state.hand.completed_tricks) == 1
    winner = state.hand.completed_tricks[0].winner
    assert winner is not None
    assert state.to_act == winner


def test_a_full_standard_hand_plays_seven_four_seat_tricks() -> None:
    state, trick_sizes = _play_out_hand(_standard_playing_state(7))
    assert trick_sizes == [4] * 7
    assert state.phase is Phase.HAND_COMPLETE
    assert state.to_act is None
    assert state.hand is not None and len(state.hand.completed_tricks) == 7


def test_a_nello_hand_plays_three_seat_tricks_skipping_the_sitting_out_partner() -> None:
    declarer = Seat.NORTH
    sitting_out = partner_of(declarer)
    state, trick_sizes = _play_out_hand(_nello_playing_state(3, declarer=declarer))
    assert trick_sizes == [3] * 7
    assert state.phase is Phase.HAND_COMPLETE
    assert state.hand is not None
    for trick in state.hand.completed_tricks:
        assert all(p.seat != sitting_out for p in trick.plays)
