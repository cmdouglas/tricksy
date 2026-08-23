from __future__ import annotations

import argparse
import secrets
from pathlib import Path

import pytest

from tricksy.cli import config, context
from tricksy.cli.api import ApiError
from tricksy.cli.command import Command
from tricksy.cli.commands import account
from tricksy.cli.main import main

from ._helpers import fake_transport

_TOKEN_RESPONSE = {"player_id": "p1", "username": "alice", "token": "tok-1"}


def _args(
    tmp_path: Path,
    *,
    username: str = "alice",
    password: str | None = "hunter2",
    profile: str | None = None,
    json: bool = False,
) -> argparse.Namespace:
    return argparse.Namespace(
        username=username,
        password=password,
        profile=profile,
        json=json,
        api_url="http://x",
    )


def _command(name: str) -> Command:
    return next(c for c in account.COMMANDS if c.name == name)


def test_register_saves_profile_named_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = fake_transport(_TOKEN_RESPONSE)
    monkeypatch.setattr(context, "transport_factory", lambda url: transport)

    status = _command("register").handler(_args(tmp_path))

    assert status == 0
    cfg = config.load()
    profile = config.get_profile(cfg, "default")
    assert profile == config.Profile("p1", "alice", "tok-1")


def test_register_saves_profile_under_given_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = fake_transport(_TOKEN_RESPONSE)
    monkeypatch.setattr(context, "transport_factory", lambda url: transport)

    _command("register").handler(_args(tmp_path, profile="north"))

    assert config.get_profile(config.load(), "north") is not None
    assert config.get_profile(config.load(), "default") is None


def test_register_sends_username_and_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = fake_transport(_TOKEN_RESPONSE)
    monkeypatch.setattr(context, "transport_factory", lambda url: transport)

    _command("register").handler(_args(tmp_path))

    call = transport.calls[0]
    assert call.method == "POST"
    assert call.url == "/players"
    assert call.json == {"username": "alice", "password": "hunter2"}


def test_register_prompts_for_password_when_omitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = fake_transport(_TOKEN_RESPONSE)
    monkeypatch.setattr(context, "transport_factory", lambda url: transport)
    monkeypatch.setattr("getpass.getpass", lambda prompt: "prompted-pw")

    _command("register").handler(_args(tmp_path, password=None))

    assert transport.calls[0].json["password"] == "prompted-pw"


def test_login_saves_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    transport = fake_transport(_TOKEN_RESPONSE)
    monkeypatch.setattr(context, "transport_factory", lambda url: transport)

    status = _command("login").handler(_args(tmp_path))

    assert status == 0
    assert transport.calls[0].method == "POST"
    assert transport.calls[0].url == "/sessions"
    assert config.get_profile(config.load(), "default") == config.Profile("p1", "alice", "tok-1")


def test_whoami_renders_the_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = config.set_profile(config.Config(), "default", config.Profile("p1", "alice", "tok-1"))
    config.save(cfg)
    body = {
        "player_id": "p1",
        "username": "alice",
        "contacts": [],
        "created_at": "2026-01-01T00:00:00Z",
        "devices": [],
    }
    transport = fake_transport(body)
    monkeypatch.setattr(context, "transport_factory", lambda url: transport)

    status = _command("whoami").handler(
        argparse.Namespace(profile=None, json=False, api_url="http://x")
    )

    assert status == 0
    assert transport.calls[0].method == "GET"
    assert transport.calls[0].url == "/players/me"
    assert "alice" in capsys.readouterr().out


def test_whoami_without_a_profile_exits_via_not_authenticated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(context, "transport_factory", lambda url: fake_transport({}))

    with pytest.raises(ApiError) as exc_info:
        _command("whoami").handler(argparse.Namespace(profile=None, json=False, api_url="http://x"))

    assert exc_info.value.code == "NOT_AUTHENTICATED"


