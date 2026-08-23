"""Thin CLI client (DESIGN.md §7, ROADMAP.md Phase 3).

``tricksy.cli`` imports nothing from ``tricksy.games``, ``tricksy.storage`` or ``boto3``. It is a
client of the HTTP API and nothing else - it renders the projected view a server sends back and
posts moves, deriving nothing the server hasn't already said (DESIGN.md §7's own framing). This
layering is enforced by test starting in ROADMAP.md 3.7.
"""

from __future__ import annotations
