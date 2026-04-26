from __future__ import annotations

import json
from typing import Any

from src.tenant_api import agent_registry, ops_control, tenant_lifecycle, webhook_registry


def _event(path: str, method: str = "GET", body: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "path": path,
        "httpMethod": method,
        "body": None if body is None else json.dumps(body),
        "queryStringParameters": {},
    }


def test_agent_registry_dispatch_registers_agent(
    module_state: dict[str, Any],
    tenant_api_caller: Any,
) -> None:
    response = agent_registry.dispatch_routes(
        "/v1/platform/agents",
        "POST",
        _event(
            "/v1/platform/agents",
            "POST",
            {"agentName": "echo-agent", "version": "1.0.0"},
        ),
        tenant_api_caller(),
        module_state["deps"],
    )

    assert response is not None
    assert response["statusCode"] == 201
    stored = module_state["db"].items[("AGENT#echo-agent", "VERSION#1.0.0")]
    assert stored["status"] == "built"


def test_ops_control_dispatches_platform_quota(
    module_state: dict[str, Any],
    tenant_api_caller: Any,
) -> None:
    response = ops_control.dispatch_platform_admin_routes(
        "/v1/platform/quota",
        "GET",
        _event("/v1/platform/quota"),
        tenant_api_caller(),
        module_state["deps"],
    )

    assert response is not None
    body = json.loads(response["body"])
    assert body["utilisation"][0]["region"] == "eu-west-1"


def test_tenant_lifecycle_dispatch_creates_tenant(
    module_state: dict[str, Any],
    tenant_api_caller: Any,
) -> None:
    response = tenant_lifecycle.dispatch_routes(
        "/v1/tenants",
        "POST",
        _event(
            "/v1/tenants",
            "POST",
            {
                "tenantId": "tenant-mod-001",
                "appId": "app-001",
                "displayName": "Acme Ltd",
                "tier": "standard",
                "ownerEmail": "owner@example.com",
                "ownerTeam": "team-acme",
                "accountId": "123456789012",
            },
        ),
        tenant_api_caller(),
        module_state["deps"],
        None,
    )

    assert response is not None
    assert response["statusCode"] == 201
    assert ("TENANT#tenant-mod-001", "METADATA") in module_state["db"].items
    assert len(module_state["deps"].secretsmanager.policy_calls) == 1


def test_webhook_registry_dispatch_registers_webhook(
    module_state: dict[str, Any],
    tenant_api_caller: Any,
) -> None:
    module_state["db"].items[("TENANT#t-001", "METADATA")] = {
        "PK": "TENANT#t-001",
        "SK": "METADATA",
        "tenantId": "t-001",
        "appId": "app-001",
        "displayName": "Acme Ltd",
        "tier": "standard",
        "status": "active",
    }
    response = webhook_registry.dispatch_routes(
        "/v1/webhooks",
        "POST",
        _event(
            "/v1/webhooks",
            "POST",
            {"callbackUrl": "https://example.com/hook", "events": ["job.completed"]},
        ),
        tenant_api_caller(tenant_id="t-001", roles=["SelfService.Admin"]),
        module_state["deps"],
    )

    assert response is not None
    assert response["statusCode"] == 201
    webhook_keys = [
        key
        for key in module_state["db"].items
        if key[0] == "TENANT#t-001" and key[1].startswith("WEBHOOK#")
    ]
    assert webhook_keys
