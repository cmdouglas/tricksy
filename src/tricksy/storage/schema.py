"""The ``Tricksy`` table definition (DESIGN.md §4.1) and a way to create it outside a test run.

:func:`create_table` is the single source of truth for the table's shape - the moto and DynamoDB
Local fixtures in ``tests/conftest.py`` both build from it, which is the property ROADMAP.md 2.7.3
wanted when it put the ``OpenGames`` index in one shared helper; this just widens "shared" to
include a local run outside pytest too. Written with literal kwargs, not a splatted dict, because
boto3-stubs types ``create_table`` as a set of overloads keyed on exactly those keyword names.

The ``OpenGames`` GSI is the table's only secondary index: a sparse index on
``GSI1PK``/``GSI1SK``, populated only by a public ``WAITING`` game's ``META`` item, projecting
``ALL`` so a browse row needs no follow-up read.

The table also has a DynamoDB Stream enabled, with both images so a consumer sees a *transition*
rather than just a current state (ROADMAP.md 4.4) - every rule Phase 4.5's notifier applies is a
transition (``is_my_turn`` false to true, and so on), and old images are what make that visible.

This module - not a ``tricksy dev`` CLI subcommand - is where local table creation lives, because
``tricksy.cli`` may not import boto3 (ROADMAP.md 3.7 has a layering test enforcing that). Run it
with::

    python -m tricksy.storage.schema
"""

from __future__ import annotations

import os
import sys

import boto3
from mypy_boto3_dynamodb.service_resource import DynamoDBServiceResource, Table

#: Same env vars ``tricksy.api.deps`` reads, so a shell already set up to run the API needs nothing
#: extra to create the table it points at.
TABLE_NAME_ENV = "TRICKSY_TABLE_NAME"
ENDPOINT_URL_ENV = "TRICKSY_DYNAMODB_ENDPOINT"


def create_table(dynamodb: DynamoDBServiceResource, name: str) -> Table:
    """Creates the ``Tricksy`` table shape under ``name`` and returns a handle to it.

    Does not wait for the table to become ACTIVE - the moto ``table`` fixture doesn't need to
    (moto is synchronous), so that stays the caller's job, as it already is for ``real_table`` in
    ``tests/conftest.py``.
    """
    dynamodb.create_table(
        TableName=name,
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
            {"AttributeName": "GSI1PK", "AttributeType": "S"},
            {"AttributeName": "GSI1SK", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "OpenGames",
                "KeySchema": [
                    {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
        BillingMode="PAY_PER_REQUEST",
        StreamSpecification={"StreamEnabled": True, "StreamViewType": "NEW_AND_OLD_IMAGES"},
    )
    return dynamodb.Table(name)


def main() -> int:
    """Creates the table against ``TRICKSY_DYNAMODB_ENDPOINT`` (DynamoDB Local, typically) under
    ``TRICKSY_TABLE_NAME``, and waits for it to exist before returning - unlike :func:`create_table`
    itself, a one-shot CLI invocation should not hand back control before the table is usable.

    No default table name, matching ``tricksy.api.deps.get_table``: silently defaulting to a name is
    how a local run ends up pointed at production.
    """
    name = os.environ.get(TABLE_NAME_ENV)
    if not name:
        print(f"{TABLE_NAME_ENV} is not set", file=sys.stderr)
        return 1
    endpoint_url = os.environ.get(ENDPOINT_URL_ENV) or None
    dynamodb = boto3.resource("dynamodb", endpoint_url=endpoint_url)
    table = create_table(dynamodb, name)
    table.wait_until_exists()
    print(f"created {name!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
