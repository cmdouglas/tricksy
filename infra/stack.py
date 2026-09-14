"""The one stack (ROADMAP.md 5.1, DESIGN.md §14).

``self.table`` is exposed so later sub-phases build onto this same stack instead of a second
one: 5.2 adds a TTL attribute (``ttl``, matching ``schema.create_table``'s
``update_time_to_live`` call), 5.3 adds the API Lambda function below and calls
``self.table.grant_read_write_data``; 5.4 does the same for a notifier function.

This mirrors ``tricksy.storage.schema.create_table`` by hand rather than importing it, because
that function is written as literal boto3 kwargs to satisfy boto3-stubs' overloads and cannot be
splatted into a CDK construct. ``tests/infra/test_table_parity.py`` is what keeps the two honest.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aws_cdk import BundlingOptions, CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk.aws_apigatewayv2 import HttpApi
from aws_cdk.aws_apigatewayv2_integrations import HttpLambdaIntegration
from aws_cdk.aws_dynamodb import (
    Attribute,
    AttributeType,
    BillingMode,
    PointInTimeRecoverySpecification,
    ProjectionType,
    StreamViewType,
    Table,
)
from aws_cdk.aws_lambda import Architecture, Code, Function, Runtime
from constructs import Construct

#: Repo root, so the Docker build context (and thus ``pyproject.toml``/``uv.lock``/``src/``) is
#: available for bundling even though ``cdk synth`` itself runs from ``infra/``.
_REPO_ROOT = Path(__file__).resolve().parent.parent

#: Excluded from the Docker build context - not needed inside the bundling container and, for
#: ``.venv``/``.git``, large enough to make every bundle slow to assemble.
_BUNDLE_EXCLUDES = [
    ".venv",
    ".git",
    "infra/cdk.out",
    "__pycache__",
    "*.pyc",
    ".pytest_cache",
    "tests",
]

#: Builds the Lambda deployment package in Docker rather than locally (ROADMAP.md 5.3): fastapi
#: pulls in pydantic-core, a compiled wheel, so a bundle assembled on a developer's macOS machine
#: won't import at all on Lambda's Linux runtime. ``uv export`` without ``--extra``/``--all-extras``
#: already omits the ``cli``/``infra`` extras, so nothing further is needed to keep them out.
_BUNDLING = BundlingOptions(
    image=Runtime.PYTHON_3_13.bundling_image,
    # The container runs as the host's numeric UID (no matching /etc/passwd entry), so HOME
    # resolves to "/" and uv's default cache dir under it isn't writable.
    environment={"UV_CACHE_DIR": "/tmp/uv-cache"},
    command=[
        "bash",
        "-c",
        "pip install --no-cache-dir uv "
        "&& uv export --frozen --no-dev --no-emit-project -o /tmp/requirements.txt "
        "&& uv pip install --target /asset-output -r /tmp/requirements.txt "
        "&& cp -r src/tricksy /asset-output/tricksy",
    ],
)


class TricksyStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs: Any) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.table = Table(
            self,
            "Table",
            table_name="Tricksy",
            partition_key=Attribute(name="PK", type=AttributeType.STRING),
            sort_key=Attribute(name="SK", type=AttributeType.STRING),
            billing_mode=BillingMode.PAY_PER_REQUEST,
            stream=StreamViewType.NEW_AND_OLD_IMAGES,
            time_to_live_attribute="ttl",
            point_in_time_recovery_specification=PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            deletion_protection=True,
            removal_policy=RemovalPolicy.RETAIN,
        )
        self.table.add_global_secondary_index(
            index_name="OpenGames",
            partition_key=Attribute(name="GSI1PK", type=AttributeType.STRING),
            sort_key=Attribute(name="GSI1SK", type=AttributeType.STRING),
            projection_type=ProjectionType.ALL,
        )

        # Shared by 5.4's notifier function too - both run from the same deployment package.
        lambda_code = Code.from_asset(str(_REPO_ROOT), exclude=_BUNDLE_EXCLUDES, bundling=_BUNDLING)

        self.api_function = Function(
            self,
            "ApiFunction",
            runtime=Runtime.PYTHON_3_13,
            architecture=Architecture.ARM_64,
            handler="tricksy.api.lambda_handler.handler",
            code=lambda_code,
            environment={
                # TRICKSY_DYNAMODB_ENDPOINT is deliberately absent: unset means "real AWS" to
                # tricksy.api.deps.
                "TRICKSY_TABLE_NAME": self.table.table_name,
            },
            # Under API Gateway HTTP API's 30s hard integration ceiling.
            timeout=Duration.seconds(29),
        )
        self.table.grant_read_write_data(self.api_function)

        self.http_api = HttpApi(
            self,
            "HttpApi",
            default_integration=HttpLambdaIntegration("ApiIntegration", self.api_function),
        )

        CfnOutput(self, "ApiUrl", value=self.http_api.api_endpoint)
