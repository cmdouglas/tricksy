"""The 28 tiles of a double-six set, and the ``a-b`` text notation from DESIGN.md §7."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

MAX_PIPS: Final = 6
PIP_VALUES: Final[tuple[int, ...]] = tuple(range(MAX_PIPS + 1))


@dataclass(frozen=True, order=True, slots=True)
class Domino:
    """A single tile.

    Ends are normalized so the higher pip count is always ``high``: ``Domino(1, 4)`` and
    ``Domino(4, 1)`` are the same tile and compare equal.
    """

    high: int
    low: int

    def __post_init__(self) -> None:
        for pip in (self.high, self.low):
            if pip not in PIP_VALUES:
                raise ValueError(f"pip count out of range: {pip!r}")
        if self.high < self.low:
            high, low = self.low, self.high
            object.__setattr__(self, "high", high)
            object.__setattr__(self, "low", low)

    @property
    def is_double(self) -> bool:
        return self.high == self.low

    @property
    def pips(self) -> int:
        """Total pip count, used by the count-domino table in :mod:`t42.engine.scoring`."""
        return self.high + self.low

    @property
    def ends(self) -> tuple[int, int]:
        return (self.high, self.low)

    def __str__(self) -> str:
        return f"{self.high}-{self.low}"


def parse(text: str) -> Domino:
    """Parse ``a-b`` notation. Ends may be given in either order: ``4-1`` == ``1-4``."""
    ends = text.strip().split("-")
    if len(ends) != 2:
        raise ValueError(f"not a domino: {text!r} (expected a-b, e.g. 6-4)")
    try:
        high, low = (int(end) for end in ends)
    except ValueError:
        raise ValueError(f"not a domino: {text!r} (expected a-b, e.g. 6-4)") from None
    return Domino(high, low)


FULL_SET: Final[tuple[Domino, ...]] = tuple(
    Domino(high, low) for high in PIP_VALUES for low in range(high + 1)
)
