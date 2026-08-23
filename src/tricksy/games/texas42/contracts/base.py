"""The contract strategy interface (DESIGN.md §5, "Contract strategies").

Everything that differs between standard play, nello, plunge, sevens and splash sits behind this
protocol, so the trick engine and bidding machine never branch on contract name.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from ..dominoes import Domino
from ..house_rules import HouseRules, OptionValue
from ..state import Bid, GameState, Seat, Team, Trick
from ..suits import Trump


@runtime_checkable
class Contract(Protocol):
    """A contract's rules for leading, trump, legal plays and scoring."""

    #: Registry key, and the value stored in events and state.
    name: str

    def validate_bid(self, bid: Bid, hand: tuple[Domino, ...], config: HouseRules) -> None:
        """Reject bids this contract cannot be bid at, given the bidder's hand. Raises
        ``IllegalMove`` (doubles-in-hand requirements for plunge and splash live here)."""
        ...

    def option_defaults(self) -> Mapping[str, OptionValue]:
        """The option keys this contract accepts, with the values used when the house does not
        override them (DESIGN.md §5.1). Empty for a contract that takes no options."""
        ...

    def validate_options(self, options: Mapping[str, OptionValue], rules: HouseRules) -> None:
        """This contract's own checks on ``options`` (already merged over ``option_defaults()``)
        against the rest of ``rules``. Raises ``RulesError`` for a range or coherence violation
        (DESIGN.md §5.1's "satisfiable" and "coherent" tiers)."""
        ...

    def requires_partner_confirmation(self) -> bool:
        """Whether the bidder's partner must explicitly accept this bid before it goes live
        (plunge). The propose/accept-or-decline exchange is always public regardless."""
        ...

    def requires_declaration(self) -> bool:
        """Whether the declaring seat must declare something (trump, sit-out) before play."""
        ...

    def declaring_seat(self, state: GameState) -> Seat:
        """Who acts during ``Phase.DECLARING``: the bidder, except for plunge/splash where it is
        the bidder's partner."""
        ...

    def opening_leader(self, state: GameState) -> Seat:
        """Who leads the first trick."""
        ...

    def sits_out(self, state: GameState) -> Seat | None:
        """The seat that sits the hand out, if the contract dictates one (nello)."""
        ...

    def legal_plays(
        self, hand: tuple[Domino, ...], trick: Trick, trump: Trump, config: HouseRules
    ) -> tuple[Domino, ...]:
        """Legal plays under this contract, tightening or replacing the default follow-suit rule."""
        ...

    def trick_winner(self, trick: Trick, trump: Trump, config: HouseRules) -> Seat:
        """Winner of a completed trick under this contract (sevens ranks by pip distance from 7)."""
        ...

    def score_hand(self, state: GameState) -> dict[Team, int]:
        """Marks won by each team when the hand ends."""
        ...
