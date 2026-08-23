"""Codec between engine dataclasses and plain DynamoDB attribute maps (DESIGN.md §4.1).

Encodes ``GameState``, ``HouseRules`` and every ``Event`` to the native Python shapes boto3's
resource-level ``Table`` API accepts directly (``dict``/``list``/``str``/``int``/``bool``/``None``)
and back. Kept separate from any repository or boto3 dependency so it is testable with no
database (ROADMAP.md 1.1). Engine dataclasses are the wire format's source of truth - adding a
field there is a data migration, and this module is where the compatibility shim would live.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from t42.engine.dominoes import Domino, parse
from t42.engine.events import (
    BidConfirmed,
    BidPlaced,
    ContractDeclared,
    DominoPlayed,
    Event,
    HandDealt,
    Passed,
)
from t42.engine.house_rules import HouseRules
from t42.engine.state import (
    Bid,
    GameState,
    HandState,
    PendingBid,
    Phase,
    PlayedDomino,
    Seat,
    Team,
    Trick,
)
from t42.engine.suits import Suit

#: A plain, JSON-shaped attribute map - the boundary this module exists to draw. ``Any`` is
#: deliberate here: everywhere else in the engine trades it for real types, but this is the wire
#: format itself.
type AttributeMap = dict[str, Any]


def _encode_domino(domino: Domino) -> str:
    return str(domino)


def _decode_domino(data: str) -> Domino:
    return parse(data)


def _encode_bid(bid: Bid) -> AttributeMap:
    return {
        "bidder": bid.bidder.value,
        "contract": bid.contract,
        "points": bid.points,
        "marks": bid.marks,
    }


def _decode_bid(data: Mapping[str, Any]) -> Bid:
    return Bid(
        bidder=Seat(data["bidder"]),
        contract=data["contract"],
        points=data["points"],
        marks=data["marks"],
    )


def _encode_played_domino(played: PlayedDomino) -> AttributeMap:
    return {"seat": played.seat.value, "domino": _encode_domino(played.domino)}


def _decode_played_domino(data: Mapping[str, Any]) -> PlayedDomino:
    return PlayedDomino(seat=Seat(data["seat"]), domino=_decode_domino(data["domino"]))


def _encode_trick(trick: Trick) -> AttributeMap:
    return {
        "plays": [_encode_played_domino(play) for play in trick.plays],
        "declared_suit": trick.declared_suit.value if trick.declared_suit is not None else None,
        "winner": trick.winner.value if trick.winner is not None else None,
    }


def _decode_trick(data: Mapping[str, Any]) -> Trick:
    declared_suit = data["declared_suit"]
    winner = data["winner"]
    return Trick(
        plays=tuple(_decode_played_domino(play) for play in data["plays"]),
        declared_suit=Suit(declared_suit) if declared_suit is not None else None,
        winner=Seat(winner) if winner is not None else None,
    )


def _encode_pending_bid(pending: PendingBid) -> AttributeMap:
    return {"bidder": pending.bidder.value, "contract": pending.contract, "marks": pending.marks}


def _decode_pending_bid(data: Mapping[str, Any]) -> PendingBid:
    return PendingBid(bidder=Seat(data["bidder"]), contract=data["contract"], marks=data["marks"])


def _encode_hand_state(hand: HandState) -> AttributeMap:
    return {
        "dealer": hand.dealer.value,
        "hands": {
            str(seat.value): [_encode_domino(domino) for domino in dominoes]
            for seat, dominoes in hand.hands.items()
        },
        "bids": [_encode_bid(bid) for bid in hand.bids],
        "pending_bid": (
            _encode_pending_bid(hand.pending_bid) if hand.pending_bid is not None else None
        ),
        "declarer": hand.declarer.value if hand.declarer is not None else None,
        "contract": hand.contract,
        "trump": hand.trump.value if hand.trump is not None else None,
        "completed_tricks": [_encode_trick(trick) for trick in hand.completed_tricks],
        "current_trick": _encode_trick(hand.current_trick),
    }


def _decode_hand_state(data: Mapping[str, Any]) -> HandState:
    pending_bid = data["pending_bid"]
    declarer = data["declarer"]
    trump = data["trump"]
    return HandState(
        dealer=Seat(data["dealer"]),
        hands={
            Seat(int(seat_key)): tuple(_decode_domino(domino) for domino in dominoes)
            for seat_key, dominoes in data["hands"].items()
        },
        bids=tuple(_decode_bid(bid) for bid in data["bids"]),
        pending_bid=_decode_pending_bid(pending_bid) if pending_bid is not None else None,
        declarer=Seat(declarer) if declarer is not None else None,
        contract=data["contract"],
        trump=Suit(trump) if trump is not None else None,
        completed_tricks=tuple(_decode_trick(trick) for trick in data["completed_tricks"]),
        current_trick=_decode_trick(data["current_trick"]),
    )


def encode_house_rules(rules: HouseRules) -> AttributeMap:
    return {
        "enabled_contracts": sorted(rules.enabled_contracts),
        "contract_options": {
            name: dict(options) for name, options in rules.contract_options.items()
        },
        "doubles_are_own_suit": rules.doubles_are_own_suit,
        "allow_declared_lead": rules.allow_declared_lead,
        "marks_to_win": rules.marks_to_win,
    }


def decode_house_rules(data: Mapping[str, Any]) -> HouseRules:
    return HouseRules(
        enabled_contracts=frozenset(data["enabled_contracts"]),
        contract_options=data["contract_options"],
        doubles_are_own_suit=data["doubles_are_own_suit"],
        allow_declared_lead=data["allow_declared_lead"],
        marks_to_win=data["marks_to_win"],
    )


def encode_game_state(state: GameState) -> AttributeMap:
    return {
        "game_id": state.game_id,
        "config": encode_house_rules(state.config),
        "players": {str(seat.value): player_id for seat, player_id in state.players.items()},
        "phase": state.phase.value,
        "marks": {str(team.value): total for team, total in state.marks.items()},
        "hand": _encode_hand_state(state.hand) if state.hand is not None else None,
        "to_act": state.to_act.value if state.to_act is not None else None,
    }


def decode_game_state(data: Mapping[str, Any]) -> GameState:
    hand = data["hand"]
    to_act = data["to_act"]
    return GameState(
        game_id=data["game_id"],
        config=decode_house_rules(data["config"]),
        players={Seat(int(key)): value for key, value in data["players"].items()},
        phase=Phase(data["phase"]),
        marks={Team(int(key)): value for key, value in data["marks"].items()},
        hand=_decode_hand_state(hand) if hand is not None else None,
        to_act=Seat(to_act) if to_act is not None else None,
    )


def encode_event(event: Event) -> AttributeMap:
    if isinstance(event, HandDealt):
        return {
            "type": event.type,
            "hands": [
                [seat.value, [_encode_domino(domino) for domino in dominoes]]
                for seat, dominoes in event.hands
            ],
            "dealer": event.dealer.value,
        }
    if isinstance(event, BidPlaced):
        return {
            "type": event.type,
            "actor": event.actor,
            "contract": event.contract,
            "points": event.points,
            "marks": event.marks,
        }
    if isinstance(event, Passed):
        return {"type": event.type, "actor": event.actor}
    if isinstance(event, BidConfirmed):
        return {"type": event.type, "actor": event.actor, "accept": event.accept}
    if isinstance(event, ContractDeclared):
        return {
            "type": event.type,
            "actor": event.actor,
            "contract": event.contract,
            "trump": event.trump.value if event.trump is not None else None,
        }
    if isinstance(event, DominoPlayed):
        return {
            "type": event.type,
            "actor": event.actor,
            "domino": _encode_domino(event.domino),
            "declared_suit": (
                event.declared_suit.value if event.declared_suit is not None else None
            ),
        }
    raise TypeError(f"unknown event type: {event!r}")


def decode_event(data: Mapping[str, Any]) -> Event:
    kind = data["type"]
    if kind == "HAND_DEALT":
        return HandDealt(
            hands=tuple(
                (Seat(seat), tuple(_decode_domino(domino) for domino in dominoes))
                for seat, dominoes in data["hands"]
            ),
            dealer=Seat(data["dealer"]),
        )
    if kind == "BID":
        return BidPlaced(
            actor=data["actor"],
            contract=data["contract"],
            points=data["points"],
            marks=data["marks"],
        )
    if kind == "PASS":
        return Passed(actor=data["actor"])
    if kind == "BID_CONFIRMED":
        return BidConfirmed(actor=data["actor"], accept=data["accept"])
    if kind == "CONTRACT_DECLARED":
        trump = data["trump"]
        return ContractDeclared(
            actor=data["actor"],
            contract=data["contract"],
            trump=Suit(trump) if trump is not None else None,
        )
    if kind == "PLAY_DOMINO":
        declared_suit = data["declared_suit"]
        return DominoPlayed(
            actor=data["actor"],
            domino=_decode_domino(data["domino"]),
            declared_suit=Suit(declared_suit) if declared_suit is not None else None,
        )
    raise ValueError(f"unknown event type: {kind!r}")
