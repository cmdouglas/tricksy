"""Saved house-rule sets (ROADMAP.md 2.7.1)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from mypy_boto3_dynamodb.service_resource import Table

from tricksy.games.texas42.errors import RulesError
from tricksy.games.texas42.house_rules import HouseRules
from tricksy.storage.errors import RuleSetNotFound
from tricksy.storage.rule_sets import (
    create_rule_set,
    delete_rule_set,
    get_rule_set,
    list_rule_sets,
    update_rule_set,
)


def test_a_saved_set_round_trips_through_get(table: Table) -> None:
    rules = HouseRules(enabled_contracts=frozenset({"standard", "nello"}))
    saved = create_rule_set(table, "player-1", "Thursday nights", rules)

    fetched = get_rule_set(table, "player-1", saved.rule_set_id)
    assert fetched == saved
    assert fetched.name == "Thursday nights"
    assert fetched.rules == rules


def test_an_incoherent_set_is_rejected_at_save(table: Table) -> None:
    """The reasoning matches ``create_pending_game``'s: a set that only fails at the table is a
    trap saved weeks earlier and since forgotten (DESIGN.md §5.1)."""
    rules = HouseRules(contract_options={"plunge": {"minimum_doubles": 8}})

    with pytest.raises(RulesError):
        create_rule_set(table, "player-1", "bad set", rules)


def test_getting_an_unknown_id_raises_rule_set_not_found(table: Table) -> None:
    with pytest.raises(RuleSetNotFound):
        get_rule_set(table, "player-1", "no-such-id")


def test_one_player_cannot_read_anothers_set(table: Table) -> None:
    saved = create_rule_set(table, "player-1", "mine", HouseRules())

    with pytest.raises(RuleSetNotFound):
        get_rule_set(table, "player-2", saved.rule_set_id)


def test_list_rule_sets_returns_only_this_players_sets_newest_first(table: Table) -> None:
    create_rule_set(table, "player-2", "not mine", HouseRules())
    first = create_rule_set(
        table, "player-1", "first", HouseRules(), now=lambda: datetime(2025, 1, 1, tzinfo=UTC)
    )
    second = create_rule_set(
        table, "player-1", "second", HouseRules(), now=lambda: datetime(2030, 1, 1, tzinfo=UTC)
    )

    listed = list_rule_sets(table, "player-1")

    assert {s.rule_set_id for s in listed} == {first.rule_set_id, second.rule_set_id}
    assert listed[0].rule_set_id == second.rule_set_id


def test_update_replaces_name_and_rules(table: Table) -> None:
    saved = create_rule_set(table, "player-1", "old name", HouseRules(marks_to_win=7))
    new_rules = HouseRules(marks_to_win=5)

    updated = update_rule_set(table, "player-1", saved.rule_set_id, "new name", new_rules)

    assert updated.name == "new name"
    assert updated.rules == new_rules
    assert updated.created_at == saved.created_at
    assert get_rule_set(table, "player-1", saved.rule_set_id) == updated


def test_updating_an_incoherent_set_is_rejected(table: Table) -> None:
    saved = create_rule_set(table, "player-1", "ok", HouseRules())
    bad_rules = HouseRules(contract_options={"plunge": {"minimum_marks": 8}})

    with pytest.raises(RulesError):
        update_rule_set(table, "player-1", saved.rule_set_id, "ok", bad_rules)


def test_updating_a_deleted_set_is_rejected_not_resurrected(table: Table) -> None:
    saved = create_rule_set(table, "player-1", "gone", HouseRules())
    delete_rule_set(table, "player-1", saved.rule_set_id)

    with pytest.raises(RuleSetNotFound):
        update_rule_set(table, "player-1", saved.rule_set_id, "gone", HouseRules())


def test_delete_removes_the_set(table: Table) -> None:
    saved = create_rule_set(table, "player-1", "temp", HouseRules())

    delete_rule_set(table, "player-1", saved.rule_set_id)

    with pytest.raises(RuleSetNotFound):
        get_rule_set(table, "player-1", saved.rule_set_id)


def test_deleting_twice_is_a_no_op(table: Table) -> None:
    saved = create_rule_set(table, "player-1", "temp", HouseRules())

    delete_rule_set(table, "player-1", saved.rule_set_id)
    delete_rule_set(table, "player-1", saved.rule_set_id)  # must not raise
