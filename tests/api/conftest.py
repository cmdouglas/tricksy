"""Fixtures for the API contract tests (ROADMAP.md 2.5).

``TestClient`` runs the real app in-process against the real storage layer, with only the boto3
table swapped for the moto-backed one from ``tests/conftest.py``. So these tests exercise routing,
validation, auth, the engine and the repository together - everything except the network - which
is what makes them contract tests rather than handler unit tests.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from mypy_boto3_dynamodb.service_resource import Table

from tricksy.api.app import app
from tricksy.api.deps import get_table
from tricksy.notifications import get_sender

from ..notifications._helpers import FakeSender
from ._helpers import Client


@pytest.fixture
def fake_sender() -> FakeSender:
    """The recording ``EmailSender`` every test's ``client`` sends through - ROADMAP.md 4.2's
    contact-verification endpoint is the first handler that actually sends mail."""
    return FakeSender()


@pytest.fixture
def client(table: Table, fake_sender: FakeSender) -> Iterator[TestClient]:
    """The app, wired to a per-test moto table and a recording sender through FastAPI's
    dependency overrides - the reason ``get_table``/``get_sender`` are dependencies rather than
    module-level handles."""
    app.dependency_overrides[get_table] = lambda: table
    app.dependency_overrides[get_sender] = lambda: fake_sender
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def alice(client: TestClient) -> Client:
    return Client.register(client, "alice")


@pytest.fixture
def bob(client: TestClient) -> Client:
    return Client.register(client, "bob")


@pytest.fixture
def carol(client: TestClient) -> Client:
    return Client.register(client, "carol")


@pytest.fixture
def dave(client: TestClient) -> Client:
    return Client.register(client, "dave")
