from __future__ import annotations

from typing import Any

from src.tenant_api import tenant_lifecycle
from src.tenant_api.models import TenantCreateInput


def test_handle_create_delegates_to_tenant_records(monkeypatch) -> None:
    expected = {"statusCode": 201, "body": '{"tenantId": "t-001"}'}

    def _fake_handle_create(request: TenantCreateInput, caller: Any, deps: Any) -> dict[str, Any]:
        assert request.tenant_id == "t-001"
        assert request.app_id == "app-001"
        assert request.display_name == "Tenant"
        assert request.tier == "standard"
        assert request.owner_email == "owner@example.com"
        assert request.owner_team == "team"
        assert request.account_id == "123456789012"
        assert request.monthly_budget_usd == 120.5
        assert caller == "caller"
        assert deps == "deps"
        return expected

    monkeypatch.setattr(tenant_lifecycle.tenant_records, "handle_create", _fake_handle_create)
    response = tenant_lifecycle.handle_create(
        {
            "body": (
                '{"tenantId": "T-001", "appId": "app-001", "displayName": "Tenant", '
                '"tier": "standard", "ownerEmail": "owner@example.com", '
                '"ownerTeam": "team", "accountId": "123456789012", '
                '"monthlyBudgetUsd": 120.5}'
            )
        },
        "caller",
        "deps",
    )
    assert response["statusCode"] == 201
    assert "tenant" in response["body"]


def test_dispatch_list_tenants_maps_query_filters(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_handle_list_tenants(request: Any, caller: Any, deps: Any) -> dict[str, Any]:
        captured["request"] = request
        captured["caller"] = caller
        captured["deps"] = deps
        return {"statusCode": 200, "body": "{}"}

    monkeypatch.setattr(tenant_lifecycle, "handle_list_tenants", _fake_handle_list_tenants)

    response = tenant_lifecycle.dispatch_routes(
        "/v1/tenants",
        "GET",
        {"queryStringParameters": {"status": "active", "tier": "premium"}},
        "caller",
        "deps",
        None,
    )

    assert response == {"statusCode": 200, "body": "{}"}
    assert captured["request"].status_filter == "active"
    assert captured["request"].tier_filter == "premium"
    assert captured["caller"] == "caller"
    assert captured["deps"] == "deps"


def test_handle_audit_export_delegates_to_audit_module(monkeypatch) -> None:
    expected = {"statusCode": 200, "body": "{}"}

    def _fake_handle_audit_export(
        event: dict[str, Any], caller: Any, *, tenant_id: str
    ) -> dict[str, Any]:
        assert event["path"].endswith("/audit-export")
        assert caller == "caller"
        assert tenant_id == "t-001"
        return expected

    monkeypatch.setattr(
        tenant_lifecycle.tenant_audit_exports,
        "handle_audit_export",
        _fake_handle_audit_export,
    )
    assert (
        tenant_lifecycle.handle_audit_export(
            {"path": "/v1/tenants/t-001/audit-export"},
            "caller",
            tenant_id="t-001",
        )
        is expected
    )


def test_handle_list_invites_delegates_to_invite_module(monkeypatch) -> None:
    expected = {"statusCode": 200, "body": "{}"}

    def _fake_handle_list_invites(caller: Any, deps: Any, *, tenant_id: str) -> dict[str, Any]:
        assert caller == "caller"
        assert deps == "deps"
        assert tenant_id == "t-001"
        return expected

    monkeypatch.setattr(
        tenant_lifecycle.tenant_invites,
        "handle_list_invites",
        _fake_handle_list_invites,
    )
    assert tenant_lifecycle.handle_list_invites("caller", "deps", tenant_id="t-001") is expected


def test_handle_sessions_delegates_to_session_module(monkeypatch) -> None:
    expected = {"statusCode": 501, "body": "{}"}

    def _fake_handle_sessions(event: dict[str, Any], caller: Any) -> dict[str, Any]:
        assert event["path"] == "/v1/sessions"
        assert caller == "caller"
        return expected

    monkeypatch.setattr(tenant_lifecycle.tenant_sessions, "handle_sessions", _fake_handle_sessions)
    assert tenant_lifecycle.handle_sessions({"path": "/v1/sessions"}, "caller") is expected
