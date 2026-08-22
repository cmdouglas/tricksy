"""The Phase 4 milestone (ROADMAP.md 4.7): a real game, played over HTTP against a real API,
notified by the real pump against real DynamoDB Local - not a spy standing in for any of them.

Every other test in this package proves one layer at a time: ``test_handler.py`` proves the
classifier and the dedup/recipient-filtering logic against moto, ``test_pump.py`` proves the pump
decodes a real stream correctly, ``tests/api/test_contacts.py`` proves the verification round
trip over HTTP. This test proves the seam between all of them still holds when nothing is faked
except the outbound mail transport - the same "moto is an approximation, DynamoDB Local is not"
reasoning ``tests/storage/test_repository_integration.py`` and ``tests/api/test_api_integration.py``
already give for their own layers.

Marked ``integration`` and excluded from the default run, since it needs Docker: run it with
``uv run pytest -m integration``.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator

import boto3
import pytest
from fastapi.testclient import TestClient
from mypy_boto3_dynamodb.client import DynamoDBClient
from mypy_boto3_dynamodb.service_resource import Table
from mypy_boto3_dynamodbstreams.client import DynamoDBStreamsClient

from t42.api.app import app
from t42.api.deps import get_table
from t42.notifications import get_sender
from t42.notifications.handler import send_notifications
from t42.notifications.pump import poll

from ..api._helpers import Client, seated_game, submit, whose_turn
from ._helpers import FakeSender

pytestmark = pytest.mark.integration

#: One of the four never verifies its contact, so the test proves the unverified gate end to end
#: against real infra rather than only through test_handler.py's moto-backed unit tests.
_UNVERIFIED_USERNAME = "dave"
_USERNAMES = ("alice", "bob", "carol", _UNVERIFIED_USERNAME)


@pytest.fixture
def fake_sender() -> FakeSender:
    return FakeSender()


@pytest.fixture
def real_client(real_table: Table, fake_sender: FakeSender) -> Iterator[TestClient]:
    """The same app ``tests/api/test_api_integration.py``'s ``real_client`` drives, with mail
    routed to a recording sender the same way ``tests/api/conftest.py``'s moto-backed ``client``
    already does - only the table is real here, not the transport."""
    app.dependency_overrides[get_table] = lambda: real_table
    app.dependency_overrides[get_sender] = lambda: fake_sender
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def players(real_client: TestClient) -> list[Client]:
    return [Client.register(real_client, name) for name in _USERNAMES]


@pytest.fixture
def dynamodb_client(dynamodb_local: str) -> DynamoDBClient:
    return boto3.client(
        "dynamodb",
        endpoint_url=dynamodb_local,
        region_name="us-east-1",
        aws_access_key_id="local",
        aws_secret_access_key="local",
    )


@pytest.fixture
def streams_client(dynamodb_local: str) -> DynamoDBStreamsClient:
    return boto3.client(
        "dynamodbstreams",
        endpoint_url=dynamodb_local,
        region_name="us-east-1",
        aws_access_key_id="local",
        aws_secret_access_key="local",
    )


def _token_from(sender: FakeSender) -> str:
    match = re.search(r"t42 contact confirm (\S+)", sender.sent[-1].body)
    assert match is not None, sender.sent[-1].body
    return match.group(1)


def _verify_email(real_client: TestClient, player: Client, fake_sender: FakeSender) -> str:
    """The real HTTP round trip ``tests/api/test_contacts.py`` already proves, driven here for
    real: add a contact, mail a verification token, redeem it signed out."""
    address = f"{player.username}@example.com"
    response = player.post("/players/me/contacts", {"kind": "email", "address": address})
    assert response.status_code == 201, response.text
    response = player.post(f"/players/me/contacts/{address}/verification")
    assert response.status_code == 202, response.text
    token = _token_from(fake_sender)
    response = real_client.post("/contacts/verify", json={"token": token})
    assert response.status_code == 204, response.text
    return address


@pytest.fixture
def addresses(
    players: list[Client], real_client: TestClient, fake_sender: FakeSender
) -> dict[str, str | None]:
    """username -> verified email address, or ``None`` for the one player who adds a contact but
    never verifies it."""
    result: dict[str, str | None] = {}
    for player in players:
        if player.username == _UNVERIFIED_USERNAME:
            response = player.post(
                "/players/me/contacts",
                {"kind": "email", "address": f"{player.username}@example.com"},
            )
            assert response.status_code == 201, response.text
            result[player.username] = None
        else:
            result[player.username] = _verify_email(real_client, player, fake_sender)
    fake_sender.sent.clear()  # only gameplay notifications matter below
    return result


def test_the_right_seat_is_mailed_at_each_turn_across_a_real_delay(
    players: list[Client],
    addresses: dict[str, str | None],
    real_table: Table,
    fake_sender: FakeSender,
    dynamodb_client: DynamoDBClient,
    streams_client: DynamoDBStreamsClient,
) -> None:
    def drain() -> None:
        """One pump cycle against the real stream. ``poll`` starts a fresh ``TRIM_HORIZON``
        iterator on every call (it has no state to persist between them), so every drain
        re-reads the entire stream so far - which makes this test exercise real record
        redelivery on every single move, not just once at the end."""
        poll(
            dynamodb_client,
            streams_client,
            real_table.name,
            handler=lambda event, _ctx: send_notifications(
                real_table, fake_sender, event["Records"]
            ),
            max_iterations=1,
            sleep=lambda _: None,
        )

    game_id = seated_game(players, marks_to_win=1)

    # Each seat's PLAYER#/GAME# item is first created (an INSERT, no old image) when that player
    # joins - never a notification. Dealing the first hand, on the fourth join, then *updates*
    # that already-existing item to flip is_my_turn true for whoever bids first: a real
    # false->true transition, so the very first actor legitimately gets a "your turn" email.
    first_actor, _ = whose_turn(players, game_id)
    drain()
    expected_first_address = addresses[first_actor.username]
    if expected_first_address is None:
        assert fake_sender.sent == []
    else:
        assert len(fake_sender.sent) == 1, fake_sender.sent
        assert fake_sender.sent[0].to == expected_first_address

    moves_played = 0
    while True:
        player, view = whose_turn(players, game_id)
        response = submit(player, game_id, view["legal_moves"][0])
        assert response.status_code == 200, response.text
        body = response.json()
        finished = body["status"] == "COMPLETE" or (
            body["view"] is not None and body["view"]["phase"] == "GAME_OVER"
        )

        before = len(fake_sender.sent)
        drain()
        new_mail = fake_sender.sent[before:]

        if finished:
            verified_addresses = {a for a in addresses.values() if a is not None}
            assert {email.to for email in new_mail} == verified_addresses
            for email in new_mail:
                assert game_id in email.subject
                assert "Final score" in email.body
                # The labels come off META.scores, so this also proves the real denormalized
                # map reached the renderer rather than the empty-map default.
                assert "North/South" in email.body
                assert "East/West" in email.body
            break

        next_player, _ = whose_turn(players, game_id)
        if next_player.player_id == player.player_id:
            # The same seat acts again (e.g. leading the next trick after winning the last one):
            # is_my_turn never flipped false->true, so nothing should have been sent.
            assert new_mail == []
        else:
            expected_address = addresses[next_player.username]
            if expected_address is None:
                assert new_mail == []
            else:
                assert len(new_mail) == 1, new_mail
                assert new_mail[0].to == expected_address
                assert game_id in new_mail[0].subject

        moves_played += 1
        if moves_played == 1:
            # A genuine wall-clock gap, not a mocked clock (ROADMAP.md 4.7) - proves the dedup
            # write and the pump both survive real elapsed time between two moves.
            time.sleep(2)

    sent_before_replay = len(fake_sender.sent)
    drain()
    assert len(fake_sender.sent) == sent_before_replay, "a re-drained stream must send nothing new"
