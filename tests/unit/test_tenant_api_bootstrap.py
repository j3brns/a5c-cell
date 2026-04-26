from __future__ import annotations

from src.tenant_api import bootstrap
from src.tenant_api.models import CallerIdentity, TenantApiDependencies


def test_build_runtime_assembles_http_request_context(
    tenant_api_env: None,
    fake_deps: TenantApiDependencies,
) -> None:
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
        dependencies=fake_deps,
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


def test_build_runtime_preserves_eventbridge_tenant_provisioner_payload(
    tenant_api_env: None,
    fake_deps: TenantApiDependencies,
) -> None:
    event = {
        "detail-type": "tenant.provisioned",
        "source": "platform.tenant_provisioner",
        "detail": {"tenantId": "tenant-001", "appId": "app-001"},
    }

    runtime = bootstrap.build_runtime(event, dependencies=fake_deps)

    assert runtime.detail_type == "tenant.provisioned"
    assert runtime.source == "platform.tenant_provisioner"
    assert runtime.detail == {"tenantId": "tenant-001", "appId": "app-001"}
    assert runtime.method == "GET"
    assert runtime.path == ""


def test_build_runtime_uses_injected_dependencies(fake_deps: TenantApiDependencies) -> None:
    runtime = bootstrap.build_runtime({}, dependencies=fake_deps)

    assert runtime.deps is fake_deps
