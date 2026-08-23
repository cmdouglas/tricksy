"""Four profiles play a full 4-player game to ``GAME_OVER`` through CLI commands, against a real
DynamoDB Local container rather than moto (ROADMAP.md 3.7). This is the scripted end-to-end CLI
smoke test DESIGN.md §9 has listed since before there was a CLI to script, and the dogfood
milestone Phase 3 was built toward: one person, one machine, four accounts.

Marked ``integration`` and excluded from the default run, since it needs Docker: run it with
``uv run pytest -m integration``.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from mypy_boto3_dynamodb.service_resource import Table

from tricksy.api.app import app
from tricksy.api.deps import get_table
from tricksy.cli import context
from tricksy.cli.main import main

from ._helpers import play_full_game_via_cli, run_json

pytestmark = pytest.mark.integration

_PASSWORD = "correct-horse-battery"
_PROFILES = ("north", "east", "south", "west")


@pytest.fixture
def real_cli_app_client(real_table: Table) -> Iterator[TestClient]:
    """The same app ``cli_app_client`` points at, pointed at a real DynamoDB Local container
    instead of moto - mirrors ``tests/api/test_api_integration.py``'s ``real_client``."""
    app.dependency_overrides[get_table] = lambda: real_table
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _wire_transport(real_cli_app_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(context, "transport_factory", lambda url: real_cli_app_client)


def test_four_profiles_play_a_full_game_to_game_over(
    capsys: pytest.CaptureFixture[str],
) -> None:
    for profile in _PROFILES:
        exit_code = main(["--profile", profile, "register", profile, "--password", _PASSWORD])
        capsys.readouterr()
        assert exit_code == 0

    game = run_json(
        ["--profile", "north", "--json", "create-game", "--seat", "0", "--marks", "1"], capsys
    )
    code = game["game_id"]

    for profile, seat in (("east", "1"), ("south", "2"), ("west", "3")):
        exit_code = main(["--profile", profile, "join", code, "--seat", seat])
        capsys.readouterr()
        assert exit_code == 0

    final = play_full_game_via_cli(list(_PROFILES), code, capsys)
    assert final["status"] == "COMPLETE" or final["view"]["phase"] == "GAME_OVER"

    # Four profiles from one machine, none stepping on another's saved credentials.
    for profile in _PROFILES:
        whoami = run_json(["--profile", profile, "--json", "whoami"], capsys)
        assert whoami["username"] == profile
