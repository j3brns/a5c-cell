from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.tenant_api import bootstrap
from src.tenant_api.models import CallerIdentity, TenantApiDependencies


def _deps() -> TenantApiDependencies:
    return TenantApiDependencies(
        secretsmanager=object(),
        events=object(),
        ssm=object(),
        awslambda=object(),
        usage_client=object(),
        memory_provisioner=object(),
        platform_quota_client=object(),
    )


def test_build_runtime_assembles_http_request_context(monkeypatch) -> None:
    monkeypatch.setenv("AWS_REGION", "eu-west-2")
    runtime = bootstrap.build_runtime(
        {
            "httpMethod": "GET",
            "path": "/v1/tenants/Tenant-Acme-001",
            "pathParameters": {"tenantId": "Tenant-Acme-001"},
            "requestContext": {
                "authorizer": {
                    "tenantid": "platform",
                    "appid": "app-admin",
                    "roles": "Platform.Admin",
                }
            },
        },
        dependencies=_deps(),
    )

    assert runtime.method == "GET"
    assert runtime.path == "/v1/tenants/tenant-acme-001"
    assert runtime.tenant_id == "tenant-acme-001"
    assert runtime.caller == CallerIdentity(
        tenant_id="platform",
        app_id="app-admin",
        tier=None,
        sub=None,
        roles=frozenset({"Platform.Admin"}),
        usage_identifier_key=None,
    )


def test_build_runtime_preserves_eventbridge_tenant_provisioner_payload(monkeypatch) -> None:
    monkeypatch.setenv("AWS_REGION", "eu-west-2")
    event = {
        "detail-type": "tenant.provisioned",
        "source": "platform.tenant_provisioner",
        "detail": {"tenantId": "tenant-001", "appId": "app-001"},
    }

    runtime = bootstrap.build_runtime(event, dependencies=_deps())

    assert runtime.detail_type == "tenant.provisioned"
    assert runtime.source == "platform.tenant_provisioner"
    assert runtime.detail == {"tenantId": "tenant-001", "appId": "app-001"}
    assert runtime.method == "GET"
    assert runtime.path == ""


def test_build_runtime_uses_injected_dependencies() -> None:
    deps = _deps()
    runtime = bootstrap.build_runtime({}, dependencies=deps)

    assert runtime.deps is deps
