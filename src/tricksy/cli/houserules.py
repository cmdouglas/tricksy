"""House-rule flags shared by ``create-game`` and ``rules save``/``replace`` (ROADMAP.md 3.4,
DESIGN.md §5.1, §7).

``add_house_rule_flags`` wires the flags; :func:`house_rules_body` turns parsed args into a
request body. The body includes a key **only** when its flag was actually given, leaving
everything else absent so the server's own ``HouseRulesRequest`` field defaults apply - the CLI
never restates a default (the same reasoning ``CreateGameRequest``/``model_fields_set`` already
applies one level up, to ``house_rules`` vs. ``rule_set_id``).

Also home to ``parse_seat``: not a house rule, but the other small argparse ``type=`` helper
``create-game`` and ``join`` share, and it duplicates ``render.py``'s own seat-name table on
purpose, per DESIGN.md §7's stated tradeoff that the CLI owns its own name tables rather than
importing the engine's enums.
"""

from __future__ import annotations

import argparse
from typing import Any

_DECLARED_LEAD_CHOICES = ("never", "first_trick", "always")

_SEAT_NAMES: dict[str, int] = {"north": 0, "east": 1, "south": 2, "west": 3}


def add_house_rule_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--contracts", default=None, help="comma-separated, e.g. nello,plunge")
    parser.add_argument("--marks", type=int, default=None, dest="marks")
    parser.add_argument("--doubles-trump", action="store_true", dest="doubles_trump")
    parser.add_argument("--declared-leads", choices=_DECLARED_LEAD_CHOICES, default=None)
    parser.add_argument(
        "--set",
        action="append",
        dest="set",
        type=_parse_set_option,
        metavar="CONTRACT.KEY=VALUE",
        default=None,
    )


def house_rules_body(args: argparse.Namespace) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if args.contracts is not None:
        body["enabled_contracts"] = [c for c in args.contracts.split(",") if c]
    if args.marks is not None:
        body["marks_to_win"] = args.marks
    if args.doubles_trump:
        body["doubles_are_own_suit"] = True
    if args.declared_leads is not None:
        body["allow_declared_lead"] = args.declared_leads
    if args.set:
        options: dict[str, dict[str, Any]] = {}
        for contract, key, value in args.set:
            options.setdefault(contract, {})[key] = value
        body["contract_options"] = options
    return body


def any_house_rule_flag_given(args: argparse.Namespace) -> bool:
    return (
        args.contracts is not None
        or args.marks is not None
        or args.doubles_trump
        or args.declared_leads is not None
        or bool(args.set)
    )


def _parse_set_option(raw: str) -> tuple[str, str, Any]:
    contract, dot, rest = raw.partition(".")
    key, eq, value = rest.partition("=")
    if not dot or not eq or not contract or not key:
        raise argparse.ArgumentTypeError(f"invalid --set {raw!r}, expected CONTRACT.KEY=VALUE")
    return contract, key, _coerce_option_value(value)


def _coerce_option_value(value: str) -> Any:
    if value == "true":
        return True
    if value == "false":
        return False
    try:
        return int(value)
    except ValueError:
        return value


def parse_seat(value: str) -> int:
    lowered = value.lower()
    if lowered in _SEAT_NAMES:
        return _SEAT_NAMES[lowered]
    try:
        seat = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"invalid seat {value!r}, expected 0-3 or north/east/south/west"
        ) from None
    if seat not in range(4):
        raise argparse.ArgumentTypeError(
            f"invalid seat {value!r}, expected 0-3 or north/east/south/west"
        )
    return seat
