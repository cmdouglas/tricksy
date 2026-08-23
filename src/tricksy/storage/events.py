"""Translates accepted moves (and the deals they trigger) into the events that get appended to
the log (ROADMAP.md 1.2). ``event_for_move`` and ``hand_dealt_event`` are the write-direction
counterpart to ``replay``'s ``_move_for_event``: nothing in ``t42.engine`` constructs an
``Event`` itself, so this is where a move becomes the record of what happened.
"""

from __future__ import annotations

from t42.engine.events import (
    BidConfirmed,
    BidPlaced,
    ContractDeclared,
    DominoPlayed,
    Event,
    HandDealt,
    Passed,
)
from t42.engine.moves import ConfirmBid, DeclareContract, Move, Pass, PlaceBid, PlayDomino
from t42.engine.state import GameState, HandState, Seat


def event_for_move(state: GameState, move: Move) -> Event:
    """The event ``move`` produces, given the state it was applied to. ``state`` is only needed
    for ``DeclareContract``: ``ContractDeclared.contract`` isn't on the move, since the engine
    already knows it from the bidding that just resolved (``state.hand.contract``)."""
    if isinstance(move, PlaceBid):
        return BidPlaced(
            actor=move.actor, contract=move.contract, points=move.points, marks=move.marks
        )
    if isinstance(move, Pass):
        return Passed(actor=move.actor)
    if isinstance(move, ConfirmBid):
        return BidConfirmed(actor=move.actor, accept=move.accept)
    if isinstance(move, DeclareContract):
        hand = state.hand
        assert hand is not None and hand.contract is not None
        return ContractDeclared(actor=move.actor, contract=hand.contract, trump=move.trump)
    if isinstance(move, PlayDomino):
        return DominoPlayed(actor=move.actor, domino=move.domino, declared_suit=move.declared_suit)
    raise TypeError(f"unknown move type: {move!r}")


def hand_dealt_event(hand: HandState) -> HandDealt:
    return HandDealt(hands=tuple((seat, hand.hands[seat]) for seat in Seat), dealer=hand.dealer)


def events_for_move(
    state_before: GameState, move: Move, state_after: GameState
) -> tuple[Event, ...]:
    """``move``'s own event, plus a trailing ``HandDealt`` when it closed a hand and the game
    dealt straight into the next one - the automatic re-deal inside ``apply_move``'s
    ``_complete_hand`` that has no move of its own. Detected by the dealer changing: at
    ``GAME_OVER`` ``state_after.hand`` is ``None`` and nothing trails."""
    events: tuple[Event, ...] = (event_for_move(state_before, move),)
    dealer_before = state_before.hand.dealer if state_before.hand is not None else None
    hand_after = state_after.hand
    if hand_after is not None and hand_after.dealer != dealer_before:
        events += (hand_dealt_event(hand_after),)
    return events
