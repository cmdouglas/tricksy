"""Shared DynamoDB fixtures, used by both ``tests/storage/`` and ``tests/api/``.

``table`` (ROADMAP.md 1.3) gives each test its own in-memory DynamoDB via moto, so most of the
suite never touches a real AWS account or needs Docker. ``real_table`` (ROADMAP.md 1.6) is its
integration-test counterpart, backed by a real DynamoDB Local running in Docker via
testcontainers - reserved for ``@pytest.mark.integration`` tests, which prove nothing in the
repository secretly depends on moto's approximation of DynamoDB.

These live at the top level rather than under ``tests/storage/`` because the API contract tests
(ROADMAP.md 2.5) drive the real storage layer too, injecting ``table`` into the app through
FastAPI's ``dependency_overrides``.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

import boto3
import pytest
from botocore.exceptions import EndpointConnectionError
from moto import mock_aws
from mypy_boto3_dynamodb.service_resource import Table
from testcontainers.core.container import DockerContainer

from tricksy.storage.schema import create_table


@pytest.fixture
def table() -> Iterator[Table]:
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        yield create_table(resource, "Tricksy")


@pytest.fixture(scope="session")
def dynamodb_local() -> Iterator[str]:
    """A real DynamoDB Local instance in Docker, alive for the whole test session. Yields its
    endpoint URL once it's actually accepting requests - readiness is checked by polling
    ``list_tables()`` through boto3 itself rather than matching a log line, so it keeps working
    across image versions."""
    with DockerContainer("amazon/dynamodb-local:latest").with_exposed_ports(8000) as container:
        endpoint_url = (
            f"http://{container.get_container_host_ip()}:{container.get_exposed_port(8000)}"
        )
        client = boto3.client(
            "dynamodb",
            endpoint_url=endpoint_url,
            region_name="us-east-1",
            aws_access_key_id="local",
            aws_secret_access_key="local",
        )
        deadline = time.monotonic() + 30
        while True:
            try:
                client.list_tables()
                break
            except EndpointConnectionError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.5)
        yield endpoint_url


@pytest.fixture
def real_table(dynamodb_local: str) -> Iterator[Table]:
    """A fresh ``Tricksy`` table in the real DynamoDB Local instance, created before and dropped
    after each test - the same per-test isolation the moto ``table`` fixture gives, just against a
    long-lived container instead of a fresh mock."""
    resource = boto3.resource(
        "dynamodb",
        endpoint_url=dynamodb_local,
        region_name="us-east-1",
        aws_access_key_id="local",
        aws_secret_access_key="local",
    )
    table = create_table(resource, "Tricksy")
    table.wait_until_exists()
    try:
        yield table
    finally:
        table.delete()
