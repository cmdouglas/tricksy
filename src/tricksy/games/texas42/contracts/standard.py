"""Standard point contract: bid 30-42, name trump, take at least the bid.

Points only in this design (DESIGN.md §2) - marks are reserved for the special contracts, so a
mark-form bid for "standard" is always rejected.
"""

from __future__ import annotations

from collections.abc import Mapping

from ..dominoes import Domino
from ..errors import IllegalMove
from ..house_rules import HouseRules, OptionValue
from ..scoring import POINT_PER_TRICK, count_of
from ..state import Bid, GameState, Seat, Team, Trick, other_team, team_of
from ..suits import Trump
from ..trick_rules import follow_suit_plays, highest_trump_or_led_suit_wins
from .registry import register


class StandardContract:
    name = "standard"

    def validate_bid(self, bid: Bid, hand: tuple[Domino, ...], config: HouseRules) -> None:
        raise IllegalMove("standard is a points bid (30-42), not a mark bid")

    def option_defaults(self) -> Mapping[str, OptionValue]:
        return {}

    def validate_options(self, options: Mapping[str, OptionValue], rules: HouseRules) -> None:
        return None

    def requires_partner_confirmation(self) -> bool:
        return False

    def requires_declaration(self) -> bool:
        return True

    def declaring_seat(self, state: GameState) -> Seat:
        assert state.hand is not None and state.hand.declarer is not None
        return state.hand.declarer

    def opening_leader(self, state: GameState) -> Seat:
        assert state.hand is not None and state.hand.declarer is not None
        return state.hand.declarer

    def sits_out(self, state: GameState) -> Seat | None:
        return None

    def legal_plays(
        self, hand: tuple[Domino, ...], trick: Trick, trump: Trump, config: HouseRules
    ) -> tuple[Domino, ...]:
        return follow_suit_plays(hand, trick, trump, config)

    def trick_winner(self, trick: Trick, trump: Trump, config: HouseRules) -> Seat:
        return highest_trump_or_led_suit_wins(trick, trump, config)

    def score_hand(self, state: GameState) -> dict[Team, int]:
        hand = state.hand
        assert hand is not None and hand.declarer is not None
        declarer = hand.declarer
        declaring_team = team_of(declarer)
        winning_bid = next(b for b in hand.bids if b.bidder == declarer and not b.is_pass)
        assert winning_bid.points is not None

        declarer_points = sum(
            count_of(tuple(play.domino for play in trick.plays)) + POINT_PER_TRICK
            for trick in hand.completed_tricks
            if trick.winner is not None and team_of(trick.winner) == declaring_team
        )
        if declarer_points >= winning_bid.points:
            return {declaring_team: 1}
        return {other_team(declaring_team): 1}


register(StandardContract())
