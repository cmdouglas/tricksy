"""Sevens: no trump; the tile closest to seven pips wins each trick, ties going to whichever tied
tile was played earliest (DESIGN.md §12). Like nello, this is an all-or-nothing marks contract -
the declaring side must take every trick to make the bid - but all four seats play; there is no
partner sit-out.
"""

from __future__ import annotations

from collections.abc import Mapping

from ..dominoes import Domino
from ..errors import IllegalMove, RulesError
from ..house_rules import HouseRules, OptionValue
from ..state import Bid, GameState, Seat, Team, Trick, other_team, team_of
from ..suits import Trump
from ..trick_rules import follow_suit_plays
from .registry import register

_OPTION_DEFAULTS: Mapping[str, OptionValue] = {"minimum_marks": 1}


class SevensContract:
    name = "sevens"

    def option_defaults(self) -> Mapping[str, OptionValue]:
        return _OPTION_DEFAULTS

    def validate_options(self, options: Mapping[str, OptionValue], rules: HouseRules) -> None:
        marks = options["minimum_marks"]
        if not isinstance(marks, int) or not 1 <= marks <= 7:
            raise RulesError(f"sevens.minimum_marks must be 1-7, got {marks!r}")

    def validate_bid(self, bid: Bid, hand: tuple[Domino, ...], config: HouseRules) -> None:
        minimum_marks = config.options_for("sevens", self.option_defaults())["minimum_marks"]
        assert isinstance(minimum_marks, int)
        if bid.marks is None or bid.marks < minimum_marks:
            raise IllegalMove(f"sevens must be bid at {minimum_marks}+ marks")

    def requires_partner_confirmation(self) -> bool:
        return False

    def requires_declaration(self) -> bool:
        return False

    def declaring_seat(self, state: GameState) -> Seat:
        raise AssertionError("sevens has no declaration step")

    def opening_leader(self, state: GameState) -> Seat:
        assert state.hand is not None and state.hand.declarer is not None
        return state.hand.declarer

    def sits_out(self, state: GameState) -> Seat | None:
        return None

    def legal_plays(
        self, hand: tuple[Domino, ...], trick: Trick, trump: Trump, config: HouseRules
    ) -> tuple[Domino, ...]:
        return follow_suit_plays(hand, trick, None, config)

    def trick_winner(self, trick: Trick, trump: Trump, config: HouseRules) -> Seat:
        """Closest to seven pips wins; a later play must strictly beat the standing winner."""
        assert trick.plays, "trick_winner called on an empty trick"
        best = trick.plays[0]
        best_distance = abs(7 - best.domino.pips)
        for play in trick.plays[1:]:
            distance = abs(7 - play.domino.pips)
            if distance < best_distance:
                best, best_distance = play, distance
        return best.seat

    def score_hand(self, state: GameState) -> dict[Team, int]:
        hand = state.hand
        assert hand is not None and hand.declarer is not None
        declarer = hand.declarer
        declaring_team = team_of(declarer)
        winning_bid = next(b for b in hand.bids if b.bidder == declarer and not b.is_pass)
        assert winning_bid.marks is not None

        swept = all(
            trick.winner is not None and team_of(trick.winner) == declaring_team
            for trick in hand.completed_tricks
        )
        if swept:
            return {declaring_team: winning_bid.marks}
        return {other_team(declaring_team): winning_bid.marks}


register(SevensContract())
