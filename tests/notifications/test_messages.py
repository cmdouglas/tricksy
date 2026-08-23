"""ROADMAP.md 4.1: the pure ``dict -> (subject, body)`` renderers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from tricksy.notifications.messages import (
    render_game_over,
    render_invite,
    render_password_reset,
    render_verify_contact,
    render_your_turn,
)

_CASES: tuple[tuple[Callable[[dict[str, Any]], tuple[str, str]], dict[str, Any]], ...] = (
    (
        render_your_turn,
        {"game_id": "7F3AKM", "recipient_username": "alice"},
    ),
    (
        render_game_over,
        {
            "game_id": "7F3AKM",
            "recipient_username": "alice",
            "scores": {"north_south": 0, "east_west": 0},
        },
    ),
    (
        render_game_over,
        {
            "game_id": "7F3AKM",
            "recipient_username": "alice",
            "scores": {"north_south": 7, "east_west": 3},
        },
    ),
    (
        render_invite,
        {"game_id": "7F3AKM", "recipient_username": "alice", "invited_by": "bob"},
    ),
    (
        render_verify_contact,
        {"address": "alice@example.com", "token": "tok123"},
    ),
    (
        render_password_reset,
        {"token": "tok123"},
    ),
)


@pytest.mark.parametrize(("renderer", "data"), _CASES)
def test_renderer_returns_nonempty_subject_and_body(
    renderer: Callable[[dict[str, Any]], tuple[str, str]], data: dict[str, Any]
) -> None:
    subject, body = renderer(data)
    assert isinstance(subject, str) and subject
    assert isinstance(body, str) and body


def test_render_your_turn_mentions_game_and_recipient() -> None:
    data = {"game_id": "7F3AKM", "recipient_username": "alice"}
    subject, body = render_your_turn(data)
    assert "7F3AKM" in subject
    assert "7F3AKM" in body
    assert "alice" in body


def test_render_game_over_reports_final_scores() -> None:
    data = {
        "game_id": "7F3AKM",
        "recipient_username": "alice",
        "scores": {"north_south": 7, "east_west": 3},
    }
    subject, body = render_game_over(data)
    assert "7F3AKM" in subject
    assert "7" in body
    assert "3" in body


def test_render_game_over_reports_zero_zero() -> None:
    data = {
        "game_id": "7F3AKM",
        "recipient_username": "alice",
        "scores": {"north_south": 0, "east_west": 0},
    }
    _, body = render_game_over(data)
    assert "North/South 0" in body
    assert "East/West 0" in body


def test_render_game_over_names_no_scoring_sides_of_its_own() -> None:
    """The labels come from the data, not from this module - so a game that scores per player
    instead of per partnership renders without a change here (DESIGN.md §11)."""
    data = {
        "game_id": "7F3AKM",
        "recipient_username": "alice",
        "scores": {"alice": 12, "bob": 4},
    }
    _, body = render_game_over(data)
    assert "Alice 12" in body
    assert "Bob 4" in body
    assert "North" not in body


def test_render_game_over_survives_an_empty_score_map() -> None:
    """``_read_scores`` returns ``{}`` when META has none; the email still has to go out."""
    data: dict[str, object] = {
        "game_id": "7F3AKM",
        "recipient_username": "alice",
        "scores": {},
    }
    subject, body = render_game_over(data)
    assert "7F3AKM" in subject
    assert "alice" in body


def test_render_invite_mentions_inviter_and_game() -> None:
    data = {"game_id": "7F3AKM", "recipient_username": "alice", "invited_by": "bob"}
    _, body = render_invite(data)
    assert "bob" in body
    assert "7F3AKM" in body


def test_render_verify_contact_mentions_address_and_token() -> None:
    data = {"address": "alice@example.com", "token": "tok123"}
    _, body = render_verify_contact(data)
    assert "alice@example.com" in body
    assert "tok123" in body


def test_render_password_reset_mentions_the_token() -> None:
    data = {"token": "tok123"}
    _, body = render_password_reset(data)
    assert "tok123" in body
