from __future__ import annotations

import json
import os
import time
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import boto3
import pytest
import requests
from moto import mock_aws

from src.bridge.handler import handler


class FakeLambdaContext:
    def __init__(self):
        self.function_name = "bridge"
        self.memory_limit_in_mb = 256
        self.invoked_function_arn = "arn:aws:lambda:eu-west-2:111111111111:function:bridge"
        self.aws_request_id = "req-123"


_REGION = "eu-west-2"


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
        os.environ["MOCK_RUNTIME_URL"] = "http://mock-runtime:8080"

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
        config_table.put_item(Item={"key": "runtime_region", "value": "eu-west-2"})
        config_table.put_item(Item={"key": "mock_runtime_url", "value": "http://mock-runtime:8080"})

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
                "invocation_mode": "sync",
                "enabled": True,
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
        patch("src.tenant_api.bootstrap.required_ssm_parameter") as mock_ssm_param,
        patch("src.bridge.handler.invoke_mock_runtime") as mock_invoke_mock,
        patch("src.bridge.handler.invoke_real_runtime") as mock_invoke_real,
    ):
        # mock_ssm_param returns initial region, then fallback after failover
        mock_ssm_param.side_effect = ["eu-west-2", "eu-central-1", "eu-west-2", "eu-central-1"]

        # First call (mock runtime) fails with 503
        mock_invoke_mock.return_value = {
            "statusCode": 503,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": {"code": "SERVICE_UNAVAILABLE"}}),
        }

        # Second call (after failover) succeeds
        mock_invoke_real.return_value = {
            "statusCode": 200,
            "body": json.dumps({"output": "Success after failover"}),
        }

        response = handler(event, FakeLambdaContext())

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["output"] == "Success after failover"

        # Verify SSM was updated to eu-central-1
        ssm = boto3.client("ssm", region_name="eu-west-2")
        from src.tenant_api.constants import DEFAULT_RUNTIME_REGION_PARAM
        param = ssm.get_parameter(Name=DEFAULT_RUNTIME_REGION_PARAM)
        assert param["Parameter"]["Value"] == "eu-central-1"


def test_handler_failover_already_in_progress(setup_data):
    # Seed the lock to simulate another instance failing over
    ddb = boto3.resource("dynamodb", region_name="eu-west-2")
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
        patch("src.tenant_api.bootstrap.required_ssm_parameter") as mock_ssm_param,
        patch("src.bridge.handler.invoke_mock_runtime") as mock_invoke_mock,
        patch("src.bridge.handler.invoke_real_runtime") as mock_invoke_real,
    ):
        mock_ssm_param.side_effect = ["eu-west-2", "eu-central-1", "eu-west-2", "eu-central-1"]

        mock_invoke_mock.side_effect = [
            {
                "statusCode": 503,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"error": {"code": "SERVICE_UNAVAILABLE"}}),
            }
        ]
        
        mock_invoke_real.return_value = {
            "statusCode": 200,
            "body": json.dumps({"output": "Success after wait"}),
        }

        # In this case, our instance will see the lock, wait, and retry.
        # But for the retry to work, the SSM parameter must be updated by "someone else"
        # Since we are mocking everything, we'll manually update SSM while the lock is held.
        ssm = boto3.client("ssm", region_name="eu-west-2")
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
