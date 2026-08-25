"""Parity between ``schema.create_table`` and the CDK-synthesized table (ROADMAP.md 5.1).

Only fields whose shape is identical in a CloudFormation template and in ``describe_table``'s
response are compared directly. Billing mode, RETAIN, point-in-time recovery and deletion
protection have no shape in common with (or no counterpart in) the fixture side and get
standalone, template-only assertions instead - see the module docstring in ``infra/stack.py``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Template
from mypy_boto3_dynamodb.service_resource import Table

from stack import TricksyStack


@pytest.fixture
def table_resource() -> Mapping[str, Any]:
    app = cdk.App()
    stack = TricksyStack(app, "TricksyStack")
    resources = Template.from_stack(stack).find_resources("AWS::DynamoDB::Table")
    assert len(resources) == 1
    return next(iter(resources.values()))


def _by_attribute_name(items: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return sorted(items, key=lambda item: item["AttributeName"])


def test_key_schema_matches_schema_py(table_resource: Mapping[str, Any], table: Table) -> None:
    described = table.meta.client.describe_table(TableName=table.table_name)["Table"]
    props = table_resource["Properties"]
    assert _by_attribute_name(props["KeySchema"]) == _by_attribute_name(described["KeySchema"])


def test_attribute_definitions_match_schema_py(
    table_resource: Mapping[str, Any], table: Table
) -> None:
    described = table.meta.client.describe_table(TableName=table.table_name)["Table"]
    props = table_resource["Properties"]
    assert _by_attribute_name(props["AttributeDefinitions"]) == _by_attribute_name(
        described["AttributeDefinitions"]
    )


def test_global_secondary_indexes_match_schema_py(
    table_resource: Mapping[str, Any], table: Table
) -> None:
    described = table.meta.client.describe_table(TableName=table.table_name)["Table"]
    props = table_resource["Properties"]

    def _normalize(gsis: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            (
                {
                    "IndexName": gsi["IndexName"],
                    "KeySchema": _by_attribute_name(gsi["KeySchema"]),
                    "Projection": gsi["Projection"],
                }
                for gsi in gsis
            ),
            key=lambda gsi: gsi["IndexName"],
        )

    assert _normalize(props["GlobalSecondaryIndexes"]) == _normalize(
        described["GlobalSecondaryIndexes"]
    )


def test_stream_view_type_matches_schema_py(
    table_resource: Mapping[str, Any], table: Table
) -> None:
    described = table.meta.client.describe_table(TableName=table.table_name)["Table"]
    props = table_resource["Properties"]
    assert (
        props["StreamSpecification"]["StreamViewType"]
        == described["StreamSpecification"]["StreamViewType"]
    )


def test_billing_mode_is_pay_per_request(table_resource: Mapping[str, Any]) -> None:
    assert table_resource["Properties"]["BillingMode"] == "PAY_PER_REQUEST"


def test_table_is_protected(table_resource: Mapping[str, Any]) -> None:
    props = table_resource["Properties"]
    assert table_resource["DeletionPolicy"] == "Retain"
    assert table_resource["UpdateReplacePolicy"] == "Retain"
    assert props["DeletionProtectionEnabled"] is True
    assert props["PointInTimeRecoverySpecification"]["PointInTimeRecoveryEnabled"] is True
