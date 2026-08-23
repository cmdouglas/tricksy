"""Rebuilds a ``GameState`` from its event log (ROADMAP.md 1.2), replaying through the same
``new_game``/``apply_move`` path live play uses so replay and play cannot diverge.

The one wrinkle: a ``HandDealt`` event records the *result* of a deal, not an RNG seed (deals must
survive rule changes to the shuffle itself), but ``new_game``/``apply_move`` both deal by calling
``rng.shuffle()``. ``_ReplayRandom`` below is a ``Random`` whose ``shuffle`` deterministically
replays the recorded deals instead of randomizing, so the real dealing code runs unmodified.

**This is a contract with the engine's dealer, and it is not enforceable by types.** Replay is
correct only while :func:`tricksy.games.texas42.game._deal_hand` calls ``rng.shuffle`` **exactly
once per hand** and then slices the shuffled deck in ``Seat`` order - the shape :func:`_deal_order`
writes the recorded deal back into. A dealer that shuffled twice, drew tile by tile, or dealt in a
different seat order would not fail here; it would silently replay a *different* game. Anything that
changes how dealing works has to change ``_deal_order`` with it. See DESIGN.md §11.1.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, MutableSequence, Sequence
from random import Random
from typing import Any

from tricksy.games.texas42.dominoes import Domino
from tricksy.games.texas42.events import (
    BidConfirmed,
    BidPlaced,
    ContractDeclared,
    DominoPlayed,
    Event,
    HandDealt,
    Passed,
)
from tricksy.games.texas42.game import apply_move, new_game
from tricksy.games.texas42.house_rules import HouseRules
from tricksy.games.texas42.moves import (
    ConfirmBid,
    DeclareContract,
    Move,
    Pass,
    PlaceBid,
    PlayDomino,
)
from tricksy.games.texas42.state import GameId, GameState, PlayerId, Seat


class _ReplayRandom(Random):
    """Feeds ``_deal_hand``'s ``rng.shuffle(tiles)`` calls the recorded deals in order, instead of
    actually randomizing - the trick that lets replay reuse the real dealing code path."""

    def __init__(self, deals: Iterator[list[Domino]]) -> None:
        super().__init__()
        self._deals = deals

    def shuffle(self, x: MutableSequence[Any]) -> None:
        x[:] = next(self._deals)


def _deal_order(event: HandDealt) -> list[Domino]:
    """The flat, Seat-order tile list ``_deal_hand`` expects after "shuffling": ``Seat``'s values
    run 0-3, so ``enumerate(Seat)`` - what ``_deal_hand`` slices by - visits North, East, South,
    West in exactly this order."""
    by_seat = dict(event.hands)
    return [domino for seat in Seat for domino in by_seat[seat]]


def _move_for_event(event: Event) -> Move:
    if isinstance(event, BidPlaced):
        return PlaceBid(
            actor=event.actor, contract=event.contract, points=event.points, marks=event.marks
        )
    if isinstance(event, Passed):
        return Pass(actor=event.actor)
    if isinstance(event, BidConfirmed):
        return ConfirmBid(actor=event.actor, accept=event.accept)
    if isinstance(event, ContractDeclared):
        return DeclareContract(actor=event.actor, trump=event.trump)
    if isinstance(event, DominoPlayed):
        return PlayDomino(actor=event.actor, domino=event.domino, declared_suit=event.declared_suit)
    raise TypeError(f"{event!r} is not a move-driven event")


def replay(
    game_id: GameId,
    players: Mapping[Seat, PlayerId],
    config: HouseRules,
    events: Sequence[Event],
) -> GameState:
    """Reconstructs the ``GameState`` that results from ``events``. ``game_id``, ``players`` and
    ``config`` come from the ``META`` item (DESIGN.md §4.1), not the event log itself, so they're
    required here rather than derived from ``events``.
    """
    if not events or not isinstance(events[0], HandDealt):
        raise ValueError("an event log must open with a HAND_DEALT event")

    deals = (_deal_order(event) for event in events if isinstance(event, HandDealt))
    rng = _ReplayRandom(deals)
    state = new_game(game_id, players, config, rng=rng)

    for event in events[1:]:
        if isinstance(event, HandDealt):
            continue
        state = apply_move(state, _move_for_event(event), rng=rng)
    return state
