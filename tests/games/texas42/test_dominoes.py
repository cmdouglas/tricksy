from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tricksy.games.texas42.dominoes import FULL_SET, MAX_PIPS, Domino, parse

dominoes = st.sampled_from(FULL_SET)


def test_full_set_is_the_28_tiles_of_a_double_six_set() -> None:
    assert len(FULL_SET) == 28
    assert len(set(FULL_SET)) == 28
    assert sum(1 for domino in FULL_SET if domino.is_double) == 7


def test_ends_are_normalized_so_order_does_not_matter() -> None:
    assert Domino(1, 4) == Domino(4, 1)
    assert Domino(1, 4).high == 4
    assert Domino(1, 4).low == 1
    assert len({Domino(1, 4), Domino(4, 1)}) == 1


@pytest.mark.parametrize("pips", [-1, MAX_PIPS + 1, 42])
def test_pip_counts_outside_zero_to_six_are_rejected(pips: int) -> None:
    with pytest.raises(ValueError, match="pip count out of range"):
        Domino(pips, 0)


def test_properties() -> None:
    assert Domino(5, 5).is_double
    assert not Domino(6, 4).is_double
    assert Domino(6, 4).pips == 10
    assert Domino(6, 4).ends == (6, 4)


def test_notation_matches_the_cli_form() -> None:
    assert str(Domino(6, 4)) == "6-4"
    assert str(Domino(0, 0)) == "0-0"
    assert parse("4-6") == Domino(6, 4)
    assert parse("  4-1 ") == Domino(4, 1)


@pytest.mark.parametrize("text", ["", "6", "6-4-1", "six-four", "6-", "-"])
def test_unparseable_notation_is_rejected(text: str) -> None:
    with pytest.raises(ValueError, match="not a domino"):
        parse(text)


@given(dominoes)
def test_parse_round_trips_through_notation(domino: Domino) -> None:
    assert parse(str(domino)) == domino


def test_dominoes_sort_deterministically() -> None:
    assert sorted([Domino(0, 0), Domino(6, 6), Domino(3, 1)]) == [
        Domino(0, 0),
        Domino(3, 1),
        Domino(6, 6),
    ]
