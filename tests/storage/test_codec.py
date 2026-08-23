"""Round-trip tests for the DynamoDB codec (ROADMAP.md 1.1): decode(encode(x)) == x for
HouseRules, every Event type, and GameState.
"""

from __future__ import annotations

from random import Random

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tricksy.games.texas42.contracts import available
from tricksy.games.texas42.dominoes import FULL_SET, Domino
from tricksy.games.texas42.events import (
    BidConfirmed,
    BidPlaced,
    ContractDeclared,
    DominoPlayed,
    HandDealt,
    Passed,
)
from tricksy.games.texas42.game import new_game
from tricksy.games.texas42.house_rules import AllowDeclaredLead, HouseRules
from tricksy.games.texas42.state import GameState, Seat
from tricksy.games.texas42.suits import Suit
from tricksy.storage import (
    decode_event,
    decode_game_state,
    decode_house_rules,
    encode_event,
    encode_game_state,
    encode_house_rules,
)

from ..games.texas42._helpers import PLAYERS, drive_to_game_over, prefer_contract

_ALLOW_DECLARED_LEAD_VALUES: tuple[AllowDeclaredLead, ...] = ("never", "first_trick", "always")

_dominoes = st.sampled_from(FULL_SET)
_seats = st.sampled_from(list(Seat))
_suits = st.sampled_from(list(Suit))
_optional_suits = st.one_of(st.none(), _suits)
_player_ids = st.text(min_size=1, max_size=8)
_contract_names = st.sampled_from(available())
_optional_contract_names = st.one_of(st.none(), _contract_names)


def _hands_of(tiles: list[Domino]) -> tuple[tuple[Seat, tuple[Domino, ...]], ...]:
    return tuple((seat, tuple(tiles[i * 7 : (i + 1) * 7])) for i, seat in enumerate(Seat))


_hand_dealt = st.builds(HandDealt, hands=st.permutations(FULL_SET).map(_hands_of), dealer=_seats)
_bid_placed = st.builds(
    BidPlaced,
    actor=_player_ids,
    contract=_optional_contract_names,
    points=st.one_of(st.none(), st.integers(min_value=30, max_value=42)),
    marks=st.one_of(st.none(), st.integers(min_value=1, max_value=7)),
)
_passed = st.builds(Passed, actor=_player_ids)
_bid_confirmed = st.builds(BidConfirmed, actor=_player_ids, accept=st.booleans())
_contract_declared = st.builds(
    ContractDeclared, actor=_player_ids, contract=_contract_names, trump=_optional_suits
)
_domino_played = st.builds(
    DominoPlayed, actor=_player_ids, domino=_dominoes, declared_suit=_optional_suits
)


@given(_hand_dealt)
def test_hand_dealt_round_trips(event: HandDealt) -> None:
    assert decode_event(encode_event(event)) == event


@given(_bid_placed)
def test_bid_placed_round_trips(event: BidPlaced) -> None:
    assert decode_event(encode_event(event)) == event


@given(_passed)
def test_passed_round_trips(event: Passed) -> None:
    assert decode_event(encode_event(event)) == event


@given(_bid_confirmed)
def test_bid_confirmed_round_trips(event: BidConfirmed) -> None:
    assert decode_event(encode_event(event)) == event


@given(_contract_declared)
def test_contract_declared_round_trips(event: ContractDeclared) -> None:
    assert decode_event(encode_event(event)) == event


@given(_domino_played)
def test_domino_played_round_trips(event: DominoPlayed) -> None:
    assert decode_event(encode_event(event)) == event


def test_unknown_event_type_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown event type"):
        decode_event({"type": "NOT_A_REAL_EVENT"})


_option_values = st.one_of(
    st.integers(min_value=-100, max_value=100), st.booleans(), st.text(max_size=5)
)
_contract_options = st.dictionaries(
    _contract_names,
    st.dictionaries(st.text(min_size=1, max_size=8), _option_values, max_size=3),
    max_size=3,
)
_enabled_contracts = st.sets(_contract_names, max_size=len(available())).map(
    lambda extra: frozenset({"standard"}) | extra
)
_house_rules = st.builds(
    HouseRules,
    enabled_contracts=_enabled_contracts,
    contract_options=_contract_options,
    doubles_are_own_suit=st.booleans(),
    allow_declared_lead=st.sampled_from(_ALLOW_DECLARED_LEAD_VALUES),
    marks_to_win=st.integers(min_value=1, max_value=20),
)


@given(_house_rules)
def test_house_rules_round_trips(rules: HouseRules) -> None:
    assert decode_house_rules(encode_house_rules(rules)) == rules


def _game_state_snapshots(contract_name: str, seed: int) -> list[GameState]:
    """Every state a real game under ``contract_name`` passes through: dealing, bidding
    (including a plunge confirmation when applicable), declaring, every trick, and the
    HAND_COMPLETE/GAME_OVER states where ``hand`` is ``None``."""
    config = HouseRules(
        enabled_contracts=frozenset(available()), marks_to_win=1, allow_declared_lead="always"
    )
    rng = Random(seed)
    state = new_game(f"codec-{contract_name}-{seed}", PLAYERS, config, rng=rng)
    snapshots = [state]
    drive_to_game_over(
        state, prefer_contract(contract_name), rng, max_moves=2000, on_state=snapshots.append
    )
    return snapshots


@pytest.mark.parametrize("contract_name", sorted(available()))
def test_game_state_round_trips_across_a_full_game(contract_name: str) -> None:
    for snapshot in _game_state_snapshots(contract_name, seed=hash(contract_name) & 0xFFFF):
        assert decode_game_state(encode_game_state(snapshot)) == snapshot
