"""Request-scoped dependencies: the table handle and the authenticated caller (ROADMAP.md 2.4).

Both are FastAPI dependencies rather than module-level lookups, for the same reason the storage
layer takes a ``Table`` argument instead of owning a connection: it makes them overridable. The
contract tests swap :func:`get_table` for a moto-backed table through
``app.dependency_overrides``, and nothing in the handlers has to know.

The boto3 resource itself is built once per process and cached. On Lambda that means one
connection pool per warm container rather than one per request, which is the whole reason the
handler module is imported at cold start rather than inside the function.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Annotated

import boto3
from fastapi import Depends, Header
from mypy_boto3_dynamodb.service_resource import Table

from t42.engine.state import PlayerId
from t42.notifications import EmailSender, get_sender
from t42.storage.accounts import player_for_token

from .errors import not_authenticated

#: Overridable so a local run can point at DynamoDB Local without editing code.
TABLE_NAME_ENV = "T42_TABLE_NAME"
ENDPOINT_URL_ENV = "T42_DYNAMODB_ENDPOINT"


@lru_cache(maxsize=1)
def _resource() -> object:
    endpoint_url = os.environ.get(ENDPOINT_URL_ENV) or None
    return boto3.resource("dynamodb", endpoint_url=endpoint_url)


def get_table() -> Table:
    """The DynamoDB table every handler writes through.

    Overridden wholesale in tests. In production it reads ``T42_TABLE_NAME``; there is no default,
    because silently defaulting to a table name is how a test run ends up writing to production.
    """
    name = os.environ.get(TABLE_NAME_ENV)
    if not name:
        raise RuntimeError(f"{TABLE_NAME_ENV} is not set")
    table: Table = _resource().Table(name)  # type: ignore[attr-defined]
    return table


TableDep = Annotated[Table, Depends(get_table)]

#: Overridden wholesale in tests, the same way ``get_table`` is - see ``t42.notifications.sender``
#: for the environment variable that picks the production implementation.
EmailSenderDep = Annotated[EmailSender, Depends(get_sender)]


def get_bearer_token(authorization: Annotated[str | None, Header()] = None) -> str:
    """The raw token from ``Authorization: Bearer <token>``.

    Separate from :func:`get_current_player` because signing out needs the token itself, to
    revoke exactly the one this request arrived with rather than every device a player has.
    """
    if not authorization:
        raise not_authenticated()
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise not_authenticated("expected an 'Authorization: Bearer <token>' header")
    return token.strip()


BearerToken = Annotated[str, Depends(get_bearer_token)]


def get_current_player(table: TableDep, token: BearerToken) -> PlayerId:
    """Resolves the bearer token to a player id (DESIGN.md §6.1).

    A missing or malformed header is a 401 raised by :func:`get_bearer_token`; a well-formed
    token that does not resolve is a 401 raised by ``player_for_token``. Neither response says
    whether the token was ever valid.
    """
    return player_for_token(table, token)


CurrentPlayer = Annotated[PlayerId, Depends(get_current_player)]


def get_idempotency_key(
    idempotency_key: Annotated[str | None, Header()] = None,
) -> str | None:
    """The client-generated request id from the ``Idempotency-Key`` header (DESIGN.md §6).

    Optional: without it a resubmitted move is simply applied twice if it is still legal, which is
    the client's problem to avoid. With it, ``repository.append`` makes the retry a no-op that
    returns the original result.
    """
    return idempotency_key


IdempotencyKey = Annotated[str | None, Depends(get_idempotency_key)]
