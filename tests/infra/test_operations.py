"""Log retention, alarms, the alerts topic and the monthly budget (ROADMAP.md 5.5).

Constructing ``TricksyStack`` bundles both functions' deployment package in Docker, so every test
here is marked ``integration`` - the same reason the other files under ``tests/infra/`` are.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Template

from stack import TricksyStack

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def template() -> Template:
    app = cdk.App()
    stack = TricksyStack(app, "TricksyStack")
    return Template.from_stack(stack)


def _only_resource(template: Template, resource_type: str, props: Any = None) -> Mapping[str, Any]:
    resources = template.find_resources(resource_type, props)
    assert len(resources) == 1
    return next(iter(resources.values()))


def test_log_retention(template: Template) -> None:
    log_groups = template.find_resources("AWS::Logs::LogGroup")
    assert len(log_groups) == 2
    for resource in log_groups.values():
        assert resource["Properties"]["RetentionInDays"] == 30
        assert resource["DeletionPolicy"] == "Delete"


def test_alerts_topic_and_subscription(template: Template) -> None:
    _only_resource(template, "AWS::SNS::Topic")
    props = _only_resource(template, "AWS::SNS::Subscription")["Properties"]
    assert props["Protocol"] == "email"
    assert props["Endpoint"] == "operator@example.com"


def test_three_alarms_on_the_right_metrics(template: Template) -> None:
    alarms = template.find_resources("AWS::CloudWatch::Alarm")
    assert len(alarms) == 3

    def _metrics(namespace: str, metric_name: str) -> list[Mapping[str, Any]]:
        return [
            resource["Properties"]
            for resource in alarms.values()
            if resource["Properties"]["Namespace"] == namespace
            and resource["Properties"]["MetricName"] == metric_name
        ]

    assert len(_metrics("AWS/Lambda", "Errors")) == 2
    assert len(_metrics("AWS/SQS", "ApproximateNumberOfMessagesVisible")) == 1

    for resource in alarms.values():
        props = resource["Properties"]
        assert props["Threshold"] == 1
        assert props["EvaluationPeriods"] == 1
        assert props["ComparisonOperator"] == "GreaterThanOrEqualToThreshold"
        assert props["AlarmActions"] != []


def test_monthly_budget(template: Template) -> None:
    props = _only_resource(template, "AWS::Budgets::Budget")["Properties"]
    budget = props["Budget"]
    assert budget["BudgetType"] == "COST"
    assert budget["TimeUnit"] == "MONTHLY"
    assert budget["BudgetLimit"] == {"Amount": 20, "Unit": "USD"}

    notification = props["NotificationsWithSubscribers"][0]
    assert notification["Notification"]["ComparisonOperator"] == "GREATER_THAN"
    assert notification["Notification"]["NotificationType"] == "ACTUAL"
    assert {"SubscriptionType": "EMAIL", "Address": "operator@example.com"} in notification[
        "Subscribers"
    ]
