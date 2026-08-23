"""The email transport (ROADMAP.md 4.1).

``EmailSender`` is a narrow protocol - ``send(to, subject, body) -> None`` - rather than a bare
function, for the same reason ``tricksy.cli.api``'s ``Transport`` is a protocol: DESIGN.md §11's
claim that SMS, push or a chat DM is a new implementation rather than a rewrite only holds if the
send channel is something pluggable in the first place.

``get_sender`` picks between implementations by environment variable, the same shape
``tricksy.api.deps`` uses for the table handle - but the default is inverted on purpose.
``tricksy.api.deps.get_table`` refuses to guess a table name because guessing wrong means writing to
production; here, guessing wrong means printing to stdout instead of emailing, which is the safe
failure mode, so an unset environment leaves a local run working rather than crashing it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

import boto3

#: Selects the ``EmailSender`` implementation: "console" (default) or "ses".
EMAIL_SENDER_ENV = "TRICKSY_EMAIL_SENDER"
#: Required only when TRICKSY_EMAIL_SENDER=ses.
SES_FROM_ADDRESS_ENV = "TRICKSY_SES_FROM_ADDRESS"


class EmailSender(Protocol):
    def send(self, to: str, subject: str, body: str) -> None: ...


@dataclass(slots=True)
class ConsoleSender:
    """Writes the email to stdout instead of sending it - the default, so a local run without SES
    configured stays legible rather than silently doing nothing."""

    def send(self, to: str, subject: str, body: str) -> None:
        print(f"--- email to {to} ---")
        print(f"Subject: {subject}")
        print()
        print(body)


@dataclass(slots=True)
class SesSender:
    """Sends through SES ``sesv2``. ``client`` is kept untyped, the same way
    ``tricksy.api.deps``'s ``_resource()`` keeps the DynamoDB resource untyped - it avoids a
    dependency on ``sesv2`` boto3 stubs for one narrow call site."""

    from_address: str
    client: object

    def send(self, to: str, subject: str, body: str) -> None:
        self.client.send_email(  # type: ignore[attr-defined]
            FromEmailAddress=self.from_address,
            Destination={"ToAddresses": [to]},
            Content={"Simple": {"Subject": {"Data": subject}, "Body": {"Text": {"Data": body}}}},
        )


@lru_cache(maxsize=1)
def _sesv2_client() -> object:
    return boto3.client("sesv2")


def get_sender() -> EmailSender:
    """The ``EmailSender`` for this process, read fresh from the environment on every call (unlike
    the cached boto3 client underneath it) so tests can vary it per case."""
    kind = os.environ.get(EMAIL_SENDER_ENV, "console")
    if kind == "console":
        return ConsoleSender()
    if kind == "ses":
        from_address = os.environ.get(SES_FROM_ADDRESS_ENV)
        if not from_address:
            raise RuntimeError(f"{SES_FROM_ADDRESS_ENV} is not set")
        return SesSender(from_address=from_address, client=_sesv2_client())
    raise RuntimeError(f"unknown {EMAIL_SENDER_ENV}: {kind!r}")
