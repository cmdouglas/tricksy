"""AWS Lambda entry point (ROADMAP.md 2.6).

Mangum adapts API Gateway's proxy event to the ASGI interface FastAPI speaks, so this module is
the whole of the Lambda-specific code and nothing above it knows it is running in Lambda. That is
the point: the same ``app`` serves ``uvicorn`` locally and ``TestClient`` in the contract tests.

Deploying this - the function, the HTTP API in front of it, the table and the IAM to connect them
- is not part of Phase 2; it is Phase 5's work (DESIGN.md §14, ROADMAP.md 5.3).
"""

from __future__ import annotations

from mangum import Mangum

from .app import app

handler = Mangum(app)
