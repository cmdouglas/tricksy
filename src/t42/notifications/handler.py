"""The notification Lambda entry point (ROADMAP.md 4.5, DESIGN.md §8).

Watches ``PLAYER#<playerId>`` item transitions for the three things worth an email (DESIGN.md
§8's table): ``is_my_turn`` false -> true on a ``GAME#<gameId>`` item ("your turn"), ``status``
``ACTIVE`` -> ``COMPLETE`` on the same item ("game over"), and an ``INSERT`` of an
``INVITE#<gameId>`` item ("you've been invited"). :func:`notifications_for` is the pure
classifier - decoded records in, a plain list of what needs to happen out, no I/O - and
:func:`send_notifications` is everything that follows: dedup, recipient resolution, rendering and
sending. This split is what :mod:`t42.notifications.pump` (ROADMAP.md 4.4) was already built
to hand a Lambda-shaped batch to, and what makes "the notifier cannot see a hand" a fact enforced
by :mod:`tests.notifications.test_layering` rather than an audited claim: this module may import
``t42.storage.accounts`` and nothing else from ``t42.storage``, and nothing at all from
``t42.engine``, so it never reaches ``STATE`` and never sees a ``GameState``.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from typing import Any, Literal

import boto3
from botocore.exceptions import ClientError
from mypy_boto3_dynamodb.service_resource import Table

from t42.storage.accounts import Player, get_player

from .messages import render_game_over, render_invite, render_your_turn
from .records import transition_from_record
from .sender import EmailSender, get_sender

#: Same env vars t42.api.deps and t42.notifications.pump read, so a shell already set up for one
#: needs nothing extra to run this too.
TABLE_NAME_ENV = "T42_TABLE_NAME"
ENDPOINT_URL_ENV = "T42_DYNAMODB_ENDPOINT"

NotificationKind = Literal["your_turn", "game_over", "invite"]

#: The two dedup attributes. Invite items carry no version counter to compare against, unlike
#: PLAYER#/GAME# items (repository.py stamps `version` there specifically for this), so they get
#: a plain once-only marker instead.
_NOTIFIED_VERSION_ATTR = "notified_version"
_NOTIFIED_ATTR = "notified"


@dataclass(frozen=True, slots=True)
class Notification:
    """One thing :func:`send_notifications` needs to act on - enough to dedupe, resolve a
    recipient and render a message, without going back to the stream record."""

    kind: NotificationKind
    game_id: str
    player_id: str
    version: int | None = None  # dedup key for your_turn / game_over
    invited_by: str | None = None  # for invite


def _as_version(value: Any) -> int | None:
    """Stream numbers decode to ``Decimal`` (:mod:`boto3.dynamodb.types`), not ``int``. ``None``
    means either the attribute is absent - a transition from before repository.py started
    stamping ``version`` on these items - or it isn't numeric; either way this record can't be
    safely deduped, so the caller skips it rather than claiming with a value that can never
    satisfy `notified_version < :v` again.
    """
    if isinstance(value, (int, Decimal)):
        return int(value)
    return None


def notifications_for(records: Iterable[Mapping[str, Any]]) -> list[Notification]:
    """Pure: decodes each record and classifies it against the three rules. No I/O."""
    notifications: list[Notification] = []
    for record in records:
        transition = transition_from_record(record)
        pk = transition.keys.get("PK")
        if not isinstance(pk, str) or not pk.startswith("PLAYER#"):
            continue
        player_id = pk.removeprefix("PLAYER#")
        sk = transition.keys.get("SK")
        if not isinstance(sk, str):
            continue

        if sk.startswith("GAME#"):
            old, new = transition.old, transition.new
            if old is None or new is None:
                # No old image means this is the INSERT lobby.join_seat writes on first joining a
                # seat - never a turn/status flip. No new image means a REMOVE, which nothing in
                # this codebase does to a PLAYER#/GAME# item.
                continue
            game_id = sk.removeprefix("GAME#")
            version = _as_version(new.get("version"))
            if version is None:
                continue
            if not old.get("is_my_turn") and new.get("is_my_turn"):
                notifications.append(Notification("your_turn", game_id, player_id, version=version))
            elif old.get("status") == "ACTIVE" and new.get("status") == "COMPLETE":
                notifications.append(Notification("game_over", game_id, player_id, version=version))
        elif sk.startswith("INVITE#"):
            if transition.event_name != "INSERT" or transition.new is None:
                continue
            game_id = sk.removeprefix("INVITE#")
            invited_by = transition.new.get("invited_by")
            notifications.append(
                Notification(
                    "invite",
                    game_id,
                    player_id,
                    invited_by=invited_by if isinstance(invited_by, str) else None,
                )
            )
    return notifications


def _recipient_email(player: Player) -> str | None:
    for contact in player.contacts:
        if contact.kind == "email" and contact.verified and contact.notify:
            return contact.address
    return None


def _claim(table: Table, notification: Notification) -> bool:
    """The at-least-once guard (DESIGN.md §8): a conditional write that succeeds exactly once per
    transition. Comes first, before any recipient lookup, so a redelivered record is rejected as
    cheaply as possible and `notified`/`notified_version` means "this transition was processed" -
    not "an email was actually sent"."""
    if notification.kind == "invite":
        key = {"PK": f"PLAYER#{notification.player_id}", "SK": f"INVITE#{notification.game_id}"}
        expression = f"SET {_NOTIFIED_ATTR} = :true"
        condition = f"attribute_not_exists({_NOTIFIED_ATTR})"
        values: dict[str, Any] = {":true": True}
    else:
        key = {"PK": f"PLAYER#{notification.player_id}", "SK": f"GAME#{notification.game_id}"}
        expression = f"SET {_NOTIFIED_VERSION_ATTR} = :v"
        condition = (
            f"attribute_not_exists({_NOTIFIED_VERSION_ATTR}) OR {_NOTIFIED_VERSION_ATTR} < :v"
        )
        values = {":v": notification.version}
    try:
        table.update_item(
            Key=key,
            UpdateExpression=expression,
            ConditionExpression=condition,
            ExpressionAttributeValues=values,
        )
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def _read_scores(table: Table, game_id: str) -> dict[str, int]:
    """Reads META directly - a raw single-item read, not a t42.storage.repository import - since
    this is the one item the notifier is allowed to enrich from (DESIGN.md §8). Defaults to an
    empty map rather than raising: by the time this runs, `_claim` has already succeeded, so this
    transition will never be retried, and a degraded email beats a silently dropped one.

    Returns whatever ``{label: score}`` pairs the writer put there, without naming any of them.
    That is deliberate and is what keeps this package free of any one game's scoring shape: for
    42 the labels happen to be ``north_south``/``east_west``, and nothing here knows that.
    """
    item = table.get_item(Key={"PK": f"GAME#{game_id}", "SK": "META"}).get("Item")
    scores = item.get("scores") if item is not None else None
    if not isinstance(scores, dict):
        return {}
    return {str(label): int(score) for label, score in scores.items()}


def _render(table: Table, notification: Notification, recipient_username: str) -> tuple[str, str]:
    data: dict[str, Any] = {
        "game_id": notification.game_id,
        "recipient_username": recipient_username,
    }
    if notification.kind == "your_turn":
        return render_your_turn(data)
    if notification.kind == "game_over":
        data["scores"] = _read_scores(table, notification.game_id)
        return render_game_over(data)
    data["invited_by"] = notification.invited_by
    return render_invite(data)


def send_notifications(
    table: Table, sender: EmailSender, records: Iterable[Mapping[str, Any]]
) -> None:
    """The I/O half, injectable the same way pump.poll()'s ``handler``/``sleep`` are: a test
    supplies a moto table and a recording sender rather than a real stream and a real inbox."""
    for notification in notifications_for(records):
        if not _claim(table, notification):
            continue
        try:
            player = get_player(table, notification.player_id)
        except KeyError:
            continue  # the player no longer exists; not an error
        to = _recipient_email(player)
        if to is None:
            continue
        subject, body = _render(table, notification, player.username)
        # A real send failure should propagate - visible to Lambda's batch retry or a crashed
        # local pump - unlike the data gaps above, which degrade gracefully instead.
        sender.send(to, subject, body)


@lru_cache(maxsize=1)
def _resource() -> object:
    endpoint_url = os.environ.get(ENDPOINT_URL_ENV) or None
    return boto3.resource("dynamodb", endpoint_url=endpoint_url)


def _get_table() -> Table:
    name = os.environ.get(TABLE_NAME_ENV)
    if not name:
        raise RuntimeError(f"{TABLE_NAME_ENV} is not set")
    table: Table = _resource().Table(name)  # type: ignore[attr-defined]
    return table


def lambda_handler(event: dict[str, Any], context: Any = None) -> None:
    send_notifications(_get_table(), get_sender(), event.get("Records", []))
