"""The API Lambda function and the HTTP API in front of it (ROADMAP.md 5.3).

Constructing ``TricksyStack`` bundles the function's deployment package in Docker, so every test
here is marked ``integration`` - the same reason ``test_table_parity.py`` is.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

from stack import TricksyStack

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def template() -> Template:
    app = cdk.App()
    stack = TricksyStack(app, "TricksyStack")
    return Template.from_stack(stack)


def _only_resource(template: Template, resource_type: str) -> Mapping[str, Any]:
    resources = template.find_resources(resource_type)
    assert len(resources) == 1
    return next(iter(resources.values()))


def test_function_runtime_and_architecture(template: Template) -> None:
    props = _only_resource(template, "AWS::Lambda::Function")["Properties"]
    assert props["Runtime"] == "python3.13"
    assert props["Architectures"] == ["arm64"]
    assert props["Handler"] == "tricksy.api.lambda_handler.handler"


def test_function_environment(template: Template) -> None:
    props = _only_resource(template, "AWS::Lambda::Function")["Properties"]
    env = props["Environment"]["Variables"]
    assert "TRICKSY_TABLE_NAME" in env
    assert "TRICKSY_DYNAMODB_ENDPOINT" not in env


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


def test_http_api_exists(template: Template) -> None:
    props = _only_resource(template, "AWS::ApiGatewayV2::Api")["Properties"]
    assert props["ProtocolType"] == "HTTP"


def test_http_api_has_default_route(template: Template) -> None:
    routes = template.find_resources("AWS::ApiGatewayV2::Route")
    assert len(routes) == 1
    route = next(iter(routes.values()))
    assert route["Properties"]["RouteKey"] == "$default"


def test_no_api_keys(template: Template) -> None:
    assert template.find_resources("AWS::ApiGateway::ApiKey") == {}
    assert template.find_resources("AWS::ApiGateway::UsagePlan") == {}


def test_outputs_the_api_url(template: Template) -> None:
    http_api_id = next(iter(template.find_resources("AWS::ApiGatewayV2::Api")))
    template.has_output("*", {"Value": {"Fn::GetAtt": [http_api_id, "ApiEndpoint"]}})
