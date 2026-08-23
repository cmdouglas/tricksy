"""ROADMAP.md 4.1: the ``EmailSender`` protocol's implementations and env-var selection."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from tricksy.notifications.sender import (
    EMAIL_SENDER_ENV,
    SES_FROM_ADDRESS_ENV,
    ConsoleSender,
    SesSender,
    _sesv2_client,
    get_sender,
)


@pytest.fixture(autouse=True)
def _clear_sesv2_client_cache() -> Iterator[None]:
    _sesv2_client.cache_clear()
    yield
    _sesv2_client.cache_clear()


def test_console_sender_writes_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    ConsoleSender().send("player@example.com", "subject line", "body text")
    out = capsys.readouterr().out
    assert "player@example.com" in out
    assert "subject line" in out
    assert "body text" in out


def test_get_sender_defaults_to_console(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(EMAIL_SENDER_ENV, raising=False)
    assert isinstance(get_sender(), ConsoleSender)


def test_get_sender_explicit_console(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EMAIL_SENDER_ENV, "console")
    assert isinstance(get_sender(), ConsoleSender)


def test_get_sender_ses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EMAIL_SENDER_ENV, "ses")
    monkeypatch.setenv(SES_FROM_ADDRESS_ENV, "noreply@example.com")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    sender = get_sender()
    assert isinstance(sender, SesSender)
    assert sender.from_address == "noreply@example.com"


def test_get_sender_ses_without_from_address_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EMAIL_SENDER_ENV, "ses")
    monkeypatch.delenv(SES_FROM_ADDRESS_ENV, raising=False)
    with pytest.raises(RuntimeError, match=SES_FROM_ADDRESS_ENV):
        get_sender()


def test_get_sender_unknown_value_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EMAIL_SENDER_ENV, "carrier-pigeon")
    with pytest.raises(RuntimeError, match="carrier-pigeon"):
        get_sender()


class _RecordingClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def send_email(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


def test_ses_sender_calls_send_email_with_expected_shape() -> None:
    client = _RecordingClient()
    sender = SesSender(from_address="noreply@example.com", client=client)

    sender.send("player@example.com", "It's your turn", "body text")

    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["FromEmailAddress"] == "noreply@example.com"
    assert call["Destination"] == {"ToAddresses": ["player@example.com"]}
    assert call["Content"] == {
        "Simple": {
            "Subject": {"Data": "It's your turn"},
            "Body": {"Text": {"Data": "body text"}},
        }
    }
