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

    def send(self, to: str, subject: str, body: str) -> None:
        self.sent.append(SentEmail(to, subject, body))
