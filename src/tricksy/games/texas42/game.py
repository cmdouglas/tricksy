"""Engine entry points (DESIGN.md §3): state + proposed move in, new state or error out.

Handlers call only this module and :mod:`tricksy.games.texas42.projection`; everything else is
internal to the engine.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from random import Random

from . import bidding, tricks
from .contracts import get as get_contract
from .contracts import validate_house_rules
from .dominoes import FULL_SET
from .errors import IllegalMove, OutOfTurn
from .house_rules import HouseRules
from .moves import ConfirmBid, DeclareContract, Move, Pass, PlaceBid, PlayDomino
from .state import (
    GameId,
    GameState,
    HandState,
    Phase,
    PlayerId,
    Seat,
    Team,
    seat_of,
)
from .suits import NUMBER_SUITS, Suit, Trump, declarable_suits


def new_game(
    game_id: GameId,
    players: Mapping[Seat, PlayerId],
    config: HouseRules,
    *,
    rng: Random,
) -> GameState:
    """Create a game and deal the first hand.

    ``rng`` is injected rather than taken from module state so deals are reproducible in tests and
    the engine stays free of ambient randomness. Dealer for the first hand is always North.

    Raises if ``config`` is not a valid house-rule set (DESIGN.md §5.1) - checked before the seat
    validation below, so an invalid rule set is rejected regardless of ``players``.
    """
    validate_house_rules(config)
    if set(players) != set(Seat):
        raise ValueError(f"a game needs all four seats filled, got {sorted(players)}")
    dealer = Seat.NORTH
    hand = _deal_hand(config, dealer, rng)
    return GameState(
        game_id=game_id,
        config=config,
        players=dict(players),
        phase=Phase.BIDDING,
        marks={Team.NORTH_SOUTH: 0, Team.EAST_WEST: 0},
        hand=hand,
        to_act=Seat((dealer + 1) % 4),
    )


def apply_move(state: GameState, move: Move, *, rng: Random) -> GameState:
    """Validate ``move`` against ``state`` and return the resulting state.

    Dispatches to the bidding machine, the declaration step or the trick engine according to the
    current phase, then deals the next hand (or ends the game) if that move just completed one.
    ``rng`` is only consulted when a new hand needs dealing; it is required unconditionally so a
    single call always fully resolves the state, matching the injected-randomness rule everywhere
    else in the engine. Raises :class:`~tricksy.games.texas42.errors.RulesError` on any rejection;
    the caller persists the resulting state only when this returns.
    """
    if state.phase is Phase.BIDDING:
        state = _apply_bidding_move(state, move)
    elif state.phase is Phase.DECLARING:
        state = _apply_declare_move(state, move)
    elif state.phase is Phase.PLAYING:
        state = _apply_play_move(state, move)
    else:
        raise IllegalMove(f"no moves are legal while the game is in {state.phase}")

    if state.phase is Phase.HAND_COMPLETE:
        state = _complete_hand(state, rng)
    return state


def legal_moves(state: GameState, player_id: PlayerId) -> tuple[Move, ...]:
    """Every move ``player_id`` may make right now; empty when it is not their turn."""
    if state.to_act is None or state.players.get(state.to_act) != player_id:
        return ()
    hand = state.hand
    assert hand is not None

    if state.phase is Phase.BIDDING:
        if hand.pending_bid is not None:
            return (
                ConfirmBid(actor=player_id, accept=True),
                ConfirmBid(actor=player_id, accept=False),
            )
        moves: list[Move] = []
        for bid in bidding.legal_bids(state):
            if bid.is_pass:
                moves.append(Pass(actor=player_id))
            else:
                moves.append(
                    PlaceBid(
                        actor=player_id, contract=bid.contract, points=bid.points, marks=bid.marks
                    )
                )
        return tuple(moves)

    if state.phase is Phase.DECLARING:
        return tuple(
            DeclareContract(actor=player_id, trump=trump) for trump in _legal_trumps(state.config)
        )

    if state.phase is Phase.PLAYING:
        assert hand.contract is not None
        contract = get_contract(hand.contract)
        playable = tricks.legal_plays(
            hand.hands[state.to_act], hand.current_trick, hand.trump, state.config, contract
        )
        leading = not hand.current_trick.plays
        play_moves: list[Move] = []
        for domino in playable:
            play_moves.append(PlayDomino(actor=player_id, domino=domino))
            if leading and tricks.declared_lead_permitted(state.config, hand):
                for suit in declarable_suits(domino, hand.trump, state.config):
                    play_moves.append(
                        PlayDomino(actor=player_id, domino=domino, declared_suit=suit)
                    )
        return tuple(play_moves)

    return ()


def _apply_bidding_move(state: GameState, move: Move) -> GameState:
    hand = state.hand
    assert hand is not None
    if hand.pending_bid is not None:
        if not isinstance(move, ConfirmBid):
            raise IllegalMove(f"{move.kind} is not legal while a bid awaits confirmation")
        return bidding.apply_confirmation(state, move)
    if isinstance(move, PlaceBid | Pass):
        return bidding.apply_bid(state, move)
    raise IllegalMove(f"{move.kind} is not legal during bidding")


def _apply_declare_move(state: GameState, move: Move) -> GameState:
    if not isinstance(move, DeclareContract):
        raise IllegalMove(f"{move.kind} is not legal while declaring")
    hand = state.hand
    assert hand is not None and hand.contract is not None
    contract = get_contract(hand.contract)

    declaring_seat = contract.declaring_seat(state)
    if seat_of(state, move.actor) != declaring_seat:
        raise OutOfTurn(f"{move.actor} is not the declaring seat")
    _validate_trump(move.trump, state.config)

    new_state = replace(state, hand=replace(hand, trump=move.trump))
    leader = contract.opening_leader(new_state)
    return replace(new_state, phase=Phase.PLAYING, to_act=leader)


def _apply_play_move(state: GameState, move: Move) -> GameState:
    if not isinstance(move, PlayDomino):
        raise IllegalMove(f"{move.kind} is not legal while playing")
    return tricks.play(state, move)


def _validate_trump(trump: Trump, config: HouseRules) -> None:
    if trump is None:
        raise IllegalMove("a trump suit must be declared")
    if trump is Suit.DOUBLES and not config.doubles_are_own_suit:
        raise IllegalMove("doubles cannot be trump unless doubles_are_own_suit is enabled")


def _legal_trumps(config: HouseRules) -> tuple[Suit, ...]:
    if config.doubles_are_own_suit:
        return (*NUMBER_SUITS, Suit.DOUBLES)
    return NUMBER_SUITS


def _complete_hand(state: GameState, rng: Random) -> GameState:
    hand = state.hand
    assert hand is not None and hand.contract is not None
    contract = get_contract(hand.contract)

    marks_won = contract.score_hand(state)
    new_marks = dict(state.marks)
    for team, amount in marks_won.items():
        new_marks[team] = new_marks.get(team, 0) + amount

    if any(total >= state.config.marks_to_win for total in new_marks.values()):
        return replace(state, marks=new_marks, phase=Phase.GAME_OVER, hand=None, to_act=None)

    next_dealer = Seat((hand.dealer + 1) % 4)
    next_hand = _deal_hand(state.config, next_dealer, rng)
    return replace(
        state,
        marks=new_marks,
        phase=Phase.BIDDING,
        hand=next_hand,
        to_act=Seat((next_dealer + 1) % 4),
    )


def _deal_hand(config: HouseRules, dealer: Seat, rng: Random) -> HandState:
    """One shuffle, then seven tiles per seat in ``Seat`` order.

    **That shape is depended on outside the engine.** ``tricksy.storage.replay`` reconstructs a game
    by handing a recorded deal back through ``rng.shuffle``, which works only because this deals
    with exactly one ``shuffle`` call per hand and slices the result in ``Seat`` order. Dealing any
    other way - two shuffles, tile-by-tile draws, a different seat order - would not raise anywhere;
    it would make replay silently rebuild a different game. Change that module in step with this
    one.
    """
    tiles = list(FULL_SET)
    rng.shuffle(tiles)
    hands = {seat: tuple(tiles[i * 7 : (i + 1) * 7]) for i, seat in enumerate(Seat)}
    return HandState(dealer=dealer, hands=hands)
