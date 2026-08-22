"""Invites: permission to join a specific table (ROADMAP.md 2.7.2, DESIGN.md §6.2)."""

from __future__ import annotations

from random import Random

import pytest
from mypy_boto3_dynamodb.service_resource import Table

from t42.engine.house_rules import HouseRules
from t42.engine.state import Seat
from t42.storage.errors import GameNotFound, NotInvited
from t42.storage.invites import (
    find_invite,
    invite_player,
    list_invites_for_game,
    list_invites_for_player,
    revoke_invite,
)
from t42.storage.lobby import Visibility, create_pending_game, get_lobby, join_seat


def _open_lobby(table: Table, visibility: Visibility, game_id: str = "g1") -> None:
    create_pending_game(
        table, game_id, "north", "north", Seat.NORTH, HouseRules(), visibility=visibility
    )


def test_invite_player_writes_both_items(table: Table) -> None:
    invite_player(table, "g1", "east", "east", inviter_username="north")

    assert table.get_item(Key={"PK": "GAME#g1", "SK": "INVITE#east"})["Item"]["username"] == "east"
    invitee_item = table.get_item(Key={"PK": "PLAYER#east", "SK": "INVITE#g1"})["Item"]
    assert invitee_item["game_id"] == "g1"
    assert invitee_item["invited_by"] == "north"


def test_revoke_invite_deletes_both_items(table: Table) -> None:
    invite_player(table, "g1", "east", "east", inviter_username="north")

    revoke_invite(table, "g1", "east")

    assert table.get_item(Key={"PK": "GAME#g1", "SK": "INVITE#east"}).get("Item") is None
    assert table.get_item(Key={"PK": "PLAYER#east", "SK": "INVITE#g1"}).get("Item") is None


def test_revoking_an_invite_that_never_existed_is_a_no_op(table: Table) -> None:
    revoke_invite(table, "g1", "east")  # must not raise


def test_re_inviting_overwrites_rather_than_raising(table: Table) -> None:
    """A client retry looks exactly like a second invite (ROADMAP.md 2.7.2)."""
    invite_player(table, "g1", "east", "east", inviter_username="north")

    invite_player(table, "g1", "east", "east", inviter_username="north")  # must not raise

    assert find_invite(table, "g1", "east")


def test_find_invite_reflects_presence_and_absence(table: Table) -> None:
    assert find_invite(table, "g1", "east") is False

    invite_player(table, "g1", "east", "east", inviter_username="north")
    assert find_invite(table, "g1", "east") is True

    revoke_invite(table, "g1", "east")
    assert find_invite(table, "g1", "east") is False


def test_list_invites_for_player_is_scoped_and_newest_first(table: Table) -> None:
    invite_player(table, "g1", "east", "east", inviter_username="north")
    invite_player(table, "g2", "east", "east", inviter_username="north")
    invite_player(table, "g3", "somebody-else", "somebody-else", inviter_username="north")

    invites = list_invites_for_player(table, "east")

    assert {i.game_id for i in invites} == {"g1", "g2"}


def test_list_invites_for_game_is_scoped_and_newest_first(table: Table) -> None:
    invite_player(table, "g1", "east", "east", inviter_username="north")
    invite_player(table, "g1", "south", "south", inviter_username="north")
    invite_player(table, "g2", "west", "west", inviter_username="north")

    invites = list_invites_for_game(table, "g1")

    assert {i.player_id for i in invites} == {"east", "south"}
    assert {i.username for i in invites} == {"east", "south"}


def test_list_invites_for_game_shrinks_on_revoke_or_join(table: Table) -> None:
    _open_lobby(table, Visibility.INVITE_ONLY)
    invite_player(table, "g1", "east", "east", inviter_username="north")
    invite_player(table, "g1", "south", "south", inviter_username="north")

    revoke_invite(table, "g1", "south")
    assert {i.player_id for i in list_invites_for_game(table, "g1")} == {"east"}

    join_seat(table, "g1", "east", "east", Seat.EAST, rng=Random(0))
    assert list_invites_for_game(table, "g1") == ()


def test_join_seat_refuses_an_uninvited_player_on_an_invite_only_game(table: Table) -> None:
    _open_lobby(table, Visibility.INVITE_ONLY)

    with pytest.raises(NotInvited):
        join_seat(table, "g1", "east", "east", Seat.EAST, rng=Random(0))


def test_join_seat_admits_an_invited_player_and_consumes_the_invite(table: Table) -> None:
    _open_lobby(table, Visibility.INVITE_ONLY)
    invite_player(table, "g1", "east", "east", inviter_username="north")

    lobby = join_seat(table, "g1", "east", "east", Seat.EAST, rng=Random(0))

    assert lobby.seat_of("east") == Seat.EAST
    assert find_invite(table, "g1", "east") is False
    assert list_invites_for_player(table, "east") == ()


def test_join_seat_on_a_public_game_needs_no_invite(table: Table) -> None:
    _open_lobby(table, Visibility.PUBLIC)

    lobby = join_seat(table, "g1", "east", "east", Seat.EAST, rng=Random(0))

    assert lobby.seat_of("east") == Seat.EAST


def test_join_seat_still_consumes_a_stray_invite_on_a_public_game(table: Table) -> None:
    """Nothing forbids inviting somebody to a public table; a join still cleans it up."""
    _open_lobby(table, Visibility.PUBLIC)
    invite_player(table, "g1", "east", "east", inviter_username="north")

    join_seat(table, "g1", "east", "east", Seat.EAST, rng=Random(0))

    assert find_invite(table, "g1", "east") is False


def test_a_new_lobby_defaults_to_public(table: Table) -> None:
    """Locks the exit criterion in ROADMAP.md 2.7.2: nothing that existed before this phase
    changes meaning."""
    create_pending_game(table, "g1", "north", "north", Seat.NORTH, HouseRules())

    assert get_lobby(table, "g1").visibility is Visibility.PUBLIC


def test_joining_an_unknown_invite_only_game_is_still_game_not_found(table: Table) -> None:
    with pytest.raises(GameNotFound):
        join_seat(table, "NOPE12", "east", "east", Seat.EAST, rng=Random(0))
