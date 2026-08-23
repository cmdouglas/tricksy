"""Contact channels and email verification over HTTP (ROADMAP.md 4.2, DESIGN.md §6.1)."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from ..notifications._helpers import FakeSender
from ._helpers import Client


def test_adding_a_contact_returns_it_unverified_and_unmuted(
    client: TestClient, alice: Client
) -> None:
    response = alice.post("/players/me/contacts", {"kind": "email", "address": "a@example.com"})

    assert response.status_code == 201
    assert response.json() == {
        "kind": "email",
        "address": "a@example.com",
        "verified": False,
        "notify": True,
    }


def test_a_duplicate_address_is_a_conflict(client: TestClient, alice: Client) -> None:
    alice.post("/players/me/contacts", {"kind": "email", "address": "a@example.com"})

    response = alice.post("/players/me/contacts", {"kind": "email", "address": "a@example.com"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONTACT_ALREADY_EXISTS"


def test_adding_a_contact_requires_authentication(client: TestClient) -> None:
    response = client.post(
        "/players/me/contacts", json={"kind": "email", "address": "a@example.com"}
    )

    assert response.status_code == 401


def test_adding_a_contact_with_a_missing_field_is_a_422(client: TestClient, alice: Client) -> None:
    response = alice.post("/players/me/contacts", {"kind": "email"})

    assert response.status_code == 422


def test_listing_contacts_reflects_verified_and_notify_flags(
    client: TestClient, alice: Client
) -> None:
    alice.post("/players/me/contacts", {"kind": "email", "address": "a@example.com"})

    response = alice.get("/players/me/contacts")

    assert response.status_code == 200
    assert response.json() == {
        "contacts": [
            {"kind": "email", "address": "a@example.com", "verified": False, "notify": True}
        ]
    }


def test_listing_contacts_requires_authentication(client: TestClient) -> None:
    assert client.get("/players/me/contacts").status_code == 401


def test_muting_a_contact_toggles_notify(client: TestClient, alice: Client) -> None:
    alice.post("/players/me/contacts", {"kind": "email", "address": "a@example.com"})

    response = alice.patch("/players/me/contacts/a@example.com", {"notify": False})

    assert response.status_code == 200
    assert response.json()["notify"] is False
    assert alice.get("/players/me/contacts").json()["contacts"][0]["notify"] is False


def test_muting_an_unknown_address_is_a_404(client: TestClient, alice: Client) -> None:
    response = alice.patch("/players/me/contacts/nobody@example.com", {"notify": False})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CONTACT_NOT_FOUND"


def test_muting_with_a_malformed_body_is_a_422(client: TestClient, alice: Client) -> None:
    alice.post("/players/me/contacts", {"kind": "email", "address": "a@example.com"})

    response = alice.patch("/players/me/contacts/a@example.com", {"notify": "not-a-bool"})

    assert response.status_code == 422


def test_removing_a_contact_deletes_it(client: TestClient, alice: Client) -> None:
    alice.post("/players/me/contacts", {"kind": "email", "address": "a@example.com"})

    response = alice.delete("/players/me/contacts/a@example.com")

    assert response.status_code == 204
    assert alice.get("/players/me/contacts").json()["contacts"] == []


def test_removing_an_unknown_address_is_a_404(client: TestClient, alice: Client) -> None:
    response = alice.delete("/players/me/contacts/nobody@example.com")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CONTACT_NOT_FOUND"


def test_verification_mails_a_single_email_to_the_address(
    client: TestClient, alice: Client, fake_sender: FakeSender
) -> None:
    alice.post("/players/me/contacts", {"kind": "email", "address": "a@example.com"})

    response = alice.post("/players/me/contacts/a@example.com/verification")

    assert response.status_code == 202
    assert len(fake_sender.sent) == 1
    assert fake_sender.sent[0].to == "a@example.com"


def test_verification_for_an_unknown_address_is_a_404(client: TestClient, alice: Client) -> None:
    response = alice.post("/players/me/contacts/nobody@example.com/verification")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CONTACT_NOT_FOUND"


def _token_from(sender: FakeSender) -> str:
    match = re.search(r"tricksy contact confirm (\S+)", sender.sent[-1].body)
    assert match is not None, sender.sent[-1].body
    return match.group(1)


def test_redeeming_a_verification_token_works_signed_out_and_marks_the_channel_verified(
    client: TestClient, alice: Client, fake_sender: FakeSender
) -> None:
    alice.post("/players/me/contacts", {"kind": "email", "address": "a@example.com"})
    alice.post("/players/me/contacts/a@example.com/verification")
    token = _token_from(fake_sender)

    # No Authorization header at all - the token is the credential (DESIGN.md §6.1).
    response = client.post("/contacts/verify", json={"token": token})

    assert response.status_code == 204
    assert alice.get("/players/me/contacts").json()["contacts"][0]["verified"] is True


def test_redeeming_the_same_token_twice_is_rejected(
    client: TestClient, alice: Client, fake_sender: FakeSender
) -> None:
    alice.post("/players/me/contacts", {"kind": "email", "address": "a@example.com"})
    alice.post("/players/me/contacts/a@example.com/verification")
    token = _token_from(fake_sender)
    client.post("/contacts/verify", json={"token": token})

    response = client.post("/contacts/verify", json={"token": token})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_VERIFICATION_TOKEN"


def test_redeeming_a_garbage_token_is_rejected(client: TestClient) -> None:
    response = client.post("/contacts/verify", json={"token": "not-a-real-token"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_VERIFICATION_TOKEN"


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="missing token"),
        pytest.param({"token": "abc", "extra": "nope"}, id="unknown field"),
    ],
)
def test_verify_with_a_malformed_body_is_a_422(client: TestClient, payload: dict[str, str]) -> None:
    assert client.post("/contacts/verify", json=payload).status_code == 422
