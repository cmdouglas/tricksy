"""Declared leads (DESIGN.md §5.2, ROADMAP.md 0.5.5): the leader may name which end of a
two-ended, non-trump tile is the suit led. Exercises the public `tricks.play`/`game.legal_moves`/
`new_game`+`apply_move` entry points, the same way a client would.
"""

from __future__ import annotations

from random import Random

import pytest

from tricksy.games.texas42.dominoes import Domino
from tricksy.games.texas42.errors import IllegalMove
from tricksy.games.texas42.game import apply_move, legal_moves, new_game
from tricksy.games.texas42.house_rules import HouseRules
from tricksy.games.texas42.moves import Move, PlayDomino
from tricksy.games.texas42.state import (
    Bid,
    GameState,
    HandState,
    Phase,
    PlayedDomino,
    Seat,
    Team,
    Trick,
)
from tricksy.games.texas42.suits import Suit
from tricksy.games.texas42.tricks import play

from ._helpers import PLAYERS, custom_deal, player_of

_NORTH_HAND = (
    Domino(3, 2),  # plain two-ended tile: declarable as deuces, defaults to treys
    Domino(6, 4),  # will be a trump tile when trump=SIXES
    Domino(3, 3),  # a double
    Domino(5, 1),
    Domino(0, 0),
    Domino(2, 2),
    Domino(4, 0),
)

_A_COMPLETED_TRICK = Trick(
    plays=tuple(
        PlayedDomino(seat=seat, domino=domino)
        for seat, domino in zip(
            Seat, [Domino(6, 6), Domino(6, 5), Domino(6, 3), Domino(6, 1)], strict=True
        )
    ),
    winner=Seat.NORTH,
)


def _playing_state(
    hands: dict[Seat, tuple[Domino, ...]],
    *,
    config: HouseRules | None = None,
    declarer: Seat = Seat.NORTH,
    trump: Suit | None = None,
    to_act: Seat | None = None,
    current_trick: Trick | None = None,
    completed_tricks: tuple[Trick, ...] = (),
) -> GameState:
    hand = HandState(
        dealer=Seat.WEST,
        hands=hands,
        bids=(Bid(bidder=declarer, points=30),),
        declarer=declarer,
        contract="standard",
        trump=trump,
        current_trick=current_trick if current_trick is not None else Trick(),
        completed_tricks=completed_tricks,
    )
    return GameState(
        game_id="declared-leads",
        config=config or HouseRules(allow_declared_lead="always"),
        players=dict(PLAYERS),
        phase=Phase.PLAYING,
        marks={Team.NORTH_SOUTH: 0, Team.EAST_WEST: 0},
        hand=hand,
        to_act=to_act if to_act is not None else declarer,
    )


def test_never_rejects_any_declaration() -> None:
    hands = custom_deal(north=_NORTH_HAND)
    state = _playing_state(hands, config=HouseRules(allow_declared_lead="never"))
    with pytest.raises(IllegalMove, match="not allowed"):
        play(
            state,
            PlayDomino(actor=player_of(Seat.NORTH), domino=Domino(3, 2), declared_suit=Suit.DEUCES),
        )


def test_first_trick_permits_a_declaration_on_the_first_trick() -> None:
    hands = custom_deal(north=_NORTH_HAND)
    state = _playing_state(hands, config=HouseRules(allow_declared_lead="first_trick"))
    new_state = play(
        state,
        PlayDomino(actor=player_of(Seat.NORTH), domino=Domino(3, 2), declared_suit=Suit.DEUCES),
    )
    assert new_state.hand is not None
    assert new_state.hand.current_trick.declared_suit is Suit.DEUCES


def test_first_trick_rejects_a_declaration_on_a_later_trick() -> None:
    hands = custom_deal(north=_NORTH_HAND)
    state = _playing_state(
        hands,
        config=HouseRules(allow_declared_lead="first_trick"),
        completed_tricks=(_A_COMPLETED_TRICK,),
    )
    with pytest.raises(IllegalMove, match="not allowed"):
        play(
            state,
            PlayDomino(actor=player_of(Seat.NORTH), domino=Domino(3, 2), declared_suit=Suit.DEUCES),
        )


def test_always_permits_a_declaration_on_a_later_trick() -> None:
    hands = custom_deal(north=_NORTH_HAND)
    state = _playing_state(
        hands,
        config=HouseRules(allow_declared_lead="always"),
        completed_tricks=(_A_COMPLETED_TRICK,),
    )
    new_state = play(
        state,
        PlayDomino(actor=player_of(Seat.NORTH), domino=Domino(3, 2), declared_suit=Suit.DEUCES),
    )
    assert new_state.hand is not None
    assert new_state.hand.current_trick.declared_suit is Suit.DEUCES


def test_only_the_leader_may_declare() -> None:
    hands = custom_deal(north=_NORTH_HAND)
    current_trick = Trick(plays=(PlayedDomino(seat=Seat.NORTH, domino=Domino(0, 0)),))
    east_hand = hands[Seat.EAST]
    config = HouseRules(allow_declared_lead="always")
    state = _playing_state(hands, current_trick=current_trick, to_act=Seat.EAST, config=config)
    playable = legal_moves(state, player_of(Seat.EAST))
    assert playable
    domino = next(m.domino for m in playable if isinstance(m, PlayDomino))
    assert domino in east_hand
    with pytest.raises(IllegalMove, match="leader"):
        play(
            state,
            PlayDomino(actor=player_of(Seat.EAST), domino=domino, declared_suit=Suit.ACES),
        )


