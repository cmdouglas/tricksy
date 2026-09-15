"""The notifier Lambda, its event source, DLQ and SES identities (ROADMAP.md 5.4).

Constructing ``TricksyStack`` bundles the function's deployment package in Docker, so every test
here is marked ``integration`` - the same reason ``test_table_parity.py``/``test_api_function.py``
are. ``tests/infra/conftest.py``'s ``ses_addresses`` fixture supplies the fake
``TRICKSY_SES_FROM_ADDRESS``/``TRICKSY_SES_DOGFOOD_RECIPIENTS`` values the stack now needs to
synthesize at all.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

from stack import TricksyStack

pytestmark = pytest.mark.integration

#: The other Lambda function is the API function (ROADMAP.md 5.3), so these tests select the
#: notifier by its handler rather than assuming it's the only one.
_NOTIFIER_FUNCTION_PROPS = {
    "Properties": {"Handler": "tricksy.notifications.handler.lambda_handler"}
}


@pytest.fixture(scope="module")
def template() -> Template:
    app = cdk.App()
    stack = TricksyStack(app, "TricksyStack")
    return Template.from_stack(stack)


def _only_resource(template: Template, resource_type: str, props: Any = None) -> Mapping[str, Any]:
    resources = template.find_resources(resource_type, props)
    assert len(resources) == 1
    return next(iter(resources.values()))


def test_function_runtime_and_environment(template: Template) -> None:
    props = _only_resource(template, "AWS::Lambda::Function", _NOTIFIER_FUNCTION_PROPS)[
        "Properties"
    ]
    assert props["Runtime"] == "python3.13"
    assert props["Architectures"] == ["arm64"]
    env = props["Environment"]["Variables"]
    assert "TRICKSY_TABLE_NAME" in env
    assert env["TRICKSY_EMAIL_SENDER"] == "ses"
    assert "TRICKSY_SES_FROM_ADDRESS" in env


def test_function_has_dynamodb_read_write_policy(template: Template) -> None:
    template.has_resource_properties(
        "AWS::IAM::Policy",
        {
            "PolicyDocument": {
                "Statement": Match.array_with(
                    [
                        Match.object_like(
                            {
                                "Action": Match.array_with(
                                    ["dynamodb:GetItem", "dynamodb:PutItem"]
                                ),
                                "Effect": "Allow",
                            }
                        ),
                    ]
                ),
            },
        },
    )


def test_function_can_send_email(template: Template) -> None:
    template.has_resource_properties(
        "AWS::IAM::Policy",
        {
            "PolicyDocument": {
                "Statement": Match.array_with(
                    [
                        Match.object_like(
                            {
                                "Action": "ses:SendEmail",
                                "Effect": "Allow",
                            }
                        ),
                    ]
                ),
            },
        },
    )


def test_dlq_exists(template: Template) -> None:
    props = _only_resource(template, "AWS::SQS::Queue")["Properties"]
    assert props["MessageRetentionPeriod"] == 14 * 24 * 60 * 60


def test_event_source_mapping(template: Template) -> None:
    props = _only_resource(template, "AWS::Lambda::EventSourceMapping")["Properties"]
    assert props["BisectBatchOnFunctionError"] is True
    assert props["MaximumRetryAttempts"] == 3
    assert "OnFailure" in props["DestinationConfig"]
    # Absent altogether, not False - CDK only emits this key when report_batch_item_failures is
    # requested, and ROADMAP.md 5.4 deliberately doesn't ask for it.
    assert "FunctionResponseTypes" not in props


def test_ses_identities(template: Template) -> None:
    # The from-address plus the two fake dogfood recipients the conftest fixture sets.
    identities = template.find_resources("AWS::SES::EmailIdentity")
    assert len(identities) == 3
