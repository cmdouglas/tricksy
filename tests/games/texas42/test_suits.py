from __future__ import annotations

import pytest

from t42.engine.dominoes import FULL_SET, Domino
from t42.engine.house_rules import HouseRules
from t42.engine.suits import (
    NUMBER_SUITS,
    Suit,
    belongs_to,
    declarable_suits,
    follows,
    is_trump,
    led_suit,
    rank_in_suit,
)

PLAIN = HouseRules()
DOUBLES_SUIT = HouseRules(doubles_are_own_suit=True)


class TestIsTrump:
    def test_a_tile_with_the_trump_number_is_trump(self) -> None:
        assert is_trump(Domino(6, 4), Suit.FOURS, PLAIN)
        assert is_trump(Domino(6, 4), Suit.SIXES, PLAIN)
        assert not is_trump(Domino(6, 4), Suit.FIVES, PLAIN)

    def test_no_tile_is_trump_in_a_no_trump_contract(self) -> None:
        assert all(not is_trump(domino, None, PLAIN) for domino in FULL_SET)

    def test_seven_tiles_are_trump_in_each_number_suit(self) -> None:
        for suit in NUMBER_SUITS:
            trumps = [domino for domino in FULL_SET if is_trump(domino, suit, PLAIN)]
            assert len(trumps) == 7

    def test_the_trump_double_is_trump_by_default(self) -> None:
        assert is_trump(Domino(5, 5), Suit.FIVES, PLAIN)

    def test_doubles_leave_the_number_suits_under_the_variant(self) -> None:
        assert not is_trump(Domino(5, 5), Suit.FIVES, DOUBLES_SUIT)
        assert is_trump(Domino(5, 5), Suit.DOUBLES, DOUBLES_SUIT)
        assert not is_trump(Domino(6, 4), Suit.DOUBLES, DOUBLES_SUIT)

    def test_only_six_tiles_are_trump_in_a_number_suit_under_the_variant(self) -> None:
        trumps = [domino for domino in FULL_SET if is_trump(domino, Suit.FIVES, DOUBLES_SUIT)]
        assert len(trumps) == 6

    def test_doubles_cannot_be_trump_unless_the_variant_is_enabled(self) -> None:
        with pytest.raises(ValueError, match="doubles_are_own_suit"):
            is_trump(Domino(5, 5), Suit.DOUBLES, PLAIN)


class TestLedSuit:
    def test_a_led_tile_calls_for_its_higher_end(self) -> None:
        assert led_suit(Domino(6, 4), None, PLAIN) is Suit.SIXES
        assert led_suit(Domino(3, 0), None, PLAIN) is Suit.TREYS

    def test_a_trump_tile_leads_trump_whichever_end_is_higher(self) -> None:
        assert led_suit(Domino(6, 4), Suit.FOURS, PLAIN) is Suit.FOURS
        assert led_suit(Domino(6, 4), Suit.SIXES, PLAIN) is Suit.SIXES

    def test_a_double_leads_its_number_suit_by_default(self) -> None:
        assert led_suit(Domino(3, 3), None, PLAIN) is Suit.TREYS

    def test_a_double_leads_doubles_under_the_variant(self) -> None:
        assert led_suit(Domino(3, 3), None, DOUBLES_SUIT) is Suit.DOUBLES
        assert led_suit(Domino(3, 3), Suit.TREYS, DOUBLES_SUIT) is Suit.DOUBLES

    def test_every_tile_leads_exactly_one_suit(self) -> None:
        for config in (PLAIN, DOUBLES_SUIT):
            for trump in (None, *NUMBER_SUITS):
                assert all(isinstance(led_suit(d, trump, config), Suit) for d in FULL_SET)


