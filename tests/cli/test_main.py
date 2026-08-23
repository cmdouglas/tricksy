from __future__ import annotations

import argparse

import pytest

from tricksy.cli.api import ApiError
from tricksy.cli.main import Command, _run, main


def test_no_command_returns_usage_exit_code() -> None:
    assert main([]) == 2


def test_unknown_command_returns_usage_exit_code() -> None:
    assert main(["nope"]) == 2


def test_unknown_command_prints_usage_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    main(["nope"])

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err != ""


def test_help_flag_returns_zero_and_prints_to_stdout(
    capsys: pytest.CaptureFixture[str],
) -> None:
    status = main(["--help"])

    captured = capsys.readouterr()
    assert status == 0
    assert "usage" in captured.out.lower()


def _fake_command(name: str, handler: object) -> Command:
    return Command(
        name=name,
        help=f"{name} help",
        configure=lambda parser: None,
        handler=handler,  # type: ignore[arg-type]
    )


def test_dispatch_calls_the_matching_handler_only() -> None:
    calls: list[str] = []

    def make_handler(name: str) -> object:
        def handler(args: argparse.Namespace) -> int:
            calls.append(name)
            return 0

        return handler

    ping = _fake_command("ping", make_handler("ping"))
    pong = _fake_command("pong", make_handler("pong"))

    _run(["ping"], (ping, pong))

    assert calls == ["ping"]


def test_dispatch_returns_the_handlers_exit_code_unchanged() -> None:
    command = _fake_command("ping", lambda args: 7)

    assert _run(["ping"], (command,)) == 7


def test_command_configure_hook_wires_its_own_arguments() -> None:
    captured: dict[str, object] = {}

    def configure(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("code")

    def handler(args: argparse.Namespace) -> int:
        captured["code"] = args.code
        return 0

    command = Command(name="status", help="status help", configure=configure, handler=handler)

    _run(["status", "ABC"], (command,))

    assert captured["code"] == "ABC"


def test_global_flags_are_parsed_before_the_subcommand() -> None:
    captured: dict[str, object] = {}

    def handler(args: argparse.Namespace) -> int:
        captured["api_url"] = args.api_url
        captured["profile"] = args.profile
        captured["json"] = args.json
        return 0

    command = _fake_command("ping", handler)

    _run(
        ["--api-url", "http://x", "--profile", "north", "--json", "ping"],
        (command,),
    )

    assert captured == {"api_url": "http://x", "profile": "north", "json": True}


def test_env_vars_supply_global_flag_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRICKSY_API_URL", "http://from-env")
    monkeypatch.setenv("TRICKSY_PROFILE", "south")
    captured: dict[str, object] = {}

    def handler(args: argparse.Namespace) -> int:
        captured["api_url"] = args.api_url
        captured["profile"] = args.profile
        return 0

    command = _fake_command("ping", handler)

    _run(["ping"], (command,))

    assert captured == {"api_url": "http://from-env", "profile": "south"}


def test_explicit_flag_overrides_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRICKSY_PROFILE", "south")
    captured: dict[str, object] = {}

    def handler(args: argparse.Namespace) -> int:
        captured["profile"] = args.profile
        return 0

    command = _fake_command("ping", handler)

    _run(["--profile", "north", "ping"], (command,))

    assert captured["profile"] == "north"


def test_global_flag_after_the_subcommand_is_rejected() -> None:
    command = _fake_command("ping", lambda args: 0)

    assert _run(["ping", "--profile", "north"], (command,)) == 2


def test_api_error_maps_to_its_documented_exit_code() -> None:
    def handler(args: argparse.Namespace) -> int:
        raise ApiError(409, "OUT_OF_TURN", "west is not the player to move")

    command = _fake_command("play", handler)

    assert _run(["play"], (command,)) == 5


def test_api_error_prints_message_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    def handler(args: argparse.Namespace) -> int:
        raise ApiError(404, "GAME_NOT_FOUND", "no such game")

    command = _fake_command("status", handler)

    _run(["status"], (command,))

    captured = capsys.readouterr()
    assert "no such game" in captured.err


def test_api_error_with_json_flag_prints_the_envelope(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def handler(args: argparse.Namespace) -> int:
        raise ApiError(404, "GAME_NOT_FOUND", "no such game")

    command = _fake_command("status", handler)

    _run(["--json", "status"], (command,))

    captured = capsys.readouterr()
    assert '"code": "GAME_NOT_FOUND"' in captured.err


def test_unrecognised_error_code_exits_one() -> None:
    def handler(args: argparse.Namespace) -> int:
        raise ApiError(500, "SOMETHING_NEW", "the server grew a new code")

    command = _fake_command("status", handler)

    assert _run(["status"], (command,)) == 1


def test_unexpected_exception_exits_one(capsys: pytest.CaptureFixture[str]) -> None:
    def handler(args: argparse.Namespace) -> int:
        raise ConnectionError("connection refused")

    command = _fake_command("status", handler)

    status = _run(["status"], (command,))

    captured = capsys.readouterr()
    assert status == 1
    assert "connection refused" in captured.err