def test_logout_removes_only_its_own_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = config.set_profile(config.Config(), "north", config.Profile("p1", "alice", "tok-1"))
    cfg = config.set_profile(cfg, "south", config.Profile("p2", "bob", "tok-2"))
    config.save(cfg)
    transport = fake_transport(None, status_code=204)
    monkeypatch.setattr(context, "transport_factory", lambda url: transport)

    status = _command("logout").handler(
        argparse.Namespace(profile="north", json=False, api_url="http://x")
    )

    assert status == 0
    loaded = config.load()
    assert config.get_profile(loaded, "north") is None
    assert config.get_profile(loaded, "south") is not None


def test_logout_calls_delete_sessions_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = config.set_profile(config.Config(), "default", config.Profile("p1", "alice", "tok-1"))
    config.save(cfg)
    transport = fake_transport(None, status_code=204)
    monkeypatch.setattr(context, "transport_factory", lambda url: transport)

    _command("logout").handler(argparse.Namespace(profile=None, json=False, api_url="http://x"))

    assert transport.calls[0].method == "DELETE"
    assert transport.calls[0].url == "/sessions/current"
    assert transport.calls[0].headers["Authorization"] == "Bearer tok-1"


# -- contacts and password reset (ROADMAP.md 4.6) ------------------------------------------------


def _sign_in(profile: str = "default") -> None:
    cfg = config.set_profile(config.Config(), profile, config.Profile("p1", "alice", "tok-1"))
    config.save(cfg)


def _contact_args(
    contact_command: str,
    *,
    address: str | None = None,
    kind: str = "email",
    token: str | None = None,
    profile: str | None = None,
    json: bool = False,
) -> argparse.Namespace:
    return argparse.Namespace(
        contact_command=contact_command,
        address=address,
        kind=kind,
        token=token,
        profile=profile,
        json=json,
        api_url="http://x",
    )


