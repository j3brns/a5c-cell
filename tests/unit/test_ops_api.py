from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.tenant_api import ops_control
from tests.unit.tenant_api_test_support import (
    invoke_handler,
    response_body,
)

DE_SCOPED_OPS_SUMMARY_ROUTES = (
    ("/v1/platform/ops/top-tenants", "GET"),
    ("/v1/platform/ops/security-events", "GET"),
    ("/v1/platform/ops/error-rate", "GET"),
)


def _load_openapi_paths() -> dict[str, Any]:
    spec_path = Path(__file__).resolve().parents[2] / "docs" / "openapi.yaml"
    with spec_path.open("r", encoding="utf-8") as handle:
        spec = yaml.safe_load(handle)
    return spec.get("paths", {})


def _ops_event(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    query: dict[str, str] | None = None,
    *,
    roles: str = "Platform.Admin",
    tenant_id: str = "platform",
    sub: str = "admin-123",
) -> dict[str, Any]:
    return {
        "httpMethod": method,
        "path": path,
        "queryStringParameters": query,
        "body": None if body is None else json.dumps(body),
        "requestContext": {
            "authorizer": {
                "tenantid": tenant_id,
                "roles": roles,
                "sub": sub,
                "appid": "app-admin",
            }
        },
    }


def test_ops_billing_status_returns_real_summary_shape(fake_state: dict[str, Any]) -> None:
    fake_state["db"].items[("TENANT#t-001", "BILLING#2026-02")] = {
        "PK": "TENANT#t-001",
        "SK": "BILLING#2026-02",
        "tenantId": "t-001",
        "totalInputTokens": 123,
        "totalOutputTokens": 456,
        "totalCostUsd": 7.89,
        "updatedAt": "2026-02-25T11:00:00Z",
    }

    response = invoke_handler(
        _ops_event("GET", "/v1/platform/billing/status"),
        dependencies=fake_state["deps"],
    )

    assert response["statusCode"] == 200
    assert response_body(response) == {
        "yearMonth": "2026-02",
        "summaries": [
            {
                "tenantId": "t-001",
                "totalInputTokens": 123,
                "totalOutputTokens": 456,
                "totalCostUsd": 7.89,
                "lastUpdated": "2026-02-25T11:00:00Z",
            }
        ],
    }


@pytest.mark.parametrize(
    ("path", "method"),
    [
        *DE_SCOPED_OPS_SUMMARY_ROUTES,
        ("/v1/platform/ops/dlq/bridge-dlq", "GET"),
        ("/v1/platform/ops/dlq/bridge-dlq/redrive", "POST"),
        ("/v1/platform/ops/tenants/t-001/invocations", "GET"),
        ("/v1/platform/ops/tenants/t-001/sessions", "GET"),
    ],
)
def test_de_scoped_ops_routes_do_not_expose_placeholder_success(
    fake_state: dict[str, Any], path: str, method: str
) -> None:
    response = invoke_handler(_ops_event(method, path), dependencies=fake_state["deps"])

    assert response["statusCode"] == 405
    assert response_body(response)["error"]["code"] == "METHOD_NOT_ALLOWED"


@pytest.mark.parametrize(("path", "method"), DE_SCOPED_OPS_SUMMARY_ROUTES)
def test_ops_summary_runtime_remains_disabled_while_spa_contract_is_documented(
    fake_state: dict[str, Any], path: str, method: str
) -> None:
    assert path in _load_openapi_paths()

    response = invoke_handler(_ops_event(method, path), dependencies=fake_state["deps"])

    assert response["statusCode"] == 405
    assert response_body(response)["error"]["code"] == "METHOD_NOT_ALLOWED"


def test_platform_agent_read_only_surface_is_bounded_to_authoritative_routes() -> None:
    assert ops_control.READ_ONLY_PLATFORM_DIAGNOSTIC_ROUTES == {
        ("GET", "/v1/platform/agents"),
        ("GET", "/v1/platform/quota"),
        ("GET", "/v1/platform/billing/status"),
    }
    assert ("POST", "/v1/platform/failover") not in ops_control.READ_ONLY_PLATFORM_DIAGNOSTIC_ROUTES
    assert ("POST", "/v1/platform/agents") not in ops_control.READ_ONLY_PLATFORM_DIAGNOSTIC_ROUTES
    assert (
        "GET",
        "/v1/platform/ops/top-tenants",
    ) not in ops_control.READ_ONLY_PLATFORM_DIAGNOSTIC_ROUTES
    assert (
        "GET",
        "/v1/platform/ops/tenants/{tenant}/invocations",
    ) not in ops_control.READ_ONLY_PLATFORM_DIAGNOSTIC_ROUTES
