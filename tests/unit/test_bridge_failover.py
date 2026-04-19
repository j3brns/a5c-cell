from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from unittest.mock import patch

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from src.bridge.handler import handler


class FakeLambdaContext:
    def __init__(self):
        self.function_name = "bridge"
        self.memory_limit_in_mb = 256
        self.invoked_function_arn = f"arn:aws:lambda:{_REGION}:111111111111:function:bridge"
        self.aws_request_id = "req-123"


_REGION = "eu-west-1"


@pytest.fixture
def fixed_now_value() -> datetime:
    return datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def setup_data(fixed_now_value):
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

        # Seed locks table
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

        # Seed configuration table
        ddb.create_table(
            TableName="platform-config",
            KeySchema=[{"AttributeName": "key", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "key", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        config_table = ddb.Table("platform-config")
        config_table.put_item(Item={"key": "runtime_region", "value": _REGION})

        # Seed SSM in both regions
        from src.tenant_api.constants import DEFAULT_RUNTIME_REGION_PARAM

        for region in [_REGION, "eu-central-1"]:
            ssm = boto3.client("ssm", region_name=region)
            ssm.put_parameter(
                Name=DEFAULT_RUNTIME_REGION_PARAM,
                Value=_REGION,
                Type="String",
                Overwrite=True,
            )

        # Seed agents table
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

        # Seed agent
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


def test_handler_failover_on_503(setup_data):
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
        patch("src.bridge.handler.get_http_session"),
        patch("src.bridge.handler.invoke_real_runtime") as mock_invoke_real,
    ):
        # First call fails with ClientError(ServiceUnavailableException)
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
            # Second call (after failover) succeeds
            {
                "statusCode": 200,
                "body": json.dumps({"output": "Success after failover"}),
            },
        ]

        response = handler(event, FakeLambdaContext())

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["output"] == "Success after failover"

        # Verify SSM was updated to eu-central-1
        ssm = boto3.client("ssm", region_name=_REGION)
        from src.tenant_api.constants import DEFAULT_RUNTIME_REGION_PARAM

        param = ssm.get_parameter(Name=DEFAULT_RUNTIME_REGION_PARAM)
        assert param["Parameter"]["Value"] == "eu-central-1"


def test_handler_failover_already_in_progress(setup_data):
    # Seed the lock to simulate another instance failing over
    ddb = boto3.resource("dynamodb", region_name=_REGION)
    lock_table = ddb.Table("platform-ops-locks")
    lock_table.put_item(
        Item={
            "PK": "LOCK#platform-runtime-failover",
            "SK": "METADATA",
            "lock_id": "other-id",
            "ttl": int(time.time()) + 300,
        }
    )

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
        patch("src.bridge.handler.get_http_session"),
        patch("src.bridge.handler.invoke_real_runtime") as mock_invoke_real,
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
            # Second call (after wait and re-fetch) succeeds
            {
                "statusCode": 200,
                "body": json.dumps({"output": "Success after wait"}),
            },
        ]

        # In this case, our instance will see the lock, wait, and retry.
        # But for the retry to work, the SSM parameter must be updated by "someone else"
        # Since we are mocking everything, we'll manually update SSM while the lock is held.
        ssm = boto3.client("ssm", region_name=_REGION)
        from src.tenant_api.constants import DEFAULT_RUNTIME_REGION_PARAM

        ssm.put_parameter(
            Name=DEFAULT_RUNTIME_REGION_PARAM,
            Value="eu-central-1",
            Type="String",
            Overwrite=True,
        )

        response = handler(event, FakeLambdaContext())

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["output"] == "Success after wait"
