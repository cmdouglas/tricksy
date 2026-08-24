"""The lobby: a game's life from creation to the deal (ROADMAP.md 2.2)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from random import Random

import pytest
from mypy_boto3_dynamodb.service_resource import Table

from tricksy.games.texas42.errors import UnknownContract
from tricksy.games.texas42.house_rules import HouseRules
from tricksy.games.texas42.moves import Move
from tricksy.games.texas42.state import GameState, Phase, Seat
from tricksy.storage.errors import (
    AlreadySeated,
    GameAlreadyExists,
    GameAlreadyStarted,
    GameNotFound,
    GameNotJoinable,
    SeatTaken,
)
from tricksy.storage.events import events_for_move
from tricksy.storage.lobby import (
    Visibility,
    create_pending_game,
    get_lobby,
    join_seat,
    list_games_for_player,
    list_open_games,
    new_game_code,
)
from tricksy.storage.repository import GameStatus, append, get_state, start_game

from ..games.texas42._helpers import PLAYERS, drive_to_game_over, prefer_contract
from ._helpers import started_game

_OTHERS = {Seat.EAST: "east", Seat.SOUTH: "south", Seat.WEST: "west"}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _open_lobby(
    table: Table,
    game_id: str = "g1",
    config: HouseRules | None = None,
    *,
    visibility: Visibility = Visibility.PUBLIC,
    now: Callable[[], datetime] = _utcnow,
) -> None:
    create_pending_game(
        table,
        game_id,
        "north",
        "north",
        Seat.NORTH,
        config or HouseRules(),
        visibility=visibility,
        now=now,
    )


def _fill(table: Table, game_id: str = "g1", *, seed: int = 0) -> None:
    for seat, player_id in _OTHERS.items():
        join_seat(table, game_id, player_id, player_id, seat, rng=Random(seed))


def test_a_new_lobby_holds_only_its_creator(table: Table) -> None:
    _open_lobby(table)

    lobby = get_lobby(table, "g1")

    assert lobby.status is GameStatus.WAITING
    assert lobby.seats.keys() == {Seat.NORTH}
    assert lobby.seats[Seat.NORTH].username == "north"
    assert lobby.open_seats == (Seat.EAST, Seat.SOUTH, Seat.WEST)


def test_a_waiting_game_has_no_state_item_yet(table: Table) -> None:
    """There is genuinely nothing to read until the deal, so ``get_state`` saying so is correct
    rather than a gap the API has to paper over."""
    _open_lobby(table)

    with pytest.raises(GameNotFound):
        get_state(table, "g1")


def test_create_pending_game_rejects_an_invalid_rule_set(table: Table) -> None:
    """Rejected at creation rather than three joins later, when the deal would first run it."""
    with pytest.raises(UnknownContract):
        _open_lobby(table, config=HouseRules(enabled_contracts=frozenset({"standard", "nope"})))

    with pytest.raises(GameNotFound):
        get_lobby(table, "g1")


def test_create_pending_game_rejects_a_duplicate_code(table: Table) -> None:
    _open_lobby(table)

    with pytest.raises(GameAlreadyExists):
        create_pending_game(table, "g1", "other", "other", Seat.EAST, HouseRules())


def test_the_fourth_join_deals_the_game(table: Table) -> None:
    _open_lobby(table)
    join_seat(table, "g1", "east", "east", Seat.EAST, rng=Random(0))
    join_seat(table, "g1", "south", "south", Seat.SOUTH, rng=Random(0))

    with pytest.raises(GameNotFound):
        get_state(table, "g1")

    lobby = join_seat(table, "g1", "west", "west", Seat.WEST, rng=Random(0))

    assert lobby.status is GameStatus.ACTIVE
    stored = get_state(table, "g1")
    assert stored.version == 1
    assert stored.state.phase is Phase.BIDDING
    assert stored.state.hand is not None
    assert all(len(tiles) == 7 for tiles in stored.state.hand.hands.values())


def test_a_full_lobby_can_only_be_dealt_once(table: Table) -> None:
    """The guarantee the conditional ``META`` update exists for. Two attempts to deal the same
    full lobby - a retried request, or two joins racing - must not produce two deals, or the
    second would overwrite the first's hands and the event log would disagree with ``STATE``."""
    _open_lobby(table)
    _fill(table)
    dealt = get_state(table, "g1")

    with pytest.raises(GameAlreadyStarted):
        start_game(table, "g1", PLAYERS, HouseRules(), Random(99))

    still = get_state(table, "g1")
    assert still.state == dealt.state
    assert still.version == dealt.version


def test_a_taken_seat_is_rejected(table: Table) -> None:
    _open_lobby(table)
    join_seat(table, "g1", "east", "east", Seat.EAST, rng=Random(0))

    with pytest.raises(SeatTaken):
        join_seat(table, "g1", "interloper", "interloper", Seat.EAST, rng=Random(0))


def test_rejoining_your_own_seat_is_a_no_op(table: Table) -> None:
    """What a retried join request looks like. It must not be an error, or a dropped response
    leaves a client unable to tell whether it is seated."""
    _open_lobby(table)
    first = join_seat(table, "g1", "east", "east", Seat.EAST, rng=Random(0))

    again = join_seat(table, "g1", "east", "east", Seat.EAST, rng=Random(0))

    assert again.seats == first.seats


def test_rejoining_your_seat_still_works_once_the_game_is_dealt(table: Table) -> None:
    _open_lobby(table)
    _fill(table)

    lobby = join_seat(table, "g1", "east", "east", Seat.EAST, rng=Random(0))

    assert lobby.status is GameStatus.ACTIVE


