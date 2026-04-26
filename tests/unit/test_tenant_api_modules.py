from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moto import mock_aws

from src.tenant_api import (
    agent_registry,
    ops_control,
    tenant_lifecycle,
    tenant_records,
    webhook_registry,
)
from src.tenant_api import (
    handler as tenant_api_handler,
)
from src.tenant_api.models import TenantCreateInput, TenantUpdateInput
from tests.unit.tenant_api_test_support import build_module_state, fixed_now_value


@pytest.fixture
def fixed_now() -> datetime:
    return fixed_now_value()


@pytest.fixture
def module_state(monkeypatch: pytest.MonkeyPatch, fixed_now: Any) -> Any:
    with mock_aws():
        yield build_module_state(monkeypatch, fixed_now)


def _caller(
    *,
    tenant_id: str | None = "t-admin",
    roles: list[str] | None = None,
    app_id: str = "app-admin",
) -> tenant_api_handler.CallerIdentity:
    return tenant_api_handler.CallerIdentity(
        tenant_id=tenant_id,
        app_id=app_id,
        tier="premium",
        sub="user-123",
        roles=frozenset(roles or ["Platform.Admin"]),
        usage_identifier_key=None,
    )


def _event(path: str, method: str = "GET", body: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "path": path,
        "httpMethod": method,
        "body": None if body is None else json.dumps(body),
        "queryStringParameters": {},
    }


@mock_aws
def test_agent_registry_dispatch_registers_agent(module_state: dict[str, Any]) -> None:
    response = agent_registry.dispatch_routes(
        "/v1/platform/agents",
        "POST",
        _event(
            "/v1/platform/agents",
            "POST",
            {"agentName": "echo-agent", "version": "1.0.0"},
        ),
        _caller(),
        module_state["deps"],
    )

    assert response is not None
    assert response["statusCode"] == 201
    stored = module_state["db"].items[("AGENT#echo-agent", "VERSION#1.0.0")]
    assert stored["status"] == "built"


@mock_aws
def test_ops_control_dispatches_platform_quota(module_state: dict[str, Any]) -> None:
    response = ops_control.dispatch_platform_admin_routes(
        "/v1/platform/quota",
        "GET",
        _event("/v1/platform/quota"),
        _caller(),
        module_state["deps"],
    )

    assert response is not None
    body = json.loads(response["body"])
    assert body["utilisation"][0]["region"] == "eu-west-1"


@mock_aws
def test_tenant_lifecycle_dispatch_creates_tenant(module_state: dict[str, Any]) -> None:
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
        _caller(),
        module_state["deps"],
        None,
    )

    assert response is not None
    assert response["statusCode"] == 201
    assert ("TENANT#tenant-mod-001", "METADATA") in module_state["db"].items
    assert len(module_state["deps"].secretsmanager.policy_calls) == 1


@mock_aws
def test_tenant_record_create_accepts_service_input_without_gateway_event(
    module_state: dict[str, Any],
) -> None:
    response = tenant_records.handle_create(
        TenantCreateInput(
            tenant_id="tenant-svc-001",
            app_id="app-001",
            display_name="Service Tenant",
            tier="standard",
            owner_email="owner@example.com",
            owner_team="team-acme",
            account_id="123456789012",
            monthly_budget_usd=None,
        ),
        _caller(),
        module_state["deps"],
    )

    assert response["statusCode"] == 201
    assert ("TENANT#tenant-svc-001", "METADATA") in module_state["db"].items
    stored = module_state["db"].items[("TENANT#tenant-svc-001", "METADATA")]
    assert stored["displayName"] == "Service Tenant"


@mock_aws
def test_tenant_record_create_rejects_blank_service_input(
    module_state: dict[str, Any],
) -> None:
    with pytest.raises(ValueError, match="displayName is required"):
        tenant_records.handle_create(
            TenantCreateInput(
                tenant_id="tenant-svc-blank",
                app_id="app-001",
                display_name="   ",
                tier="standard",
                owner_email="owner@example.com",
                owner_team="team-acme",
                account_id="123456789012",
                monthly_budget_usd=None,
            ),
            _caller(),
            module_state["deps"],
        )
    assert module_state["db"].items == {}


@mock_aws
def test_tenant_record_update_accepts_service_input_without_gateway_event(
    module_state: dict[str, Any],
) -> None:
    module_state["db"].items[("TENANT#tenant-svc-002", "METADATA")] = {
        "PK": "TENANT#tenant-svc-002",
        "SK": "METADATA",
        "tenantId": "tenant-svc-002",
        "appId": "app-001",
        "displayName": "Old Tenant",
        "tier": "basic",
        "status": "active",
    }

    response = tenant_records.handle_update(
        TenantUpdateInput(
            display_name="Updated Tenant",
            tier="premium",
        ),
        _caller(),
        module_state["deps"],
        tenant_id="tenant-svc-002",
    )

    assert response["statusCode"] == 200
    stored = module_state["db"].items[("TENANT#tenant-svc-002", "METADATA")]
    assert stored["displayName"] == "Updated Tenant"
    assert stored["tier"] == "premium"


def test_tenant_update_input_rejects_unmarked_values() -> None:
    with pytest.raises(ValueError, match="Unmarked update field"):
        TenantUpdateInput(
            provided_fields=frozenset({"tier"}),
            display_name="Ignored Tenant",
            tier="premium",
        )


@mock_aws
def test_webhook_registry_dispatch_registers_webhook(module_state: dict[str, Any]) -> None:
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
        _caller(tenant_id="t-001", roles=["SelfService.Admin"]),
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
