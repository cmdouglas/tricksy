"""Pure ``dict -> (subject, body)`` renderers, one per notification kind (ROADMAP.md 4.1).

No I/O, no client library: the interesting part of a notification is its content, and content
should be assertable without a transport - the same property ``t42.cli.render`` has for the same
reason. These renderers do not decide who gets notified or when; that is ROADMAP.md 4.5's handler,
which will call the function it needs by name, the same way ``t42.cli``'s command handlers call
``render.render_game`` and friends directly rather than through a dispatch table.

Every dict shares ``game_id`` (game ids double as join codes, DESIGN.md §4.1, so there is no
separate join-code key) and ``recipient_username``. ``render_game_over``'s ``scores`` key is an
open ``{label: int}`` map, passed through from ``META.scores`` exactly as stored - deliberately not
a fixed pair of partnership keys, so nothing in this package encodes how any one game scores
(DESIGN.md §11). ``render_invite``'s
``invited_by`` key does not exist in storage yet - ``invite_player`` currently writes only
``{game_id, created_at}`` - 4.5 adds it; this module just assumes it will be present.

``render_verify_contact`` (ROADMAP.md 4.2) and ``render_password_reset`` (ROADMAP.md 4.3) are the
odd ones out: neither is driven by a ``PLAYER#`` item transition the way the other three are
(4.5) - both are sent synchronously from the API layer, the moment a contact channel is added or
a reset is requested. Neither takes a ``recipient_username`` either: the address being verified,
or the token being redeemed, is enough to identify who is being written to.
"""

from __future__ import annotations

from typing import Any


def render_your_turn(data: dict[str, Any]) -> tuple[str, str]:
    """Expects: game_id, recipient_username."""
    game_id = data["game_id"]
    subject = f"It's your turn in game {game_id}"
    body = f"Hi {data['recipient_username']},\n\nIt's your turn in game {game_id}."
    return subject, body


def _score_label(label: str) -> str:
    """``"north_south"`` -> ``"North/South"``. A scoring side's stored label, made readable
    without knowing what sides the game has."""
    return label.replace("_", "/").title()


def render_game_over(data: dict[str, Any]) -> tuple[str, str]:
    """Expects: game_id, recipient_username, scores {label: int}.

    ``scores`` is an open map, not a fixed pair of partnership keys - whatever
    ``META.scores`` holds, in the order it was written. That is what lets this renderer
    belong to a package that knows nothing about how any particular game scores.
    """
    game_id = data["game_id"]
    scores: dict[str, Any] = data["scores"]
    subject = f"Game {game_id} is over"
    tally = (
        " - ".join(f"{_score_label(label)} {score}" for label, score in scores.items())
        or "unavailable"
    )
    body = f"Hi {data['recipient_username']},\n\nGame {game_id} is over. Final score: {tally}."
    return subject, body


def render_invite(data: dict[str, Any]) -> tuple[str, str]:
    """Expects: game_id, recipient_username, invited_by."""
    subject = "You've been invited to a table"
    body = (
        f"Hi {data['recipient_username']},\n\n"
        f"{data['invited_by']} has invited you to game {data['game_id']}."
    )
    return subject, body


def render_verify_contact(data: dict[str, Any]) -> tuple[str, str]:
    """Expects: address, token."""
    subject = "Verify this contact address"
    body = f"To verify {data['address']}, run:\n\n    t42 contact confirm {data['token']}\n"
    return subject, body


def render_password_reset(data: dict[str, Any]) -> tuple[str, str]:
    """Expects: token."""
    subject = "Reset your password"
    body = f"To reset your password, run:\n\n    t42 reset-password {data['token']}\n"
    return subject, body
