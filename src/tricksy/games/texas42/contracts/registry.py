"""Name-keyed registry of contract strategies.

New contracts plug in by registering themselves; nothing in the engine switches on contract name
(DESIGN.md §5).
"""

from __future__ import annotations

from ..errors import UnknownContract
from ..house_rules import HouseRules
from .base import Contract

_REGISTRY: dict[str, Contract] = {}


def register(contract: Contract) -> Contract:
    """Register ``contract`` under its name. Returns it, so it can be used as a decorator."""
    if contract.name in _REGISTRY:
        raise ValueError(f"contract already registered: {contract.name!r}")
    _REGISTRY[contract.name] = contract
    return contract


def get(name: str) -> Contract:
    """Look up a registered contract. Raises :class:`UnknownContract` if there is none."""
    try:
        return _REGISTRY[name]
    except KeyError:
        raise UnknownContract(f"unknown contract: {name!r}") from None


def get_enabled(name: str, config: HouseRules) -> Contract:
    """Look up a contract, rejecting one this game has not enabled."""
    contract = get(name)
    if not config.allows(name):
        raise UnknownContract(f"contract not enabled for this game: {name!r}")
    return contract


def available() -> tuple[str, ...]:
    """Every registered contract name, sorted."""
    return tuple(sorted(_REGISTRY))


def validate_house_rules(rules: HouseRules) -> None:
    """Registry-aware validation of a house-rule set (DESIGN.md §5.1). Call this at game
    creation so an invalid rule set can never produce a game.

    Structural checks that need no registry (``marks_to_win``, ``standard`` enabled, option
    value types) already ran in ``HouseRules.__post_init__``. This covers everything that needs
    the registry: every enabled contract is registered, every contract given options is actually
    enabled, and each contract's own ``validate_options`` passes (the "satisfiable" and
    "coherent" tiers, e.g. the plunge/splash entry-bar ordering).
    """
    for name in sorted(rules.enabled_contracts):
        get(name)

    for name in sorted(rules.contract_options):
        if name not in rules.enabled_contracts:
            raise UnknownContract(
                f"house rules set options for {name!r}, which is not enabled for this game"
            )
        get(name)

    for name in sorted(rules.enabled_contracts):
        contract = get(name)
        options = rules.options_for(name, contract.option_defaults())
        contract.validate_options(options, rules)
