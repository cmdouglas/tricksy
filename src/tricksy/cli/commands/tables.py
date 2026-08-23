"""``create-game``, ``join``, ``open``, ``invite``, ``invited``, ``uninvite``, ``decline``,
``invites`` (ROADMAP.md 3.4, DESIGN.md §7).
"""

from __future__ import annotations

import argparse
import sys
from typing import cast

from t42.cli import config, render
from t42.cli.api import ApiError
from t42.cli.command import Command
from t42.cli.context import build_client, emit
from t42.cli.houserules import (
    add_house_rule_flags,
    any_house_rule_flag_given,
    house_rules_body,
    parse_seat,
)


def _configure_create_game(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--seat", type=parse_seat, default=0)
    parser.add_argument("--visibility", choices=("public", "invite_only"), default="public")
    parser.add_argument("--rule-set", dest="rule_set", default=None)
    add_house_rule_flags(parser)


def _create_game(args: argparse.Namespace) -> int:
    if args.rule_set is not None and any_house_rule_flag_given(args):
        print("error: --rule-set cannot be combined with house-rule flags", file=sys.stderr)
        return 2

    client, _ = build_client(args)
    body: dict[str, object] = {"seat": args.seat, "visibility": args.visibility}
    if args.rule_set is not None:
        body["rule_set_id"] = args.rule_set
    else:
        body["house_rules"] = house_rules_body(args)
    response = client.request("POST", "/games", json=body)
    emit(args, response, render.render_game)
    return 0


def _configure_join(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("code")
    parser.add_argument("--seat", type=parse_seat, required=True)


def _join(args: argparse.Namespace) -> int:
    client, _ = build_client(args)
    response = client.request("POST", f"/games/{args.code}/join", json={"seat": args.seat})
    emit(args, response, render.render_game)
    return 0


def _open(args: argparse.Namespace) -> int:
    client, _ = build_client(args)
    response = client.request("GET", "/games/open")
    emit(args, response, render.render_open_games)
    return 0


def _configure_code_and_username(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("code")
    parser.add_argument("username")


def _invite(args: argparse.Namespace) -> int:
    client, _ = build_client(args)
    response = client.request(
        "POST", f"/games/{args.code}/invites", json={"username": args.username}
    )
    emit(args, response, confirmation=f"invited {args.username} to {args.code}")
    return 0


def _configure_code_only(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("code")


def _invited(args: argparse.Namespace) -> int:
    client, _ = build_client(args)
    response = client.request("GET", f"/games/{args.code}/invites")
    emit(args, response, render.render_game_invites)
    return 0


def _uninvite(args: argparse.Namespace) -> int:
    client, _ = build_client(args)
    invites = client.request("GET", f"/games/{args.code}/invites")
    match = next(
        (i for i in invites["invites"] if i["username"] == args.username),
        None,
    )
    if match is None:
        raise ApiError(
            0, "PLAYER_NOT_FOUND", f"no pending invite for {args.username!r} on {args.code}"
        )
    response = client.request("DELETE", f"/games/{args.code}/invites/{match['player_id']}")
    emit(args, response, confirmation=f"revoked {args.username}'s invite to {args.code}")
    return 0


def _decline(args: argparse.Namespace) -> int:
    client, profile = build_client(args)
    # build_client(require_auth=True, the default) already raised if no profile was found.
    profile = cast(config.Profile, profile)
    response = client.request("DELETE", f"/games/{args.code}/invites/{profile.player_id}")
    emit(args, response, confirmation=f"declined invite to {args.code}")
    return 0


def _invites(args: argparse.Namespace) -> int:
    client, _ = build_client(args)
    response = client.request("GET", "/players/me/invites")
    emit(args, response, render.render_invite_list)
    return 0


COMMANDS: tuple[Command, ...] = (
    Command(
        name="create-game",
        help="open a new table",
        configure=_configure_create_game,
        handler=_create_game,
    ),
    Command(name="join", help="take a seat at a table", configure=_configure_join, handler=_join),
    Command(
        name="open",
        help="browse public tables with a seat free",
        configure=lambda parser: None,
        handler=_open,
    ),
    Command(
        name="invite",
        help="invite a player to a table",
        configure=_configure_code_and_username,
        handler=_invite,
    ),
    Command(
        name="invited",
        help="who is invited to a table",
        configure=_configure_code_only,
        handler=_invited,
    ),
    Command(
        name="uninvite",
        help="revoke a player's invite",
        configure=_configure_code_and_username,
        handler=_uninvite,
    ),
    Command(
        name="decline",
        help="decline your own invite to a table",
        configure=_configure_code_only,
        handler=_decline,
    ),
    Command(
        name="invites",
        help="list your pending invites",
        configure=lambda parser: None,
        handler=_invites,
    ),
)
