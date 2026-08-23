"""``register``, ``login``, ``logout``, ``whoami``, contacts and password reset (ROADMAP.md 3.4,
4.6, DESIGN.md §7).

``register`` and ``login`` both mint a token and save it as a local profile - the only difference
is which endpoint mints it. Neither takes a plaintext password on the command line unless the
caller opts into that with ``--password``; left unset, ``getpass`` prompts instead, so scripts and
tests can still drive these commands non-interactively. ``reset-password`` reuses that same
``_read_password`` helper for its new password, for the same reason.

``contact confirm`` and ``reset-password`` are the two commands that work signed out, via
``build_client(..., require_auth=False)`` - the token each one carries is itself the credential
(DESIGN.md §6.1), the same shape ``register``/``login`` already use for a different reason.
"""

from __future__ import annotations

import argparse
import getpass

from tricksy.cli import config, render
from tricksy.cli.command import Command
from tricksy.cli.context import build_client, emit


def _add_credential_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("username")
    parser.add_argument("--password", default=None)


def _read_password(args: argparse.Namespace) -> str:
    if args.password is not None:
        return str(args.password)
    return getpass.getpass("Password: ")


def _save_profile(args: argparse.Namespace, response: dict[str, object]) -> None:
    profile_name = args.profile or "default"
    cfg = config.load()
    cfg = config.set_profile(
        cfg,
        profile_name,
        config.Profile(
            player_id=str(response["player_id"]),
            username=str(response["username"]),
            token=str(response["token"]),
        ),
    )
    config.save(cfg)


def _register(args: argparse.Namespace) -> int:
    client, _ = build_client(args, require_auth=False)
    response = client.request(
        "POST",
        "/players",
        json={"username": args.username, "password": _read_password(args)},
    )
    _save_profile(args, response)
    emit(args, response, render.render_token)
    return 0


def _login(args: argparse.Namespace) -> int:
    client, _ = build_client(args, require_auth=False)
    response = client.request(
        "POST",
        "/sessions",
        json={"username": args.username, "password": _read_password(args)},
    )
    _save_profile(args, response)
    emit(args, response, render.render_token)
    return 0


def _logout(args: argparse.Namespace) -> int:
    client, _ = build_client(args)
    response = client.request("DELETE", "/sessions/current")
    profile_name = args.profile or "default"
    cfg = config.load()
    cfg = config.remove_profile(cfg, profile_name)
    config.save(cfg)
    emit(args, response, confirmation=f"logged out ({profile_name})")
    return 0


def _whoami(args: argparse.Namespace) -> int:
    client, _ = build_client(args)
    response = client.request("GET", "/players/me")
    emit(args, response, render.render_profile)
    return 0


def _contacts(args: argparse.Namespace) -> int:
    client, _ = build_client(args)
    response = client.request("GET", "/players/me/contacts")
    emit(args, response, render.render_contact_list)
    return 0


def _configure_contact(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="contact_command", required=True, metavar="contact-command")

    add = sub.add_parser("add", help="add a contact channel")
    add.add_argument("address")
    add.add_argument("--kind", default="email")

    remove = sub.add_parser("remove", help="remove a contact channel")
    remove.add_argument("address")

    verify = sub.add_parser("verify", help="send a verification email to a contact channel")
    verify.add_argument("address")

    confirm = sub.add_parser("confirm", help="redeem a verification token")
    confirm.add_argument("token")

    mute = sub.add_parser("mute", help="stop notifications on a contact channel")
    mute.add_argument("address")

    unmute = sub.add_parser("unmute", help="resume notifications on a contact channel")
    unmute.add_argument("address")


def _handle_contact(args: argparse.Namespace) -> int:
    if args.contact_command == "confirm":
        client, _ = build_client(args, require_auth=False)
        response = client.request("POST", "/contacts/verify", json={"token": args.token})
        emit(args, response, confirmation="contact verified")
        return 0

    client, _ = build_client(args)

    if args.contact_command == "add":
        response = client.request(
            "POST", "/players/me/contacts", json={"kind": args.kind, "address": args.address}
        )
        emit(args, response, render.render_contact)
        return 0

    if args.contact_command == "remove":
        response = client.request("DELETE", f"/players/me/contacts/{args.address}")
        emit(args, response, confirmation=f"removed {args.address}")
        return 0

    if args.contact_command == "verify":
        response = client.request("POST", f"/players/me/contacts/{args.address}/verification")
        emit(args, response, confirmation=f"verification email sent to {args.address}")
        return 0

    if args.contact_command == "mute":
        response = client.request(
            "PATCH", f"/players/me/contacts/{args.address}", json={"notify": False}
        )
        emit(args, response, render.render_contact)
        return 0

    # unmute
    response = client.request(
        "PATCH", f"/players/me/contacts/{args.address}", json={"notify": True}
    )
    emit(args, response, render.render_contact)
    return 0


def _forgot_password(args: argparse.Namespace) -> int:
    client, _ = build_client(args, require_auth=False)
    response = client.request("POST", "/password-resets", json={"username": args.username})
    emit(
        args,
        response,
        confirmation=f"if {args.username} has a verified contact, a reset email was sent",
    )
    return 0


def _configure_reset_password(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("token")
    parser.add_argument("--password", default=None)


def _add_username_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("username")


def _reset_password(args: argparse.Namespace) -> int:
    client, _ = build_client(args, require_auth=False)
    response = client.request(
        "POST",
        "/password-resets/confirm",
        json={"token": args.token, "new_password": _read_password(args)},
    )
    emit(args, response, confirmation="password reset")
    return 0


COMMANDS: tuple[Command, ...] = (
    Command(
        name="register",
        help="create an account and sign in",
        configure=_add_credential_args,
        handler=_register,
    ),
    Command(
        name="login", help="sign in on this device", configure=_add_credential_args, handler=_login
    ),
    Command(
        name="logout", help="sign out this device", configure=lambda parser: None, handler=_logout
    ),
    Command(
        name="whoami",
        help="show the signed-in player",
        configure=lambda parser: None,
        handler=_whoami,
    ),
    Command(
        name="contacts",
        help="list contact channels",
        configure=lambda parser: None,
        handler=_contacts,
    ),
    Command(
        name="contact",
        help="manage a contact channel",
        configure=_configure_contact,
        handler=_handle_contact,
    ),
    Command(
        name="forgot-password",
        help="request a password reset email",
        configure=_add_username_arg,
        handler=_forgot_password,
    ),
    Command(
        name="reset-password",
        help="redeem a password reset token",
        configure=_configure_reset_password,
        handler=_reset_password,
    ),
)
