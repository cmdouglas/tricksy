"""Accounts and auth tokens (ROADMAP.md 2.1, 4.2, 4.3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from mypy_boto3_dynamodb.service_resource import Table

from tricksy.storage.accounts import (
    MAX_CONTACTS,
    ContactChannel,
    add_contact,
    authenticate,
    begin_password_reset,
    begin_verification,
    complete_password_reset,
    complete_verification,
    create_player,
    get_player,
    hash_token,
    issue_token,
    list_tokens,
    player_for_token,
    remove_contact,
    revoke_token,
    set_contact_notify,
)
from tricksy.storage.errors import (
    ContactAlreadyExists,
    ContactNotFound,
    InvalidCredentials,
    InvalidResetToken,
    InvalidToken,
    InvalidVerificationToken,
    TooManyContacts,
    TooManyDevices,
    UsernameTaken,
)


def test_create_player_round_trips_through_get_player(table: Table) -> None:
    player = create_player(
        table,
        "Charlie",
        "correct horse battery staple",
        [ContactChannel(kind="email", address="c@example.com")],
    )

    assert get_player(table, player.player_id) == player
    assert player.username == "Charlie"
    assert player.contacts == (ContactChannel("email", "c@example.com", verified=False),)


def test_a_player_needs_no_contact_channels(table: Table) -> None:
    """Nothing requires an email address (DESIGN.md §12): notifications are a later, optional
    thing rather than a condition of having an account."""
    player = create_player(table, "hermit", "pw")

    assert get_player(table, player.player_id).contacts == ()


def test_usernames_are_unique_case_insensitively(table: Table) -> None:
    create_player(table, "Charlie", "pw")

    with pytest.raises(UsernameTaken):
        create_player(table, "charlie", "different-password")


def test_player_ids_are_opaque_not_usernames(table: Table) -> None:
    """Invariant 6 makes the event log immutable, so a renameable username must never be what
    identifies a player in it."""
    player = create_player(table, "charlie", "pw")

    assert player.player_id != "charlie"
    assert "charlie" not in player.player_id.lower()


def test_the_password_hash_never_leaves_the_module(table: Table) -> None:
    player = create_player(table, "charlie", "hunter2")

    assert "hunter2" not in repr(get_player(table, player.player_id))

    stored = table.get_item(Key={"PK": f"PLAYER#{player.player_id}", "SK": "PROFILE"})["Item"]
    assert "hunter2" not in str(stored["password_hash"])
    assert str(stored["password_hash"]).startswith("scrypt$")


def test_authenticate_accepts_the_right_password(table: Table) -> None:
    player = create_player(table, "charlie", "hunter2")

    assert authenticate(table, "charlie", "hunter2") == player.player_id
    assert authenticate(table, "CHARLIE", "hunter2") == player.player_id


@pytest.mark.parametrize(
    ("username", "password"),
    [
        pytest.param("charlie", "wrong", id="wrong password"),
        pytest.param("nobody", "hunter2", id="unknown username"),
    ],
)
def test_authenticate_rejects_bad_credentials(table: Table, username: str, password: str) -> None:
    create_player(table, "charlie", "hunter2")

    with pytest.raises(InvalidCredentials) as excinfo:
        authenticate(table, username, password)

    # The message must not say which half was wrong, or the endpoint becomes a username oracle.
    assert "username" not in str(excinfo.value).replace("invalid username or password", "")


def test_issue_token_resolves_back_to_its_player(table: Table) -> None:
    player = create_player(table, "charlie", "pw")
    token = issue_token(table, player.player_id, "laptop")

    assert player_for_token(table, token) == player.player_id


def test_an_unissued_token_is_rejected(table: Table) -> None:
    with pytest.raises(InvalidToken):
        player_for_token(table, "not-a-real-token")


def test_only_the_token_hash_is_stored(table: Table) -> None:
    player = create_player(table, "charlie", "pw")
    token = issue_token(table, player.player_id)

    assert table.get_item(Key={"PK": f"TOKEN#{token}", "SK": "TOKEN"}).get("Item") is None
    stored = table.get_item(Key={"PK": f"TOKEN#{hash_token(token)}", "SK": "TOKEN"})["Item"]
    assert stored["player_id"] == player.player_id
    assert token not in str(stored)


def test_each_device_gets_its_own_token(table: Table) -> None:
    """The requirement that motivates per-device tokens: signing in on a phone must not disturb
    a session already running on a desktop (DESIGN.md §6.1)."""
    player = create_player(table, "charlie", "pw")
    desktop = issue_token(table, player.player_id, "desktop")
    phone = issue_token(table, player.player_id, "phone")

    assert desktop != phone
    assert player_for_token(table, desktop) == player.player_id
    assert player_for_token(table, phone) == player.player_id
    assert {d.label for d in list_tokens(table, player.player_id)} == {"desktop", "phone"}


def test_revoking_one_device_leaves_the_others_signed_in(table: Table) -> None:
    player = create_player(table, "charlie", "pw")
    desktop = issue_token(table, player.player_id, "desktop")
    phone = issue_token(table, player.player_id, "phone")

    revoke_token(table, player.player_id, hash_token(phone))

    assert player_for_token(table, desktop) == player.player_id
    with pytest.raises(InvalidToken):
        player_for_token(table, phone)
    assert [d.label for d in list_tokens(table, player.player_id)] == ["desktop"]


def test_a_player_cannot_revoke_another_players_token(table: Table) -> None:
    victim = create_player(table, "victim", "pw")
    attacker = create_player(table, "attacker", "pw")
    victim_token = issue_token(table, victim.player_id, "laptop")

    revoke_token(table, attacker.player_id, hash_token(victim_token))

    assert player_for_token(table, victim_token) == victim.player_id


def test_revoking_twice_is_a_no_op(table: Table) -> None:
    player = create_player(table, "charlie", "pw")
    token = issue_token(table, player.player_id)
    digest = hash_token(token)

    revoke_token(table, player.player_id, digest)
    revoke_token(table, player.player_id, digest)

    assert list_tokens(table, player.player_id) == ()


def test_issue_token_rejects_past_the_device_cap(table: Table) -> None:
    """ROADMAP.md 5.0: nothing expires a bearer token, so an unbounded mint loop could otherwise
    grow a player's ``PLAYER#`` partition without limit. Doesn't assert the exact cap value - just
    that one exists and stops the count from growing further."""
    player = create_player(table, "charlie", "pw")
    with pytest.raises(TooManyDevices):
        for i in range(100):
            issue_token(table, player.player_id, f"device-{i}")

    minted = len(list_tokens(table, player.player_id))
    assert 0 < minted < 100

    with pytest.raises(TooManyDevices):
        issue_token(table, player.player_id, "one-too-many")
    assert len(list_tokens(table, player.player_id)) == minted


# --------------------------------------------------------------------- contact channels (4.2)


def test_notify_defaults_to_true_and_needs_no_migration(table: Table) -> None:
    player = create_player(table, "charlie", "pw", [ContactChannel("email", "c@example.com")])

    assert get_player(table, player.player_id).contacts == (
        ContactChannel("email", "c@example.com", verified=False, notify=True),
    )

    # A pre-4.2 item has no "notify" key at all - simulate one directly and confirm it still
    # decodes as notify=True rather than raising a KeyError.
    table.update_item(
        Key={"PK": f"PLAYER#{player.player_id}", "SK": "PROFILE"},
        UpdateExpression="SET contacts = :c",
        ExpressionAttributeValues={
            ":c": [{"kind": "email", "address": "c@example.com", "verified": False}]
        },
    )
    assert get_player(table, player.player_id).contacts == (
        ContactChannel("email", "c@example.com", verified=False, notify=True),
    )


def test_add_contact_appends_a_new_unverified_unmuted_channel(table: Table) -> None:
    player = create_player(table, "charlie", "pw")

    updated = add_contact(table, player.player_id, "email", "c@example.com")

    assert updated.contacts == (ContactChannel("email", "c@example.com"),)
    assert get_player(table, player.player_id).contacts == updated.contacts


def test_add_contact_rejects_a_duplicate_address(table: Table) -> None:
    player = create_player(table, "charlie", "pw", [ContactChannel("email", "c@example.com")])

    with pytest.raises(ContactAlreadyExists):
        add_contact(table, player.player_id, "email", "c@example.com")


def test_add_contact_rejects_past_the_contact_cap(table: Table) -> None:
    player = create_player(table, "charlie", "pw")
    for i in range(MAX_CONTACTS):
        add_contact(table, player.player_id, "email", f"c{i}@example.com")

    with pytest.raises(TooManyContacts):
        add_contact(table, player.player_id, "email", "one-too-many@example.com")

    assert len(get_player(table, player.player_id).contacts) == MAX_CONTACTS


def test_remove_contact_removes_only_the_matching_address(table: Table) -> None:
    player = create_player(
        table,
        "charlie",
        "pw",
        [ContactChannel("email", "c@example.com"), ContactChannel("email", "other@example.com")],
    )

    updated = remove_contact(table, player.player_id, "c@example.com")

    assert updated.contacts == (ContactChannel("email", "other@example.com"),)


def test_remove_contact_rejects_an_unknown_address(table: Table) -> None:
    player = create_player(table, "charlie", "pw")

    with pytest.raises(ContactNotFound):
        remove_contact(table, player.player_id, "nobody@example.com")


def test_set_contact_notify_mutes_only_the_matching_channel(table: Table) -> None:
    player = create_player(
        table,
        "charlie",
        "pw",
        [ContactChannel("email", "c@example.com"), ContactChannel("email", "other@example.com")],
    )

    updated = set_contact_notify(table, player.player_id, "c@example.com", False)

    contacts = {c.address: c for c in updated.contacts}
    assert contacts["c@example.com"].notify is False
    assert contacts["other@example.com"].notify is True


def test_set_contact_notify_rejects_an_unknown_address(table: Table) -> None:
    player = create_player(table, "charlie", "pw")

    with pytest.raises(ContactNotFound):
        set_contact_notify(table, player.player_id, "nobody@example.com", False)


def test_verification_round_trip_marks_the_channel_verified(table: Table) -> None:
    player = create_player(table, "charlie", "pw", [ContactChannel("email", "c@example.com")])

    token = begin_verification(table, player.player_id, "c@example.com")
    complete_verification(table, token)

    assert get_player(table, player.player_id).contacts == (
        ContactChannel("email", "c@example.com", verified=True),
    )


def test_begin_verification_rejects_an_unknown_address(table: Table) -> None:
    player = create_player(table, "charlie", "pw")

    with pytest.raises(ContactNotFound):
        begin_verification(table, player.player_id, "nobody@example.com")


def test_an_unissued_verification_token_is_rejected(table: Table) -> None:
    with pytest.raises(InvalidVerificationToken):
        complete_verification(table, "not-a-real-token")


def test_a_verification_token_is_single_use(table: Table) -> None:
    player = create_player(table, "charlie", "pw", [ContactChannel("email", "c@example.com")])
    token = begin_verification(table, player.player_id, "c@example.com")

    complete_verification(table, token)

    with pytest.raises(InvalidVerificationToken):
        complete_verification(table, token)


def test_an_expired_verification_token_is_rejected_and_still_consumed(table: Table) -> None:
    player = create_player(table, "charlie", "pw", [ContactChannel("email", "c@example.com")])
    start = datetime(2026, 1, 1, tzinfo=UTC)
    token = begin_verification(table, player.player_id, "c@example.com", now=lambda: start)

    with pytest.raises(InvalidVerificationToken):
        complete_verification(table, token, now=lambda: start + timedelta(hours=25))

    # Single-use even when rejected for expiry: a retry must not somehow succeed.
    with pytest.raises(InvalidVerificationToken):
        complete_verification(table, token, now=lambda: start)

    assert get_player(table, player.player_id).contacts == (
        ContactChannel("email", "c@example.com", verified=False),
    )


def test_verifying_a_channel_removed_after_the_token_was_minted_is_a_no_op(table: Table) -> None:
    player = create_player(table, "charlie", "pw", [ContactChannel("email", "c@example.com")])
    token = begin_verification(table, player.player_id, "c@example.com")

    remove_contact(table, player.player_id, "c@example.com")
    complete_verification(table, token)  # does not raise

    assert get_player(table, player.player_id).contacts == ()


# ----------------------------------------------------------------------- password reset (4.3)


def test_password_reset_round_trip_changes_the_password(table: Table) -> None:
    player = create_player(table, "charlie", "hunter2")

    token = begin_password_reset(table, player.player_id)
    complete_password_reset(table, token, "correct horse battery staple")

    assert authenticate(table, "charlie", "correct horse battery staple") == player.player_id
    with pytest.raises(InvalidCredentials):
        authenticate(table, "charlie", "hunter2")


def test_completing_a_password_reset_revokes_every_device(table: Table) -> None:
    player = create_player(table, "charlie", "hunter2")
    desktop = issue_token(table, player.player_id, "desktop")
    phone = issue_token(table, player.player_id, "phone")

    token = begin_password_reset(table, player.player_id)
    complete_password_reset(table, token, "new-password")

    assert list_tokens(table, player.player_id) == ()
    with pytest.raises(InvalidToken):
        player_for_token(table, desktop)
    with pytest.raises(InvalidToken):
        player_for_token(table, phone)


def test_an_unissued_reset_token_is_rejected(table: Table) -> None:
    with pytest.raises(InvalidResetToken):
        complete_password_reset(table, "not-a-real-token", "new-password")


def test_a_reset_token_is_single_use(table: Table) -> None:
    player = create_player(table, "charlie", "hunter2")
    token = begin_password_reset(table, player.player_id)

    complete_password_reset(table, token, "new-password")

    with pytest.raises(InvalidResetToken):
        complete_password_reset(table, token, "another-password")


def test_an_expired_reset_token_is_rejected_and_still_consumed(table: Table) -> None:
    player = create_player(table, "charlie", "hunter2")
    start = datetime(2026, 1, 1, tzinfo=UTC)
    token = begin_password_reset(table, player.player_id, now=lambda: start)

    with pytest.raises(InvalidResetToken):
        complete_password_reset(
            table, token, "new-password", now=lambda: start + timedelta(hours=2)
        )

    # Single-use even when rejected for expiry: a retry must not somehow succeed.
    with pytest.raises(InvalidResetToken):
        complete_password_reset(table, token, "new-password", now=lambda: start)

    assert authenticate(table, "charlie", "hunter2") == player.player_id


def test_a_single_use_token_never_starts_with_a_dash(table: Table) -> None:
    """``secrets.token_urlsafe`` emits base64url, so roughly one token in 64 would start with
    ``-``. ``tricksy.notifications.messages`` prints these inside a command the player is told to
    copy and run verbatim, and ``argparse`` reads a leading ``-`` as an option flag - so such a
    token would make ``tricksy contact confirm``/``reset-password`` fail as a usage error before
    reaching the server. Enough draws that the pre-fix code fails this essentially always."""
    player = create_player(table, "charlie", "hunter2")
    add_contact(table, player.player_id, "email", "c@example.com")

    for _ in range(200):
        assert not begin_verification(table, player.player_id, "c@example.com").startswith("-")
        assert not begin_password_reset(table, player.player_id).startswith("-")
