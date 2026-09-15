"""The one stack (ROADMAP.md 5.1, DESIGN.md §14).

``self.table`` is exposed so later sub-phases build onto this same stack instead of a second
one: 5.2 adds a TTL attribute (``ttl``, matching ``schema.create_table``'s
``update_time_to_live`` call), 5.3 adds the API Lambda function below and calls
``self.table.grant_read_write_data``; 5.4 does the same for the notifier function below.

This mirrors ``tricksy.storage.schema.create_table`` by hand rather than importing it, because
that function is written as literal boto3 kwargs to satisfy boto3-stubs' overloads and cannot be
splatted into a CDK construct. ``tests/infra/test_table_parity.py`` is what keeps the two honest.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from aws_cdk import BundlingOptions, CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk.aws_apigatewayv2 import HttpApi
from aws_cdk.aws_apigatewayv2_integrations import HttpLambdaIntegration
from aws_cdk.aws_budgets import CfnBudget
from aws_cdk.aws_cloudwatch import ComparisonOperator
from aws_cdk.aws_cloudwatch_actions import SnsAction
from aws_cdk.aws_dynamodb import (
    Attribute,
    AttributeType,
    BillingMode,
    PointInTimeRecoverySpecification,
    ProjectionType,
    StreamViewType,
    Table,
)
from aws_cdk.aws_iam import PolicyStatement
from aws_cdk.aws_lambda import Architecture, Code, Function, Runtime, StartingPosition
from aws_cdk.aws_lambda_event_sources import DynamoEventSource, SqsDlq
from aws_cdk.aws_logs import LogGroup, RetentionDays
from aws_cdk.aws_ses import EmailIdentity, Identity
from aws_cdk.aws_sns import Topic
from aws_cdk.aws_sns_subscriptions import EmailSubscription
from aws_cdk.aws_sqs import Queue
from constructs import Construct

#: Env var name shared with ``tricksy.notifications.sender.SES_FROM_ADDRESS_ENV`` - read here at
#: synth time (to build the SES identity) and passed through verbatim as the notifier function's
#: own runtime environment variable, so there is one source of truth rather than two.
_SES_FROM_ADDRESS_ENV = "TRICKSY_SES_FROM_ADDRESS"

#: Synth-time only - comma-separated dogfood recipient addresses to pre-register as SES
#: identities so the sandbox will accept sending to them. No runtime code reads this: each
#: message's actual recipient is resolved dynamically from the player's own verified contact
#: (DESIGN.md §8), so an empty/unset value is valid - it just means no recipient identities exist
#: yet.
_SES_DOGFOOD_RECIPIENTS_ENV = "TRICKSY_SES_DOGFOOD_RECIPIENTS"

#: Synth-time only - who the alarm/budget SNS topic notifies (ROADMAP.md 5.5). Distinct from
#: TRICKSY_SES_FROM_ADDRESS on purpose: that's a noreply-style sending identity, not an inbox
#: anyone reads.
_OPERATOR_EMAIL_ENV = "TRICKSY_OPERATOR_EMAIL"

#: The cheapest possible guard against a runaway (ROADMAP.md 5.5) - sized for a PAY_PER_REQUEST
#: table and a handful of Lambda invocations during dogfooding. Easy to raise in source if real
#: usage says otherwise.
_MONTHLY_BUDGET_USD = 20


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not set")
    return value


def _dogfood_recipients() -> list[str]:
    raw = os.environ.get(_SES_DOGFOOD_RECIPIENTS_ENV, "")
    return [address.strip() for address in raw.split(",") if address.strip()]


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

        # 30 days rather than the default of forever (ROADMAP.md 5.5) - paying indefinitely to
        # store the logs of a game nobody is playing is the easiest cost mistake available here.
        # DESTROY rather than RETAIN, unlike the table: logs aren't data worth keeping once the
        # stack itself is torn down.
        api_log_group = LogGroup(
            self,
            "ApiFunctionLogGroup",
            retention=RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY,
        )

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
            log_group=api_log_group,
        )
        self.table.grant_read_write_data(self.api_function)

        self.http_api = HttpApi(
            self,
            "HttpApi",
            default_integration=HttpLambdaIntegration("ApiIntegration", self.api_function),
        )

        CfnOutput(self, "ApiUrl", value=self.http_api.api_endpoint)

        # A poison record's failure destination (ROADMAP.md 5.4). 14 days, SQS's max, rather than
        # the 4-day default - the point is giving the operator (the DLQ-depth alarm below) time
        # to investigate before it's lost rather than losing it quickly.
        self.notifier_dlq = Queue(self, "NotifierDlq", retention_period=Duration.days(14))

        from_address = _require_env(_SES_FROM_ADDRESS_ENV)

        notifier_log_group = LogGroup(
            self,
            "NotifierFunctionLogGroup",
            retention=RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY,
        )

        self.notifier_function = Function(
            self,
            "NotifierFunction",
            runtime=Runtime.PYTHON_3_13,
            architecture=Architecture.ARM_64,
            handler="tricksy.notifications.handler.lambda_handler",
            code=lambda_code,
            environment={
                "TRICKSY_TABLE_NAME": self.table.table_name,
                "TRICKSY_EMAIL_SENDER": "ses",
                _SES_FROM_ADDRESS_ENV: from_address,
            },
            # No API Gateway ceiling here; a batch means several SES sends per invocation.
            timeout=Duration.seconds(60),
            log_group=notifier_log_group,
        )
        self.table.grant_read_write_data(self.notifier_function)

        self.notifier_function.add_event_source(
            DynamoEventSource(
                self.table,
                starting_position=StartingPosition.TRIM_HORIZON,
                bisect_batch_on_error=True,
                # Finite, so a record that always throws reaches the DLQ within a bounded number
                # of retries instead of blocking the shard until DynamoDB Streams' 24h max record
                # age quietly ages it out. No report_batch_item_failures: 4.5's conditional
                # notified_version/notified advance already makes a redelivered record a no-op,
                # so retrying the whole batch is correct and merely wasteful, not incorrect.
                retry_attempts=3,
                on_failure=SqsDlq(self.notifier_dlq),
            )
        )

        from_address_identity = EmailIdentity(
            self, "FromAddressIdentity", identity=Identity.email(from_address)
        )
        for index, recipient in enumerate(_dogfood_recipients()):
            EmailIdentity(self, f"DogfoodRecipient{index}", identity=Identity.email(recipient))

        # Each identity still needs a human to click SES's confirmation mail before it can send
        # or receive (ROADMAP.md 5.4/5.6) - that step is inherently manual, not automated here.
        self.notifier_function.add_to_role_policy(
            PolicyStatement(
                actions=["ses:SendEmail"],
                resources=[from_address_identity.email_identity_arn],
            )
        )

        # Small on purpose (ROADMAP.md 5.5): enough to know something broke, and no more. The
        # operator's own address, distinct from the SES from-address above - same confirmation-
        # click caveat, again left for 5.6 to document rather than automate.
        operator_email = _require_env(_OPERATOR_EMAIL_ENV)

        self.alerts_topic = Topic(self, "AlertsTopic")
        self.alerts_topic.add_subscription(EmailSubscription(operator_email))

        for alarm_id, metric in (
            ("ApiFunctionErrorsAlarm", self.api_function.metric_errors()),
            ("NotifierFunctionErrorsAlarm", self.notifier_function.metric_errors()),
            (
                "NotifierDlqDepthAlarm",
                self.notifier_dlq.metric_approximate_number_of_messages_visible(),
            ),
        ):
            alarm = metric.create_alarm(
                self,
                alarm_id,
                evaluation_periods=1,
                threshold=1,
                comparison_operator=ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            )
            alarm.add_alarm_action(SnsAction(self.alerts_topic))

        CfnBudget(
            self,
            "MonthlyBudget",
            budget=CfnBudget.BudgetDataProperty(
                budget_type="COST",
                time_unit="MONTHLY",
                budget_limit=CfnBudget.SpendProperty(amount=_MONTHLY_BUDGET_USD, unit="USD"),
            ),
            notifications_with_subscribers=[
                CfnBudget.NotificationWithSubscribersProperty(
                    notification=CfnBudget.NotificationProperty(
                        notification_type="ACTUAL",
                        comparison_operator="GREATER_THAN",
                        threshold=100,
                        threshold_type="PERCENTAGE",
                    ),
                    subscribers=[
                        CfnBudget.SubscriberProperty(
                            subscription_type="EMAIL", address=operator_email
                        )
                    ],
                )
            ],
        )
