"""The one stack (ROADMAP.md 5.1, DESIGN.md §14).

``self.table`` is exposed so later sub-phases build onto this same stack instead of a second
one: 5.2 adds a TTL attribute, 5.3/5.4 add Lambda functions and call
``self.table.grant_read_write_data``.

This mirrors ``tricksy.storage.schema.create_table`` by hand rather than importing it, because
that function is written as literal boto3 kwargs to satisfy boto3-stubs' overloads and cannot be
splatted into a CDK construct. ``tests/infra/test_table_parity.py`` is what keeps the two honest.
"""

from __future__ import annotations

from typing import Any

from aws_cdk import RemovalPolicy, Stack
from aws_cdk.aws_dynamodb import (
    Attribute,
    AttributeType,
    BillingMode,
    PointInTimeRecoverySpecification,
    ProjectionType,
    StreamViewType,
    Table,
)
from constructs import Construct


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
