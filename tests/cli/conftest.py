"""Shared fixtures for ``tests/cli/`` (ROADMAP.md 3.7).

``_config_home`` is autouse so no test under here ever touches a real
``~/.config/tricksy/config.json`` - it's the same one-line isolation every ``test_commands_*.py``
file already hand-rolled individually; promoted here once ``conftest.py`` existed for
:func:`cli_app_client` anyway.

``cli_app_client`` mirrors ``tests/api/conftest.py``'s ``client`` fixture exactly: the real FastAPI
app in-process, with the boto3 table swapped for the moto-backed one and the email sender swapped
for a recording fake. It's what ``context.transport_factory`` gets pointed at so `main(argv)` can
be driven against the real app rather than a `FakeTransport` (ROADMAP.md 3.2's whole reason for
the `Transport` protocol). The sender override (ROADMAP.md 4.6) is what lets
``test_commands.py``'s full walkthrough capture a mailed verification/reset token and complete
those round trips end to end, the same way ``tests/api/conftest.py``'s ``fake_sender`` does for
the API-level contract tests.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mypy_boto3_dynamodb.service_resource import Table

from tricksy.api.app import app
from tricksy.api.deps import get_table
from tricksy.notifications import get_sender

from ..notifications._helpers import FakeSender


@pytest.fixture(autouse=True)
def _config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))


@pytest.fixture
def fake_sender() -> FakeSender:
    return FakeSender()


@pytest.fixture
def cli_app_client(table: Table, fake_sender: FakeSender) -> Iterator[TestClient]:
    app.dependency_overrides[get_table] = lambda: table
    app.dependency_overrides[get_sender] = lambda: fake_sender
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
