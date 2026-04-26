from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import boto3
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "data-access-lib" / "src"))

bridge_handler: Any = importlib.import_module("src.bridge.handler")
tenant_api_handler: Any = importlib.import_module("src.tenant_api.handler")


class _FakeEvents:
    def put_events(self, **kwargs: Any) -> dict[str, Any]:
        return {"FailedEntryCount": 0, "Entries": [{"EventId": "evt-1"}]}


class _FakeSecretsManager:
    pass


class _FakeUsageClient:
    pass


class _FakeMemoryProvisioner:
    pass


def _failover_event() -> dict[str, Any]:
    return {
        "httpMethod": "POST",
        "path": "/v1/platform/failover",
        "body": json.dumps({"targetRegion": "eu-central-1", "lockId": "unused-for-v0-2"}),
        "requestContext": {
            "authorizer": {
                "tenantid": "platform",
                "appid": "app-admin",
                "tier": "premium",
                "sub": "ops@example.com",
                "roles": ["Platform.Admin"],
            }
        },
    }


def test_failover_route_is_disabled_and_bridge_keeps_serving_region(monkeypatch) -> None:
    with mock_aws():
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
        monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
        monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
        monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-2")
        monkeypatch.setenv("AWS_REGION", "eu-west-2")
        monkeypatch.setenv("RUNTIME_REGION_PARAM", "/platform/config/runtime-region")

        ssm = boto3.client("ssm", region_name="eu-west-2")
        ssm.put_parameter(Name="/platform/config/runtime-region", Value="eu-west-2", Type="String")
        ssm.put_parameter(
            Name="/platform/config/mock-runtime-url",
            Value="http://localhost:8765",
            Type="String",
        )

        deps = tenant_api_handler.TenantApiDependencies(
            secretsmanager=_FakeSecretsManager(),
            events=_FakeEvents(),
            ssm=ssm,
            awslambda=MagicMock(),
            usage_client=_FakeUsageClient(),
            memory_provisioner=_FakeMemoryProvisioner(),
            platform_quota_client=MagicMock(),
        )
        response = tenant_api_handler.handle_event(_failover_event(), dependencies=deps)

        assert response["statusCode"] == 409
        assert json.loads(response["body"])["error"]["code"] == "RUNTIME_FAILOVER_DISABLED"

        bridge_handler._ssm_client = None
        bridge_handler._config_cache = {}
        bridge_handler._config_cache_expiry = 0
        config = bridge_handler.get_config(force_refresh=True)

        assert config["runtime_region"] == "eu-west-2"
