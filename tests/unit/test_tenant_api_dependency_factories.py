from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.tenant_api import composition, dependency_factories


class FakeSession:
    def __init__(self) -> None:
        self.clients: dict[str, Any] = {}

    def client(self, service_name: str, *, region_name: str | None = None) -> Any:
        key = f"{service_name}:{region_name or 'default'}"
        return self.clients.setdefault(key, object())


def test_build_tenant_api_dependencies_uses_session_factories(monkeypatch) -> None:
    session = FakeSession()

    monkeypatch.setattr(
        dependency_factories.boto3.session,
        "Session",
        lambda *, region_name: session if region_name == "eu-west-2" else None,
    )

    deps = dependency_factories.build_tenant_api_dependencies(region="eu-west-2")

    assert deps.secretsmanager is session.client("secretsmanager")
    assert deps.events is session.client("events")
    assert deps.ssm is session.client("ssm")
    assert deps.awslambda is session.client("lambda")
    assert isinstance(deps.usage_client, dependency_factories._NoopUsageClient)
    assert isinstance(deps.memory_provisioner, dependency_factories._NoopMemoryProvisioner)
    assert isinstance(deps.platform_quota_client, dependency_factories._AwsPlatformQuotaClient)


def test_composition_config_reads_required_aws_region() -> None:
    config = composition.TenantApiConfig.from_env({"AWS_REGION": "eu-west-2"})

    assert config.aws_region == "eu-west-2"


def test_composition_config_rejects_missing_aws_region() -> None:
    with pytest.raises(RuntimeError, match="AWS_REGION"):
        composition.TenantApiConfig.from_env({})


def test_composition_build_runtime_uses_injected_dependencies() -> None:
    deps = object()
    event = {
        "httpMethod": "GET",
        "path": "/v1/health",
        "requestContext": {"authorizer": {"tenantid": "platform", "roles": "Platform.Admin"}},
    }

    runtime = composition.build_runtime(event, dependencies=deps)  # type: ignore[arg-type]

    assert runtime.deps is deps
