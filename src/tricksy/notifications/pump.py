"""Polls DynamoDB Streams locally and hands batches to the notification handler (ROADMAP.md 4.4).

There is no Lambda event-source mapping outside AWS, so this stands in for one: it discovers the
``Tricksy`` table's stream, tracks a shard iterator per open shard, and calls
:func:`~tricksy.notifications.handler.lambda_handler` with a ``{"Records": [...]}`` batch shaped
exactly like the one a real event-source mapping would deliver - the local path and the deployed
path exercise the same entry point, and nothing about this module needs to change when Phase 5
wires up the real thing.

Single-shard-aware, not a full stream consumer: shards are re-discovered once per poll cycle, with
no parent/child shard lineage tracking. A shard that stops yielding a ``NextShardIterator`` (closed
and fully drained) is remembered in ``_drained`` rather than merely dropped from ``_iterators`` -
``describe_stream`` keeps listing a closed shard until it ages out of the stream's retention
window, so without that memory the next cycle's discovery step would treat it as new and open a
fresh ``TRIM_HORIZON`` iterator on it, re-delivering every record in it forever. That is enough for
DynamoDB Local's normal single-shard case and the local dev/test role this module plays; a
production consumer's shard management (including persisting which shards are done across
restarts) is AWS's job once Phase 5 wires up a real event-source mapping, not this file's.

Run with::

    python -m tricksy.notifications.pump
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable
from typing import Any, cast

import boto3
from mypy_boto3_dynamodb.client import DynamoDBClient
from mypy_boto3_dynamodbstreams.client import DynamoDBStreamsClient

from .handler import lambda_handler

#: Same env vars ``tricksy.storage.schema`` reads, so a shell already set up to create the table
#: needs nothing extra to point the pump at it.
TABLE_NAME_ENV = "TRICKSY_TABLE_NAME"
ENDPOINT_URL_ENV = "TRICKSY_DYNAMODB_ENDPOINT"

#: Between poll cycles, matching DynamoDB Streams' own guidance of about one ``GetRecords`` call
#: per shard per second.
_POLL_INTERVAL_SECONDS = 1.0


def _stream_arn(dynamodb_client: DynamoDBClient, table_name: str) -> str:
    description = dynamodb_client.describe_table(TableName=table_name)
    stream_arn = description["Table"].get("LatestStreamArn")
    if stream_arn is None:
        raise RuntimeError(f"table {table_name!r} has no stream enabled")
    return stream_arn


def poll(
    dynamodb_client: DynamoDBClient,
    streams_client: DynamoDBStreamsClient,
    table_name: str,
    handler: Callable[[dict[str, Any], Any], None] = lambda_handler,
    *,
    max_iterations: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Polls ``table_name``'s stream forever, or ``max_iterations`` cycles when set (for tests),
    calling ``handler`` once per shard per cycle that has new records.

    ``handler`` and ``sleep`` are both injectable, the same dependency-injection shape
    ``now: Callable[[], datetime]`` has throughout ``tricksy.storage.accounts`` - a test supplies a
    spy handler and a no-op sleep rather than waiting on a real clock or a real notification.
    """
    stream_arn = _stream_arn(dynamodb_client, table_name)
    iterators: dict[str, str] = {}
    drained: set[str] = set()
    iterations = 0
    while max_iterations is None or iterations < max_iterations:
        description = streams_client.describe_stream(StreamArn=stream_arn)
        for shard in description["StreamDescription"].get("Shards", []):
            shard_id = shard.get("ShardId")
            if shard_id is not None and shard_id not in iterators and shard_id not in drained:
                response = streams_client.get_shard_iterator(
                    StreamArn=stream_arn, ShardId=shard_id, ShardIteratorType="TRIM_HORIZON"
                )
                iterators[shard_id] = response["ShardIterator"]

        for shard_id, iterator in list(iterators.items()):
            records_response = streams_client.get_records(ShardIterator=iterator)
            records = records_response.get("Records", [])
            if records:
                handler({"Records": records}, None)
            # boto3-stubs types NextShardIterator as always present, but the real API omits it
            # once a shard is closed and fully drained - the signal this loop needs to stop
            # tracking it. Cast away the stub's over-narrow typing rather than the runtime fact.
            next_iterator = cast(dict[str, Any], records_response).get("NextShardIterator")
            if next_iterator is None:
                del iterators[shard_id]
                drained.add(shard_id)
            else:
                iterators[shard_id] = next_iterator

        iterations += 1
        if max_iterations is None or iterations < max_iterations:
            sleep(_POLL_INTERVAL_SECONDS)


def main() -> int:
    """Reads ``TRICKSY_TABLE_NAME``/``TRICKSY_DYNAMODB_ENDPOINT`` and polls forever.

    No default table name, the same reasoning ``tricksy.storage.schema.main`` and
    ``tricksy.api.deps.get_table`` already give: silently defaulting is how a local run ends up
    pointed at production.
    """
    table_name = os.environ.get(TABLE_NAME_ENV)
    if not table_name:
        print(f"{TABLE_NAME_ENV} is not set", file=sys.stderr)
        return 1
    endpoint_url = os.environ.get(ENDPOINT_URL_ENV) or None
    dynamodb_client: DynamoDBClient = boto3.client("dynamodb", endpoint_url=endpoint_url)
    streams_client: DynamoDBStreamsClient = boto3.client(
        "dynamodbstreams", endpoint_url=endpoint_url
    )
    poll(dynamodb_client, streams_client, table_name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
