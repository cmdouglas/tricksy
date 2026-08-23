"""Shared plunge/splash mechanics: the bidder's partner (not the bidder) names trump and leads
the first trick, after the bidder shows enough doubles to support a mostly-doubles hand. Trick
play proceeds exactly like standard once trump is set. The declaring side must take every point
(all 42) to make the bid - anything less means the bid is set. The two contracts differ only in
their doubles/marks minimum and whether the bid needs the partner's confirmation (plunge only) -
see DESIGN.md §12.
"""

from __future__ import annotations

from collections.abc import Mapping

from ..dominoes import Domino
from ..errors import IllegalMove, RulesError
from ..house_rules import HouseRules, OptionValue
from ..scoring import MAX_HAND_POINTS, POINT_PER_TRICK, count_of
from ..state import Bid, GameState, Seat, Team, Trick, other_team, partner_of, team_of
from ..suits import Trump
from ..trick_rules import follow_suit_plays, highest_trump_or_led_suit_wins
from .registry import get


class PartnerDeclaresBase:
    """Not itself registered - ``plunge.py`` and ``splash.py`` set the option defaults and
    confirmation requirement."""

    name: str
    _option_defaults: Mapping[str, OptionValue]
    _requires_confirmation: bool

    def option_defaults(self) -> Mapping[str, OptionValue]:
        return self._option_defaults

    def validate_options(self, options: Mapping[str, OptionValue], rules: HouseRules) -> None:
        doubles = options["minimum_doubles"]
        marks = options["minimum_marks"]
        if not isinstance(doubles, int) or not 0 <= doubles <= 7:
            raise RulesError(f"{self.name}.minimum_doubles must be 0-7, got {doubles!r}")
        if not isinstance(marks, int) or not 1 <= marks <= 7:
            raise RulesError(f"{self.name}.minimum_marks must be 1-7, got {marks!r}")
        if not self._requires_confirmation:
            return
        # Coherence (DESIGN.md §5.1 tier 3): a contract requiring partner confirmation is by
        # definition the heavier of the two, so it must be at least as hard as any sibling
        # partner-declares contract that does not require confirmation. Generic over
        # PartnerDeclaresBase rather than either contract's own name (invariant 4).
        for other_name in sorted(rules.enabled_contracts - {self.name}):
            other = get(other_name)
            if not isinstance(other, PartnerDeclaresBase) or other._requires_confirmation:
                continue
            other_options = rules.options_for(other_name, other.option_defaults())
            other_doubles = other_options["minimum_doubles"]
            other_marks = other_options["minimum_marks"]
            assert isinstance(other_doubles, int) and isinstance(other_marks, int)
            if doubles < other_doubles or marks < other_marks:
                raise RulesError(
                    f"{self.name} (doubles>={doubles}, marks>={marks}) must be at least as hard "
                    f"as {other_name} (doubles>={other_doubles}, marks>={other_marks})"
                )

    def validate_bid(self, bid: Bid, hand: tuple[Domino, ...], config: HouseRules) -> None:
        options = config.options_for(self.name, self.option_defaults())
        minimum_marks = options["minimum_marks"]
        minimum_doubles = options["minimum_doubles"]
        assert isinstance(minimum_marks, int) and isinstance(minimum_doubles, int)
        if bid.marks is None or bid.marks < minimum_marks:
            raise IllegalMove(f"{self.name} must be bid at {minimum_marks}+ marks")
        doubles_held = sum(1 for domino in hand if domino.is_double)
        if doubles_held < minimum_doubles:
            raise IllegalMove(
                f"{self.name} requires holding at least {minimum_doubles} doubles, "
                f"got {doubles_held}"
            )

    def requires_partner_confirmation(self) -> bool:
        return self._requires_confirmation

    def requires_declaration(self) -> bool:
        return True

    def declaring_seat(self, state: GameState) -> Seat:
        assert state.hand is not None and state.hand.declarer is not None
        return partner_of(state.hand.declarer)

    def opening_leader(self, state: GameState) -> Seat:
        assert state.hand is not None and state.hand.declarer is not None
        return partner_of(state.hand.declarer)

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
        assert winning_bid.marks is not None

        declarer_points = sum(
            count_of(tuple(play.domino for play in trick.plays)) + POINT_PER_TRICK
            for trick in hand.completed_tricks
            if trick.winner is not None and team_of(trick.winner) == declaring_team
        )
        if declarer_points == MAX_HAND_POINTS:
            return {declaring_team: winning_bid.marks}
        return {other_team(declaring_team): winning_bid.marks}
