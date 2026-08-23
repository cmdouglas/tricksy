"""Tricksy: trick-taking games online, server-authoritative and asynchronous.

Layers are kept separate on purpose (see DESIGN.md §3):

- ``tricksy.games``         - the rules engines. One game ships, ``tricksy.games.texas42``:
                              a pure rules library, no I/O and no AWS dependencies
- ``tricksy.storage``       - DynamoDB event log + materialized state (Phase 1)
- ``tricksy.api``           - Lambda handlers behind API Gateway (Phase 2)
- ``tricksy.cli``           - thin command-line client (Phase 3)
- ``tricksy.notifications`` - the email send channel, driven off the table's stream (Phase 4)
"""

__version__ = "0.1.0"
