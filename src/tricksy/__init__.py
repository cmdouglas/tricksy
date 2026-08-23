"""Texas 42 online.

Layers are kept separate on purpose (see DESIGN.md §3):

- ``t42.engine``  - pure rules library, no I/O and no AWS dependencies
- ``t42.storage`` - DynamoDB event log + materialized state (Phase 1)
- ``t42.api``     - Lambda handlers behind API Gateway (Phase 2)
- ``t42.cli``     - thin command-line client (Phase 3)
"""

__version__ = "0.1.0"
