"""ROADMAP.md 4.5: the notification handler - who gets told what, and when."""

from __future__ import annotations

from typing import Any

import pytest
from boto3.dynamodb.types import TypeSerializer
from mypy_boto3_dynamodb.service_resource import Table

from tricksy.notifications import handler
from tricksy.notifications.handler import Notification, notifications_for, send_notifications
from tricksy.storage.accounts import ContactChannel, create_player

from ._helpers import FakeSender

_serializer = TypeSerializer()


def _image(**attrs: Any) -> dict[str, Any]:
    return {key: _serializer.serialize(value) for key, value in attrs.items()}


def _record(
    event_name: str,
    *,
    pk: str,
    sk: str,
    old: dict[str, Any] | None = None,
    new: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"Keys": _image(PK=pk, SK=sk)}
    if old is not None:
        body["OldImage"] = _image(PK=pk, SK=sk, **old)
    if new is not None:
        body["NewImage"] = _image(PK=pk, SK=sk, **new)
    return {"eventName": event_name, "dynamodb": body}


def _game_record(
    player_id: str,
    game_id: str,
    *,
    old: dict[str, Any],
    new: dict[str, Any],
    event_name: str = "MODIFY",
) -> dict[str, Any]:
    return _record(event_name, pk=f"PLAYER#{player_id}", sk=f"GAME#{game_id}", old=old, new=new)


def _invite_record(
    player_id: str, game_id: str, *, event_name: str = "INSERT", **new: Any
) -> dict[str, Any]:
    return _record(event_name, pk=f"PLAYER#{player_id}", sk=f"INVITE#{game_id}", new=new)


# ------------------------------------------------------------------------- notifications_for


def test_your_turn_transition_is_classified() -> None:
    record = _game_record(
        "p1",
        "g1",
        old={"is_my_turn": False, "status": "ACTIVE", "version": 3},
        new={"is_my_turn": True, "status": "ACTIVE", "version": 4},
    )

    assert notifications_for([record]) == [Notification("your_turn", "g1", "p1", version=4)]


def test_game_over_transition_is_classified() -> None:
    record = _game_record(
        "p1",
        "g1",
        old={"is_my_turn": False, "status": "ACTIVE", "version": 9},
        new={"is_my_turn": False, "status": "COMPLETE", "version": 10},
    )

    assert notifications_for([record]) == [Notification("game_over", "g1", "p1", version=10)]


def test_invite_insert_is_classified() -> None:
    record = _invite_record("p1", "g1", invited_by="north", created_at="2026-01-01T00:00:00")

    assert notifications_for([record]) == [Notification("invite", "g1", "p1", invited_by="north")]


def test_initial_join_insert_is_not_a_your_turn_transition() -> None:
    """lobby.join_seat's first write to a PLAYER#/GAME# item has no old image at all - even if
    the joiner happens to be the dealer and is_my_turn is already true, there was no flip."""
    record = _record(
        "INSERT",
        pk="PLAYER#p1",
        sk="GAME#g1",
        new={"is_my_turn": True, "status": "WAITING", "version": 1},
    )

    assert notifications_for([record]) == []


def test_own_dedup_write_is_ignored() -> None:
    """The handler's own claim only touches notified_version - is_my_turn/status are unchanged -
    so the MODIFY record it produces must match neither rule, or the handler would notify itself
    forever."""
    record = _game_record(
        "p1",
        "g1",
        old={"is_my_turn": True, "status": "ACTIVE", "version": 4, "notified_version": 3},
        new={"is_my_turn": True, "status": "ACTIVE", "version": 4, "notified_version": 4},
    )

    assert notifications_for([record]) == []


def test_remove_of_game_item_is_ignored() -> None:
    record = _record(
        "REMOVE",
        pk="PLAYER#p1",
        sk="GAME#g1",
        old={"is_my_turn": True, "status": "ACTIVE", "version": 4},
    )

    assert notifications_for([record]) == []


def test_remove_of_invite_is_ignored() -> None:
    """A revoked or declined invite must not fire - only an INSERT does."""
    record = _record("REMOVE", pk="PLAYER#p1", sk="INVITE#g1", old={"invited_by": "north"})

    assert notifications_for([record]) == []


def test_modify_of_invite_is_ignored() -> None:
    """A re-invite overwrites rather than raising (invites.py), producing a MODIFY - only the
    first INSERT should ever notify."""
    record = _record(
        "MODIFY",
        pk="PLAYER#p1",
        sk="INVITE#g1",
        old={"invited_by": "north"},
        new={"invited_by": "north"},
    )

    assert notifications_for([record]) == []


def test_non_player_partition_records_are_ignored() -> None:
    records = [
        _record("MODIFY", pk="GAME#g1", sk="EVENT#000001", new={"type": "PLAY_DOMINO"}),
        _record("MODIFY", pk="GAME#g1", sk="STATE", old={"version": 1}, new={"version": 2}),
        _record("MODIFY", pk="GAME#g1", sk="META", new={"status": "ACTIVE"}),
    ]

    assert notifications_for(records) == []