def test_declaring_a_suit_the_tile_does_not_belong_to_is_rejected() -> None:
    hands = custom_deal(north=_NORTH_HAND)
    state = _playing_state(hands)
    with pytest.raises(IllegalMove, match="cannot be declared"):
        play(
            state,
            PlayDomino(actor=player_of(Seat.NORTH), domino=Domino(3, 2), declared_suit=Suit.SIXES),
        )


def test_a_trump_tile_cannot_be_declared_out_of_trump() -> None:
    hands = custom_deal(north=_NORTH_HAND)
    state = _playing_state(hands, trump=Suit.SIXES)
    with pytest.raises(IllegalMove, match="cannot be declared"):
        play(
            state,
            PlayDomino(actor=player_of(Seat.NORTH), domino=Domino(6, 4), declared_suit=Suit.FOURS),
        )


@pytest.mark.parametrize("doubles_are_own_suit", [False, True])
def test_a_double_offers_no_declaration(doubles_are_own_suit: bool) -> None:
    hands = custom_deal(north=_NORTH_HAND)
    config = HouseRules(allow_declared_lead="always", doubles_are_own_suit=doubles_are_own_suit)
    state = _playing_state(hands, config=config)
    with pytest.raises(IllegalMove, match="cannot be declared"):
        play(
            state,
            PlayDomino(actor=player_of(Seat.NORTH), domino=Domino(3, 3), declared_suit=Suit.TREYS),
        )


def test_declared_suit_survives_the_trick_closing() -> None:
    """The regression the fix targets: `tricks.py` used to rebuild a fresh `Trick` when closing
    one, which would silently drop the leader's declaration on exactly the trick it decided."""
    deuces = (
        Domino(2, 0),
        Domino(2, 1),
        Domino(2, 2),
        Domino(3, 2),
        Domino(4, 2),
        Domino(5, 2),
        Domino(6, 2),
    )
    hands = custom_deal(north=deuces)  # every deuces-suit tile, so nobody else can hold one
    state = _playing_state(hands, config=HouseRules(allow_declared_lead="always"))
    state = play(
        state,
        PlayDomino(actor=player_of(Seat.NORTH), domino=Domino(3, 2), declared_suit=Suit.DEUCES),
    )
    assert state.hand is not None
    assert state.hand.current_trick.declared_suit is Suit.DEUCES

    for seat in (Seat.EAST, Seat.SOUTH, Seat.WEST):
        assert state.hand is not None
        tile = state.hand.hands[seat][0]
        state = play(state, PlayDomino(actor=player_of(seat), domino=tile))

    assert state.hand is not None
    assert len(state.hand.completed_tricks) == 1
    assert state.hand.completed_tricks[0].declared_suit is Suit.DEUCES
    assert state.hand.current_trick == Trick()


def test_legal_moves_offers_declared_variants_only_when_leading_and_permitted() -> None:
    hands = custom_deal(north=_NORTH_HAND)
    state = _playing_state(hands, config=HouseRules(allow_declared_lead="always"), trump=Suit.SIXES)
    options = legal_moves(state, player_of(Seat.NORTH))
    plain = PlayDomino(actor=player_of(Seat.NORTH), domino=Domino(3, 2))
    declared = PlayDomino(
        actor=player_of(Seat.NORTH), domino=Domino(3, 2), declared_suit=Suit.DEUCES
    )
    assert plain in options
    assert declared in options
    # the trump tile (6-4) and the double (3-3) never get a declared variant
    assert not any(
        isinstance(o, PlayDomino) and o.domino in (Domino(6, 4), Domino(3, 3)) and o.declared_suit
        for o in options
    )


def test_legal_moves_offers_no_declared_variant_under_never() -> None:
    hands = custom_deal(north=_NORTH_HAND)
    state = _playing_state(hands, config=HouseRules(allow_declared_lead="never"), trump=Suit.SIXES)
    options = legal_moves(state, player_of(Seat.NORTH))
    assert options
    assert not any(isinstance(o, PlayDomino) and o.declared_suit is not None for o in options)


def test_legal_moves_offers_no_declared_variant_when_not_leading() -> None:
    hands = custom_deal(north=_NORTH_HAND)
    current_trick = Trick(plays=(PlayedDomino(seat=Seat.NORTH, domino=Domino(0, 0)),))
    state = _playing_state(
        hands,
        config=HouseRules(allow_declared_lead="always"),
        trump=Suit.SIXES,
        current_trick=current_trick,
        to_act=Seat.EAST,
    )
    options = legal_moves(state, player_of(Seat.EAST))
    assert options
    assert not any(isinstance(o, PlayDomino) and o.declared_suit is not None for o in options)


def test_full_game_under_always_reaches_game_over() -> None:
    rng = Random(7)
    config = HouseRules(marks_to_win=1, allow_declared_lead="always")
    state = new_game("g-declared", PLAYERS, config, rng=rng)

    def choose(state: GameState, options: tuple[Move, ...]) -> Move:
        declared = [o for o in options if isinstance(o, PlayDomino) and o.declared_suit is not None]
        return declared[0] if declared else options[0]

    for _ in range(500):
        if state.phase is Phase.GAME_OVER:
            break
        seat = state.to_act
        assert seat is not None
        options = legal_moves(state, player_of(seat))
        assert options
        state = apply_move(state, choose(state, options), rng=rng)
    else:
        raise AssertionError("game did not reach GAME_OVER within 500 moves")

    assert state.phase is Phase.GAME_OVER
    assert state.hand is None
