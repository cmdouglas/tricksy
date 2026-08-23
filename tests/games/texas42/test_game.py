from __future__ import annotations

from random import Random

import pytest

from t42.engine.dominoes import FULL_SET
from t42.engine.errors import UnknownContract
from t42.engine.game import new_game
from t42.engine.house_rules import HouseRules
from t42.engine.state import Phase, Seat, Team

from ._helpers import PLAYERS


def test_new_game_deals_a_partition_of_the_full_set() -> None:
    state = new_game("g1", PLAYERS, HouseRules(), rng=Random(1))
    assert state.hand is not None
    dealt = [domino for tiles in state.hand.hands.values() for domino in tiles]
    assert sorted(dealt) == sorted(FULL_SET)
    assert all(len(tiles) == 7 for tiles in state.hand.hands.values())


def test_new_game_is_reproducible_from_the_same_seed() -> None:
    a = new_game("g1", PLAYERS, HouseRules(), rng=Random(42))
    b = new_game("g1", PLAYERS, HouseRules(), rng=Random(42))
    assert a.hand is not None and b.hand is not None
    assert a.hand.hands == b.hand.hands


def test_different_seeds_usually_deal_differently() -> None:
    a = new_game("g1", PLAYERS, HouseRules(), rng=Random(1))
    b = new_game("g1", PLAYERS, HouseRules(), rng=Random(2))
    assert a.hand is not None and b.hand is not None
    assert a.hand.hands != b.hand.hands


def test_new_game_starts_in_bidding_with_north_as_first_dealer() -> None:
    state = new_game("g1", PLAYERS, HouseRules(), rng=Random(1))
    assert state.phase is Phase.BIDDING
    assert state.hand is not None and state.hand.dealer is Seat.NORTH
    assert state.to_act is Seat.EAST  # dealer's left
    assert state.marks == {Team.NORTH_SOUTH: 0, Team.EAST_WEST: 0}


def test_new_game_requires_all_four_seats_filled() -> None:
    with pytest.raises(ValueError, match="all four seats"):
        new_game("g1", {Seat.NORTH: "n", Seat.EAST: "e"}, HouseRules(), rng=Random(1))


def test_new_game_rejects_an_unknown_contract() -> None:
    bad = HouseRules(enabled_contracts=frozenset({"standard", "nelo"}))
    with pytest.raises(UnknownContract, match="nelo"):
        new_game("g1", PLAYERS, bad, rng=Random(1))
