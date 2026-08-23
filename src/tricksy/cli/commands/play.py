"""``status``, ``games``, ``bid``, ``declare``, ``play`` (ROADMAP.md 3.5, DESIGN.md §7).

``bid``'s five spellings (a points bid, ``pass``, a marks bid with an optional ``--contract``,
``confirm``, ``decline``) are one endpoint discriminated on ``kind`` (DESIGN.md §6) - there is no
``DECLINE`` kind on the wire, ``confirm``/``decline`` are both ``CONFIRM_BID`` with
``accept=True``/``False``. The token is parsed in the handler rather than through a positional
``type=`` callable because ``--contract`` is only valid combined with a marks bid, and a ``type=``
callable never sees a sibling argument's value - the same reason ``tables._create_game`` checks
``--rule-set`` against the house-rule flags after parsing rather than inside any one flag's
``type=``.

``declare``'s ``trump=<suit>``/``trump=none`` token has no such sibling-argument interaction, so it
is a ``type=`` callable, the same shape ``houserules._parse_set_option`` already uses for
``--set CONTRACT.KEY=VALUE``.

``parse_suit``/``_SUIT_NAMES`` below are the input-side counterpart to ``render.py``'s ``_SUITS``
output table, and live here rather than in ``houserules.py`` or a new shared module because this
module is their only consumer - ``render.py``'s ``_SEATS`` and ``houserules.py``'s ``_SEAT_NAMES``
already establish that a small, stable name table is cheap enough to duplicate where it's used
rather than pre-consolidate (DESIGN.md §7).
"""

from __future__ import annotations

import argparse
import re
import sys
from typing import Any

from t42.cli import render
from t42.cli.command import Command
from t42.cli.context import build_client, emit

_MARKS_RE = re.compile(r"^(\d+)-marks?$")

_SUIT_NAMES: dict[str, int] = {
    "blanks": 0,
    "aces": 1,
    "deuces": 2,
    "treys": 3,
    "fours": 4,
    "fives": 5,
    "sixes": 6,
    "doubles": 7,
}


def parse_suit(value: str) -> int:
    lowered = value.lower()
    if lowered in _SUIT_NAMES:
        return _SUIT_NAMES[lowered]
    try:
        suit = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"invalid suit {value!r}, expected 0-7 or "
            "blanks/aces/deuces/treys/fours/fives/sixes/doubles"
        ) from None
    if suit not in range(8):
        raise argparse.ArgumentTypeError(
            f"invalid suit {value!r}, expected 0-7 or "
            "blanks/aces/deuces/treys/fours/fives/sixes/doubles"
        )
    return suit


def _configure_status(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("code")


def _status(args: argparse.Namespace) -> int:
    client, _ = build_client(args)
    response = client.request("GET", f"/games/{args.code}")
    emit(args, response, render.render_game)
    return 0


def _games(args: argparse.Namespace) -> int:
    client, _ = build_client(args)
    response = client.request("GET", "/players/me/games")
    emit(args, response, render.render_games_list)
    return 0


def _configure_bid(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("code")
    parser.add_argument("token", metavar="32|pass|N-marks|confirm|decline")
    parser.add_argument("--contract", default=None)


def _bid(args: argparse.Namespace) -> int:
    body = _parse_bid_body(args.token, args.contract)
    if body is None:
        print(
            f"error: invalid bid {args.token!r}, expected a points bid (30-42), "
            "'pass', 'N-marks' (optionally with --contract), 'confirm', or 'decline'",
            file=sys.stderr,
        )
        return 2

    client, _ = build_client(args)
    response = client.request("POST", _moves_path(args.code), json=body, idempotent=True)
    emit(args, response, render.render_game)
    return 0


def _moves_path(code: str) -> str:
    """Every move goes to the one endpoint; the ``kind`` in the body says which move it is.

    The three commands here stay separate because they are three different things to *type*, not
    because they are three different things to send.
    """
    return f"/games/{code}/moves"


def _parse_bid_body(raw_token: str, contract: str | None) -> dict[str, Any] | None:
    token = raw_token.lower()

    if token in ("pass", "confirm", "decline"):
        if contract is not None:
            return None
        if token == "pass":
            return {"kind": "PASS"}
        return {"kind": "CONFIRM_BID", "accept": token == "confirm"}

    marks_match = _MARKS_RE.match(token)
    if marks_match is not None:
        body: dict[str, Any] = {"kind": "BID", "marks": int(marks_match.group(1))}
        if contract is not None:
            body["contract"] = contract
        return body

    try:
        points = int(token)
    except ValueError:
        return None
    if contract is not None:
        return None
    return {"kind": "BID", "points": points}


def _configure_declare(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("code")
    parser.add_argument("trump", type=_parse_trump_assignment, metavar="trump=<suit>|trump=none")


def _declare(args: argparse.Namespace) -> int:
    client, _ = build_client(args)
    response = client.request(
        "POST",
        _moves_path(args.code),
        json={"kind": "DECLARE_CONTRACT", "trump": args.trump},
        idempotent=True,
    )
    emit(args, response, render.render_game)
    return 0


def _parse_trump_assignment(raw: str) -> int | None:
    key, eq, value = raw.partition("=")
    if not eq or key != "trump":
        raise argparse.ArgumentTypeError(f"invalid {raw!r}, expected trump=<suit> or trump=none")
    if value == "none":
        return None
    return parse_suit(value)


def _configure_play(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("code")
    parser.add_argument("domino")
    # No "none" spelling here, unlike declare's trump=: a declared lead is either named via
    # --declare or omitted entirely, never explicitly declared absent.
    parser.add_argument("--declare", dest="declared_suit", type=parse_suit, default=None)


def _play(args: argparse.Namespace) -> int:
    client, _ = build_client(args)
    body: dict[str, Any] = {"kind": "PLAY_DOMINO", "domino": args.domino}
    if args.declared_suit is not None:
        body["declared_suit"] = args.declared_suit
    response = client.request("POST", _moves_path(args.code), json=body, idempotent=True)
    emit(args, response, render.render_game)
    return 0


COMMANDS: tuple[Command, ...] = (
    Command(
        name="status",
        help="show a table's current state",
        configure=_configure_status,
        handler=_status,
    ),
    Command(
        name="games",
        help="list your games",
        configure=lambda parser: None,
        handler=_games,
    ),
    Command(
        name="bid",
        help="bid, pass, or answer a plunge confirmation",
        configure=_configure_bid,
        handler=_bid,
    ),
    Command(
        name="declare",
        help="name trump after winning the bid",
        configure=_configure_declare,
        handler=_declare,
    ),
    Command(
        name="play",
        help="play a domino",
        configure=_configure_play,
        handler=_play,
    ),
)
