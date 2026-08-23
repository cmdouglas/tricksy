"""Bidding state machine (DESIGN.md §5).

Turn order is a single pass around the table starting at the dealer's left: each seat bids or
passes exactly once, except the dealer may not pass if the other three already have (see
``legal_bids``) - so the auction always ends with at least one real bid, never four passes.

Bids strictly increase: any mark bid outranks any point bid, and within a tier the higher
points/marks wins (see ``_rank``). A mark bid for a contract requiring partner confirmation
(plunge) does not enter ``hand.bids`` until the partner accepts; declined, it leaves no trace in
the auction and the proposer acts again on the same turn - but the proposal and the decline are
still public events once persisted (Phase 1), never a private channel.
"""

from __future__ import annotations

from dataclasses import replace

from .contracts import get_enabled as get_enabled_contract
from .errors import IllegalMove, OutOfTurn
from .house_rules import STANDARD_CONTRACT
from .moves import ConfirmBid, Pass, PlaceBid
from .scoring import MAX_HAND_POINTS, MINIMUM_BID
from .state import Bid, GameState, PendingBid, Phase, Seat, partner_of, seat_of

_MAX_MARKS = 7


def legal_bids(state: GameState) -> tuple[Bid, ...]:
    """Every bid the player to act may legally make, for client display and validation."""
    hand = state.hand
    assert hand is not None and state.to_act is not None
    actor = state.to_act
    high = _highest_rank(hand.bids)

    options: list[Bid] = []
    for points in range(MINIMUM_BID, MAX_HAND_POINTS + 1):
        candidate = Bid(bidder=actor, points=points)
        if _rank(candidate) > high:
            options.append(candidate)

    for name in sorted(state.config.enabled_contracts):
        contract = get_enabled_contract(name, state.config)
        for marks in range(1, _MAX_MARKS + 1):
            candidate = Bid(bidder=actor, contract=name, marks=marks)
            if _rank(candidate) <= high:
                continue
            try:
                contract.validate_bid(candidate, hand.hands[actor], state.config)
            except IllegalMove:
                continue
            options.append(candidate)

    if not _dealer_must_bid(state):
        options.append(Bid(bidder=actor))
    return tuple(options)


def apply_bid(state: GameState, move: PlaceBid | Pass) -> GameState:
    """Validate and apply a bid or pass, advancing the auction.

    Raises :class:`~tricksy.games.texas42.errors.OutOfTurn` or
    :class:`~tricksy.games.texas42.errors.IllegalMove`.
    """
    hand = state.hand
    assert hand is not None and state.to_act is not None
    actor_seat = seat_of(state, move.actor)
    if actor_seat != state.to_act:
        raise OutOfTurn(f"{move.actor} is not the seat to act")

    candidate = (
        Bid(bidder=actor_seat)
        if isinstance(move, Pass)
        else Bid(bidder=actor_seat, contract=move.contract, points=move.points, marks=move.marks)
    )
    if candidate not in legal_bids(state):
        raise IllegalMove(f"{candidate} is not a legal bid")

    if not candidate.is_pass and candidate.contract is not None:
        contract = get_enabled_contract(candidate.contract, state.config)
        if contract.requires_partner_confirmation():
            assert candidate.marks is not None
            pending = PendingBid(
                bidder=actor_seat, contract=candidate.contract, marks=candidate.marks
            )
            new_hand = replace(hand, pending_bid=pending)
            return replace(state, hand=new_hand, to_act=partner_of(actor_seat))

    new_hand = replace(hand, bids=(*hand.bids, candidate))
    new_state = replace(state, hand=new_hand)
    return _settle_or_advance(new_state, actor_seat)


def apply_confirmation(state: GameState, move: ConfirmBid) -> GameState:
    """Apply the partner's public accept/decline of a pending plunge proposal."""
    hand = state.hand
    assert hand is not None and hand.pending_bid is not None
    pending = hand.pending_bid
    if seat_of(state, move.actor) != state.to_act:
        raise OutOfTurn(f"{move.actor} is not the seat awaiting confirmation")

    if not move.accept:
        new_hand = replace(hand, pending_bid=None)
        return replace(state, hand=new_hand, to_act=pending.bidder)

    confirmed = Bid(bidder=pending.bidder, contract=pending.contract, marks=pending.marks)
    new_hand = replace(hand, bids=(*hand.bids, confirmed), pending_bid=None)
    new_state = replace(state, hand=new_hand)
    return _settle_or_advance(new_state, pending.bidder)


def auction_is_settled(state: GameState) -> bool:
    """Whether every seat has acted and a declarer can be resolved."""
    hand = state.hand
    assert hand is not None
    return len(hand.bids) == len(Seat)


def resolve_auction(state: GameState) -> GameState:
    """Close the auction: set declarer, winning bid and contract, and open declaring or play."""
    hand = state.hand
    assert hand is not None
    real_bids = [b for b in hand.bids if not b.is_pass]
    assert real_bids, "no bids to resolve - the dealer-must-bid rule makes this unreachable"

    winning = max(real_bids, key=_rank)
    contract_name = winning.contract if winning.contract is not None else STANDARD_CONTRACT
    contract = get_enabled_contract(contract_name, state.config)

    new_state = replace(state, hand=replace(hand, declarer=winning.bidder, contract=contract_name))
    if contract.requires_declaration():
        return replace(new_state, phase=Phase.DECLARING, to_act=contract.declaring_seat(new_state))
    return replace(new_state, phase=Phase.PLAYING, to_act=contract.opening_leader(new_state))


def _settle_or_advance(state: GameState, actor_seat: Seat) -> GameState:
    if auction_is_settled(state):
        return resolve_auction(state)
    return replace(state, to_act=Seat((actor_seat + 1) % 4))


def _rank(bid: Bid) -> tuple[int, int]:
    if bid.marks is not None:
        return (1, bid.marks)
    assert bid.points is not None
    return (0, bid.points)


def _highest_rank(bids: tuple[Bid, ...]) -> tuple[int, int]:
    real_bids = [b for b in bids if not b.is_pass]
    if not real_bids:
        return (0, MINIMUM_BID - 1)
    return max(_rank(b) for b in real_bids)


def _dealer_must_bid(state: GameState) -> bool:
    hand = state.hand
    assert hand is not None
    return state.to_act == hand.dealer and len(hand.bids) == 3 and all(b.is_pass for b in hand.bids)