class TestDeclarableSuits:
    def test_a_two_ended_tile_offers_its_low_end_as_the_one_alternative(self) -> None:
        assert declarable_suits(Domino(3, 2), None, PLAIN) == (Suit.DEUCES,)

    def test_a_double_offers_no_declaration_regardless_of_the_variant(self) -> None:
        assert declarable_suits(Domino(3, 3), None, PLAIN) == ()
        assert declarable_suits(Domino(3, 3), None, DOUBLES_SUIT) == ()

    def test_a_trump_tile_offers_no_declaration(self) -> None:
        assert declarable_suits(Domino(6, 4), Suit.SIXES, PLAIN) == ()
        assert declarable_suits(Domino(6, 4), Suit.FOURS, PLAIN) == ()

    def test_an_off_suit_tile_still_offers_its_low_end_under_trump(self) -> None:
        assert declarable_suits(Domino(6, 4), Suit.FIVES, PLAIN) == (Suit.FOURS,)


class TestFollows:
    def test_either_end_follows_the_led_suit(self) -> None:
        assert follows(Domino(6, 4), Suit.SIXES, None, PLAIN)
        assert follows(Domino(6, 4), Suit.FOURS, None, PLAIN)
        assert not follows(Domino(6, 4), Suit.FIVES, None, PLAIN)

    def test_a_trump_tile_follows_only_trump(self) -> None:
        # 6-4 is a trump when fours are trump, so it can no longer follow a lead of sixes.
        assert follows(Domino(6, 4), Suit.FOURS, Suit.FOURS, PLAIN)
        assert not follows(Domino(6, 4), Suit.SIXES, Suit.FOURS, PLAIN)

    def test_doubles_follow_only_doubles_under_the_variant(self) -> None:
        assert follows(Domino(5, 5), Suit.DOUBLES, None, DOUBLES_SUIT)
        assert not follows(Domino(5, 5), Suit.FIVES, None, DOUBLES_SUIT)
        assert follows(Domino(5, 5), Suit.FIVES, None, PLAIN)

    def test_the_trump_double_still_follows_trump_by_default(self) -> None:
        assert follows(Domino(5, 5), Suit.FIVES, Suit.FIVES, PLAIN)

    def test_seven_tiles_can_follow_each_number_suit(self) -> None:
        for suit in NUMBER_SUITS:
            followers = [d for d in FULL_SET if follows(d, suit, None, PLAIN)]
            assert len(followers) == 7

    def test_the_doubles_suit_holds_all_seven_doubles(self) -> None:
        followers = [d for d in FULL_SET if follows(d, Suit.DOUBLES, None, DOUBLES_SUIT)]
        assert len(followers) == 7
        assert all(d.is_double for d in followers)


class TestRankInSuit:
    def test_the_double_is_high_in_its_suit(self) -> None:
        ranked = sorted(
            (d for d in FULL_SET if belongs_to(d, Suit.SIXES, PLAIN)),
            key=lambda d: rank_in_suit(d, Suit.SIXES, PLAIN),
        )
        assert ranked[-1] == Domino(6, 6)
        assert ranked[0] == Domino(6, 0)

    def test_tiles_rank_by_their_off_end(self) -> None:
        rank = rank_in_suit
        assert rank(Domino(6, 5), Suit.SIXES, PLAIN) > rank(Domino(6, 1), Suit.SIXES, PLAIN)
        assert rank(Domino(6, 1), Suit.ACES, PLAIN) > rank(Domino(5, 1), Suit.ACES, PLAIN)

    def test_the_doubles_suit_ranks_from_six_six_down(self) -> None:
        ranked = sorted(
            (d for d in FULL_SET if belongs_to(d, Suit.DOUBLES, DOUBLES_SUIT)),
            key=lambda d: rank_in_suit(d, Suit.DOUBLES, DOUBLES_SUIT),
        )
        assert ranked == [Domino(pips, pips) for pips in range(7)]

    def test_ranking_a_tile_outside_the_suit_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="not in suit"):
            rank_in_suit(Domino(6, 4), Suit.FIVES, PLAIN)
        with pytest.raises(ValueError, match="not in suit"):
            rank_in_suit(Domino(5, 5), Suit.FIVES, DOUBLES_SUIT)
