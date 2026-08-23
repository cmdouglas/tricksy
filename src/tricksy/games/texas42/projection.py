"""Player-specific views (DESIGN.md §4.2). The one place hidden-information rules live
(invariant 5): every client type consumes this output, so it must strip other players' hands and
stay plain JSON-able data with nothing CLI-specific (DESIGN.md §11).

``GameState`` turns out to hide very little - the auction, tricks, marks, declarer and contract
are all things every seat already knows at a real table. The only genuine secret is
``HandState.hands`` for every seat other than the caller, so ``project()`` is mostly the public
parts of ``state`` copied to plain data, with the caller's own hand substituted for the full
``hands`` mapping and ``legal_moves`` filled in when it's their turn.

The small encoders below deliberately don't share code with :mod:`t42.storage.codec`: invariant 1
forbids ``t42.engine`` importing from ``t42.storage``, and the two modules serve different
purposes anyway - ``codec`` is a durable DynamoDB wire format, this is a read-model for clients.
"""

from __future__ import annotations

from typing import Any

from .dominoes import Domino
from .game import legal_moves as _legal_moves
from .moves import ConfirmBid, DeclareContract, Move, Pass, PlaceBid, PlayDomino
from .state import GameState, PlayerId, Team, Trick, seat_of


def _encode_domino(domino: Domino) -> str:
    return str(domino)


def _encode_trick(trick: Trick) -> dict[str, Any]:
    return {
        "plays": [
            {"seat": play.seat.value, "domino": _encode_domino(play.domino)} for play in trick.plays
        ],
        "declared_suit": trick.declared_suit.value if trick.declared_suit is not None else None,
        "winner": trick.winner.value if trick.winner is not None else None,
    }


def _encode_move(move: Move) -> dict[str, Any]:
    if isinstance(move, PlaceBid):
        return {
            "kind": move.kind,
            "contract": move.contract,
            "points": move.points,
            "marks": move.marks,
        }
    if isinstance(move, Pass):
        return {"kind": move.kind}
    if isinstance(move, ConfirmBid):
        return {"kind": move.kind, "accept": move.accept}
    if isinstance(move, DeclareContract):
        return {"kind": move.kind, "trump": move.trump.value if move.trump is not None else None}
    if isinstance(move, PlayDomino):
        return {
            "kind": move.kind,
            "domino": _encode_domino(move.domino),
            "declared_suit": (move.declared_suit.value if move.declared_suit is not None else None),
        }
    raise TypeError(f"unknown move type: {move!r}")


def project(state: GameState, player_id: PlayerId) -> dict[str, Any]:
    """Render ``state`` as the view ``player_id`` is allowed to see.

    Includes their own hand, the current trick, trump, scores, whose turn it is, and their legal
    moves when it is their turn. Raises ``KeyError`` if ``player_id`` is not seated in this game,
    the same convention :func:`t42.engine.state.seat_of` uses elsewhere in the engine.
    """
    seat = seat_of(state, player_id)
    hand = state.hand

    return {
        "game_id": state.game_id,
        "phase": state.phase.value,
        "seat": seat.value,
        "dealer": hand.dealer.value if hand is not None else None,
        "to_act": state.to_act.value if state.to_act is not None else None,
        "marks": {
            "north_south": state.marks.get(Team.NORTH_SOUTH, 0),
            "east_west": state.marks.get(Team.EAST_WEST, 0),
        },
        "declarer": (
            hand.declarer.value if hand is not None and hand.declarer is not None else None
        ),
        "contract": hand.contract if hand is not None else None,
        "trump": hand.trump.value if hand is not None and hand.trump is not None else None,
        "hand": (
            [_encode_domino(domino) for domino in hand.hands[seat]] if hand is not None else None
        ),
        "current_trick": _encode_trick(hand.current_trick) if hand is not None else None,
        "completed_tricks": (
            [_encode_trick(trick) for trick in hand.completed_tricks] if hand is not None else []
        ),
        "legal_moves": [_encode_move(move) for move in _legal_moves(state, player_id)],
    }