def test_contacts_lists_channels(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _sign_in()
    body = {
        "contacts": [
            {"kind": "email", "address": "a@example.com", "verified": True, "notify": True}
        ]
    }
    transport = fake_transport(body)
    monkeypatch.setattr(context, "transport_factory", lambda url: transport)

    status = _command("contacts").handler(
        argparse.Namespace(profile=None, json=False, api_url="http://x")
    )

    assert status == 0
    assert transport.calls[0].method == "GET"
    assert transport.calls[0].url == "/players/me/contacts"


def test_contact_add_sends_kind_and_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sign_in()
    contact = {"kind": "email", "address": "a@example.com", "verified": False, "notify": True}
    transport = fake_transport(contact, status_code=201)
    monkeypatch.setattr(context, "transport_factory", lambda url: transport)

    status = _command("contact").handler(
        _contact_args("add", address="a@example.com", kind="email")
    )

    assert status == 0
    call = transport.calls[0]
    assert call.method == "POST"
    assert call.url == "/players/me/contacts"
    assert call.json == {"kind": "email", "address": "a@example.com"}


def test_contact_remove_calls_delete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _sign_in()
    transport = fake_transport(None, status_code=204)
    monkeypatch.setattr(context, "transport_factory", lambda url: transport)

    status = _command("contact").handler(_contact_args("remove", address="a@example.com"))

    assert status == 0
    assert transport.calls[0].method == "DELETE"
    assert transport.calls[0].url == "/players/me/contacts/a@example.com"


def test_contact_verify_calls_verification_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sign_in()
    transport = fake_transport(None, status_code=202)
    monkeypatch.setattr(context, "transport_factory", lambda url: transport)

    status = _command("contact").handler(_contact_args("verify", address="a@example.com"))

    assert status == 0
    assert transport.calls[0].method == "POST"
    assert transport.calls[0].url == "/players/me/contacts/a@example.com/verification"


def test_contact_confirm_works_without_a_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = fake_transport(None, status_code=204)
    monkeypatch.setattr(context, "transport_factory", lambda url: transport)

    status = _command("contact").handler(_contact_args("confirm", token="tok-abc"))

    assert status == 0
    call = transport.calls[0]
    assert call.method == "POST"
    assert call.url == "/contacts/verify"
    assert call.json == {"token": "tok-abc"}
    assert "Authorization" not in call.headers


def test_contact_mute_sends_notify_false(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _sign_in()
    contact = {"kind": "email", "address": "a@example.com", "verified": True, "notify": False}
    transport = fake_transport(contact)
    monkeypatch.setattr(context, "transport_factory", lambda url: transport)

    status = _command("contact").handler(_contact_args("mute", address="a@example.com"))

    assert status == 0
    call = transport.calls[0]
    assert call.method == "PATCH"
    assert call.url == "/players/me/contacts/a@example.com"
    assert call.json == {"notify": False}


def test_contact_unmute_sends_notify_true(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _sign_in()
    contact = {"kind": "email", "address": "a@example.com", "verified": True, "notify": True}
    transport = fake_transport(contact)
    monkeypatch.setattr(context, "transport_factory", lambda url: transport)

    status = _command("contact").handler(_contact_args("unmute", address="a@example.com"))

    assert status == 0
    assert transport.calls[0].json == {"notify": True}


def test_forgot_password_works_without_a_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = fake_transport(None, status_code=202)
    monkeypatch.setattr(context, "transport_factory", lambda url: transport)

    status = _command("forgot-password").handler(
        argparse.Namespace(username="alice", profile=None, json=False, api_url="http://x")
    )

    assert status == 0
    call = transport.calls[0]
    assert call.method == "POST"
    assert call.url == "/password-resets"
    assert call.json == {"username": "alice"}
    assert "Authorization" not in call.headers


def test_reset_password_reads_password_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = fake_transport(None, status_code=204)
    monkeypatch.setattr(context, "transport_factory", lambda url: transport)

    status = _command("reset-password").handler(
        argparse.Namespace(
            token="reset-tok", password="new-secret", profile=None, json=False, api_url="http://x"
        )
    )

    assert status == 0
    call = transport.calls[0]
    assert call.method == "POST"
    assert call.url == "/password-resets/confirm"
    assert call.json == {"token": "reset-tok", "new_password": "new-secret"}


def test_reset_password_prompts_for_password_when_omitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = fake_transport(None, status_code=204)
    monkeypatch.setattr(context, "transport_factory", lambda url: transport)
    monkeypatch.setattr("getpass.getpass", lambda prompt: "prompted-pw")

    _command("reset-password").handler(
        argparse.Namespace(
            token="reset-tok", password=None, profile=None, json=False, api_url="http://x"
        )
    )

    assert transport.calls[0].json["new_password"] == "prompted-pw"


def test_every_shape_of_minted_token_parses_through_main(monkeypatch: pytest.MonkeyPatch) -> None:
    """The CLI half of the leading-dash fix, driven through ``main(argv)`` rather than the handler
    so ``argparse`` itself is exercised.

    ``tricksy.notifications.messages`` prints these tokens inside a command the player copies and
    runs, so every token the server can mint has to survive the parser. A leading ``-`` does not:
    ``argparse`` reads it as an option flag and exits 2 with "the following arguments are required:
    token" before any request is made. ``accounts._mint_single_use_token`` therefore re-mints past
    that one case, and this asserts the resulting alphabet really is parseable - including the
    ``-`` and ``_`` that base64url still puts *inside* a token, which are harmless and are not
    worth re-minting over.

    Note the deliberate limit: this locks the contract at the seam, not the parser's tolerance in
    general. A leading-dash value still fails, which is why the guarantee lives at the mint."""
    transport = fake_transport(None, status_code=204)
    monkeypatch.setattr(context, "transport_factory", lambda url: transport)

    tokens = [t for t in (secrets.token_urlsafe(32) for _ in range(400)) if not t.startswith("-")]
    tokens += ["a-b_c", "_leading_underscore", "trailing-"]
    assert any("-" in t for t in tokens) and any("_" in t for t in tokens)

    for token in tokens:
        for argv in (
            ["contact", "confirm", token],
            ["reset-password", token, "--password", "hunter2"],
        ):
            transport.calls.clear()
            status = main(argv)

            assert status == 0, f"{argv[0]} exited {status} for {token!r} (2 = argparse rejected)"
            assert transport.calls[0].json is not None
            assert transport.calls[0].json["token"] == token
