from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from unittest.mock import patch

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from src.bridge.handler import handler
from src.tenant_api.constants import DEFAULT_RUNTIME_REGION_PARAM

_REGION = "eu-west-2"


class FakeLambdaContext:
    def __init__(self):
        self.function_name = "bridge"
        self.memory_limit_in_mb = 256
        self.invoked_function_arn = f"arn:aws:lambda:{_REGION}:111111111111:function:bridge"
        self.aws_request_id = "req-123"


@pytest.fixture
def fixed_now_value() -> datetime:
    return datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def setup_data(fixed_now_value):
    _ = fixed_now_value
    with mock_aws():
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"  # pragma: allowlist secret
        os.environ["AWS_SECURITY_TOKEN"] = "testing"
        os.environ["AWS_SESSION_TOKEN"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = _REGION
        os.environ["AWS_REGION"] = _REGION
        os.environ["PLATFORM_ENV"] = "local"
        os.environ["POWERTOOLS_SERVICE_NAME"] = "bridge"

        ddb = boto3.resource("dynamodb", region_name=_REGION)

        ddb.create_table(
            TableName="platform-ops-locks",
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        ddb.create_table(
            TableName="platform-agents",
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        ssm = boto3.client("ssm", region_name=_REGION)
        ssm.put_parameter(
            Name=DEFAULT_RUNTIME_REGION_PARAM,
            Value=_REGION,
            Type="String",
            Overwrite=True,
        )

        agents_table = ddb.Table("platform-agents")
        agents_table.put_item(
            Item={
                "PK": "AGENT#echo-agent",
                "SK": "VERSION#1.0.0",
                "agent_name": "echo-agent",
                "version": "1.0.0",
                "invocation_mode": "sync",
                "status": "promoted",
                "deployed_at": "2026-01-01T00:00:00Z",
                "owner_team": "platform",
                "tier_minimum": "basic",
                "layer_hash": "some-hash",
                "layer_s3_key": "some-key",
            }
        )

        yield ddb


def test_handler_returns_runtime_failure_when_failover_is_disabled(setup_data):
    _ = setup_data
    event = {
        "path": "/v1/agents/echo-agent/invoke",
        "pathParameters": {"agentName": "echo-agent"},
        "requestContext": {
            "authorizer": {
                "tenantid": "t-001",
                "appid": "app-001",
                "tier": "basic",
                "sub": "user-1",
            }
        },
        "body": json.dumps({"input": "Hello"}),
    }

    with (
        patch("src.bridge.route_adapter.get_http_session"),
        patch("src.bridge.route_adapter.runtime_calls.invoke_real_runtime") as mock_invoke_real,
    ):
        mock_invoke_real.side_effect = [
            ClientError(
                {
                    "Error": {
                        "Code": "ServiceUnavailableException",
                        "Message": "Service unavailable",
                    }
                },
                "InvokeAgent",
            ),
        ]

        response = handler(event, FakeLambdaContext())

        assert response["statusCode"] == 502
        body = json.loads(response["body"])
        assert body["error"]["code"] == "ServiceUnavailableException"
        assert mock_invoke_real.call_count == 1

        ssm = boto3.client("ssm", region_name=_REGION)
        param = ssm.get_parameter(Name=DEFAULT_RUNTIME_REGION_PARAM)
        assert param["Parameter"]["Value"] == _REGION
