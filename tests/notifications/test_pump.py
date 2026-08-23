"""ROADMAP.md 4.4: polling DynamoDB Streams locally and handing batches to a handler."""

from __future__ import annotations

from typing import Any, cast

import boto3
from mypy_boto3_dynamodb.service_resource import Table
from mypy_boto3_dynamodbstreams.client import DynamoDBStreamsClient

from tricksy.notifications.pump import poll


class _SpyHandler:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, event: dict[str, Any], context: Any) -> None:
        self.calls.append(event)


def test_poll_delivers_a_batch_for_a_new_item(table: Table) -> None:
    dynamodb_client = boto3.client("dynamodb", region_name="us-east-1")
    streams_client = boto3.client("dynamodbstreams", region_name="us-east-1")
    table.put_item(Item={"PK": "PLAYER#abc", "SK": "GAME#7F3AKM", "is_my_turn": False})
    handler = _SpyHandler()

    poll(
        dynamodb_client,
        streams_client,
        table.name,
        handler=handler,
        max_iterations=1,
        sleep=lambda _: None,
    )

    assert len(handler.calls) == 1
    records = handler.calls[0]["Records"]
    assert len(records) == 1
    record = records[0]
    assert record["eventName"] == "INSERT"
    assert record["dynamodb"]["Keys"] == {
        "PK": {"S": "PLAYER#abc"},
        "SK": {"S": "GAME#7F3AKM"},
    }


class _OneShardClosingAfterOneBatch:
    """A fake ``dynamodbstreams`` client with one shard that yields exactly one batch of records
    before closing (``NextShardIterator`` absent from then on) - but, matching real DynamoDB
    Streams, ``describe_stream`` keeps listing that shard for a while after it closes. Proves
    ``poll`` never asks for a fresh iterator on a shard it has already drained."""

    def __init__(self) -> None:
        self.shard_iterator_requests = 0

    def describe_stream(self, **kwargs: Any) -> dict[str, Any]:
        return {"StreamDescription": {"Shards": [{"ShardId": "shard-1"}]}}

    def get_shard_iterator(self, **kwargs: Any) -> dict[str, Any]:
        self.shard_iterator_requests += 1
        return {"ShardIterator": "iter-0"}

    def get_records(self, **kwargs: Any) -> dict[str, Any]:
        if kwargs["ShardIterator"] == "iter-0":
            return {
                "Records": [{"eventName": "INSERT", "dynamodb": {"Keys": {}}}],
                "NextShardIterator": "iter-1",
            }
        return {"Records": []}  # closed: no NextShardIterator key at all


def test_poll_never_reopens_a_drained_shard(table: Table) -> None:
    dynamodb_client = boto3.client("dynamodb", region_name="us-east-1")
    streams_client = cast(DynamoDBStreamsClient, _OneShardClosingAfterOneBatch())
    handler = _SpyHandler()

    poll(
        dynamodb_client,
        streams_client,
        table.name,
        handler=handler,
        max_iterations=4,
        sleep=lambda _: None,
    )

    assert len(handler.calls) == 1, "the closed shard must not be redelivered on later cycles"
    assert cast(_OneShardClosingAfterOneBatch, streams_client).shard_iterator_requests == 1


def test_poll_calls_nothing_when_the_stream_is_empty(table: Table) -> None:
    dynamodb_client = boto3.client("dynamodb", region_name="us-east-1")
    streams_client = boto3.client("dynamodbstreams", region_name="us-east-1")
    handler = _SpyHandler()

    poll(
        dynamodb_client,
        streams_client,
        table.name,
        handler=handler,
        max_iterations=1,
        sleep=lambda _: None,
    )

    assert handler.calls == []
