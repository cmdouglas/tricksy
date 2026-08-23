"""Per-game rule configuration (DESIGN.md §4.1, §5).

Rule variants are per-game data set at creation, never global constants, so that games created
under different variants replay and score correctly side by side.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final, Literal

from .errors import UnknownContract

STANDARD_CONTRACT: Final = "standard"

DEFAULT_CONTRACTS: Final[frozenset[str]] = frozenset(
    {STANDARD_CONTRACT, "nello", "plunge", "sevens"}
)

DEFAULT_MARKS_TO_WIN: Final = 7

#: A per-contract option's value (DESIGN.md §5.1). Plain data, so the Phase 1 codec needs no
#: special case for it.
OptionValue = int | bool | str

#: How far the declared-lead privilege extends (DESIGN.md §5.2): never at all, only on a hand's
#: first trick, or on any trick the declarer leads.
AllowDeclaredLead = Literal["never", "first_trick", "always"]
_ALLOW_DECLARED_LEAD_VALUES: Final = ("never", "first_trick", "always")


@dataclass(frozen=True, slots=True)
class HouseRules:
    """Rule variants chosen when the game is created.

    Contract names and their options are validated against the registry by
    :func:`t42.engine.contracts.registry.validate_house_rules`; this type stays dependency-free
    of the registry itself.
    """

    enabled_contracts: frozenset[str] = DEFAULT_CONTRACTS
    contract_options: Mapping[str, Mapping[str, OptionValue]] = field(default_factory=dict)
    doubles_are_own_suit: bool = False
    allow_declared_lead: AllowDeclaredLead = "never"
    marks_to_win: int = DEFAULT_MARKS_TO_WIN

    def __post_init__(self) -> None:
        if self.marks_to_win < 1:
            raise ValueError(f"marks_to_win must be positive, got {self.marks_to_win}")
        if STANDARD_CONTRACT not in self.enabled_contracts:
            raise ValueError(f"the {STANDARD_CONTRACT!r} contract cannot be disabled")
        if self.allow_declared_lead not in _ALLOW_DECLARED_LEAD_VALUES:
            raise ValueError(
                f"allow_declared_lead must be one of {_ALLOW_DECLARED_LEAD_VALUES}, "
                f"got {self.allow_declared_lead!r}"
            )
        for name, options in self.contract_options.items():
            for key, value in options.items():
                if not isinstance(value, int | bool | str):
                    raise ValueError(f"{name}.{key} must be an int, bool or str, got {value!r}")

    def allows(self, contract: str) -> bool:
        return contract in self.enabled_contracts

    def options_for(
        self, name: str, defaults: Mapping[str, OptionValue]
    ) -> Mapping[str, OptionValue]:
        """Merge this game's overrides for ``name`` over its declared ``defaults``.

        ``defaults`` comes from the contract's own ``option_defaults()`` - this type has no
        registry access to look it up itself. An override key ``defaults`` does not declare is
        rejected, which gives typo detection with no schema language.
        """
        overrides = self.contract_options.get(name, {})
        unknown = overrides.keys() - defaults.keys()
        if unknown:
            raise UnknownContract(f"{name} does not declare option(s): {sorted(unknown)}")
        return {**defaults, **overrides}
