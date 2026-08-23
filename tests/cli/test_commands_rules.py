from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from tricksy.cli import config, context
from tricksy.cli.command import Command
from tricksy.cli.commands import rules

from ._helpers import fake_transport

_RULE_SET = {
    "rule_set_id": "rs1",
    "name": "tournament",
    "house_rules": {
        "enabled_contracts": ["standard", "nello", "plunge"],
        "contract_options": {},
        "doubles_are_own_suit": False,
        "allow_declared_lead": "never",
        "marks_to_win": 7,
    },
    "created_at": "2026-01-01T00:00:00Z",
}


def _command() -> Command:
    return rules.COMMANDS[0]


def _parse(
    argv: list[str], *, profile: str | None = None, json: bool = False
) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    _command().configure(parser)
    args = parser.parse_args(argv)
    args.profile = profile
    args.json = json
    args.api_url = "http://x"
    return args


@pytest.fixture(autouse=True)
def _config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cfg = config.set_profile(config.Config(), "default", config.Profile("p1", "alice", "tok-1"))
    config.save(cfg)


def test_save_sends_only_given_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = fake_transport(_RULE_SET)
    monkeypatch.setattr(context, "transport_factory", lambda url: transport)

    status = _command().handler(_parse(["save", "tournament", "--marks", "5"]))

    assert status == 0
    call = transport.calls[0]
    assert call.method == "POST"
    assert call.url == "/players/me/rule-sets"
    assert call.json == {"name": "tournament", "house_rules": {"marks_to_win": 5}}


def test_save_with_no_flags_sends_an_empty_house_rules_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = fake_transport(_RULE_SET)
    monkeypatch.setattr(context, "transport_factory", lambda url: transport)

    _command().handler(_parse(["save", "tournament"]))

    assert transport.calls[0].json == {"name": "tournament", "house_rules": {}}


def test_list(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    transport = fake_transport({"rule_sets": [_RULE_SET]})
    monkeypatch.setattr(context, "transport_factory", lambda url: transport)

    status = _command().handler(_parse(["list"]))

    assert status == 0
    assert transport.calls[0].method == "GET"
    assert transport.calls[0].url == "/players/me/rule-sets"
    assert "tournament" in capsys.readouterr().out


def test_show(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = fake_transport(_RULE_SET)
    monkeypatch.setattr(context, "transport_factory", lambda url: transport)

    _command().handler(_parse(["show", "rs1"]))

    assert transport.calls[0].method == "GET"
    assert transport.calls[0].url == "/players/me/rule-sets/rs1"


def test_replace_sends_name_and_only_given_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = fake_transport(_RULE_SET)
    monkeypatch.setattr(context, "transport_factory", lambda url: transport)

    _command().handler(_parse(["replace", "rs1", "tournament-v2", "--doubles-trump"]))

    call = transport.calls[0]
    assert call.method == "PUT"
    assert call.url == "/players/me/rule-sets/rs1"
    assert call.json == {
        "name": "tournament-v2",
        "house_rules": {"doubles_are_own_suit": True},
    }


def test_delete_prints_a_confirmation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    transport = fake_transport(None, status_code=204)
    monkeypatch.setattr(context, "transport_factory", lambda url: transport)

    status = _command().handler(_parse(["delete", "rs1"]))

    assert status == 0
    assert transport.calls[0].method == "DELETE"
    assert transport.calls[0].url == "/players/me/rule-sets/rs1"
    assert "rs1" in capsys.readouterr().out


def test_delete_json_mode_prints_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    transport = fake_transport(None, status_code=204)
    monkeypatch.setattr(context, "transport_factory", lambda url: transport)

    _command().handler(_parse(["delete", "rs1"], json=True))

    assert capsys.readouterr().out == ""
