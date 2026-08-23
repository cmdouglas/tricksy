"""HTTP client for the API (ROADMAP.md 3.2).

``ApiClient`` is reached through a narrow ``Transport`` protocol rather than a concrete HTTP
library. ``fastapi.testclient.TestClient`` is built on httpx 0.28, but the CLI's own runtime
dependency is ``httpx2`` (declared in the ``cli`` optional extra) - without the protocol, 3.7's
tests would have no way to drive the CLI against the app in-process and would have to start a real
server just to exercise a ``--help`` path. Both ``TestClient`` and ``httpx2.Client`` already expose
a compatible ``request(method, url, *, json=, headers=)`` -> object-with-``status_code``/``json()``
shape, so the protocol below costs nothing beyond declaring it.

The ``{"error": {"code", "message"}}`` envelope (``tricksy/api/errors.py``) decodes into a typed
``ApiError`` carrying the ``code`` - that symbol is the contract this client is built against, so
nothing here branches on a status code or on message prose. A response that does not carry that
envelope (notably FastAPI's own unwrapped 422 validation errors) decodes to ``code="UNKNOWN"``
rather than raising a decode error, since DESIGN.md §7.2 already specifies that an unrecognised
code is a clean exit 1, not a crash.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    import httpx2


class TransportResponse(Protocol):
    status_code: int

    def json(self) -> Any: ...


class Transport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        json: Any = None,
        headers: Mapping[str, str] | None = None,
    ) -> TransportResponse: ...


class ApiError(Exception):
    """A decoded ``{"error": {"code", "message"}}`` response, or its ``code="UNKNOWN"`` fallback
    when a 4xx/5xx response does not carry that envelope."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


class ApiClient:
    """Base URL and auth live on the transport (a ``httpx2.Client(base_url=...)`` or a fixed-base
    ``TestClient``) - ``ApiClient`` only ever passes relative paths, which is what keeps it
    transport-agnostic."""

    def __init__(self, transport: Transport, *, token: str | None = None) -> None:
        self._transport = transport
        self._token = token

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        idempotent: bool = False,
    ) -> Any:
        headers: dict[str, str] = {}
        if self._token is not None:
            headers["Authorization"] = f"Bearer {self._token}"
        if idempotent:
            headers["Idempotency-Key"] = str(uuid.uuid4())

        response = self._transport.request(method, path, json=json, headers=headers)

        if response.status_code >= 400:
            raise _decode_error(response)

        if response.status_code == 204:
            return None
        return response.json()


def _decode_error(response: TransportResponse) -> ApiError:
    try:
        body = response.json()
        error = body["error"]
        code = error["code"]
        message = error["message"]
    except (ValueError, KeyError, TypeError):
        code = "UNKNOWN"
        message = f"unexpected {response.status_code} response"
    return ApiError(response.status_code, code, message)


class HttpTransport:
    """The real ``Transport``, used by every command outside of tests (DESIGN.md §7.3)."""

    def __init__(self, base_url: str) -> None:
        import httpx2

        self._client = httpx2.Client(base_url=base_url)

    def request(
        self,
        method: str,
        url: str,
        *,
        json: Any = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx2.Response:
        return self._client.request(method, url, json=json, headers=headers)
