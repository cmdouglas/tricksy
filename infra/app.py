#!/usr/bin/env python3
"""CDK entry point, run only via ``cdk synth``/``cdk deploy`` (ROADMAP.md 5.1, DESIGN.md §14).

Never imported by tests, which construct ``TricksyStack`` directly
(``tests/infra/test_table_parity.py``) - this module calls ``app.synth()`` at import time, which
a test importing it would trigger as a side effect.

No pinned ``env=``: the stack stays environment-agnostic and ``cdk deploy`` resolves the AWS
account/region from the operator's active credentials, matching "one operator, deployed by hand."
"""

from __future__ import annotations

import aws_cdk as cdk

from stack import TricksyStack

app = cdk.App()
TricksyStack(app, "TricksyStack")
app.synth()
