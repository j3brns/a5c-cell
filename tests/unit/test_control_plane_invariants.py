from __future__ import annotations

from pathlib import Path

import yaml

from src.authoriser.handler import is_admin_route, is_platform_route
from src.tenant_api import ops_control
from tests.unit.tenant_api_test_support import (
    invoke_handler,
    response_body,
)
from tests.unit.test_ops_api import _ops_event


def _load_openapi() -> dict:
    spec_path = Path(__file__).resolve().parents[2] / "docs" / "openapi.yaml"
    with spec_path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def test_tenant_api_uses_factory_for_scan_capable_control_plane_db() -> None:
    tenant_api_dir = Path(__file__).resolve().parents[2] / "src" / "tenant_api"
    allowed = {
        tenant_api_dir / "db_factory.py",
    }
    offenders: list[str] = []

    for path in tenant_api_dir.glob("*.py"):
        if path in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        if "ControlPlaneDynamoDB" in text:
            offenders.append(str(path.relative_to(tenant_api_dir.parent.parent)))

    assert offenders == []


def _method_arn(method: str, path: str) -> str:
    normalized = path.lstrip("/")
    return f"arn:aws:execute-api:eu-west-2:123456789012:api/dev/{method}/{normalized}"


def test_health_route_stays_public_across_openapi_and_authoriser() -> None:
    spec = _load_openapi()
    health_get = spec["paths"]["/v1/health"]["get"]

    assert health_get["security"] == []
    assert is_admin_route(_method_arn("GET", "/v1/health")) is False
    assert is_platform_route(_method_arn("GET", "/v1/health")) is False


def test_platform_read_only_surface_is_declared_and_admin_protected() -> None:
    spec = _load_openapi()
    paths = spec["paths"]

    for method, path in ops_control.READ_ONLY_PLATFORM_DIAGNOSTIC_ROUTES:
        operation = paths[path][method.lower()]
        assert operation.get("security") != []
        assert operation["x-required-roles"] == ["Platform.Admin", "Platform.Operator"]


def test_non_platform_tenants_cannot_access_platform_diagnostic_surface(
    fake_state: dict[str, object],
) -> None:
    for path in ("/v1/platform/quota", "/v1/platform/billing/status", "/v1/platform/agents"):
        response = invoke_handler(
            _ops_event(
                "GET",
                path,
                roles="Tenant.Admin",
                tenant_id="t-test-001",
                sub="tenant-admin",
            ),
            dependencies=fake_state["deps"],  # type: ignore[arg-type]
        )
        assert response["statusCode"] == 403
        assert response_body(response)["error"]["code"] == "FORBIDDEN"