def test_one_player_cannot_hold_two_seats(table: Table) -> None:
    _open_lobby(table)
    join_seat(table, "g1", "east", "east", Seat.EAST, rng=Random(0))

    with pytest.raises(AlreadySeated):
        join_seat(table, "g1", "east", "east", Seat.SOUTH, rng=Random(0))


def test_a_dealt_game_cannot_be_joined_by_a_newcomer(table: Table) -> None:
    _open_lobby(table)
    _fill(table)

    with pytest.raises(GameNotJoinable):
        join_seat(table, "g1", "latecomer", "latecomer", Seat.NORTH, rng=Random(0))


def test_joining_an_unknown_game_is_not_found(table: Table) -> None:
    with pytest.raises(GameNotFound):
        join_seat(table, "NOPE12", "east", "east", Seat.EAST, rng=Random(0))


def test_house_rules_survive_the_lobby_unchanged(table: Table) -> None:
    """The rule set is chosen at creation but not used until the deal, three joins later, so it
    has to round-trip through ``META`` intact."""
    config = HouseRules(
        enabled_contracts=frozenset({"standard", "nello", "plunge", "splash"}),
        contract_options={"plunge": {"minimum_doubles": 5}},
        doubles_are_own_suit=True,
        allow_declared_lead="always",
        marks_to_win=3,
    )
    _open_lobby(table, config=config)

    assert get_lobby(table, "g1").config == config

    _fill(table)
    assert get_state(table, "g1").state.config == config


def test_list_games_for_player_flags_whose_turn_it_is(table: Table) -> None:
    _open_lobby(table)
    _fill(table)
    to_act = get_state(table, "g1").state.to_act
    assert to_act is not None

    for seat, player_id in {Seat.NORTH: "north", **_OTHERS}.items():
        (summary,) = list_games_for_player(table, player_id)
        assert summary.game_id == "g1"
        assert summary.seat == seat
        assert summary.status is GameStatus.ACTIVE
        assert summary.is_my_turn == (seat is to_act)


def test_list_games_for_player_covers_waiting_games_too(table: Table) -> None:
    _open_lobby(table)

    (summary,) = list_games_for_player(table, "north")

    assert summary.status is GameStatus.WAITING
    assert summary.is_my_turn is False


def test_list_games_for_player_is_empty_for_a_stranger(table: Table) -> None:
    _open_lobby(table)

    assert list_games_for_player(table, "nobody") == ()


def test_list_games_for_player_skips_a_row_with_no_game_id(table: Table) -> None:
    """Defense in depth (ROADMAP.md 5.0): before the seat claim and the ``PLAYER#`` put became
    one transaction, a crash between them could leave a partial row - later ``UpdateItem`` calls
    from ``start_game``/``append`` would resurrect it with no ``game_id``. Simulate that shape
    directly rather than the crash itself, since the write path can no longer produce it."""
    _open_lobby(table)
    table.put_item(
        Item={
            "PK": "PLAYER#north",
            "SK": "GAME#partial",
            "kind": "texas42",
            "status": "WAITING",
            "is_my_turn": False,
        }
    )

    (summary,) = list_games_for_player(table, "north")

    assert summary.game_id == "g1"


def test_a_finished_game_is_marked_complete(table: Table) -> None:
    """``append`` retires the game from every listing in the transaction that ends it, so a
    finished game can never show up as still waiting on somebody."""
    state = started_game(table, "g1", config=HouseRules(marks_to_win=1))
    rng = Random(7)

    def record(before: GameState, move: Move, after: GameState) -> None:
        events = events_for_move(before, move, after)
        stored = get_state(table, "g1")
        append(table, "g1", events, after, stored.version)

    final = drive_to_game_over(
        state, prefer_contract("standard"), rng, max_moves=5000, on_transition=record
    )
    assert final.phase is Phase.GAME_OVER

    assert get_lobby(table, "g1").status is GameStatus.COMPLETE
    for player_id in ("north", "east", "south", "west"):
        (summary,) = list_games_for_player(table, player_id)
        assert summary.status is GameStatus.COMPLETE
        assert summary.is_my_turn is False


def test_game_codes_avoid_confusable_characters(table: Table) -> None:
    """The code gets read aloud and typed from a phone screen (DESIGN.md §4.1)."""
    codes = {new_game_code() for _ in range(200)}

    assert all(len(code) == 6 for code in codes)
    assert not set("".join(codes)) & set("ILOU01")
    assert len(codes) > 190  # not a constant, and not obviously clustered


def test_list_open_games_is_newest_first(table: Table) -> None:
    _open_lobby(table, "g1", now=lambda: datetime(2025, 1, 1, tzinfo=UTC))
    _open_lobby(table, "g2", now=lambda: datetime(2030, 1, 1, tzinfo=UTC))

    games = [lobby.game_id for lobby in list_open_games(table)]

    assert games == ["g2", "g1"]


def test_list_open_games_excludes_invite_only(table: Table) -> None:
    _open_lobby(table, "g1", visibility=Visibility.PUBLIC)
    _open_lobby(table, "g2", visibility=Visibility.INVITE_ONLY)

    games = [lobby.game_id for lobby in list_open_games(table)]

    assert games == ["g1"]


def test_list_open_games_drops_a_game_once_dealt(table: Table) -> None:
    _open_lobby(table)
    assert [lobby.game_id for lobby in list_open_games(table)] == ["g1"]

    _fill(table)

    assert list_open_games(table) == ()
