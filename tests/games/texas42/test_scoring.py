from __future__ import annotations

from tricksy.games.texas42.dominoes import FULL_SET, Domino
from tricksy.games.texas42.scoring import (
    COUNT_VALUES,
    HAND_COUNT_TOTAL,
    MAX_HAND_POINTS,
    POINT_PER_TRICK,
    TRICKS_PER_HAND,
    count_of,
    count_value,
    is_count,
)


def test_there_are_exactly_five_count_dominoes() -> None:
    assert set(COUNT_VALUES) == {
        Domino(5, 5),
        Domino(6, 4),
        Domino(5, 0),
        Domino(4, 1),
        Domino(3, 2),
    }


def test_count_dominoes_are_the_tens_and_fives() -> None:
    assert count_value(Domino(5, 5)) == 10
    assert count_value(Domino(6, 4)) == 10
    assert count_value(Domino(5, 0)) == 5
    assert count_value(Domino(4, 1)) == 5
    assert count_value(Domino(3, 2)) == 5


def test_non_count_dominoes_are_worth_nothing() -> None:
    assert count_value(Domino(6, 6)) == 0
    assert count_value(Domino(0, 0)) == 0
    assert not is_count(Domino(6, 6))
    assert is_count(Domino(6, 4))


def test_every_count_domino_totals_pips_of_five_or_ten() -> None:
    for domino, value in COUNT_VALUES.items():
        assert domino.pips == value


def test_the_deck_carries_thirty_five_count() -> None:
    assert count_of(FULL_SET) == HAND_COUNT_TOTAL


def test_a_hand_is_worth_forty_two_points() -> None:
    assert HAND_COUNT_TOTAL + TRICKS_PER_HAND * POINT_PER_TRICK == 42
    assert MAX_HAND_POINTS == 42
