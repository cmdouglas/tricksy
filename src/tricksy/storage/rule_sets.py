"""Saved house-rule sets (ROADMAP.md 2.7.1, DESIGN.md §5.1).

A player may keep named ``HouseRules`` values under their own account and name one when creating a
game, so a group that plays the same way every week does not retype its rules every table. This is
a convenience layer on top of the house-rules model and changes nothing about it: a saved set is
exactly a ``HouseRules``, stored with the same codec ``META.config`` uses.

One item type, in the same single table as everything else (DESIGN.md §4.1):

``PLAYER#<id> / RULESET#<ruleSetId>``
    A display name plus the encoded rules. Lives under the owning player's partition, so a
    ``rule_set_id`` belonging to somebody else is simply not found - access control needs no check
    of its own.

**Applying a set copies it.** Nothing here enforces that; it falls out of
``t42.storage.lobby.create_pending_game`` writing ``META.config`` from the resolved ``HouseRules``
value rather than a reference to this item, so a game's rules survive a later edit or delete of the
set it was created from (DESIGN.md §5.1) - a guarantee proved at the API layer, where a game is
actually created from a saved set.

**Validated on save**, through the same :func:`t42.engine.contracts.validate_house_rules` that
guards game creation - the reasoning matches the lobby's: an incoherent set that only fails at the
table is a trap saved weeks earlier and since forgotten.

**The id is opaque and the display name is not unique.** Same argument as ``PlayerId`` (DESIGN.md
§12): keying on the name would make renaming a delete-and-recreate. A player keeps a handful of
these, so duplicate names are a cosmetic matter for the client to warn about, not a correctness one
worth a uniqueness-reservation item.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from botocore.exceptions import ClientError
from mypy_boto3_dynamodb.service_resource import Table

from t42.engine import GAME_KIND
from t42.engine.contracts import validate_house_rules
from t42.engine.house_rules import HouseRules
from t42.engine.state import PlayerId

from ._dynamo import as_text, from_dynamo
from .codec import decode_house_rules, encode_house_rules
from .errors import RuleSetNotFound

_RULE_SET_ID_BYTES: Final = 12


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _player_pk(player_id: PlayerId) -> str:
    return f"PLAYER#{player_id}"


def _rule_set_sk(rule_set_id: str) -> str:
    return f"RULESET#{rule_set_id}"


@dataclass(frozen=True, slots=True)
class RuleSet:
    """One saved rule set.

    ``kind`` says which game's rules these are, so a set can never be applied to a table of a
    different game - the check lives in the API's game-creation handler, where both are in scope
    (DESIGN.md §11).
    """

    rule_set_id: str
    name: str
    kind: str
    rules: HouseRules
    created_at: str


def _rule_set_from_item(item: Mapping[str, Any]) -> RuleSet:
    normalized = from_dynamo(item)
    return RuleSet(
        rule_set_id=as_text(normalized["rule_set_id"]),
        name=as_text(normalized["name"]),
        kind=as_text(normalized.get("kind", GAME_KIND)),
        rules=decode_house_rules(normalized["rules"]),
        created_at=as_text(normalized["created_at"]),
    )


def create_rule_set(
    table: Table,
    player_id: PlayerId,
    name: str,
    rules: HouseRules,
    *,
    now: Callable[[], datetime] = _utcnow,
) -> RuleSet:
    """Saves a named rule set under ``player_id``.

    Validates ``rules`` here, through the same check ``new_game`` runs, rather than deferring to
    the moment a game is created from it - see the module docstring.
    """
    validate_house_rules(rules)
    rule_set_id = secrets.token_urlsafe(_RULE_SET_ID_BYTES)
    timestamp = now().isoformat()
    table.put_item(
        Item={
            "PK": _player_pk(player_id),
            "SK": _rule_set_sk(rule_set_id),
            "rule_set_id": rule_set_id,
            "name": name,
            "kind": GAME_KIND,
            "rules": encode_house_rules(rules),
            "created_at": timestamp,
        },
        ConditionExpression="attribute_not_exists(SK)",
    )
    return RuleSet(
        rule_set_id=rule_set_id, name=name, kind=GAME_KIND, rules=rules, created_at=timestamp
    )


def get_rule_set(table: Table, player_id: PlayerId, rule_set_id: str) -> RuleSet:
    """Raises :class:`~t42.storage.errors.RuleSetNotFound` if ``player_id`` holds no such set."""
    item = table.get_item(Key={"PK": _player_pk(player_id), "SK": _rule_set_sk(rule_set_id)}).get(
        "Item"
    )
    if item is None:
        raise RuleSetNotFound(rule_set_id)
    return _rule_set_from_item(item)


def list_rule_sets(table: Table, player_id: PlayerId) -> tuple[RuleSet, ...]:
    """Every set ``player_id`` has saved, newest first."""
    response = table.query(
        KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
        ExpressionAttributeValues={":pk": _player_pk(player_id), ":prefix": "RULESET#"},
    )
    sets = [_rule_set_from_item(item) for item in response.get("Items", ())]
    return tuple(sorted(sets, key=lambda s: s.created_at, reverse=True))


def update_rule_set(
    table: Table, player_id: PlayerId, rule_set_id: str, name: str, rules: HouseRules
) -> RuleSet:
    """Replaces ``rule_set_id``'s name and rules wholesale.

    Conditioned on the item already existing, so editing something already deleted is an error
    rather than a resurrection. Does not touch any game already created from this set - see the
    module docstring's copy-not-reference guarantee.
    """
    validate_house_rules(rules)
    try:
        table.update_item(
            Key={"PK": _player_pk(player_id), "SK": _rule_set_sk(rule_set_id)},
            UpdateExpression="SET #name = :name, #rules = :rules",
            ConditionExpression="attribute_exists(SK)",
            ExpressionAttributeNames={"#name": "name", "#rules": "rules"},
            ExpressionAttributeValues={":name": name, ":rules": encode_house_rules(rules)},
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise RuleSetNotFound(rule_set_id) from exc
        raise
    return get_rule_set(table, player_id, rule_set_id)


def delete_rule_set(table: Table, player_id: PlayerId, rule_set_id: str) -> None:
    """Deletes a saved set. Idempotent, like ``accounts.revoke_token``: deleting one already gone
    is a no-op rather than an error."""
    table.delete_item(Key={"PK": _player_pk(player_id), "SK": _rule_set_sk(rule_set_id)})
