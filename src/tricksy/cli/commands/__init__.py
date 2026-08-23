"""The real command table (ROADMAP.md 3.4, 3.5). ``main.py`` assembles this into its dispatch."""

from __future__ import annotations

from tricksy.cli.command import Command
from tricksy.cli.commands import account, play, rules, tables

COMMANDS: tuple[Command, ...] = account.COMMANDS + rules.COMMANDS + tables.COMMANDS + play.COMMANDS
