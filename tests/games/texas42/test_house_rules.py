from __future__ import annotations

import dataclasses

import pytest

from tricksy.games.texas42 import contracts
from tricksy.games.texas42.bidding import legal_bids
from tricksy.games.texas42.dominoes import Domino
from tricksy.games.texas42.errors import IllegalMove, RulesError, UnknownContract
from tricksy.games.texas42.house_rules import DEFAULT_CONTRACTS, DEFAULT_MARKS_TO_WIN, HouseRules
from tricksy.games.texas42.state import Bid, Seat

from ._helpers import custom_deal, make_game


def test_defaults_match_the_design() -> None:
    config = HouseRules()
    assert config.marks_to_win == DEFAULT_MARKS_TO_WIN == 7
    assert not config.doubles_are_own_suit
    assert config.enabled_contracts == frozenset({"standard", "nello", "plunge", "sevens"})
    assert config.contract_options == {}
    assert config.allow_declared_lead == "never"


def test_an_invalid_allow_declared_lead_value_is_rejected() -> None:
    with pytest.raises(ValueError, match="allow_declared_lead"):
        HouseRules(allow_declared_lead="sometimes")  # type: ignore[arg-type]


def test_allows_reports_enabled_contracts() -> None:
    config = HouseRules(enabled_contracts=frozenset({"standard", "nello"}))
    assert config.allows("nello")
    assert not config.allows("sevens")


def test_house_rules_is_immutable() -> None:
    config = HouseRules()
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.marks_to_win = 3  # type: ignore[misc]


@pytest.mark.parametrize("marks", [0, -1])
def test_marks_to_win_must_be_positive(marks: int) -> None:
    with pytest.raises(ValueError, match="marks_to_win must be positive"):
        HouseRules(marks_to_win=marks)


def test_the_standard_contract_cannot_be_disabled() -> None:
    with pytest.raises(ValueError, match="cannot be disabled"):
        HouseRules(enabled_contracts=frozenset({"nello"}))


def test_a_contract_option_of_the_wrong_type_is_rejected() -> None:
    with pytest.raises(ValueError, match="must be an int, bool or str"):
        HouseRules(contract_options={"plunge": {"minimum_doubles": [4]}})  # type: ignore[dict-item]


def test_options_for_merges_overrides_over_defaults() -> None:
    config = HouseRules(contract_options={"plunge": {"minimum_marks": 5}})
    defaults = {"minimum_doubles": 4, "minimum_marks": 4}
    assert config.options_for("plunge", defaults) == {"minimum_doubles": 4, "minimum_marks": 5}


def test_options_for_rejects_an_unknown_key() -> None:
    config = HouseRules(contract_options={"plunge": {"typo": 1}})
    with pytest.raises(UnknownContract, match="does not declare"):
        config.options_for("plunge", {"minimum_doubles": 4, "minimum_marks": 4})


def test_an_override_changes_which_bids_legal_bids_offers() -> None:
    hands = custom_deal(
        north=(*(Domino(n, n) for n in range(4)), Domino(6, 5), Domino(6, 4), Domino(5, 4))
    )
    config = HouseRules(contract_options={"plunge": {"minimum_marks": 5}})
    state = make_game(hands, to_act=Seat.NORTH, config=config)
    bids = legal_bids(state)
    assert Bid(bidder=Seat.NORTH, contract="plunge", marks=4) not in bids
    assert Bid(bidder=Seat.NORTH, contract="plunge", marks=5) in bids


def test_validate_house_rules_rejects_an_unknown_contract() -> None:
    rules = HouseRules(enabled_contracts=frozenset({"standard", "nelo"}))
    with pytest.raises(UnknownContract, match="nelo"):
        contracts.validate_house_rules(rules)


def test_validate_house_rules_rejects_options_for_a_disabled_contract() -> None:
    rules = HouseRules(
        enabled_contracts=frozenset({"standard"}), contract_options={"splash": {"minimum_marks": 2}}
    )
    with pytest.raises(UnknownContract, match="not enabled"):
        contracts.validate_house_rules(rules)


def test_validate_house_rules_rejects_an_unknown_option_key() -> None:
    rules = HouseRules(contract_options={"plunge": {"minimum_typo": 1}})
    with pytest.raises(UnknownContract, match="does not declare"):
        contracts.validate_house_rules(rules)


def test_validate_house_rules_rejects_a_doubles_minimum_above_seven() -> None:
    rules = HouseRules(contract_options={"plunge": {"minimum_doubles": 8}})
    with pytest.raises(RulesError, match="minimum_doubles"):
        contracts.validate_house_rules(rules)


def test_validate_house_rules_rejects_a_marks_minimum_above_seven() -> None:
    rules = HouseRules(contract_options={"nello": {"minimum_marks": 8}})
    with pytest.raises(RulesError, match="minimum_marks"):
        contracts.validate_house_rules(rules)


def test_validate_house_rules_rejects_splash_harder_than_plunge_on_doubles() -> None:
    rules = HouseRules(
        enabled_contracts=DEFAULT_CONTRACTS | {"splash"},
        contract_options={"splash": {"minimum_doubles": 5}},  # plunge's default is 4
    )
    with pytest.raises(RulesError, match="at least as hard"):
        contracts.validate_house_rules(rules)


def test_validate_house_rules_rejects_splash_harder_than_plunge_on_marks() -> None:
    rules = HouseRules(
        enabled_contracts=DEFAULT_CONTRACTS | {"splash"},
        contract_options={"splash": {"minimum_marks": 5}},  # plunge's default is 4
    )
    with pytest.raises(RulesError, match="at least as hard"):
        contracts.validate_house_rules(rules)


def test_default_house_rules_validate_clean() -> None:
    contracts.validate_house_rules(HouseRules())


def test_default_bars_validate_clean_with_both_plunge_and_splash_enabled() -> None:
    # Splash is weakly dominated by plunge's defaults at 4/4 vs 3/2, but that's the table's
    # business, not a contradiction - DESIGN.md §5.1 explicitly declines to reject it.
    contracts.validate_house_rules(HouseRules(enabled_contracts=DEFAULT_CONTRACTS | {"splash"}))


def test_nello_and_nello_low_together_validates_clean() -> None:
    contracts.validate_house_rules(HouseRules(enabled_contracts=DEFAULT_CONTRACTS | {"nello_low"}))


def test_two_games_under_different_plunge_minimums_score_independently() -> None:
    """The regression Phase 0.5 exists for: the minimum used to live on the registered contract
    instance, shared by every game in the process. Interleaving two HouseRules values against the
    same PlungeContract object proves neither mutates shared state."""
    plunge = contracts.get("plunge")
    hand = tuple(Domino(n, n) for n in range(4))
    lenient = HouseRules()
    strict = HouseRules(contract_options={"plunge": {"minimum_marks": 6}})
    bid_four = Bid(bidder=Seat.NORTH, contract="plunge", marks=4)
    bid_six = Bid(bidder=Seat.NORTH, contract="plunge", marks=6)

    plunge.validate_bid(bid_four, hand, lenient)
    with pytest.raises(IllegalMove, match="marks"):
        plunge.validate_bid(bid_four, hand, strict)
    plunge.validate_bid(bid_six, hand, strict)
    plunge.validate_bid(bid_four, hand, lenient)
