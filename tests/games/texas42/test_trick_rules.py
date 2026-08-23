from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from tricksy.games.texas42.dominoes import FULL_SET, Domino
from tricksy.games.texas42.house_rules import HouseRules
from tricksy.games.texas42.state import PlayedDomino, Seat, Trick
from tricksy.games.texas42.suits import NUMBER_SUITS, Suit
from tricksy.games.texas42.trick_rules import (
    follow_suit_plays,
    highest_trump_or_led_suit_wins,
    suit_led,
)

PLAIN = HouseRules()
DOUBLES_SUIT = HouseRules(doubles_are_own_suit=True)


def _trick(*plays: tuple[Seat, Domino], declared_suit: Suit | None = None) -> Trick:
    return Trick(
        plays=tuple(PlayedDomino(seat=seat, domino=domino) for seat, domino in plays),
        declared_suit=declared_suit,
    )


class TestSuitLed:
    def test_falls_back_to_led_suit_when_nothing_was_declared(self) -> None:
        trick = _trick((Seat.NORTH, Domino(3, 2)))
        assert suit_led(trick, None, PLAIN) is Suit.TREYS

    def test_returns_the_declaration_when_one_was_made(self) -> None:
        trick = _trick((Seat.NORTH, Domino(3, 2)), declared_suit=Suit.DEUCES)
        assert suit_led(trick, None, PLAIN) is Suit.DEUCES


class TestFollowSuitPlays:
    def test_leading_may_play_anything(self) -> None:
        hand = (Domino(6, 4), Domino(2, 1))
        assert follow_suit_plays(hand, Trick(), None, PLAIN) == hand

    def test_must_follow_the_led_suit_when_holding_it(self) -> None:
        trick = _trick((Seat.NORTH, Domino(6, 5)))  # leads sixes (no trump)
        hand = (Domino(6, 2), Domino(3, 1), Domino(5, 5))
        assert follow_suit_plays(hand, trick, None, PLAIN) == (Domino(6, 2),)

    def test_a_two_end_tile_follows_via_either_end(self) -> None:
        trick = _trick((Seat.NORTH, Domino(6, 5)))  # leads sixes
        hand = (Domino(6, 2), Domino(2, 1))
        # 6-2 follows because it holds a six, even though its other end (2) doesn't match.
        assert follow_suit_plays(hand, trick, None, PLAIN) == (Domino(6, 2),)

    def test_may_play_anything_when_not_holding_the_led_suit(self) -> None:
        trick = _trick((Seat.NORTH, Domino(6, 5)))
        hand = (Domino(3, 1), Domino(2, 0))
        assert follow_suit_plays(hand, trick, None, PLAIN) == hand

    def test_a_trump_in_hand_must_be_played_over_a_trump_lead(self) -> None:
        trick = _trick((Seat.NORTH, Domino(6, 4)))  # trump=sixes: this tile is trump
        hand = (Domino(6, 1), Domino(5, 3))
        assert follow_suit_plays(hand, trick, Suit.SIXES, PLAIN) == (Domino(6, 1),)

    def test_doubles_own_suit_variant_changes_what_follows(self) -> None:
        trick = _trick((Seat.NORTH, Domino(5, 5)))  # leads the doubles suit under the variant
        hand = (Domino(6, 6), Domino(5, 4), Domino(3, 1))
        # 5-4 contains a five but is not itself a double, so it does not follow doubles.
        assert follow_suit_plays(hand, trick, None, DOUBLES_SUIT) == (Domino(6, 6),)

    def test_a_declared_suit_changes_what_follows(self) -> None:
        # 6-5 defaults to leading sixes; declared as fives, a five-holder must follow instead.
        trick = _trick((Seat.NORTH, Domino(6, 5)), declared_suit=Suit.FIVES)
        hand = (Domino(6, 2), Domino(5, 1))
        assert follow_suit_plays(hand, trick, None, PLAIN) == (Domino(5, 1),)


class TestHighestTrumpOrLedSuitWins:
    def test_highest_of_the_led_suit_wins_with_no_trump(self) -> None:
        trick = _trick(
            (Seat.NORTH, Domino(6, 1)),
            (Seat.EAST, Domino(3, 2)),  # sloughed, doesn't follow sixes
            (Seat.SOUTH, Domino(6, 4)),
            (Seat.WEST, Domino(6, 0)),
        )
        assert highest_trump_or_led_suit_wins(trick, None, PLAIN) is Seat.SOUTH

    def test_a_lone_trump_beats_a_higher_off_suit_tile(self) -> None:
        trick = _trick(
            (Seat.NORTH, Domino(6, 4)),
            (Seat.EAST, Domino(5, 0)),  # trump (fives), even though low-ranked
            (Seat.SOUTH, Domino(6, 1)),
            (Seat.WEST, Domino(6, 2)),
        )
        assert highest_trump_or_led_suit_wins(trick, Suit.FIVES, PLAIN) is Seat.EAST

    def test_the_trump_double_wins_among_trumps(self) -> None:
        trick = _trick(
            (Seat.NORTH, Domino(5, 3)),
            (Seat.EAST, Domino(5, 5)),
            (Seat.SOUTH, Domino(5, 1)),
            (Seat.WEST, Domino(6, 2)),  # not trump, sloughed
        )
        assert highest_trump_or_led_suit_wins(trick, Suit.FIVES, PLAIN) is Seat.EAST

    def test_doubles_rank_low_reverses_who_wins(self) -> None:
        trick = _trick(
            (Seat.NORTH, Domino(5, 3)),
            (Seat.EAST, Domino(5, 5)),
        )
        assert highest_trump_or_led_suit_wins(trick, None, PLAIN, doubles_rank="high") is Seat.EAST
        assert highest_trump_or_led_suit_wins(trick, None, PLAIN, doubles_rank="low") is Seat.NORTH

    def test_a_declared_suit_can_change_the_winner(self) -> None:
        plays = (
            (Seat.NORTH, Domino(6, 5)),  # led
            (Seat.EAST, Domino(6, 0)),  # belongs to sixes only
            (Seat.SOUTH, Domino(5, 5)),  # belongs to fives only, and it's the double
            (Seat.WEST, Domino(4, 1)),  # belongs to neither, sloughed
        )
        default_trick = _trick(*plays)
        assert highest_trump_or_led_suit_wins(default_trick, None, PLAIN) is Seat.NORTH

        declared_trick = _trick(*plays, declared_suit=Suit.FIVES)
        assert highest_trump_or_led_suit_wins(declared_trick, None, PLAIN) is Seat.SOUTH

    @given(
        st.permutations(FULL_SET).map(lambda tiles: tiles[:4]),
        st.one_of(st.none(), st.sampled_from(NUMBER_SUITS)),
        st.booleans(),
    )
    def test_exactly_one_seat_wins_every_well_formed_trick(
        self, tiles: list[Domino], trump: Suit | None, doubles_are_own_suit: bool
    ) -> None:
        config = HouseRules(doubles_are_own_suit=doubles_are_own_suit)
        trick = _trick(*zip(Seat, tiles, strict=True))
        winner = highest_trump_or_led_suit_wins(trick, trump, config)
        assert winner in list(Seat)
