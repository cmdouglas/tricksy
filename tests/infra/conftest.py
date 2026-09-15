"""Shared fixtures for ``tests/infra/`` (ROADMAP.md 5.4, 5.5).

Constructing ``TricksyStack`` now reads ``TRICKSY_SES_FROM_ADDRESS``/
``TRICKSY_SES_DOGFOOD_RECIPIENTS``/``TRICKSY_OPERATOR_EMAIL`` from the environment to build its
SES identities and alerts subscription, the same way ``tests/conftest.py``'s ``table`` fixture
keeps the storage tests independent of a real AWS account: fake-but-valid-looking addresses here,
never anything real committed to this (public) repo.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True, scope="module")
def ses_addresses() -> Iterator[None]:
    os.environ["TRICKSY_SES_FROM_ADDRESS"] = "noreply@example.com"
    os.environ["TRICKSY_SES_DOGFOOD_RECIPIENTS"] = "player1@example.com,player2@example.com"
    os.environ["TRICKSY_OPERATOR_EMAIL"] = "operator@example.com"
    try:
        yield
    finally:
        del os.environ["TRICKSY_SES_FROM_ADDRESS"]
        del os.environ["TRICKSY_SES_DOGFOOD_RECIPIENTS"]
        del os.environ["TRICKSY_OPERATOR_EMAIL"]