def test_other_player_sk_prefixes_are_ignored() -> None:
    records = [
        _record("MODIFY", pk="PLAYER#p1", sk="PROFILE", new={"username": "p1"}),
        _record("INSERT", pk="PLAYER#p1", sk="TOKEN#abc", new={"label": "phone"}),
        _record("INSERT", pk="PLAYER#p1", sk="RULESET#abc", new={"name": "my rules"}),
    ]

    assert notifications_for(records) == []


def test_transition_missing_version_is_not_claimable() -> None:
    """A game predating this phase's version stamp - or any malformed item - must be skipped
    rather than emitted with version=None, which would make the dedup condition permanently
    unsatisfiable."""
    record = _game_record(
        "p1",
        "g1",
        old={"is_my_turn": False, "status": "ACTIVE"},
        new={"is_my_turn": True, "status": "ACTIVE"},
    )

    assert notifications_for([record]) == []


def test_your_turn_and_game_over_cannot_both_fire_from_one_record() -> None:
    record = _game_record(
        "p1",
        "g1",
        old={"is_my_turn": False, "status": "ACTIVE", "version": 4},
        new={"is_my_turn": True, "status": "COMPLETE", "version": 5},
    )

    notifications = notifications_for([record])

    assert len(notifications) == 1
    assert notifications[0].kind == "your_turn"


# ------------------------------------------------------------------------- send_notifications


def _player_with_contact(
    table: Table, username: str, *, verified: bool = True, notify: bool = True, kind: str = "email"
) -> str:
    player = create_player(
        table,
        username,
        "password123",
        contacts=[
            ContactChannel(kind, f"{username}@example.com", verified=verified, notify=notify)
        ],
    )
    return player.player_id


def test_your_turn_sends_email_and_stamps_notified_version(table: Table) -> None:
    player_id = _player_with_contact(table, "alice")
    record = _game_record(
        player_id,
        "g1",
        old={"is_my_turn": False, "status": "ACTIVE", "version": 3},
        new={"is_my_turn": True, "status": "ACTIVE", "version": 4},
    )
    sender = FakeSender()

    send_notifications(table, sender, [record])

    assert len(sender.sent) == 1
    email = sender.sent[0]
    assert email.to == "alice@example.com"
    assert "your turn" in email.subject.lower()
    assert "g1" in email.body

    item = table.get_item(Key={"PK": f"PLAYER#{player_id}", "SK": "GAME#g1"})["Item"]
    assert item["notified_version"] == 4


def test_duplicate_record_sends_nothing_second_time(table: Table) -> None:
    player_id = _player_with_contact(table, "alice")
    record = _game_record(
        player_id,
        "g1",
        old={"is_my_turn": False, "status": "ACTIVE", "version": 3},
        new={"is_my_turn": True, "status": "ACTIVE", "version": 4},
    )
    sender = FakeSender()

    send_notifications(table, sender, [record])
    send_notifications(table, sender, [record])  # redelivered

    assert len(sender.sent) == 1


def test_game_over_reads_final_scores_from_meta(table: Table) -> None:
    player_id = _player_with_contact(table, "alice")
    table.put_item(
        Item={
            "PK": "GAME#g1",
            "SK": "META",
            "scores": {"north_south": 7, "east_west": 3},
        }
    )
    record = _game_record(
        player_id,
        "g1",
        old={"is_my_turn": False, "status": "ACTIVE", "version": 9},
        new={"is_my_turn": False, "status": "COMPLETE", "version": 10},
    )
    sender = FakeSender()

    send_notifications(table, sender, [record])

    assert len(sender.sent) == 1
    assert "7" in sender.sent[0].body
    assert "3" in sender.sent[0].body


def test_game_over_still_sends_when_meta_has_no_scores(table: Table) -> None:
    """A degraded email beats a dropped one: `_claim` has already succeeded by the time the scores
    are read, so this transition will never be retried."""
    player_id = _player_with_contact(table, "alice")
    record = _game_record(
        player_id,
        "g1",
        old={"is_my_turn": False, "status": "ACTIVE", "version": 9},
        new={"is_my_turn": False, "status": "COMPLETE", "version": 10},
    )
    sender = FakeSender()

    send_notifications(table, sender, [record])

    assert len(sender.sent) == 1
    assert "g1" in sender.sent[0].body


def test_game_over_renders_whatever_score_labels_meta_carries(table: Table) -> None:
    """The handler names no scoring side of its own - a game that scores per player rather than
    per partnership needs no change here (DESIGN.md §11)."""
    player_id = _player_with_contact(table, "alice")
    table.put_item(
        Item={"PK": "GAME#g1", "SK": "META", "scores": {"alice": 12, "bob": 4, "carol": 9}}
    )
    record = _game_record(
        player_id,
        "g1",
        old={"is_my_turn": False, "status": "ACTIVE", "version": 9},
        new={"is_my_turn": False, "status": "COMPLETE", "version": 10},
    )
    sender = FakeSender()

    send_notifications(table, sender, [record])

    body = sender.sent[0].body
    assert "Alice 12" in body
    assert "Bob 4" in body
    assert "Carol 9" in body


