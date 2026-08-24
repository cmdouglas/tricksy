from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import pytest

from tricksy.cli.api import ApiClient, ApiError, HttpTransport


@dataclass(slots=True)
class _FakeResponse:
    status_code: int
    body: Any = None

    def json(self) -> Any:
        if self.body is None:
            raise ValueError("no body")
        return self.body


@dataclass(slots=True)
class _FakeCall:
    method: str
    url: str
    json: Any
    headers: Mapping[str, str]


@dataclass(slots=True)
class _FakeTransport:
    response: _FakeResponse
    calls: list[_FakeCall] = field(default_factory=list)

    def request(
        self,
        method: str,
        url: str,
        *,
        json: Any = None,
        headers: Mapping[str, str] | None = None,
    ) -> _FakeResponse:
        self.calls.append(_FakeCall(method, url, json, headers or {}))
        return self.response


def test_success_returns_decoded_body() -> None:
    transport = _FakeTransport(_FakeResponse(200, {"ok": True}))
    client = ApiClient(transport)

    assert client.request("GET", "/players/me") == {"ok": True}


def test_204_returns_none_without_decoding_body() -> None:
    transport = _FakeTransport(_FakeResponse(204))
    client = ApiClient(transport)

    assert client.request("DELETE", "/sessions/current") is None


def test_token_sends_authorization_header() -> None:
    transport = _FakeTransport(_FakeResponse(200, {}))
    client = ApiClient(transport, token="tok-123")

    client.request("GET", "/players/me")

    assert transport.calls[0].headers["Authorization"] == "Bearer tok-123"


def test_no_token_omits_authorization_header() -> None:
    transport = _FakeTransport(_FakeResponse(200, {}))
    client = ApiClient(transport)

    client.request("GET", "/games/open")

    assert "Authorization" not in transport.calls[0].headers


def test_idempotent_sends_a_fresh_key_each_call() -> None:
    transport = _FakeTransport(_FakeResponse(200, {}))
    client = ApiClient(transport)

    client.request("POST", "/games/ABC/play", json={}, idempotent=True)
    client.request("POST", "/games/ABC/play", json={}, idempotent=True)

    first = transport.calls[0].headers["Idempotency-Key"]
    second = transport.calls[1].headers["Idempotency-Key"]
    assert first != second


def test_non_idempotent_omits_idempotency_key() -> None:
    transport = _FakeTransport(_FakeResponse(200, {}))
    client = ApiClient(transport)

    client.request("GET", "/players/me")

    assert "Idempotency-Key" not in transport.calls[0].headers


def test_error_envelope_raises_api_error_with_code_and_message() -> None:
    body = {"error": {"code": "OUT_OF_TURN", "message": "west is not the player to move"}}
    transport = _FakeTransport(_FakeResponse(409, body))
    client = ApiClient(transport)

    with pytest.raises(ApiError) as exc_info:
        client.request("POST", "/games/ABC/play", json={})

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "OUT_OF_TURN"
    assert exc_info.value.message == "west is not the player to move"


def test_malformed_envelope_raises_api_error_with_unknown_code() -> None:
    body = {"detail": [{"loc": ["body", "tile"], "msg": "field required"}]}
    transport = _FakeTransport(_FakeResponse(422, body))
    client = ApiClient(transport)

    with pytest.raises(ApiError) as exc_info:
        client.request("POST", "/games/ABC/play", json={})

    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "UNKNOWN"


def test_error_with_no_body_raises_api_error_with_unknown_code() -> None:
    transport = _FakeTransport(_FakeResponse(500, None))
    client = ApiClient(transport)

    with pytest.raises(ApiError) as exc_info:
        client.request("GET", "/players/me")

    assert exc_info.value.code == "UNKNOWN"


def test_http_transport_has_an_explicit_default_timeout() -> None:
    """ROADMAP.md 5.0: relying on the underlying HTTP client's own default risked a cold Lambda
    start plus a ~16 MiB scrypt hash brushing a short timeout during the dogfood game."""
    transport = HttpTransport("https://example.invalid")

    assert transport._client.timeout.connect == 30.0


def test_http_transport_timeout_is_configurable() -> None:
    transport = HttpTransport("https://example.invalid", timeout=5.0)

    assert transport._client.timeout.connect == 5.0
