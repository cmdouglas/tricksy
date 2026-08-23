"""Per-invocation request context (ROADMAP.md 3.4).

Two things every command handler needs, factored out so a handler is little more than "build a
body, call the client, emit the response":

- :func:`build_client` turns ``--profile``/``TRICKSY_PROFILE`` into an authenticated ``ApiClient``.
- :func:`emit` is the ``--json``-vs-text split every handler needs, since ``render.py`` promises
  ``--json`` never reaches it (see that module's docstring) - this is where that promise is kept.

``transport_factory`` is a module attribute rather than baked into :func:`build_client`, for the
same reason 3.2 put ``Transport`` behind a protocol in the first place: 3.7's ``test_commands.py``
needs to point every command at ``TestClient(app)`` in-process, and this phase's own unit tests
need a fake transport too. Monkeypatching one attribute is what both get.
"""

from __future__ import annotations

import argparse
import json as jsonlib
from collections.abc import Callable
from typing import Any

from tricksy.cli import config
from tricksy.cli.api import ApiClient, ApiError, HttpTransport, Transport

transport_factory: Callable[[str], Transport] = HttpTransport


def build_client(
    args: argparse.Namespace, *, require_auth: bool = True
) -> tuple[ApiClient, config.Profile | None]:
    """The profile named by ``--profile``/``TRICKSY_PROFILE``, defaulting to ``"default"`` so a solo
    user never has to pass it - the 4-profile dogfood milestone still works, since seats 2-4 can't
    all be ``"default"`` and so must pass ``--profile`` by construction.

    Raises :class:`ApiError` with code ``NOT_AUTHENTICATED`` when ``require_auth`` and no such
    profile exists, reusing ``ApiClient``'s own exception rather than inventing a parallel one:
    ``main.py``'s ``_run`` already catches ``ApiError`` and maps that code to exit 3 through
    ``errors.exit_code_for``.
    """
    cfg = config.load()
    profile_name = args.profile or "default"
    profile = config.get_profile(cfg, profile_name)
    if require_auth and profile is None:
        raise ApiError(
            0, "NOT_AUTHENTICATED", f"no profile {profile_name!r} - run `tricksy login` first"
        )
    token = profile.token if profile is not None else None
    client = ApiClient(transport_factory(args.api_url), token=token)
    return client, profile


def emit(
    args: argparse.Namespace,
    response: Any,
    render_fn: Callable[[Any], str] | None = None,
    *,
    confirmation: str | None = None,
) -> None:
    """Print ``response`` per ``--json``. JSON mode prints the body verbatim (nothing for a 204,
    since there is no body); text mode calls ``render_fn`` when given, else prints
    ``confirmation`` for a void endpoint (e.g. ``rules delete``), else prints nothing."""
    if args.json:
        if response is not None:
            print(jsonlib.dumps(response))
        return
    if render_fn is not None:
        print(render_fn(response))
    elif confirmation is not None:
        print(confirmation)
