"""Argument parsing and command dispatch (ROADMAP.md 3.1).

``main`` returns an exit code for every expected failure rather than raising or calling
``sys.exit`` itself, so a command stays a plain function a test can call - the process boundary
(the ``tricksy`` console script installed by ``pyproject.toml``) carries no logic beyond
``sys.exit(main())``.

Global flags (``--api-url``, ``--profile``, ``--json``) are declared once, on the top-level parser
only, and so must precede the subcommand (``tricksy --profile north status ABC``, not the reverse).
``argparse`` subparsers parse into a fresh sub-namespace and merge it onto the outer one, defaults
included - declaring the same flags on every subparser so they could also appear after the
subcommand would let an unset subparser default silently clobber a value already given before it.
One declaration site avoids that.

``_COMMANDS`` is assembled from ``tricksy.cli.commands.COMMANDS`` (ROADMAP.md 3.4, 3.5) - the
``Command`` shape itself lives in ``command.py`` rather than here, so that package can build
``Command`` values without importing this module (which is what assembles them into the dispatch
table below).
"""

from __future__ import annotations

import argparse
import json as jsonlib
import os
import sys
from collections.abc import Sequence
from typing import NoReturn

from tricksy.cli import config, errors
from tricksy.cli.api import ApiError
from tricksy.cli.command import Command as Command
from tricksy.cli.commands import COMMANDS as _REAL_COMMANDS


class _ExitSignal(Exception):
    """Raised in place of ``SystemExit`` so ``main`` can turn it back into a return value."""

    def __init__(self, status: int) -> None:
        super().__init__(status)
        self.status = status


class _ArgumentParser(argparse.ArgumentParser):
    def exit(self, status: int = 0, message: str | None = None) -> NoReturn:
        if message:
            self._print_message(message, sys.stderr)
        raise _ExitSignal(status)


_COMMANDS: tuple[Command, ...] = _REAL_COMMANDS


def build_parser(commands: Sequence[Command] = _COMMANDS) -> _ArgumentParser:
    parser = _ArgumentParser(prog="tricksy")
    parser.add_argument(
        "--api-url",
        default=os.environ.get("TRICKSY_API_URL", config.DEFAULT_API_URL),
    )
    parser.add_argument("--profile", default=os.environ.get("TRICKSY_PROFILE"))
    parser.add_argument("--json", action="store_true")

    subparsers = parser.add_subparsers(dest="command", required=True, metavar="command")
    for command in commands:
        sub = subparsers.add_parser(command.name, help=command.help)
        command.configure(sub)

    return parser


def _print_error(json_mode: bool, code: str | None, message: str) -> None:
    if json_mode:
        error: dict[str, str] = {"message": message}
        if code is not None:
            error["code"] = code
        print(jsonlib.dumps({"error": error}), file=sys.stderr)
    else:
        print(f"error: {message}", file=sys.stderr)


def _run(argv: list[str] | None, commands: Sequence[Command]) -> int:
    parser = build_parser(commands)
    try:
        args = parser.parse_args(argv)
    except _ExitSignal as exc:
        return exc.status

    by_name = {command.name: command for command in commands}
    try:
        return by_name[args.command].handler(args)
    except ApiError as exc:
        _print_error(args.json, exc.code, exc.message)
        return errors.exit_code_for(exc.code)
    except Exception as exc:  # network failure, or anything else unexpected
        _print_error(args.json, None, str(exc))
        return 1


def main(argv: list[str] | None = None) -> int:
    return _run(argv, _COMMANDS)


if __name__ == "__main__":
    sys.exit(main())
