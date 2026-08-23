"""Errors raised by the engine.

The engine signals rejection by raising; handlers map these to HTTP responses (DESIGN.md §6).
"""

from __future__ import annotations


class RulesError(Exception):
    """Base class for every rejection the engine can produce."""


class IllegalMove(RulesError):
    """The move is well-formed but not permitted by the rules or the current state."""


class OutOfTurn(IllegalMove):
    """The actor is not the player to move."""


class UnknownContract(RulesError):
    """No contract is registered under that name, or it is disabled for this game."""
