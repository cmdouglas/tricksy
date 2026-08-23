"""Password reset over HTTP (ROADMAP.md 4.3, DESIGN.md §6.1)."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from ..notifications._helpers import FakeSender
from ._helpers import PASSWORD, Client


def _verify(client: TestClient, player: Client, address: str, fake_sender: FakeSender) -> None:
    """Adds and verifies one contact channel, through the real 4.2 endpoints."""
    player.post("/players/me/contacts", {"kind": "email", "address": address})
    player.post(f"/players/me/contacts/{address}/verification")
    match = re.search(r"tricksy contact confirm (\S+)", fake_sender.sent[-1].body)
    assert match is not None, fake_sender.sent[-1].body
    response = client.post("/contacts/verify", json={"token": match.group(1)})
    assert response.status_code == 204, response.text


def _reset_token_from(sender: FakeSender) -> str:
    match = re.search(r"tricksy reset-password (\S+)", sender.sent[-1].body)
    assert match is not None, sender.sent[-1].body
    return match.group(1)


def test_request_for_an_unknown_username_is_202_with_no_email(
    client: TestClient, fake_sender: FakeSender
) -> None:
    response = client.post("/password-resets", json={"username": "nobody"})

    assert response.status_code == 202
    assert fake_sender.sent == []


def test_request_for_a_username_with_no_verified_contact_is_202_with_no_email(
    client: TestClient, alice: Client, fake_sender: FakeSender
) -> None:
    alice.post("/players/me/contacts", {"kind": "email", "address": "a@example.com"})

    response = client.post("/password-resets", json={"username": "alice"})

    assert response.status_code == 202
    assert fake_sender.sent == []


def test_request_for_a_username_with_a_verified_contact_mails_it(
    client: TestClient, alice: Client, fake_sender: FakeSender
) -> None:
    _verify(client, alice, "a@example.com", fake_sender)
    sent_before = len(fake_sender.sent)

    response = client.post("/password-resets", json={"username": "alice"})

    assert response.status_code == 202
    assert len(fake_sender.sent) == sent_before + 1
    assert fake_sender.sent[-1].to == "a@example.com"


def test_request_mails_only_the_first_verified_contact(
    client: TestClient, alice: Client, fake_sender: FakeSender
) -> None:
    """Not every verified contact: one send, so a mid-flight failure across multiple addresses
    can never happen (ROADMAP.md 4.3)."""
    _verify(client, alice, "a@example.com", fake_sender)
    _verify(client, alice, "b@example.com", fake_sender)

    sent_before = len(fake_sender.sent)

    response = client.post("/password-resets", json={"username": "alice"})

    assert response.status_code == 202
    reset_mails = fake_sender.sent[sent_before:]
    assert [m.to for m in reset_mails] == ["a@example.com"]


def test_confirming_a_reset_changes_the_password_and_revokes_every_device(
    client: TestClient, alice: Client, fake_sender: FakeSender
) -> None:
    _verify(client, alice, "a@example.com", fake_sender)
    old_token = alice.token

    client.post("/password-resets", json={"username": "alice"})
    token = _reset_token_from(fake_sender)

    response = client.post(
        "/password-resets/confirm", json={"token": token, "new_password": "new-password-123"}
    )

    assert response.status_code == 204
    # The old device is signed out.
    old_auth = {"Authorization": f"Bearer {old_token}"}
    assert client.get("/players/me", headers=old_auth).status_code == 401
    # The old password no longer works, the new one does.
    old_login = client.post("/sessions", json={"username": "alice", "password": PASSWORD})
    assert old_login.status_code == 401
    new_login = client.post("/sessions", json={"username": "alice", "password": "new-password-123"})
    assert new_login.status_code == 201


def test_confirming_with_an_unissued_token_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/password-resets/confirm", json={"token": "not-a-real-token", "new_password": "x" * 12}
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_RESET_TOKEN"


def test_confirming_with_the_same_token_twice_is_rejected(
    client: TestClient, alice: Client, fake_sender: FakeSender
) -> None:
    _verify(client, alice, "a@example.com", fake_sender)
    client.post("/password-resets", json={"username": "alice"})
    token = _reset_token_from(fake_sender)
    client.post("/password-resets/confirm", json={"token": token, "new_password": "x" * 12})

    response = client.post(
        "/password-resets/confirm", json={"token": token, "new_password": "y" * 12}
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_RESET_TOKEN"


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"username": "a" * 33}, id="username too long"),
        pytest.param({}, id="missing username"),
    ],
)
def test_request_with_a_malformed_body_is_a_422(
    client: TestClient, payload: dict[str, str]
) -> None:
    assert client.post("/password-resets", json=payload).status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"token": "abc"}, id="missing new_password"),
        pytest.param({"token": "abc", "new_password": "short"}, id="new_password too short"),
        pytest.param(
            {"token": "abc", "new_password": "x" * 12, "extra": "nope"}, id="unknown field"
        ),
    ],
)
def test_confirm_with_a_malformed_body_is_a_422(
    client: TestClient, payload: dict[str, str]
) -> None:
    assert client.post("/password-resets/confirm", json=payload).status_code == 422
