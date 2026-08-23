"""Decodes raw DynamoDB Streams records into plain data (ROADMAP.md 4.4).

A stream record carries DynamoDB-JSON attribute values (``{"S": "foo"}``, ``{"N": "3"}``, ...) -
the one place in this codebase that shape is visible, since everywhere else goes through the
resource-level ``Table`` API, which hands back plain Python values directly. This module is the
single translation point, so :mod:`tricksy.notifications.handler` (ROADMAP.md 4.5) can pattern-match
against plain dicts and be tested without a stream, the same way :mod:`tricksy.storage.codec` and
:mod:`tricksy.cli.render` each have their own single translation point for their own wire shapes.

Pure, no I/O - this only ever touches the record dict it's handed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from boto3.dynamodb.types import TypeDeserializer

_deserializer = TypeDeserializer()


@dataclass(frozen=True, slots=True)
class Transition:
    """One stream record, decoded. ``keys`` is always present (every event type carries it);
    ``old``/``new`` are ``None`` when the record has no ``OldImage`` (an ``INSERT``) or no
    ``NewImage`` (a ``REMOVE``) - never present in the record at all rather than an empty dict, so
    a rule can tell "no prior value" apart from "prior value happened to be empty"."""

    event_name: str
    keys: dict[str, Any]
    old: dict[str, Any] | None
    new: dict[str, Any] | None


def _restore_empty_map_wrapper(value: Any) -> Any:
    """Real DynamoDB Streams - confirmed against DynamoDB Local, not just moto - represents an
    empty map attribute as a bare ``{}`` in ``OldImage``/``NewImage``, dropping the ``"M"`` type
    wrapper every other attribute value carries (``HouseRules.contract_options`` is the common
    case: it's ``{}`` whenever a game uses no contract options at all). ``TypeDeserializer``
    treats a bare ``{}`` as malformed input rather than an empty map and raises. Empty lists are
    not affected - they arrive as ``{"L": []}``, wrapper intact - so this only ever needs to
    restore ``"M"``. Walks the attribute-value tree ahead of deserializing, since the bare ``{}``
    can appear at any nesting depth, not just the top level.
    """
    if not isinstance(value, dict):
        return value
    if not value:
        return {"M": {}}
    ((type_key, inner),) = value.items()
    if type_key == "M":
        return {"M": {k: _restore_empty_map_wrapper(v) for k, v in inner.items()}}
    if type_key == "L":
        return {"L": [_restore_empty_map_wrapper(v) for v in inner]}
    return value


def _decode(image: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: _deserializer.deserialize(_restore_empty_map_wrapper(value))
        for key, value in image.items()
    }


def _decode_optional(image: Mapping[str, Any] | None) -> dict[str, Any] | None:
    return None if image is None else _decode(image)


def transition_from_record(record: Mapping[str, Any]) -> Transition:
    """Decodes one entry of a stream batch's ``Records`` list - the same shape whether it arrived
    via a real Lambda event-source mapping or :mod:`tricksy.notifications.pump`'s local poll."""
    body = record["dynamodb"]
    return Transition(
        event_name=record["eventName"],
        keys=_decode(body["Keys"]),
        old=_decode_optional(body.get("OldImage")),
        new=_decode_optional(body.get("NewImage")),
    )
