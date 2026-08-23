"""The send channel for player notifications (DESIGN.md §8, ROADMAP.md Phase 4).

``tricksy.notifications`` may import ``tricksy.storage.accounts`` and ``boto3``; nothing else from
``tricksy.storage``, and nothing from any game engine under ``tricksy.games``. That is what makes
"the notifier cannot see a hand" checkable rather than merely claimed: ``repository``, ``codec``
and ``replay`` are the only path to ``STATE`` or a ``GameState``, and this package never imports
them (enforced by ROADMAP.md 4.7).

``sender`` is the transport - an ``EmailSender`` protocol with a console and an SES
implementation, chosen by environment variable. ``messages`` is pure content - a
``dict -> (subject, body)`` renderer per notification kind. ``records`` decodes a raw DynamoDB
Streams record into plain data, and ``pump`` (``python -m tricksy.notifications.pump``,
ROADMAP.md 4.4) polls the stream locally and hands batches to ``handler.lambda_handler``
(ROADMAP.md 4.5), which
decides *who* to notify and *when* for the three ``PLAYER#``-item-driven kinds via the pure
``handler.notifications_for`` plus the injectable ``handler.send_notifications``. Contact
verification (4.2) and password reset (4.3) are the exception: both are sent synchronously from
the API layer, not stream-driven, so ``render_verify_contact``/``render_password_reset`` already
have real callers.
"""

from __future__ import annotations

from .messages import (
    render_game_over,
    render_invite,
    render_password_reset,
    render_verify_contact,
    render_your_turn,
)
from .sender import (
    EMAIL_SENDER_ENV,
    SES_FROM_ADDRESS_ENV,
    ConsoleSender,
    EmailSender,
    SesSender,
    get_sender,
)

__all__ = [
    "EMAIL_SENDER_ENV",
    "SES_FROM_ADDRESS_ENV",
    "ConsoleSender",
    "EmailSender",
    "SesSender",
    "get_sender",
    "render_game_over",
    "render_invite",
    "render_password_reset",
    "render_verify_contact",
    "render_your_turn",
]
