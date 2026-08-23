"""Shared boto3 plumbing for the repository modules.

Two wrinkles of the resource-level ``Table`` API that every module here has to deal with, kept in
one place rather than repeated:

``TransactWriteItems`` isn't a resource-level method - it's only on the client - but
``table.meta.client`` is still the *resource's* client, carrying the same auto-serialization hooks
as ``Table.put_item``/``get_item``: it accepts plain Python values in ``Item``/``Key``/
``ExpressionAttributeValues`` directly. (A client built via ``boto3.client("dynamodb")`` instead
of a resource does not carry those hooks and would need ``boto3.dynamodb.types.TypeSerializer``.)

Reads go through the resource's ``get_item``/``query``, which auto-deserialize, but always to
``Decimal`` for numbers rather than ``int``; ``from_dynamo`` converts that back before anything
reaches :mod:`t42.storage.codec`, which was never written to expect ``Decimal`` (deliberately -
``codec.py`` stays boto3-agnostic, so the conversion lives here instead).
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any, cast

from botocore.exceptions import ClientError
from mypy_boto3_dynamodb.service_resource import Table
from mypy_boto3_dynamodb.type_defs import TransactWriteItemTypeDef


def transact_write(table: Table, items: list[dict[str, Any]]) -> None:
    """boto3-stubs types ``TransactItems`` against the raw wire-format ``AttributeValue`` shape,
    unaware that ``table.meta.client`` accepts plain Python values at runtime (see the module
    docstring) - the ``cast`` reconciles the two."""
    table.meta.client.transact_write_items(
        TransactItems=cast(Sequence[TransactWriteItemTypeDef], items)
    )


def from_dynamo(value: Any) -> Any:
    """Undoes the resource API's Number -> ``Decimal`` deserialization, recursively. Domino
    scoring never produces a fractional value, so this is always exact."""
    if isinstance(value, Decimal):
        return int(value)
    if isinstance(value, list):
        return [from_dynamo(v) for v in value]
    if isinstance(value, dict):
        return {k: from_dynamo(v) for k, v in value.items()}
    return value


def as_text(value: Any) -> str:
    """Narrow one attribute read back from an item to ``str``.

    boto3-stubs types every attribute as a union of everything DynamoDB can hold, which is wider
    than what we wrote. This checks at runtime rather than casting, so an item corrupted into the
    wrong shape fails loudly here instead of somewhere further downstream.
    """
    if not isinstance(value, str):
        raise TypeError(f"expected a string attribute, got {value!r}")
    return value


def is_transaction_cancelled(exc: ClientError) -> bool:
    """Whether ``exc`` is DynamoDB rejecting a transaction, which is how every conditional write
    in this package reports that its condition did not hold."""
    return exc.response["Error"]["Code"] == "TransactionCanceledException"
