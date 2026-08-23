"""``rules save|list|show|replace|delete`` (ROADMAP.md 3.4, DESIGN.md §7).

The one command in DESIGN.md §7 with nested sub-subparsers - everything else is flat. ``configure``
adds its own ``add_subparsers`` on the parser it's handed, and the handler dispatches on
``args.rules_command``.
"""

from __future__ import annotations

import argparse

from t42.cli import render
from t42.cli.command import Command
from t42.cli.context import build_client, emit
from t42.cli.houserules import add_house_rule_flags, house_rules_body


def _configure(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="rules_command", required=True, metavar="rules-command")

    save = sub.add_parser("save", help="save a named house-rule set")
    save.add_argument("name")
    add_house_rule_flags(save)

    sub.add_parser("list", help="list saved rule sets")

    show = sub.add_parser("show", help="show a saved rule set")
    show.add_argument("rule_set_id")

    replace = sub.add_parser("replace", help="replace a saved rule set")
    replace.add_argument("rule_set_id")
    replace.add_argument("name")
    add_house_rule_flags(replace)

    delete = sub.add_parser("delete", help="delete a saved rule set")
    delete.add_argument("rule_set_id")


def _handle(args: argparse.Namespace) -> int:
    client, _ = build_client(args)

    if args.rules_command == "save":
        body = {"name": args.name, "house_rules": house_rules_body(args)}
        response = client.request("POST", "/players/me/rule-sets", json=body)
        emit(args, response, render.render_rule_set)
        return 0

    if args.rules_command == "list":
        response = client.request("GET", "/players/me/rule-sets")
        emit(args, response, render.render_rule_set_list)
        return 0

    if args.rules_command == "show":
        response = client.request("GET", f"/players/me/rule-sets/{args.rule_set_id}")
        emit(args, response, render.render_rule_set)
        return 0

    if args.rules_command == "replace":
        body = {"name": args.name, "house_rules": house_rules_body(args)}
        response = client.request("PUT", f"/players/me/rule-sets/{args.rule_set_id}", json=body)
        emit(args, response, render.render_rule_set)
        return 0

    response = client.request("DELETE", f"/players/me/rule-sets/{args.rule_set_id}")
    emit(args, response, confirmation=f"deleted rule set {args.rule_set_id}")
    return 0


COMMANDS: tuple[Command, ...] = (
    Command(
        name="rules", help="manage saved house-rule sets", configure=_configure, handler=_handle
    ),
)
