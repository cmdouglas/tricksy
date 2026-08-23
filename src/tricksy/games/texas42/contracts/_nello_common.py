"""Shared nello/nello_low mechanics.

Both: declarer's partner sits out (hand played 3-handed), no trump, declarer leads the first
trick, declarer's side makes the bid only by losing every trick. The two contracts differ only in
how a double ranks - each subclass fixes its own ``_doubles_are_own_suit``/``_doubles_rank``,
independent of the game's ``HouseRules.doubles_are_own_suit`` (DESIGN.md §12).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from ..dominoes import Domino
from ..errors import IllegalMove, RulesError
from ..house_rules import HouseRules, OptionValue
from ..state import Bid, GameState, Seat, Team, Trick, other_team, partner_of, team_of
from ..suits import DoublesRank, Trump
from ..trick_rules import follow_suit_plays, highest_trump_or_led_suit_wins

_OPTION_DEFAULTS: Mapping[str, OptionValue] = {"minimum_marks": 1}


class NelloBase:
    """Not itself registered - ``nello.py`` and ``nello_low.py`` provide the two doubles modes."""

    name: str
    _doubles_are_own_suit: bool
    _doubles_rank: DoublesRank

    def _suit_config(self, config: HouseRules) -> HouseRules:
        return replace(config, doubles_are_own_suit=self._doubles_are_own_suit)

    def option_defaults(self) -> Mapping[str, OptionValue]:
        return _OPTION_DEFAULTS

    def validate_options(self, options: Mapping[str, OptionValue], rules: HouseRules) -> None:
        marks = options["minimum_marks"]
        if not isinstance(marks, int) or not 1 <= marks <= 7:
            raise RulesError(f"{self.name}.minimum_marks must be 1-7, got {marks!r}")

    def validate_bid(self, bid: Bid, hand: tuple[Domino, ...], config: HouseRules) -> None:
        minimum_marks = config.options_for(self.name, self.option_defaults())["minimum_marks"]
        assert isinstance(minimum_marks, int)
        if bid.marks is None or bid.marks < minimum_marks:
            raise IllegalMove(f"{self.name} must be bid at {minimum_marks}+ marks")

    def requires_partner_confirmation(self) -> bool:
        return False

    def requires_declaration(self) -> bool:
        return False

    def declaring_seat(self, state: GameState) -> Seat:
        raise AssertionError(f"{self.name} has no declaration step")

    def opening_leader(self, state: GameState) -> Seat:
        assert state.hand is not None and state.hand.declarer is not None
        return state.hand.declarer

    def sits_out(self, state: GameState) -> Seat | None:
        assert state.hand is not None and state.hand.declarer is not None
        return partner_of(state.hand.declarer)

    def legal_plays(
        self, hand: tuple[Domino, ...], trick: Trick, trump: Trump, config: HouseRules
    ) -> tuple[Domino, ...]:
        return follow_suit_plays(hand, trick, None, self._suit_config(config))

    def trick_winner(self, trick: Trick, trump: Trump, config: HouseRules) -> Seat:
        return highest_trump_or_led_suit_wins(
            trick, None, self._suit_config(config), doubles_rank=self._doubles_rank
        )

    def score_hand(self, state: GameState) -> dict[Team, int]:
        hand = state.hand
        assert hand is not None and hand.declarer is not None
        declarer = hand.declarer
        declaring_team = team_of(declarer)
        winning_bid = next(b for b in hand.bids if b.bidder == declarer and not b.is_pass)
        assert winning_bid.marks is not None

        declarer_took_a_trick = any(
            trick.winner is not None and team_of(trick.winner) == declaring_team
            for trick in hand.completed_tricks
        )
        if not declarer_took_a_trick:
            return {declaring_team: winning_bid.marks}
        return {other_team(declaring_team): winning_bid.marks}
