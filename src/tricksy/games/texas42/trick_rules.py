"""Generic trick-play algorithms shared by contract implementations.

Lives below both :mod:`tricksy.games.texas42.contracts` and :mod:`tricksy.games.texas42.tricks` in
the dependency graph, since :mod:`tricksy.games.texas42.tricks` needs to import
:mod:`tricksy.games.texas42.contracts` (to look up the active contract) and contracts need these
helpers - putting the helpers here means neither module has to import the other. Most contracts'
``legal_plays``/``trick_winner`` are exactly these functions; a contract only needs its own logic
where it genuinely differs (sevens' trick winner).
"""

from __future__ import annotations

from .dominoes import Domino
from .house_rules import HouseRules
from .state import Seat, Trick
from .suits import DoublesRank, Suit, Trump, belongs_to, follows, is_trump, led_suit, rank_in_suit


def suit_led(trick: Trick, trump: Trump, config: HouseRules) -> Suit:
    """The suit ``trick``'s lead establishes: the leader's declaration if they made one
    (DESIGN.md §5.2), else the default higher-end reading."""
    if trick.declared_suit is not None:
        return trick.declared_suit
    return led_suit(trick.plays[0].domino, trump, config)


def follow_suit_plays(
    hand: tuple[Domino, ...], trick: Trick, trump: Trump, config: HouseRules
) -> tuple[Domino, ...]:
    """The standard follow-suit rule: lead anything; otherwise the led suit if you hold it."""
    if not trick.plays:
        return hand
    led = suit_led(trick, trump, config)
    following = tuple(domino for domino in hand if follows(domino, led, trump, config))
    return following if following else hand


def highest_trump_or_led_suit_wins(
    trick: Trick, trump: Trump, config: HouseRules, *, doubles_rank: DoublesRank = "high"
) -> Seat:
    """The standard trick-winner rule: highest trump if any was played, else highest tile of the
    led suit. With ``trump=None`` this degenerates to "highest of the led suit", which is exactly
    the no-trump contracts' rule (nello, nello_low, sevens' follow-suit legality if not its
    winner)."""
    assert trick.plays, "trick_winner called on an empty trick"
    led = suit_led(trick, trump, config)

    trump_plays = [play for play in trick.plays if is_trump(play.domino, trump, config)]
    if trump_plays:
        assert trump is not None
        return max(
            trump_plays,
            key=lambda play: rank_in_suit(play.domino, trump, config, doubles_rank=doubles_rank),
        ).seat

    led_plays = [play for play in trick.plays if belongs_to(play.domino, led, config)]
    return max(
        led_plays,
        key=lambda play: rank_in_suit(play.domino, led, config, doubles_rank=doubles_rank),
    ).seat
