"""Shared test doubles for ``tests/notifications/`` (ROADMAP.md 4.1), mirroring the
``tests/cli/_helpers.py`` / ``FakeTransport`` convention of a per-package recording fake.

``FakeSender`` implements ``tricksy.notifications.sender.EmailSender`` without any network, and
records every send so a test can assert on ``to``/``subject``/``body``.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class SentEmail:
    to: str
    subject: str
    body: str


@dataclass(slots=True)
class FakeSender:
    sent: list[SentEmail] = field(default_factory=list)
    #: When set, ``send`` raises this instead of recording, once. Exists to exercise the
    #: send-failure retry path (ROADMAP.md 5.0): a notification must stay unclaimed until a send
    #: actually succeeds.
    fail_next: Exception | None = None

    def send(self, to: str, subject: str, body: str) -> None:
        if self.fail_next is not None:
            exc, self.fail_next = self.fail_next, None
            raise exc
        self.sent.append(SentEmail(to, subject, body))