def test_invite_sends_with_invited_by(table: Table) -> None:
    player_id = _player_with_contact(table, "alice")
    record = _invite_record(player_id, "g1", invited_by="north", created_at="2026-01-01T00:00:00")
    sender = FakeSender()

    send_notifications(table, sender, [record])

    assert len(sender.sent) == 1
    assert "north" in sender.sent[0].body

    item = table.get_item(Key={"PK": f"PLAYER#{player_id}", "SK": "INVITE#g1"})["Item"]
    assert item["notified"] is True


def test_duplicate_invite_insert_sends_nothing_second_time(table: Table) -> None:
    player_id = _player_with_contact(table, "alice")
    record = _invite_record(player_id, "g1", invited_by="north", created_at="2026-01-01T00:00:00")
    sender = FakeSender()

    send_notifications(table, sender, [record])
    send_notifications(table, sender, [record])

    assert len(sender.sent) == 1


def test_unverified_channel_sends_nothing_but_still_claims(table: Table) -> None:
    player_id = _player_with_contact(table, "alice", verified=False)
    record = _game_record(
        player_id,
        "g1",
        old={"is_my_turn": False, "status": "ACTIVE", "version": 3},
        new={"is_my_turn": True, "status": "ACTIVE", "version": 4},
    )
    sender = FakeSender()

    send_notifications(table, sender, [record])

    assert sender.sent == []
    item = table.get_item(Key={"PK": f"PLAYER#{player_id}", "SK": "GAME#g1"})["Item"]
    assert item["notified_version"] == 4


def test_muted_channel_sends_nothing(table: Table) -> None:
    player_id = _player_with_contact(table, "alice", notify=False)
    record = _game_record(
        player_id,
        "g1",
        old={"is_my_turn": False, "status": "ACTIVE", "version": 3},
        new={"is_my_turn": True, "status": "ACTIVE", "version": 4},
    )
    sender = FakeSender()

    send_notifications(table, sender, [record])

    assert sender.sent == []


def test_no_contacts_sends_nothing(table: Table) -> None:
    player = create_player(table, "alice", "password123")
    record = _game_record(
        player.player_id,
        "g1",
        old={"is_my_turn": False, "status": "ACTIVE", "version": 3},
        new={"is_my_turn": True, "status": "ACTIVE", "version": 4},
    )
    sender = FakeSender()

    send_notifications(table, sender, [record])

    assert sender.sent == []


def test_non_email_channel_kind_is_ignored(table: Table) -> None:
    player_id = _player_with_contact(table, "alice", kind="sms")
    record = _game_record(
        player_id,
        "g1",
        old={"is_my_turn": False, "status": "ACTIVE", "version": 3},
        new={"is_my_turn": True, "status": "ACTIVE", "version": 4},
    )
    sender = FakeSender()

    send_notifications(table, sender, [record])

    assert sender.sent == []


def test_missing_player_is_skipped_without_crashing_the_batch(table: Table) -> None:
    real_player_id = _player_with_contact(table, "alice")
    records = [
        _game_record(
            "no-such-player",
            "g1",
            old={"is_my_turn": False, "status": "ACTIVE", "version": 3},
            new={"is_my_turn": True, "status": "ACTIVE", "version": 4},
        ),
        _game_record(
            real_player_id,
            "g2",
            old={"is_my_turn": False, "status": "ACTIVE", "version": 3},
            new={"is_my_turn": True, "status": "ACTIVE", "version": 4},
        ),
    ]
    sender = FakeSender()

    send_notifications(table, sender, records)

    assert len(sender.sent) == 1
    assert sender.sent[0].to == "alice@example.com"


# ------------------------------------------------------------------------- lambda_handler


def test_lambda_handler_delegates_records_to_send_notifications(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Any] = []

    def fake_send_notifications(table: Any, sender: Any, records: Any) -> None:
        calls.append((table, sender, list(records)))

    monkeypatch.setattr(handler, "_get_table", lambda: "the-table")
    monkeypatch.setattr(handler, "get_sender", lambda: "the-sender")
    monkeypatch.setattr(handler, "send_notifications", fake_send_notifications)

    handler.lambda_handler({"Records": [{"eventName": "INSERT"}]})

    assert calls == [("the-table", "the-sender", [{"eventName": "INSERT"}])]


def test_lambda_handler_defaults_missing_records_key_to_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Any] = []

    def fake_send_notifications(table: Any, sender: Any, records: Any) -> None:
        calls.append(list(records))

    monkeypatch.setattr(handler, "_get_table", lambda: "the-table")
    monkeypatch.setattr(handler, "get_sender", lambda: "the-sender")
    monkeypatch.setattr(handler, "send_notifications", fake_send_notifications)

    handler.lambda_handler({})

    assert calls == [[]]
