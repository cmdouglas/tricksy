"""The ``Command`` shape ``main.py``'s dispatch table is built from (ROADMAP.md 3.1, 3.4).

Split out from ``main.py`` so ``tricksy.cli.commands.*`` can build ``Command`` values without
importing ``main.py`` itself, which would be circular: ``main.py`` is what assembles
``tricksy.cli.commands.COMMANDS`` into its dispatch table.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Command:
    name: str
    help: str
    configure: Callable[[argparse.ArgumentParser], None]
    handler: Callable[[argparse.Namespace], int]
