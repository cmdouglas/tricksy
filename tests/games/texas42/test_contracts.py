from __future__ import annotations

import pytest

from tricksy.games.texas42 import contracts
from tricksy.games.texas42.errors import UnknownContract
from tricksy.games.texas42.house_rules import HouseRules


def test_every_designed_contract_is_registered() -> None:
    assert contracts.available() == (
        "nello",
        "nello_low",
        "plunge",
        "sevens",
        "splash",
        "standard",
    )


@pytest.mark.parametrize("name", contracts.available())
def test_registered_contracts_satisfy_the_contract_protocol(name: str) -> None:
    contract = contracts.get(name)
    assert contract.name == name
    assert isinstance(contract, contracts.Contract)


def test_unknown_contracts_are_rejected() -> None:
    with pytest.raises(UnknownContract, match="unknown contract"):
        contracts.get("moonshine")


def test_a_contract_disabled_for_the_game_is_rejected() -> None:
    config = HouseRules()  # splash is off by default
    assert contracts.get_enabled("nello", config).name == "nello"
    with pytest.raises(UnknownContract, match="not enabled"):
        contracts.get_enabled("splash", config)


def test_registering_a_duplicate_name_is_rejected() -> None:
    with pytest.raises(ValueError, match="already registered"):
        contracts.register(contracts.get("standard"))
