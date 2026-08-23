"""Shared test doubles for ``tests/cli/`` (ROADMAP.md 3.4), mirroring the ``tests/storage/`` /
``tests/api/`` convention of a per-package ``_helpers.py``.

``FakeTransport`` implements ``tricksy.cli.api.Transport`` without any network, and records every
call so a test can assert on method/path/body/headers - the same pair ``test_api.py`` already
defines privately; this is the promoted, reusable version.
"""

from __future__ import annotations

import json as jsonlib
import shlex
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import pytest

from tricksy.cli import render
from tricksy.cli.main import main


@dataclass(slots=True)
class FakeResponse:
    status_code: int
    body: Any = None

    def json(self) -> Any:
        return self.body


@dataclass(slots=True)
class FakeCall:
    method: str
    url: str
    json: Any
    headers: Mapping[str, str]


@dataclass(slots=True)
class FakeTransport:
    """Returns ``responses`` in order, one per call; the last one repeats once exhausted."""

    responses: list[FakeResponse]
    calls: list[FakeCall] = field(default_factory=list)

    def request(
        self,
        method: str,
        url: str,
        *,
        json: Any = None,
        headers: Mapping[str, str] | None = None,
    ) -> FakeResponse:
        self.calls.append(FakeCall(method, url, json, headers or {}))
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        return self.responses[index]


def fake_transport(*bodies: Any, status_code: int = 200) -> FakeTransport:
    """One 200 response per body, in order - the common case for tests that don't care about
    varying the status code."""
    return FakeTransport([FakeResponse(status_code, body) for body in bodies])


# -- driving `main(argv)` against a real app (ROADMAP.md 3.7) ------------------------------------


def run_json(argv: list[str], capsys: pytest.CaptureFixture[str]) -> Any:
    """Runs ``main(argv)``, asserting it succeeded, and decodes the ``--json`` output it printed -
    the round trip a scripted test needs to read back what a command did."""
    exit_code = main(argv)
    captured = capsys.readouterr()
    assert exit_code == 0, captured.err or captured.out
    return jsonlib.loads(captured.out)


def whose_turn_via_cli(
    profiles: list[str], game_id: str, capsys: pytest.CaptureFixture[str]
) -> tuple[str, Any] | None:
    """The profile to act and their current status, found by asking each profile's own ``tricksy
    status --json`` in turn - a client only ever knows its own view, so this asks rather than
    assumes, the CLI-driven counterpart to ``tests/api/_helpers.py``'s ``whose_turn``.

    Returns ``None`` once the game is over rather than raising, so callers can tell "finished"
    apart from "malfunctioning" (``whose_turn``'s HTTP counterpart never needs to, since its
    caller stops as soon as a submitted move's own response says so).
    """
    for profile in profiles:
        game = run_json(["--profile", profile, "--json", "status", game_id], capsys)
        view = game["view"]
        if game["status"] == "COMPLETE" or (view is not None and view["phase"] == "GAME_OVER"):
            return None
        if view is not None and view["legal_moves"]:
            return profile, game
    raise AssertionError(f"nobody has a legal move in game {game_id}")


def play_full_game_via_cli(
    profiles: list[str],
    game_id: str,
    capsys: pytest.CaptureFixture[str],
    *,
    max_moves: int = 200,
) -> Any:
    """Drives a game to ``GAME_OVER`` entirely through ``main(argv)`` calls, one profile at a time
    - the CLI-driven counterpart to ``tests/api/_helpers.py``'s ``play_until``.

    Each step asks :func:`whose_turn_via_cli`, then reuses ``render.py``'s own
    ``render_legal_moves`` to turn the first legal move into the literal command that submits it -
    the same text a player would read off their own terminal, rather than a second, parallel
    "which endpoint does this move kind hit" translation.
    """
    for _ in range(max_moves):
        found = whose_turn_via_cli(profiles, game_id, capsys)
        if found is None:
            return run_json(["--profile", profiles[0], "--json", "status", game_id], capsys)
        turn_profile, game = found

        command = render.render_legal_moves(game["view"]["legal_moves"][:1], game_id)
        move_argv = shlex.split(command)[1:]  # drop the leading "tricksy"
        exit_code = main(["--profile", turn_profile, *move_argv])
        capsys.readouterr()  # discard the rendered game text so the next status read stays clean
        assert exit_code == 0

    raise AssertionError(f"game {game_id} did not finish within {max_moves} moves")
