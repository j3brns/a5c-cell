from __future__ import annotations

import importlib
import json
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from botocore.exceptions import EndpointConnectionError

from src.tenant_api import handler as tenant_api_handler
from src.tenant_api import tenant_lifecycle
from tests.unit.tenant_api_test_support import (
    FailingPlatformQuotaClient,
    FakeAwsSession,
    FakeCloudWatchClient,
    FakeServiceQuotasClient,
    FakeTenantScopedS3,
    response_body,
)

RESERVED_TENANT_IDS = ("platform", "admin", "root", "system", "stub")


def _event(
    *,
    method: str,
    tenant_id: str | None = None,
    body: dict[str, Any] | None = None,
    caller_tenant_id: str | None = "t-admin",
    roles: str | list[str] = "Platform.Admin",
    app_id: str = "app-admin",
    usage_identifier_key: str | None = None,
) -> dict[str, Any]:
    path_params = None
    if tenant_id is not None:
        path_params = {"tenantId": tenant_id}
    authorizer: dict[str, Any] = {
        "tenantid": caller_tenant_id,
        "appid": app_id,
        "tier": "premium",
        "sub": "user-123",
        "roles": roles,
    }
    if usage_identifier_key is not None:
        authorizer["usageIdentifierKey"] = usage_identifier_key

    path = "/v1/tenants"
    if tenant_id is not None:
        path = f"/v1/tenants/{tenant_id}"

    return {
        "httpMethod": method,
        "path": path,
        "pathParameters": path_params,
        "body": None if body is None else json.dumps(body),
        "requestContext": {"authorizer": authorizer},
    }


_body = response_body

_CURRENT_STATE: dict[str, Any] | None = None


@pytest.fixture(autouse=True)
def _setup_state(fake_state: dict[str, Any]) -> None:
    global _CURRENT_STATE
    _CURRENT_STATE = fake_state


def _invoke(event: dict[str, Any]) -> dict[str, Any]:
    assert _CURRENT_STATE is not None
    return tenant_api_handler.handle_event(event, dependencies=_CURRENT_STATE["deps"])


def _last_event_detail(fake_state: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    calls = fake_state["deps"].events.calls
    assert calls, "expected EventBridge put_events call"
    entry = calls[-1]["Entries"][0]
    return entry["DetailType"], json.loads(entry["Detail"])


def _seed_agent_version(
    fake_state: dict[str, Any],
    *,
    agent_name: str,
    version: str,
    status: str = "built",
    extra: dict[str, Any] | None = None,
) -> None:
    item = {
        "PK": f"AGENT#{agent_name}",
        "SK": f"VERSION#{version}",
        "agent_name": agent_name,
        "version": version,
        "owner_team": "platform",
        "tier_minimum": "basic",
        "layer_hash": f"hash-{version}",
        "layer_s3_key": f"layers/{version}.zip",
        "script_s3_key": f"scripts/{version}.zip",
        "deployed_at": "2026-02-25T12:00:00Z",
        "invocation_mode": "sync",
        "streaming_enabled": False,
        "status": status,
    }
    if extra:
        item.update(extra)
    fake_state["db"].items[(f"AGENT#{agent_name}", f"VERSION#{version}")] = item


def test_create_tenant_writes_record_provisions_memory_secret_and_emits_event(
    fake_state: dict[str, Any],
) -> None:
    response = _invoke(
        _event(
            method="POST",
            tenant_id=None,
            body={
                "tenantId": "t-001",
                "appId": "app-001",
                "displayName": "Acme Ltd",
                "tier": "standard",
                "ownerEmail": "owner@example.com",
                "ownerTeam": "team-acme",
                "accountId": "123456789012",
                "monthlyBudgetUsd": 99.5,
            },
        )
    )

    assert response["statusCode"] == 201
    tenant = _body(response)["tenant"]
    assert tenant["tenantId"] == "t-001"
    assert tenant["tier"] == "standard"
    assert tenant["provisioningStatus"] == "pending"
    assert tenant["apiKeySecretArn"].startswith("arn:aws:secretsmanager:")
    assert tenant["memoryStoreArn"].endswith("/t-001")
    assert fake_state["deps"].memory_provisioner.calls == [
        {"tenant_id": "t-001", "app_id": "app-001"}
    ]
    assert len(fake_state["deps"].secretsmanager.calls) == 1
    assert len(fake_state["deps"].secretsmanager.policy_calls) == 1
    policy_call = fake_state["deps"].secretsmanager.policy_calls[0]
    assert policy_call["SecretId"].endswith("platform/tenants/t-001/api-key")
    policy = json.loads(policy_call["ResourcePolicy"])
    statement = policy["Statement"][0]
    assert statement["Effect"] == "Deny"
    assert statement["Action"] == "secretsmanager:GetSecretValue"
    assert (
        statement["Principal"]["AWS"] == "arn:aws:iam::111111111111:role/platform-tenant-mgmt-dev"
    )
    detail_type, detail = _last_event_detail(fake_state)
    assert detail_type == "tenant.created"
    assert detail["tenantId"] == "t-001"
    assert detail["appId"] == "app-001"
    assert detail["tier"] == "standard"
    assert detail["accountId"] == "123456789012"


def test_create_tenant_rejects_non_home_account_id(fake_state: dict[str, Any]) -> None:
    response = _invoke(
        _event(
            method="POST",
            tenant_id=None,
            body={
                "tenantId": "t-cross-001",
                "appId": "app-001",
                "displayName": "Cross Account Ltd",
                "tier": "standard",
                "ownerEmail": "owner@example.com",
                "ownerTeam": "team-cross",
                "accountId": "999999999999",
            },
        )
    )

    assert response["statusCode"] == 400
    error = _body(response)["error"]
    assert error["code"] == "BAD_REQUEST"
    assert error["message"] == "accountId must equal the platform home account"
    assert fake_state["deps"].memory_provisioner.calls == []


def test_tenant_provisioned_event_updates_tenant_record(fake_state: dict[str, Any]) -> None:
    fake_state["db"].items[("TENANT#t-prov-001", "METADATA")] = {
        "PK": "TENANT#t-prov-001",
        "SK": "METADATA",
        "tenantId": "t-prov-001",
        "appId": "app-001",
        "displayName": "Acme Ltd",
        "tier": "standard",
        "status": "active",
        "provisioningStatus": "pending",
        "createdAt": "2026-03-28T12:00:00Z",
        "updatedAt": "2026-03-28T12:00:00Z",
        "ownerEmail": "owner@example.com",
        "ownerTeam": "team-acme",
        "accountId": "123456789012",
    }

    response = tenant_api_handler.handle_event(
        {
            "source": "platform.tenant_provisioner",
            "detail-type": "tenant.provisioned",
            "detail": {
                "tenantId": "t-prov-001",
                "appId": "app-001",
                "ExecutionRoleArn": "arn:role",
                "MemoryStoreArn": "arn:mem",
            },
        },
        dependencies=fake_state["deps"],
    )

    assert response["statusCode"] == 200
    tenant = _body(response)["tenant"]
    assert tenant["provisioningStatus"] == "ready"
    assert tenant["executionRoleArn"] == "arn:role"
    assert tenant["memoryStoreArn"] == "arn:mem"


def test_tenant_provisioning_event_rejects_non_home_account_id(
    fake_state: dict[str, Any],
) -> None:
    fake_state["db"].items[("TENANT#t-prov-cross", "METADATA")] = {
        "PK": "TENANT#t-prov-cross",
        "SK": "METADATA",
        "tenantId": "t-prov-cross",
        "appId": "app-001",
        "displayName": "Cross Account Ltd",
        "tier": "standard",
        "status": "active",
        "provisioningStatus": "pending",
        "createdAt": "2026-03-28T12:00:00Z",
        "updatedAt": "2026-03-28T12:00:00Z",
        "ownerEmail": "owner@example.com",
        "ownerTeam": "team-cross",
        "accountId": "123456789012",
    }

    response = tenant_api_handler.handle_event(
        {
            "source": "platform.tenant_provisioner",
            "detail-type": "tenant.provisioned",
            "detail": {
                "tenantId": "t-prov-cross",
                "appId": "app-001",
                "accountId": "999999999999",
                "ExecutionRoleArn": "arn:role",
            },
        },
        dependencies=fake_state["deps"],
    )

    assert response["statusCode"] == 400
    error = _body(response)["error"]
    assert error["code"] == "BAD_REQUEST"
    assert error["message"] == "accountId must equal the platform home account"
    item = fake_state["db"].items[("TENANT#t-prov-cross", "METADATA")]
    assert item["provisioningStatus"] == "pending"
    assert "executionRoleArn" not in item


def test_tenant_provisioning_failed_event_updates_tenant_record(fake_state: dict[str, Any]) -> None:
    fake_state["db"].items[("TENANT#t-prov-002", "METADATA")] = {
        "PK": "TENANT#t-prov-002",
        "SK": "METADATA",
        "tenantId": "t-prov-002",
        "appId": "app-002",
        "displayName": "Beta Ltd",
        "tier": "standard",
        "status": "active",
        "provisioningStatus": "pending",
        "createdAt": "2026-03-28T12:00:00Z",
        "updatedAt": "2026-03-28T12:00:00Z",
        "ownerEmail": "owner@example.com",
        "ownerTeam": "team-beta",
        "accountId": "123456789012",
    }

    response = tenant_api_handler.handle_event(
        {
            "source": "platform.tenant_provisioner",
            "detail-type": "tenant.provisioning_failed",
            "detail": {
                "tenantId": "t-prov-002",
                "appId": "app-002",
                "reason": "ROLLBACK_COMPLETE",
            },
        },
        dependencies=fake_state["deps"],
    )

    assert response["statusCode"] == 200
    tenant = _body(response)["tenant"]
    assert tenant["provisioningStatus"] == "failed"
    assert tenant["provisioningError"] == "ROLLBACK_COMPLETE"


def test_reserved_platform_provisioning_event_is_accepted(fake_state: dict[str, Any]) -> None:
    fake_state["db"].items[("TENANT#platform", "METADATA")] = {
        "PK": "TENANT#platform",
        "SK": "METADATA",
        "tenantId": "platform",
        "appId": "platform-internal",
        "displayName": "Platform Internal",
        "tier": "premium",
        "status": "active",
        "provisioningStatus": "pending",
        "createdAt": "2026-03-28T12:00:00Z",
        "updatedAt": "2026-03-28T12:00:00Z",
        "ownerEmail": "platform@example.invalid",
        "ownerTeam": "platform",
        "accountId": "123456789012",
    }

    response = tenant_api_handler.handle_event(
        {
            "source": "platform.tenant_provisioner",
            "detail-type": "tenant.provisioned",
            "detail": {
                "tenantId": "platform",
                "appId": "platform-internal",
                "ExecutionRoleArn": "arn:platform-role",
            },
        },
        dependencies=fake_state["deps"],
    )

    assert response["statusCode"] == 200
    tenant = _body(response)["tenant"]
    assert tenant["tenantId"] == "platform"
    assert tenant["executionRoleArn"] == "arn:platform-role"
    assert tenant["provisioningStatus"] == "ready"


def test_create_tenant_normalizes_tenant_id_to_lowercase(fake_state: dict[str, Any]) -> None:
    response = _invoke(
        _event(
            method="POST",
            body={
                "tenantId": "Tenant-Acme-001",
                "appId": "app-001",
                "displayName": "Acme Ltd",
                "tier": "standard",
                "ownerEmail": "owner@example.com",
                "ownerTeam": "team-acme",
                "accountId": "123456789012",
            },
        )
    )

    assert response["statusCode"] == 201
    body = _body(response)
    assert body["tenant"]["tenantId"] == "tenant-acme-001"
    assert fake_state["deps"].memory_provisioner.calls == [
        {"tenant_id": "tenant-acme-001", "app_id": "app-001"}
    ]


@pytest.mark.parametrize(
    ("tenant_id", "expected_error"),
    [
        ("ab", "tenantId must be 3-32 characters"),
        ("a" * 33, "tenantId must be 3-32 characters"),
        ("tenant--one", "tenantId must not contain consecutive hyphens"),
        ("tenant_one", "tenantId must match ^[a-z](?:[a-z0-9-]{1,30}[a-z0-9])$"),
        *[(tenant_id, "tenantId is reserved") for tenant_id in RESERVED_TENANT_IDS],
    ],
)
def test_create_tenant_rejects_invalid_tenant_id_values(
    fake_state: dict[str, Any], tenant_id: str, expected_error: str
) -> None:
    response = _invoke(
        _event(
            method="POST",
            body={
                "tenantId": tenant_id,
                "appId": "app-001",
                "displayName": "Acme Ltd",
                "tier": "standard",
                "ownerEmail": "owner@example.com",
                "ownerTeam": "team-acme",
                "accountId": "123456789012",
            },
        )
    )

    assert response["statusCode"] == 400
    error = _body(response)["error"]
    assert error["code"] == "BAD_REQUEST"
    assert error["message"] == expected_error


def _seed_platform_tenant(fake_state: dict[str, Any]) -> None:
    fake_state["db"].items[("TENANT#platform", "METADATA")] = {
        "PK": "TENANT#platform",
        "SK": "METADATA",
        "tenant_id": "platform",
        "tenantId": "platform",
        "app_id": "platform-internal",
        "appId": "platform-internal",
        "tier": "premium",
        "status": "active",
    }


def test_admin_can_read_reserved_platform_tenant(fake_state: dict[str, Any]) -> None:
    _seed_platform_tenant(fake_state)

    response = _invoke(_event(method="GET", tenant_id="platform"))

    assert response["statusCode"] == 200
    tenant = _body(response)["tenant"]
    assert tenant["tenantId"] == "platform"


@pytest.mark.parametrize("roles", [[], ["Platform.Operator"]])
def test_non_admin_cannot_read_reserved_platform_tenant(
    fake_state: dict[str, Any], roles: list[str]
) -> None:
    _seed_platform_tenant(fake_state)

    response = _invoke(
        _event(
            method="GET",
            tenant_id="platform",
            caller_tenant_id="platform",
            roles=roles,
            app_id="platform-agent",
        )
    )

    assert response["statusCode"] == 400
    error = _body(response)["error"]
    assert error["code"] == "BAD_REQUEST"
    assert error["message"] == "tenantId is reserved"


def test_admin_can_update_reserved_platform_tenant(fake_state: dict[str, Any]) -> None:
    _seed_platform_tenant(fake_state)

    response = _invoke(
        _event(
            method="PATCH",
            tenant_id="platform",
            body={"displayName": "Platform Internal Updated"},
        )
    )

    assert response["statusCode"] == 200
    tenant = _body(response)["tenant"]
    assert tenant["tenantId"] == "platform"
    assert tenant["displayName"] == "Platform Internal Updated"


@pytest.mark.parametrize("roles", [[], ["Platform.Operator"]])
def test_non_admin_cannot_update_reserved_platform_tenant(
    fake_state: dict[str, Any], roles: list[str]
) -> None:
    _seed_platform_tenant(fake_state)

    response = _invoke(
        _event(
            method="PATCH",
            tenant_id="platform",
            body={"displayName": "Platform Internal Updated"},
            caller_tenant_id="platform",
            roles=roles,
            app_id="platform-agent",
        )
    )

    assert response["statusCode"] == 400
    error = _body(response)["error"]
    assert error["code"] == "BAD_REQUEST"
    assert error["message"] == "tenantId is reserved"


def test_admin_cannot_delete_reserved_platform_tenant(fake_state: dict[str, Any]) -> None:
    _seed_platform_tenant(fake_state)

    response = _invoke(_event(method="DELETE", tenant_id="platform"))

    assert response["statusCode"] == 403
    error = _body(response)["error"]
    assert error["code"] == "FORBIDDEN"
    assert error["message"] == "Reserved tenant IDs cannot be deleted"
    assert fake_state["db"].items[("TENANT#platform", "METADATA")]["status"] == "active"


@pytest.mark.parametrize("method", ["GET", "PATCH", "DELETE"])
@pytest.mark.parametrize("tenant_id", ["admin", "root", "system", "stub"])
def test_admin_cannot_use_non_platform_reserved_tenant_ids(
    fake_state: dict[str, Any],
    method: str,
    tenant_id: str,
) -> None:
    body = {"displayName": "Reserved"} if method == "PATCH" else None

    response = _invoke(_event(method=method, tenant_id=tenant_id, body=body))

    assert response["statusCode"] == 400
    error = _body(response)["error"]
    assert error["code"] == "BAD_REQUEST"
    assert error["message"] == "tenantId is reserved"


def test_create_tenant_detects_collision_after_tenant_id_normalization(
    fake_state: dict[str, Any],
) -> None:
    first = _invoke(
        _event(
            method="POST",
            body={
                "tenantId": "tenant-collision-001",
                "appId": "app-001",
                "displayName": "Acme Ltd",
                "tier": "standard",
                "ownerEmail": "owner@example.com",
                "ownerTeam": "team-acme",
                "accountId": "123456789012",
            },
        )
    )
    assert first["statusCode"] == 201

    second = _invoke(
        _event(
            method="POST",
            body={
                "tenantId": "TENANT-COLLISION-001",
                "appId": "app-001",
                "displayName": "Acme Ltd 2",
                "tier": "standard",
                "ownerEmail": "owner2@example.com",
                "ownerTeam": "team-acme",
                "accountId": "123456789012",
            },
        )
    )

    assert second["statusCode"] == 409
    error = _body(second)["error"]
    assert error["code"] == "CONFLICT"
    assert error["message"] == "Tenant already exists"


def test_read_own_tenant_allowed_and_enriched_with_usage(fake_state: dict[str, Any]) -> None:
    fake_state["db"].items[("TENANT#t-002", "METADATA")] = {
        "PK": "TENANT#t-002",
        "SK": "METADATA",
        "tenantId": "t-002",
        "appId": "app-002",
        "displayName": "Bravo",
        "tier": "basic",
        "status": "active",
        "createdAt": "2026-02-25T12:00:00Z",
        "updatedAt": "2026-02-25T12:00:00Z",
        "ownerEmail": "b@example.com",
        "ownerTeam": "team-b",
        "accountId": "123456789012",
        "fallbackRegion": "eu-central-1",
        "monthlyBudgetUsd": Decimal("50"),
    }

    response = _invoke(
        _event(
            method="GET",
            tenant_id="t-002",
            caller_tenant_id="t-002",
            roles=[],
            app_id="app-002",
            usage_identifier_key="usage-key-1",
        )
    )

    assert response["statusCode"] == 200
    tenant = _body(response)["tenant"]
    assert tenant["tenantId"] == "t-002"
    assert "fallbackRegion" not in tenant
    assert tenant["usage"]["requestsToday"] == 12
    assert tenant["usage"]["usageIdentifierKey"] == "usage-key-1"


def test_read_own_tenant_canonicalizes_mixed_case_path_tenant_id(
    fake_state: dict[str, Any],
) -> None:
    fake_state["db"].items[("TENANT#tenant-acme-001", "METADATA")] = {
        "PK": "TENANT#tenant-acme-001",
        "SK": "METADATA",
        "tenantId": "tenant-acme-001",
        "appId": "app-002",
        "displayName": "Bravo",
        "tier": "basic",
        "status": "active",
        "createdAt": "2026-02-25T12:00:00Z",
        "updatedAt": "2026-02-25T12:00:00Z",
        "ownerEmail": "b@example.com",
        "ownerTeam": "team-b",
        "accountId": "123456789012",
    }

    response = _invoke(
        _event(
            method="GET",
            tenant_id="Tenant-Acme-001",
            caller_tenant_id="tenant-acme-001",
            roles=[],
            app_id="app-002",
        )
    )

    assert response["statusCode"] == 200
    tenant = _body(response)["tenant"]
    assert tenant["tenantId"] == "tenant-acme-001"


@pytest.mark.parametrize("method", ["GET", "PATCH", "DELETE"])
@pytest.mark.parametrize("tenant_id", ["ab", "tenant--one", "tenant_one", "stub"])
def test_path_based_tenant_routes_reject_invalid_tenant_ids_deterministically(
    fake_state: dict[str, Any],
    method: str,
    tenant_id: str,
) -> None:
    body = {"tier": "premium"} if method == "PATCH" else None

    response = _invoke(_event(method=method, tenant_id=tenant_id, body=body))

    assert response["statusCode"] == 400
    error = _body(response)["error"]
    assert error["code"] == "BAD_REQUEST"
    assert error["message"].startswith("tenantId ")


def test_read_other_tenant_forbidden_for_non_admin(fake_state: dict[str, Any]) -> None:
    fake_state["db"].items[("TENANT#t-victim", "METADATA")] = {
        "PK": "TENANT#t-victim",
        "SK": "METADATA",
        "tenantId": "t-victim",
        "appId": "app-victim",
        "displayName": "Victim",
        "tier": "standard",
        "status": "active",
        "createdAt": "2026-02-25T12:00:00Z",
        "updatedAt": "2026-02-25T12:00:00Z",
        "ownerEmail": "v@example.com",
        "ownerTeam": "team-v",
        "accountId": "123456789012",
    }

    response = _invoke(
        _event(
            method="GET",
            tenant_id="t-victim",
            caller_tenant_id="t-attacker",
            roles=[],
            app_id="app-attacker",
        )
    )

    assert response["statusCode"] == 403
    error = _body(response)["error"]
    assert error["code"] == "FORBIDDEN"


def test_read_other_tenant_admin_without_platform_actor_rejected_by_factory(
    fake_state: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ = fake_state
    from src.tenant_api import db_factory, db_utils

    importlib.reload(db_factory)
    importlib.reload(db_utils)

    class UnexpectedTenantScopedDynamoDB:
        def __init__(self, tenant_context: Any) -> None:
            _ = tenant_context
            raise AssertionError("Tenant-scoped DAL client should not be constructed")

    monkeypatch.setattr(db_factory, "TenantScopedDynamoDB", UnexpectedTenantScopedDynamoDB)

    response = _invoke(
        _event(
            method="GET",
            tenant_id="t-victim",
            caller_tenant_id="t-admin",
            roles=["Platform.Admin"],
            app_id="app-admin",
        )
    )

    assert response["statusCode"] == 403
    error = _body(response)["error"]
    assert error["code"] == "FORBIDDEN"
    assert error["message"] == "Tenant-scoped client target mismatch"


def test_update_tier_admin_only_emits_tier_changed_event(fake_state: dict[str, Any]) -> None:
    fake_state["db"].items[("TENANT#t-003", "METADATA")] = {
        "PK": "TENANT#t-003",
        "SK": "METADATA",
        "tenantId": "t-003",
        "appId": "app-003",
        "displayName": "Charlie",
        "tier": "basic",
        "status": "active",
        "createdAt": "2026-02-25T12:00:00Z",
        "updatedAt": "2026-02-25T12:00:00Z",
        "ownerEmail": "c@example.com",
        "ownerTeam": "team-c",
        "accountId": "123456789012",
    }

    response = _invoke(_event(method="PATCH", tenant_id="t-003", body={"tier": "premium"}))

    assert response["statusCode"] == 200
    tenant = _body(response)["tenant"]
    assert tenant["tier"] == "premium"
    detail_type, detail = _last_event_detail(fake_state)
    assert detail_type == "tenant.tier_changed"
    assert detail["oldTier"] == "basic"
    assert detail["newTier"] == "premium"


def test_update_canonicalizes_mixed_case_path_tenant_id(fake_state: dict[str, Any]) -> None:
    fake_state["db"].items[("TENANT#tenant-acme-002", "METADATA")] = {
        "PK": "TENANT#tenant-acme-002",
        "SK": "METADATA",
        "tenantId": "tenant-acme-002",
        "appId": "app-003",
        "displayName": "Charlie",
        "tier": "basic",
        "status": "active",
        "createdAt": "2026-02-25T12:00:00Z",
        "updatedAt": "2026-02-25T12:00:00Z",
        "ownerEmail": "c@example.com",
        "ownerTeam": "team-c",
        "accountId": "123456789012",
    }

    response = _invoke(
        _event(method="PATCH", tenant_id="TENANT-ACME-002", body={"tier": "premium"})
    )

    assert response["statusCode"] == 200
    tenant = _body(response)["tenant"]
    assert tenant["tenantId"] == "tenant-acme-002"
    assert tenant["tier"] == "premium"


def test_delete_is_soft_delete_with_30_day_retention_and_event(
    fake_state: dict[str, Any],
    fixed_now: datetime,
) -> None:
    fake_state["db"].items[("TENANT#t-004", "METADATA")] = {
        "PK": "TENANT#t-004",
        "SK": "METADATA",
        "tenantId": "t-004",
        "appId": "app-004",
        "displayName": "Delta",
        "tier": "standard",
        "status": "active",
        "createdAt": "2026-02-25T12:00:00Z",
        "updatedAt": "2026-02-25T12:00:00Z",
        "ownerEmail": "d@example.com",
        "ownerTeam": "team-d",
        "accountId": "123456789012",
    }

    response = _invoke(_event(method="DELETE", tenant_id="t-004"))

    assert response["statusCode"] == 200
    tenant = _body(response)["tenant"]
    assert tenant["status"] == "deleted"
    expected_purge = int((fixed_now + timedelta(days=30)).timestamp())
    assert tenant["purgeAtEpochSeconds"] == expected_purge
    detail_type, detail = _last_event_detail(fake_state)
    assert detail_type == "tenant.deleted"
    assert detail["retentionDays"] == 30
    assert detail["purgeAtEpochSeconds"] == expected_purge


def test_delete_canonicalizes_mixed_case_path_tenant_id(
    fake_state: dict[str, Any],
    fixed_now: datetime,
) -> None:
    fake_state["db"].items[("TENANT#tenant-acme-003", "METADATA")] = {
        "PK": "TENANT#tenant-acme-003",
        "SK": "METADATA",
        "tenantId": "tenant-acme-003",
        "appId": "app-004",
        "displayName": "Delta",
        "tier": "standard",
        "status": "active",
        "createdAt": "2026-02-25T12:00:00Z",
        "updatedAt": "2026-02-25T12:00:00Z",
        "ownerEmail": "d@example.com",
        "ownerTeam": "team-d",
        "accountId": "123456789012",
    }

    response = _invoke(_event(method="DELETE", tenant_id="Tenant-Acme-003"))

    assert response["statusCode"] == 200
    tenant = _body(response)["tenant"]
    assert tenant["tenantId"] == "tenant-acme-003"
    assert tenant["status"] == "deleted"
    assert tenant["purgeAtEpochSeconds"] == int((fixed_now + timedelta(days=30)).timestamp())


def test_list_tenants_admin_only(fake_state: dict[str, Any]) -> None:
    fake_state["db"].items[("TENANT#t-1", "METADATA")] = {
        "PK": "TENANT#t-1",
        "SK": "METADATA",
        "tenantId": "t-1",
        "status": "active",
        "tier": "basic",
    }
    fake_state["db"].items[("TENANT#t-2", "METADATA")] = {
        "PK": "TENANT#t-2",
        "SK": "METADATA",
        "tenantId": "t-2",
        "status": "active",
        "tier": "premium",
    }

    # 1. Admin list all
    response = _invoke(_event(method="GET", tenant_id=None))
    assert response["statusCode"] == 200
    items = _body(response)["items"]
    assert len(items) == 2

    # 2. Non-admin only sees own
    response = _invoke(
        _event(method="GET", tenant_id=None, caller_tenant_id="t-1", roles=[], app_id="app-1")
    )
    assert response["statusCode"] == 200
    items = _body(response)["items"]
    assert len(items) == 1
    assert items[0]["tenantId"] == "t-1"


def test_list_tenants_pagination_limit_and_token(fake_state: dict[str, Any]) -> None:
    for i in range(1, 6):
        fake_state["db"].items[(f"TENANT#t-{i}", "METADATA")] = {
            "PK": f"TENANT#t-{i}",
            "SK": "METADATA",
            "tenantId": f"t-{i}",
            "status": "active",
            "tier": "basic",
        }
        # non-METADATA items must be excluded from list results
        fake_state["db"].items[(f"TENANT#t-{i}", "INVITE#inv-1")] = {
            "PK": f"TENANT#t-{i}",
            "SK": "INVITE#inv-1",
        }

    # Exhaust all pages with a small limit and collect every tenant ID returned.
    # DynamoDB Limit applies before SK filtering, so each page may return fewer
    # METADATA items than the limit; all items must nonetheless be reachable.
    all_ids: set[str] = set()
    qs: dict[str, str] = {"limit": "2"}
    page_count = 0
    while True:
        ev = _event(method="GET", tenant_id=None)
        ev["queryStringParameters"] = dict(qs)
        resp = _invoke(ev)
        assert resp["statusCode"] == 200
        b = _body(resp)
        # All returned items must be tenant records, not INVITE records
        for item in b["items"]:
            assert "tenantId" in item
        all_ids.update(item["tenantId"] for item in b["items"])
        page_count += 1
        if not b["nextToken"]:
            break
        qs["nextToken"] = b["nextToken"]
        assert page_count < 20, "pagination loop did not terminate"

    assert all_ids == {"t-1", "t-2", "t-3", "t-4", "t-5"}


def test_list_tenants_invalid_limit_rejected(fake_state: dict[str, Any]) -> None:
    event = _event(method="GET", tenant_id=None)
    event["queryStringParameters"] = {"limit": "0"}
    response = _invoke(event)
    assert response["statusCode"] == 400

    event["queryStringParameters"] = {"limit": "101"}
    response = _invoke(event)
    assert response["statusCode"] == 400


def test_list_tenants_invalid_token_rejected(fake_state: dict[str, Any]) -> None:
    event = _event(method="GET", tenant_id=None)
    event["queryStringParameters"] = {"nextToken": "not-valid-base64!!"}
    response = _invoke(event)
    assert response["statusCode"] == 400


def test_list_tenants_nexttoken_is_opaque(fake_state: dict[str, Any]) -> None:
    import base64
    import json as _json

    # Seed enough items so that limit=1 guarantees a nextToken on the first page
    for i in range(1, 4):
        fake_state["db"].items[(f"TENANT#t-{i}", "METADATA")] = {
            "PK": f"TENANT#t-{i}",
            "SK": "METADATA",
            "tenantId": f"t-{i}",
            "status": "active",
            "tier": "basic",
        }

    # Find a page that actually returns a nextToken (scan Limit=1 may hit a non-METADATA item
    # first, but will eventually produce a token because items remain)
    token = None
    qs: dict[str, str] = {"limit": "1"}
    for _ in range(10):
        ev = _event(method="GET", tenant_id=None)
        ev["queryStringParameters"] = dict(qs)
        resp = _invoke(ev)
        assert resp["statusCode"] == 200
        b = _body(resp)
        if b["nextToken"]:
            token = b["nextToken"]
            break
        if not b["nextToken"]:
            break

    assert token is not None, "expected at least one page with a nextToken"
    # Token must be decodable base64 JSON and contain a DynamoDB resume key
    decoded = _json.loads(base64.urlsafe_b64decode(token.encode()))
    assert "PK" in decoded


def test_list_tenants_status_filter_with_pagination(fake_state: dict[str, Any]) -> None:
    fake_state["db"].items[("TENANT#t-a", "METADATA")] = {
        "PK": "TENANT#t-a",
        "SK": "METADATA",
        "tenantId": "t-a",
        "status": "active",
        "tier": "basic",
    }
    fake_state["db"].items[("TENANT#t-s", "METADATA")] = {
        "PK": "TENANT#t-s",
        "SK": "METADATA",
        "tenantId": "t-s",
        "status": "suspended",
        "tier": "basic",
    }

    event = _event(method="GET", tenant_id=None)
    event["queryStringParameters"] = {"status": "active"}
    response = _invoke(event)
    assert response["statusCode"] == 200
    items = _body(response)["items"]
    assert all(i["status"] == "active" for i in items)
    assert any(i["tenantId"] == "t-a" for i in items)
    assert not any(i["tenantId"] == "t-s" for i in items)


def test_audit_export_writes_real_s3_export_and_returns_presigned_url(
    fake_state: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.tenant_api import tenant_lifecycle

    fake_state["db"].items[("TENANT#t-005", "METADATA")] = {
        "PK": "TENANT#t-005",
        "SK": "METADATA",
        "tenantId": "t-005",
        "status": "active",
        "appId": "app-005",
    }
    fake_state["db"].items[("TENANT#t-005", "INV#2026-02-25T10:00:00Z#inv-001")] = {
        "PK": "TENANT#t-005",
        "SK": "INV#2026-02-25T10:00:00Z#inv-001",
        "tenantId": "t-005",
        "appId": "app-005",
        "invocationId": "inv-001",
        "timestamp": "2026-02-25T10:00:00Z",
        "status": "success",
    }
    fake_state["db"].items[("TENANT#t-005", "INV#2026-02-25T13:00:00Z#inv-002")] = {
        "PK": "TENANT#t-005",
        "SK": "INV#2026-02-25T13:00:00Z#inv-002",
        "tenantId": "t-005",
        "appId": "app-005",
        "invocationId": "inv-002",
        "timestamp": "2026-02-25T13:00:00Z",
        "status": "success",
    }

    fake_s3 = FakeTenantScopedS3()
    monkeypatch.setattr(tenant_lifecycle.secrets, "token_hex", lambda _n: "feedfacecafebeef")
    monkeypatch.setattr(tenant_lifecycle.db_factory, "s3_for_tenant", lambda **_kwargs: fake_s3)

    event = _event(method="GET", tenant_id="t-005")
    event["path"] = "/v1/tenants/t-005/audit-export"
    event["queryStringParameters"] = {
        "start": "2026-02-25T09:00:00Z",
        "end": "2026-02-25T11:00:00Z",
    }
    response = _invoke(event)

    assert response["statusCode"] == 200
    body = _body(response)
    assert body["tenantId"] == "t-005"
    assert body["downloadUrl"].startswith("https://example.com/download/")
    assert body["expiresAt"] == "2026-02-25T12:30:00Z"

    assert len(fake_s3.put_calls) == 1
    put_call = fake_s3.put_calls[0]
    assert put_call["bucket"] == "platform-audit-exports"
    assert (
        put_call["key"]
        == "tenants/t-005/audit-exports/audit-export-20260225T120000Z-feedfacecafebeef.json"
    )
    assert put_call["kwargs"]["ContentType"] == "application/json"

    exported_payload = json.loads(put_call["body"].decode("utf-8"))
    assert exported_payload["tenantId"] == "t-005"
    assert exported_payload["recordCount"] == 1
    assert exported_payload["windowStart"] == "2026-02-25T09:00:00Z"
    assert exported_payload["windowEnd"] == "2026-02-25T11:00:00Z"
    assert exported_payload["records"][0]["invocationId"] == "inv-001"

    assert fake_s3.presign_calls == [
        {
            "bucket": "platform-audit-exports",
            "key": (
                "tenants/t-005/audit-exports/audit-export-20260225T120000Z-feedfacecafebeef.json"
            ),
            "expires_in": 1800,
            "client_method": "get_object",
        }
    ]


def test_audit_export_rejects_invalid_time_window(fake_state: dict[str, Any]) -> None:
    fake_state["db"].items[("TENANT#t-005", "METADATA")] = {
        "PK": "TENANT#t-005",
        "SK": "METADATA",
        "tenantId": "t-005",
        "status": "active",
        "appId": "app-005",
    }

    event = _event(method="GET", tenant_id="t-005")
    event["path"] = "/v1/tenants/t-005/audit-export"
    event["queryStringParameters"] = {
        "start": "2026-02-25T12:00:00Z",
        "end": "2026-02-25T11:00:00Z",
    }

    response = _invoke(event)

    assert response["statusCode"] == 400
    error = _body(response)["error"]
    assert error["code"] == "BAD_REQUEST"
    assert error["message"] == "start must be less than or equal to end"


def test_audit_export_requires_bucket_configuration(
    fake_state: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_state["db"].items[("TENANT#t-005", "METADATA")] = {
        "PK": "TENANT#t-005",
        "SK": "METADATA",
        "tenantId": "t-005",
        "status": "active",
        "appId": "app-005",
    }
    monkeypatch.delenv("AUDIT_EXPORT_BUCKET")

    event = _event(method="GET", tenant_id="t-005")
    event["path"] = "/v1/tenants/t-005/audit-export"

    response = _invoke(event)

    assert response["statusCode"] == 500
    error = _body(response)["error"]
    assert error["code"] == "INTERNAL_ERROR"
    assert error["message"] == "Audit export bucket is not configured"


def test_platform_failover_is_disabled_for_v0_2_topology(fake_state: dict[str, Any]) -> None:
    event = _event(
        method="POST",
        body={"targetRegion": "eu-central-1", "lockId": "lock-123"},
        caller_tenant_id="platform",
    )
    event["path"] = "/v1/platform/failover"
    response = _invoke(event)

    assert response["statusCode"] == 409
    assert _body(response)["error"]["code"] == "RUNTIME_FAILOVER_DISABLED"
    assert fake_state["deps"].ssm.put_calls == []


def test_platform_failover_requires_platform_admin_role(fake_state: dict[str, Any]) -> None:
    event = _event(
        method="POST",
        body={"targetRegion": "eu-central-1", "lockId": "lock-123"},
        caller_tenant_id="platform",
    )
    event["path"] = "/v1/platform/failover"

    # Non-admin forbidden
    event_non_admin = _event(
        method="POST",
        roles=[],
        body={"targetRegion": "x", "lockId": "y"},
        caller_tenant_id="platform",
    )
    event_non_admin["path"] = "/v1/platform/failover"
    response = _invoke(event_non_admin)
    assert response["statusCode"] == 403


def test_platform_route_requires_platform_tenant_context(fake_state: dict[str, Any]) -> None:
    event = _event(
        method="GET",
        caller_tenant_id="t-admin",
    )
    event["path"] = "/v1/platform/quota"

    response = _invoke(event)

    assert response["statusCode"] == 403


def test_health_route_returns_openapi_shape(fake_state: dict[str, Any]) -> None:
    event = _event(method="GET")
    event["path"] = "/v1/health"
    response = _invoke(event)

    assert response["statusCode"] == 200
    body = _body(response)
    assert body["status"] == "ok"
    assert "version" in body
    assert "runtimeRegion" in body
    assert "timestamp" in body


def test_sessions_route_returns_not_implemented(fake_state: dict[str, Any]) -> None:
    event = _event(method="GET", roles=[], caller_tenant_id="t-001", app_id="app-001")
    event["path"] = "/v1/sessions"
    event["queryStringParameters"] = {"limit": "5"}
    response = _invoke(event)

    assert response["statusCode"] == 501
    error = _body(response)["error"]
    assert error["code"] == "NOT_IMPLEMENTED"
    assert "tenant-backed session tracking" in error["message"]


def test_sessions_route_rejects_invalid_limit(fake_state: dict[str, Any]) -> None:
    event = _event(method="GET", roles=[], caller_tenant_id="t-001", app_id="app-001")
    event["path"] = "/v1/sessions"
    event["queryStringParameters"] = {"limit": "abc"}
    response = _invoke(event)

    assert response["statusCode"] == 400
    error = _body(response)["error"]
    assert error["code"] == "BAD_REQUEST"


def test_platform_quota_report(fake_state: dict[str, Any]) -> None:
    event = _event(method="GET", caller_tenant_id="platform")
    event["path"] = "/v1/platform/quota"
    response = _invoke(event)

    assert response["statusCode"] == 200
    body = _body(response)
    utilisation = body["utilisation"]
    assert utilisation == fake_state["deps"].platform_quota_client.response
    assert body["audit"]["actorTenantId"] == "platform"
    assert body["audit"]["operationType"] == "quota_report"
    assert fake_state["deps"].platform_quota_client.calls == [{"active_region": "eu-west-2"}]


def test_platform_quota_report_returns_explicit_aws_error(fake_state: dict[str, Any]) -> None:
    event = _event(method="GET", caller_tenant_id="platform")
    event["path"] = "/v1/platform/quota"
    object.__setattr__(
        fake_state["deps"],
        "platform_quota_client",
        FailingPlatformQuotaClient(
            tenant_api_handler.ClientError(
                {"Error": {"Code": "AccessDeniedException", "Message": "nope"}},
                "GetMetricStatistics",
            ),
        ),
    )

    response = _invoke(event)

    assert response["statusCode"] == 502
    error = _body(response)["error"]
    assert error["code"] == "AWS_CLIENT_ERROR"
    assert error["message"] == "AccessDeniedException"


def test_aws_platform_quota_client_reads_metrics_and_service_quotas() -> None:
    session = FakeAwsSession(
        cloudwatch_clients={
            "eu-west-2": FakeCloudWatchClient([{"Maximum": 7.0}, {"Maximum": 11.0}]),
            "eu-central-1": FakeCloudWatchClient([]),
        },
        service_quotas_clients={
            "eu-west-2": FakeServiceQuotasClient(
                [
                    {
                        "Quotas": [{"QuotaName": "Other quota", "Value": 1.0}],
                        "NextToken": "next-page",
                    },
                    {
                        "Quotas": [
                            {
                                "QuotaName": "Active session workloads per account",
                                "Value": 600.0,
                            }
                        ]
                    },
                ]
            ),
            "eu-central-1": FakeServiceQuotasClient(
                [
                    {
                        "Quotas": [
                            {
                                "QuotaName": "Active session workloads per account",
                                "Value": 400.0,
                            }
                        ]
                    }
                ]
            ),
        },
    )

    client = tenant_api_handler._AwsPlatformQuotaClient(session)

    utilisation = client.get_utilisation(active_region="eu-west-2")

    assert utilisation == [
        {
            "region": "eu-west-2",
            "quotaName": "ConcurrentSessions",
            "currentValue": 11.0,
            "limit": 600.0,
            "utilisationPercentage": 1.83,
        },
    ]
    eu_west_service_quotas = session.service_quotas_clients["eu-west-2"]
    assert eu_west_service_quotas.calls == [
        {"ServiceCode": "bedrock-agentcore"},
        {"ServiceCode": "bedrock-agentcore", "NextToken": "next-page"},
    ]


def test_aws_platform_quota_client_falls_back_to_documented_default_limit() -> None:
    session = FakeAwsSession(
        cloudwatch_clients={"eu-central-1": FakeCloudWatchClient([{"Maximum": 5.0}])},
        service_quotas_clients={"eu-central-1": FakeServiceQuotasClient([{"Quotas": []}])},
    )

    client = tenant_api_handler._AwsPlatformQuotaClient(session)

    utilisation = client.get_utilisation(active_region="eu-central-1")

    assert utilisation == [
        {
            "region": "eu-central-1",
            "quotaName": "ConcurrentSessions",
            "currentValue": 5.0,
            "limit": 500.0,
            "utilisationPercentage": 1.0,
        }
    ]


def test_platform_split_accounts_requires_platform_admin(fake_state: dict[str, Any]) -> None:
    event = _event(
        method="POST",
        body={"tier": "premium", "targetAccountId": "123456789012"},
        caller_tenant_id="platform",
    )
    event["path"] = "/v1/platform/quota/split-accounts"

    # 1. Platform.Admin succeeds
    response = _invoke(event)
    assert response["statusCode"] == 202
    assert "jobId" in _body(response)

    # 2. Platform.Operator (regular admin role in our _event helper) fails
    event_operator = _event(
        method="POST",
        roles=["Platform.Operator"],
        body={"tier": "premium", "targetAccountId": "123456789012"},
        caller_tenant_id="platform",
    )
    event_operator["path"] = "/v1/platform/quota/split-accounts"
    response = _invoke(event_operator)
    assert response["statusCode"] == 403


@pytest.mark.parametrize("target_account_id", ["12345678901", "1234567890123", "12345abc9012"])
def test_platform_split_accounts_rejects_invalid_target_account_id(
    fake_state: dict[str, Any],
    target_account_id: str,
) -> None:
    event = _event(
        method="POST",
        body={"tier": "premium", "targetAccountId": target_account_id},
        caller_tenant_id="platform",
    )
    event["path"] = "/v1/platform/quota/split-accounts"

    response = _invoke(event)

    assert response["statusCode"] == 400
    error = _body(response)["error"]
    assert error["code"] == "BAD_REQUEST"
    assert error["message"] == "targetAccountId must match ^[0-9]{12}$"


def test_parse_roles_accepts_json_encoded_array() -> None:
    parsed = tenant_api_handler._parse_roles('["Platform.Admin","Platform.Operator"]')
    assert parsed == frozenset({"Platform.Admin", "Platform.Operator"})


def test_create_tenant_allows_json_encoded_admin_roles(fake_state: dict[str, Any]) -> None:
    response = _invoke(
        _event(
            method="POST",
            tenant_id=None,
            roles='["Platform.Admin"]',
            body={
                "tenantId": "t-json-001",
                "appId": "app-json-001",
                "displayName": "Json Role Tenant",
                "tier": "basic",
                "ownerEmail": "json@example.com",
                "ownerTeam": "team-json",
                "accountId": "123456789012",
            },
        )
    )

    assert response["statusCode"] == 201


def test_rotate_api_key_for_own_tenant_requires_self_service_admin_role(
    fake_state: dict[str, Any],
) -> None:
    fake_state["db"].items[("TENANT#t-rotate", "METADATA")] = {
        "PK": "TENANT#t-rotate",
        "SK": "METADATA",
        "tenantId": "t-rotate",
        "appId": "app-rotate",
        "displayName": "Rotate",
        "tier": "standard",
        "status": "active",
        "createdAt": "2026-02-25T12:00:00Z",
        "updatedAt": "2026-02-25T12:00:00Z",
        "ownerEmail": "r@example.com",
        "ownerTeam": "team-r",
        "accountId": "123456789012",
        "apiKeySecretArn": (
            "arn:aws:secretsmanager:eu-west-2:111111111111:secret:platform/tenants/t-rotate/api-key"
        ),
    }
    event = _event(
        method="POST",
        tenant_id="t-rotate",
        caller_tenant_id="t-rotate",
        roles=[],
        app_id="app-rotate",
    )
    event["path"] = "/v1/tenants/t-rotate/api-key/rotate"

    response = _invoke(event)

    assert response["statusCode"] == 403
    assert _body(response)["error"]["code"] == "FORBIDDEN"
    assert fake_state["deps"].secretsmanager.rotate_calls == []


def test_rotate_api_key_for_own_tenant_succeeds_for_platform_operator(
    fake_state: dict[str, Any],
) -> None:
    fake_state["db"].items[("TENANT#t-rotate", "METADATA")] = {
        "PK": "TENANT#t-rotate",
        "SK": "METADATA",
        "tenantId": "t-rotate",
        "appId": "app-rotate",
        "displayName": "Rotate",
        "tier": "standard",
        "status": "active",
        "createdAt": "2026-02-25T12:00:00Z",
        "updatedAt": "2026-02-25T12:00:00Z",
        "ownerEmail": "r@example.com",
        "ownerTeam": "team-r",
        "accountId": "123456789012",
        "apiKeySecretArn": (
            "arn:aws:secretsmanager:eu-west-2:111111111111:secret:platform/tenants/t-rotate/api-key"
        ),
    }
    event = _event(
        method="POST",
        tenant_id="t-rotate",
        caller_tenant_id="t-rotate",
        roles=["Platform.Operator"],
        app_id="app-rotate",
    )
    event["path"] = "/v1/tenants/t-rotate/api-key/rotate"

    response = _invoke(event)

    assert response["statusCode"] == 200
    body = _body(response)
    assert body["tenantId"] == "t-rotate"
    assert body["versionId"] == "ver-rotated-001"
    rotate_calls = fake_state["deps"].secretsmanager.rotate_calls
    assert len(rotate_calls) == 1
    assert rotate_calls[0]["SecretId"].endswith("/t-rotate/api-key")
    detail_type, detail = _last_event_detail(fake_state)
    assert detail_type == "tenant.api_key_rotated"
    assert detail["tenantId"] == "t-rotate"


def test_rotate_api_key_canonicalizes_mixed_case_path_tenant_id(
    fake_state: dict[str, Any],
) -> None:
    fake_state["db"].items[("TENANT#tenant-rotate-001", "METADATA")] = {
        "PK": "TENANT#tenant-rotate-001",
        "SK": "METADATA",
        "tenantId": "tenant-rotate-001",
        "appId": "app-rotate",
        "displayName": "Rotate",
        "tier": "standard",
        "status": "active",
        "createdAt": "2026-02-25T12:00:00Z",
        "updatedAt": "2026-02-25T12:00:00Z",
        "ownerEmail": "r@example.com",
        "ownerTeam": "team-r",
        "accountId": "123456789012",
        "apiKeySecretArn": (
            "arn:aws:secretsmanager:eu-west-2:111111111111:secret:"
            "platform/tenants/tenant-rotate-001/api-key"
        ),
    }
    event = _event(
        method="POST",
        tenant_id="Tenant-Rotate-001",
        caller_tenant_id="tenant-rotate-001",
        roles=["Platform.Operator"],
        app_id="app-rotate",
    )
    event["path"] = "/v1/tenants/Tenant-Rotate-001/api-key/rotate"

    response = _invoke(event)

    assert response["statusCode"] == 200
    body = _body(response)
    assert body["tenantId"] == "tenant-rotate-001"
    rotate_calls = fake_state["deps"].secretsmanager.rotate_calls
    assert rotate_calls[0]["SecretId"].endswith("/tenant-rotate-001/api-key")


def test_rotate_api_key_cross_tenant_forbidden(fake_state: dict[str, Any]) -> None:
    fake_state["db"].items[("TENANT#t-owner", "METADATA")] = {
        "PK": "TENANT#t-owner",
        "SK": "METADATA",
        "tenantId": "t-owner",
        "appId": "app-owner",
        "status": "active",
        "apiKeySecretArn": (
            "arn:aws:secretsmanager:eu-west-2:111111111111:secret:platform/tenants/t-owner/api-key"
        ),
    }
    event = _event(
        method="POST",
        tenant_id="t-owner",
        caller_tenant_id="t-attacker",
        roles=[],
        app_id="app-attacker",
    )
    event["path"] = "/v1/tenants/t-owner/api-key/rotate"

    response = _invoke(event)

    assert response["statusCode"] == 403
    assert _body(response)["error"]["code"] == "FORBIDDEN"


def test_rotate_api_key_cross_tenant_self_service_admin_forbidden(
    fake_state: dict[str, Any],
) -> None:
    fake_state["db"].items[("TENANT#t-owner", "METADATA")] = {
        "PK": "TENANT#t-owner",
        "SK": "METADATA",
        "tenantId": "t-owner",
        "appId": "app-owner",
        "status": "active",
        "apiKeySecretArn": (
            "arn:aws:secretsmanager:eu-west-2:111111111111:secret:platform/tenants/t-owner/api-key"
        ),
    }
    event = _event(
        method="POST",
        tenant_id="t-owner",
        caller_tenant_id="t-attacker",
        roles=["SelfService.Admin"],
        app_id="app-attacker",
    )
    event["path"] = "/v1/tenants/t-owner/api-key/rotate"

    response = _invoke(event)

    assert response["statusCode"] == 403
    assert _body(response)["error"]["code"] == "FORBIDDEN"
    assert fake_state["deps"].secretsmanager.rotate_calls == []


def test_rotate_api_key_cross_tenant_platform_actor_succeeds(
    fake_state: dict[str, Any],
) -> None:
    fake_state["db"].items[("TENANT#t-owner", "METADATA")] = {
        "PK": "TENANT#t-owner",
        "SK": "METADATA",
        "tenantId": "t-owner",
        "appId": "app-owner",
        "status": "active",
        "apiKeySecretArn": (
            "arn:aws:secretsmanager:eu-west-2:111111111111:secret:platform/tenants/t-owner/api-key"
        ),
    }
    event = _event(
        method="POST",
        tenant_id="t-owner",
        caller_tenant_id="platform",
        roles=["Platform.Operator"],
        app_id="platform-admin",
    )
    event["path"] = "/v1/tenants/t-owner/api-key/rotate"

    response = _invoke(event)

    assert response["statusCode"] == 200
    assert _body(response)["tenantId"] == "t-owner"
    assert len(fake_state["deps"].secretsmanager.rotate_calls) == 1


def test_invite_user_for_own_tenant_requires_self_service_admin_role(
    fake_state: dict[str, Any],
) -> None:
    fake_state["db"].items[("TENANT#t-invite", "METADATA")] = {
        "PK": "TENANT#t-invite",
        "SK": "METADATA",
        "tenantId": "t-invite",
        "appId": "app-invite",
        "status": "active",
    }
    event = _event(
        method="POST",
        tenant_id="t-invite",
        caller_tenant_id="t-invite",
        roles=[],
        app_id="app-invite",
        body={"email": "new.user@example.com", "role": "Agent.Invoke"},
    )
    event["path"] = "/v1/tenants/t-invite/users/invite"

    response = _invoke(event)

    assert response["statusCode"] == 403
    assert _body(response)["error"]["code"] == "FORBIDDEN"


def test_invite_user_for_own_tenant_succeeds_for_platform_operator(
    fake_state: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_state["db"].items[("TENANT#t-invite", "METADATA")] = {
        "PK": "TENANT#t-invite",
        "SK": "METADATA",
        "tenantId": "t-invite",
        "appId": "app-invite",
        "status": "active",
    }
    observed_invites: list[dict[str, Any]] = []

    def _assert_invite_persisted_before_event(
        deps: Any,
        *,
        detail_type: str,
        detail: dict[str, Any],
    ) -> None:
        invite_key = ("TENANT#t-invite", f"INVITE#{detail['inviteId']}")
        invite_record = fake_state["db"].items.get(invite_key)
        assert invite_record is not None
        observed_invites.append(dict(invite_record))
        deps.events.put_events(
            Entries=[
                {
                    "DetailType": detail_type,
                    "Detail": json.dumps(detail),
                }
            ]
        )

    monkeypatch.setattr(
        tenant_lifecycle.events,
        "put_event",
        _assert_invite_persisted_before_event,
    )
    event = _event(
        method="POST",
        tenant_id="t-invite",
        caller_tenant_id="t-invite",
        roles=["Platform.Operator"],
        app_id="app-invite",
        body={"email": "new.user@example.com", "role": "Agent.Invoke"},
    )
    event["path"] = "/v1/tenants/t-invite/users/invite"

    response = _invoke(event)

    assert response["statusCode"] == 202
    invite = _body(response)["invite"]
    assert invite["tenantId"] == "t-invite"
    assert invite["email"] == "new.user@example.com"
    assert invite["normalizedEmail"] == "new.user@example.com"
    assert invite["status"] == "pending"
    assert invite["notificationStatus"] == "sent"
    detail_type, detail = _last_event_detail(fake_state)
    assert detail_type == "tenant.user_invited"
    assert detail["tenantId"] == "t-invite"
    invite_record = fake_state["db"].items[("TENANT#t-invite", f"INVITE#{detail['inviteId']}")]
    assert invite_record["email"] == invite["email"]
    assert invite_record["actorSub"] == "user-123"
    assert invite_record["actorAppId"] == "app-invite"
    assert invite_record["notificationStatus"] == "sent"
    lookup_record = fake_state["db"].items[("TENANT#t-invite", "INVITEEMAIL#new.user@example.com")]
    assert lookup_record["inviteId"] == detail["inviteId"]
    assert len(observed_invites) == 1
    assert observed_invites[0]["inviteId"] == invite_record["inviteId"]
    assert observed_invites[0]["notificationStatus"] == "pending"


def test_invite_user_cross_tenant_self_service_admin_forbidden(
    fake_state: dict[str, Any],
) -> None:
    fake_state["db"].items[("TENANT#t-invite", "METADATA")] = {
        "PK": "TENANT#t-invite",
        "SK": "METADATA",
        "tenantId": "t-invite",
        "appId": "app-invite",
        "status": "active",
    }
    event = _event(
        method="POST",
        tenant_id="t-invite",
        caller_tenant_id="t-attacker",
        roles=["SelfService.Admin"],
        app_id="app-attacker",
        body={"email": "new.user@example.com", "role": "Agent.Invoke"},
    )
    event["path"] = "/v1/tenants/t-invite/users/invite"

    response = _invoke(event)

    assert response["statusCode"] == 403
    assert _body(response)["error"]["code"] == "FORBIDDEN"
    assert ("TENANT#t-invite", "INVITEEMAIL#new.user@example.com") not in fake_state["db"].items


def test_invite_user_cross_tenant_platform_actor_succeeds(
    fake_state: dict[str, Any],
) -> None:
    fake_state["db"].items[("TENANT#t-invite", "METADATA")] = {
        "PK": "TENANT#t-invite",
        "SK": "METADATA",
        "tenantId": "t-invite",
        "appId": "app-invite",
        "status": "active",
    }
    event = _event(
        method="POST",
        tenant_id="t-invite",
        caller_tenant_id="platform",
        roles=["Platform.Operator"],
        app_id="platform-admin",
        body={"email": "new.user@example.com", "role": "Agent.Invoke"},
    )
    event["path"] = "/v1/tenants/t-invite/users/invite"

    response = _invoke(event)

    assert response["statusCode"] == 202
    assert _body(response)["invite"]["tenantId"] == "t-invite"


def test_invite_user_returns_existing_pending_invite_for_normalized_email(
    fake_state: dict[str, Any],
) -> None:
    fake_state["db"].items[("TENANT#t-invite-existing", "METADATA")] = {
        "PK": "TENANT#t-invite-existing",
        "SK": "METADATA",
        "tenantId": "t-invite-existing",
        "appId": "app-invite-existing",
        "status": "active",
    }
    fake_state["db"].items[("TENANT#t-invite-existing", "INVITE#inv-existing")] = {
        "PK": "TENANT#t-invite-existing",
        "SK": "INVITE#inv-existing",
        "inviteId": "inv-existing",
        "tenantId": "t-invite-existing",
        "email": "existing.user@example.com",
        "normalizedEmail": "existing.user@example.com",
        "role": "Agent.Invoke",
        "status": "pending",
        "expiresAt": "2026-03-04T12:00:00+00:00",
        "ttl": 1772625600,
        "actorSub": "seed-user",
        "actorAppId": "seed-app",
        "notificationStatus": "sent",
    }
    fake_state["db"].items[
        ("TENANT#t-invite-existing", "INVITEEMAIL#existing.user@example.com")
    ] = {
        "PK": "TENANT#t-invite-existing",
        "SK": "INVITEEMAIL#existing.user@example.com",
        "tenantId": "t-invite-existing",
        "inviteId": "inv-existing",
        "normalizedEmail": "existing.user@example.com",
        "status": "pending",
        "expiresAt": "2026-03-04T12:00:00+00:00",
        "ttl": 1772625600,
    }

    event = _event(
        method="POST",
        tenant_id="t-invite-existing",
        caller_tenant_id="t-invite-existing",
        roles=["Platform.Operator"],
        app_id="app-invite-existing",
        body={"email": " Existing.User@Example.com ", "role": "Agent.Invoke"},
    )
    event["path"] = "/v1/tenants/t-invite-existing/users/invite"

    response = _invoke(event)

    assert response["statusCode"] == 202
    assert _body(response)["invite"]["inviteId"] == "inv-existing"
    assert fake_state["deps"].events.calls == []
    assert sum(1 for _, sk in fake_state["db"].items if sk.startswith("INVITE#")) == 1


@pytest.mark.parametrize(
    "role",
    ["Platform.Operator", "Platform.Admin", "Unknown.Role", "agent.invoke"],
)
def test_invite_user_rejects_non_tenant_scoped_roles(fake_state: dict[str, Any], role: str) -> None:
    fake_state["db"].items[("TENANT#t-invite-role", "METADATA")] = {
        "PK": "TENANT#t-invite-role",
        "SK": "METADATA",
        "tenantId": "t-invite-role",
        "appId": "app-invite-role",
        "status": "active",
    }
    event = _event(
        method="POST",
        tenant_id="t-invite-role",
        caller_tenant_id="t-invite-role",
        roles=["Platform.Operator"],
        app_id="app-invite-role",
        body={"email": "new.user@example.com", "role": role},
    )
    event["path"] = "/v1/tenants/t-invite-role/users/invite"

    response = _invoke(event)

    assert response["statusCode"] == 400
    error = _body(response)["error"]
    assert error["code"] == "BAD_REQUEST"
    assert error["message"] == "role must be one of: Agent.Invoke"


def test_invite_user_defaults_role_to_agent_invoke(fake_state: dict[str, Any]) -> None:
    fake_state["db"].items[("TENANT#t-invite-default", "METADATA")] = {
        "PK": "TENANT#t-invite-default",
        "SK": "METADATA",
        "tenantId": "t-invite-default",
        "appId": "app-invite-default",
        "status": "active",
    }
    event = _event(
        method="POST",
        tenant_id="t-invite-default",
        caller_tenant_id="t-invite-default",
        roles=["Platform.Operator"],
        app_id="app-invite-default",
        body={"email": "new.user@example.com"},
    )
    event["path"] = "/v1/tenants/t-invite-default/users/invite"

    response = _invoke(event)

    assert response["statusCode"] == 202
    assert _body(response)["invite"]["role"] == "Agent.Invoke"


def test_invite_user_canonicalizes_mixed_case_path_tenant_id(fake_state: dict[str, Any]) -> None:
    fake_state["db"].items[("TENANT#tenant-invite-001", "METADATA")] = {
        "PK": "TENANT#tenant-invite-001",
        "SK": "METADATA",
        "tenantId": "tenant-invite-001",
        "appId": "app-invite",
        "status": "active",
    }
    event = _event(
        method="POST",
        tenant_id="Tenant-Invite-001",
        caller_tenant_id="tenant-invite-001",
        roles=["Platform.Operator"],
        app_id="app-invite",
        body={"email": "new.user@example.com", "role": "Agent.Invoke"},
    )
    event["path"] = "/v1/tenants/Tenant-Invite-001/users/invite"

    response = _invoke(event)

    assert response["statusCode"] == 202
    invite = _body(response)["invite"]
    assert invite["tenantId"] == "tenant-invite-001"


@pytest.mark.parametrize(
    ("path", "tenant_id"),
    [
        ("/v1/tenants/stub/api-key/rotate", "stub"),
        ("/v1/tenants/tenant_one/users/invite", "tenant_one"),
        ("/v1/tenants/tenant--one/audit-export", "tenant--one"),
    ],
)
def test_tenant_subroutes_reject_invalid_path_tenant_ids_before_route_logic(
    fake_state: dict[str, Any],
    path: str,
    tenant_id: str,
) -> None:
    event = _event(
        method="POST" if path.endswith(("rotate", "invite")) else "GET",
        tenant_id=tenant_id,
        caller_tenant_id="tenant-owner-001",
        roles=["Platform.Admin"],
        body={"email": "new.user@example.com"} if path.endswith("invite") else None,
    )
    event["path"] = path

    response = _invoke(event)

    assert response["statusCode"] == 400
    error = _body(response)["error"]
    assert error["code"] == "BAD_REQUEST"
    assert error["message"].startswith("tenantId ")


def test_invite_user_requires_valid_email(fake_state: dict[str, Any]) -> None:
    fake_state["db"].items[("TENANT#t-invite-2", "METADATA")] = {
        "PK": "TENANT#t-invite-2",
        "SK": "METADATA",
        "tenantId": "t-invite-2",
        "appId": "app-invite-2",
        "status": "active",
    }
    event = _event(
        method="POST",
        tenant_id="t-invite-2",
        caller_tenant_id="t-invite-2",
        roles=["Platform.Operator"],
        app_id="app-invite-2",
        body={"email": "not-an-email"},
    )
    event["path"] = "/v1/tenants/t-invite-2/users/invite"

    response = _invoke(event)

    assert response["statusCode"] == 400
    assert _body(response)["error"]["code"] == "BAD_REQUEST"


def test_invite_user_returns_not_found_when_tenant_metadata_is_missing(
    fake_state: dict[str, Any],
) -> None:
    event = _event(
        method="POST",
        tenant_id="t-missing-invite",
        caller_tenant_id="t-missing-invite",
        roles=["Platform.Operator"],
        app_id="app-missing-invite",
        body={"email": "new.user@example.com", "role": "Agent.Invoke"},
    )
    event["path"] = "/v1/tenants/t-missing-invite/users/invite"

    response = _invoke(event)

    assert response["statusCode"] == 404
    assert _body(response)["error"]["code"] == "NOT_FOUND"
    assert all(sk != "INVITEEMAIL#new.user@example.com" for _, sk in fake_state["db"].items)
    assert all(not sk.startswith("INVITE#") for _, sk in fake_state["db"].items)
    assert fake_state["deps"].events.calls == []


def test_invite_user_records_notification_failure_after_persistence(
    fake_state: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_state["db"].items[("TENANT#t-invite-failure", "METADATA")] = {
        "PK": "TENANT#t-invite-failure",
        "SK": "METADATA",
        "tenantId": "t-invite-failure",
        "appId": "app-invite-failure",
        "status": "active",
    }

    def _failed_event(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"FailedEntryCount": 1, "Entries": [{"ErrorCode": "InternalFailure"}]}

    monkeypatch.setattr(tenant_lifecycle.events, "put_event", _failed_event)
    event = _event(
        method="POST",
        tenant_id="t-invite-failure",
        caller_tenant_id="t-invite-failure",
        roles=["Platform.Operator"],
        app_id="app-invite-failure",
        body={"email": "failed.user@example.com", "role": "Agent.Invoke"},
    )
    event["path"] = "/v1/tenants/t-invite-failure/users/invite"

    response = _invoke(event)

    assert response["statusCode"] == 502
    assert _body(response)["error"]["code"] == "EVENT_DELIVERY_FAILED"
    invite_record = next(
        item
        for (pk, sk), item in fake_state["db"].items.items()
        if pk == "TENANT#t-invite-failure" and sk.startswith("INVITE#")
    )
    assert invite_record["notificationStatus"] == "failed"
    assert invite_record["notificationError"] == "InternalFailure"


def test_invite_user_records_botocore_notification_failure_after_persistence(
    fake_state: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_state["db"].items[("TENANT#t-invite-endpoint-failure", "METADATA")] = {
        "PK": "TENANT#t-invite-endpoint-failure",
        "SK": "METADATA",
        "tenantId": "t-invite-endpoint-failure",
        "appId": "app-invite-endpoint-failure",
        "status": "active",
    }

    def _endpoint_failure(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise EndpointConnectionError(endpoint_url="https://events.eu-west-2.amazonaws.com")

    monkeypatch.setattr(tenant_lifecycle.events, "put_event", _endpoint_failure)
    event = _event(
        method="POST",
        tenant_id="t-invite-endpoint-failure",
        caller_tenant_id="t-invite-endpoint-failure",
        roles=["Platform.Operator"],
        app_id="app-invite-endpoint-failure",
        body={"email": "endpoint.user@example.com", "role": "Agent.Invoke"},
    )
    event["path"] = "/v1/tenants/t-invite-endpoint-failure/users/invite"

    response = _invoke(event)

    assert response["statusCode"] == 502
    assert _body(response)["error"]["code"] == "EVENT_DELIVERY_FAILED"
    invite_record = next(
        item
        for (pk, sk), item in fake_state["db"].items.items()
        if pk == "TENANT#t-invite-endpoint-failure" and sk.startswith("INVITE#")
    )
    assert invite_record["notificationStatus"] == "failed"
    assert invite_record["notificationError"] == "EndpointConnectionError"


def test_invite_user_retries_failed_notification_on_repeat_request(
    fake_state: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_state["db"].items[("TENANT#t-invite-retry", "METADATA")] = {
        "PK": "TENANT#t-invite-retry",
        "SK": "METADATA",
        "tenantId": "t-invite-retry",
        "appId": "app-invite-retry",
        "status": "active",
    }
    attempts = {"count": 0}

    def _flaky_event(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        attempts["count"] += 1
        if attempts["count"] == 1:
            return {"FailedEntryCount": 1, "Entries": [{"ErrorCode": "InternalFailure"}]}
        return {"FailedEntryCount": 0, "Entries": [{"EventId": "evt-1"}]}

    monkeypatch.setattr(tenant_lifecycle.events, "put_event", _flaky_event)
    event = _event(
        method="POST",
        tenant_id="t-invite-retry",
        caller_tenant_id="t-invite-retry",
        roles=["Platform.Operator"],
        app_id="app-invite-retry",
        body={"email": "retry.user@example.com", "role": "Agent.Invoke"},
    )
    event["path"] = "/v1/tenants/t-invite-retry/users/invite"

    first_response = _invoke(event)
    second_response = _invoke(event)

    assert first_response["statusCode"] == 502
    assert second_response["statusCode"] == 202
    first_invite_id = next(
        item["inviteId"]
        for (pk, sk), item in fake_state["db"].items.items()
        if pk == "TENANT#t-invite-retry" and sk.startswith("INVITE#")
    )
    assert _body(second_response)["invite"]["inviteId"] == first_invite_id
    assert attempts["count"] == 2
    invite_record = fake_state["db"].items[("TENANT#t-invite-retry", f"INVITE#{first_invite_id}")]
    assert invite_record["notificationStatus"] == "sent"
    assert invite_record["notificationError"] is None


def test_invite_user_duplicate_race_materializes_single_pending_invite(
    fake_state: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_state["db"].items[("TENANT#t-invite-race", "METADATA")] = {
        "PK": "TENANT#t-invite-race",
        "SK": "METADATA",
        "tenantId": "t-invite-race",
        "appId": "app-invite-race",
        "status": "active",
    }
    original_put_item = fake_state["db"].put_item
    race_state: dict[str, Any] = {"interleaved": False, "second_response": None}
    second_event = _event(
        method="POST",
        tenant_id="t-invite-race",
        caller_tenant_id="t-invite-race",
        roles=["Platform.Operator"],
        app_id="app-invite-race",
        body={"email": "race.user@example.com", "role": "Agent.Invoke"},
    )
    second_event["path"] = "/v1/tenants/t-invite-race/users/invite"

    def _interleave_second_request(
        table_name: str,
        item: dict[str, Any],
        *,
        condition_expression: str | None = None,
    ) -> dict[str, Any]:
        result = original_put_item(
            table_name,
            item,
            condition_expression=condition_expression,
        )
        if str(item["SK"]).startswith("INVITEEMAIL#") and not race_state["interleaved"]:
            race_state["interleaved"] = True
            race_state["second_response"] = _invoke(second_event)
        return result

    monkeypatch.setattr(fake_state["db"], "put_item", _interleave_second_request)
    first_event = _event(
        method="POST",
        tenant_id="t-invite-race",
        caller_tenant_id="t-invite-race",
        roles=["Platform.Operator"],
        app_id="app-invite-race",
        body={"email": "race.user@example.com", "role": "Agent.Invoke"},
    )
    first_event["path"] = "/v1/tenants/t-invite-race/users/invite"

    first_response = _invoke(first_event)

    assert first_response["statusCode"] == 202
    assert race_state["second_response"]["statusCode"] == 202
    invite_items = [
        item
        for (pk, sk), item in fake_state["db"].items.items()
        if pk == "TENANT#t-invite-race" and sk.startswith("INVITE#")
    ]
    assert len(invite_items) == 1
    assert len(fake_state["deps"].events.calls) == 1
    assert _body(first_response)["invite"]["inviteId"] == invite_items[0]["inviteId"]
    assert _body(race_state["second_response"])["invite"]["inviteId"] == invite_items[0]["inviteId"]


def test_invite_user_rolls_back_lookup_when_invite_write_fails_once(
    fake_state: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_state["db"].items[("TENANT#t-invite-write-fail", "METADATA")] = {
        "PK": "TENANT#t-invite-write-fail",
        "SK": "METADATA",
        "tenantId": "t-invite-write-fail",
        "appId": "app-invite-write-fail",
        "status": "active",
    }
    original_put_item = fake_state["db"].put_item
    state = {"failed": False}

    def _fail_first_invite_write(
        table_name: str,
        item: dict[str, Any],
        *,
        condition_expression: str | None = None,
    ) -> dict[str, Any]:
        if str(item["SK"]).startswith("INVITE#") and not state["failed"]:
            state["failed"] = True
            raise tenant_api_handler.ClientError(
                {
                    "Error": {
                        "Code": "ProvisionedThroughputExceededException",
                        "Message": "throttled",
                    }
                },
                "PutItem",
            )
        return original_put_item(
            table_name,
            item,
            condition_expression=condition_expression,
        )

    monkeypatch.setattr(fake_state["db"], "put_item", _fail_first_invite_write)
    event = _event(
        method="POST",
        tenant_id="t-invite-write-fail",
        caller_tenant_id="t-invite-write-fail",
        roles=["Platform.Operator"],
        app_id="app-invite-write-fail",
        body={"email": "retryable.user@example.com", "role": "Agent.Invoke"},
    )
    event["path"] = "/v1/tenants/t-invite-write-fail/users/invite"

    first_response = _invoke(event)
    assert first_response["statusCode"] == 502
    assert (
        "TENANT#t-invite-write-fail",
        "INVITEEMAIL#retryable.user@example.com",
    ) not in fake_state["db"].items

    second_response = _invoke(event)

    assert second_response["statusCode"] == 202
    assert (
        "TENANT#t-invite-write-fail",
        "INVITEEMAIL#retryable.user@example.com",
    ) in fake_state["db"].items


def test_invite_user_create_then_list_returns_persisted_invite(
    fake_state: dict[str, Any],
) -> None:
    fake_state["db"].items[("TENANT#t-create-list", "METADATA")] = {
        "PK": "TENANT#t-create-list",
        "SK": "METADATA",
        "tenantId": "t-create-list",
        "appId": "app-create-list",
        "status": "active",
    }

    create_event = _event(
        method="POST",
        tenant_id="t-create-list",
        caller_tenant_id="t-create-list",
        roles=["Platform.Operator"],
        app_id="app-create-list",
        body={"email": "Create.List@Example.com", "role": "Agent.Invoke"},
    )
    create_event["path"] = "/v1/tenants/t-create-list/users/invite"

    create_response = _invoke(create_event)

    assert create_response["statusCode"] == 202
    invite = _body(create_response)["invite"]

    list_event = _event(
        method="GET",
        tenant_id="t-create-list",
        caller_tenant_id="t-create-list",
        roles=["Platform.Operator"],
        app_id="app-create-list",
    )
    list_event["path"] = "/v1/tenants/t-create-list/users/invites"

    list_response = _invoke(list_event)

    assert list_response["statusCode"] == 200
    items = _body(list_response)["items"]
    assert len(items) == 1
    assert items[0]["inviteId"] == invite["inviteId"]
    assert items[0]["email"] == "create.list@example.com"


def test_webhook_management_requires_self_service_admin_role(
    fake_state: dict[str, Any],
) -> None:
    fake_state["db"].items[("TENANT#t-webhook", "METADATA")] = {
        "PK": "TENANT#t-webhook",
        "SK": "METADATA",
        "tenantId": "t-webhook",
        "appId": "app-webhook",
        "status": "active",
    }
    fake_state["db"].items[("TENANT#t-webhook", "WEBHOOK#wh-existing")] = {
        "PK": "TENANT#t-webhook",
        "SK": "WEBHOOK#wh-existing",
        "webhookId": "wh-existing",
        "tenantId": "t-webhook",
        "callbackUrl": "https://example.com/callback",
        "events": ["job.completed"],
        "status": "active",
    }

    register_event = _event(
        method="POST",
        tenant_id="t-webhook",
        caller_tenant_id="t-webhook",
        roles=["Agent.Invoke"],
        body={
            "callbackUrl": "https://example.com/callback",
            "events": ["job.completed"],
            "description": "My Webhook",
        },
    )
    register_event["path"] = "/v1/webhooks"

    register_response = _invoke(register_event)
    assert register_response["statusCode"] == 403

    list_event = _event(
        method="GET",
        tenant_id="t-webhook",
        caller_tenant_id="t-webhook",
        roles=["Agent.Invoke"],
    )
    list_event["path"] = "/v1/webhooks"

    list_response = _invoke(list_event)
    assert list_response["statusCode"] == 403

    delete_event = _event(
        method="DELETE",
        tenant_id="t-webhook",
        caller_tenant_id="t-webhook",
        roles=["Agent.Invoke"],
    )
    delete_event["path"] = "/v1/webhooks/wh-existing"

    delete_response = _invoke(delete_event)
    assert delete_response["statusCode"] == 403


def test_webhook_management_succeeds_for_platform_operator(fake_state: dict[str, Any]) -> None:
    fake_state["db"].items[("TENANT#t-webhook", "METADATA")] = {
        "PK": "TENANT#t-webhook",
        "SK": "METADATA",
        "tenant_id": "t-webhook",
        "app_id": "app-webhook",
        "status": "active",
    }

    # 1. Register a webhook
    event = _event(
        method="POST",
        tenant_id="t-webhook",
        caller_tenant_id="t-webhook",
        roles=["Platform.Operator"],
        body={
            "callbackUrl": "https://example.com/callback",
            "events": ["job.completed"],
            "description": "My Webhook",
        },
    )
    event["path"] = "/v1/webhooks"

    response = _invoke(event)
    assert response["statusCode"] == 201
    body = _body(response)
    webhook_id = body["webhookId"]
    assert body["callbackUrl"] == "https://example.com/callback"
    assert body["events"] == ["job.completed"]
    assert "createdAt" in body
    assert body["signatureHeader"] == "X-Platform-Signature"
    assert body["signatureAlgorithm"] == "HMAC-SHA256"

    # Verify database item
    db_item = fake_state["db"].items[("TENANT#t-webhook", f"WEBHOOK#{webhook_id}")]
    assert db_item["callback_url"] == "https://example.com/callback"
    assert db_item["events"] == ["job.completed"]
    assert db_item["status"] == "active"
    assert "signature_secret" in db_item

    # 2. List webhooks
    event = _event(
        method="GET",
        tenant_id="t-webhook",
        caller_tenant_id="t-webhook",
        roles=["Platform.Operator"],
    )
    event["path"] = "/v1/webhooks"

    response = _invoke(event)
    assert response["statusCode"] == 200
    body = _body(response)
    assert len(body["items"]) == 1
    assert body["items"][0]["webhookId"] == webhook_id

    # 3. Delete webhook
    event = _event(
        method="DELETE",
        tenant_id="t-webhook",
        caller_tenant_id="t-webhook",
        roles=["Platform.Operator"],
    )
    event["path"] = f"/v1/webhooks/{webhook_id}"

    response = _invoke(event)
    assert response["statusCode"] == 204

    # 4. Verify deleted
    event = _event(
        method="GET",
        tenant_id="t-webhook",
        caller_tenant_id="t-webhook",
        roles=["Platform.Operator"],
    )
    event["path"] = "/v1/webhooks"

    response = _invoke(event)
    body = _body(response)
    assert len(body["items"]) == 0


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/v1/tenants/t-webhook/webhooks", None),
        (
            "POST",
            "/v1/tenants/t-webhook/webhooks",
            {"callbackUrl": "https://example.com/callback", "events": ["job.completed"]},
        ),
        ("DELETE", "/v1/tenants/t-webhook/webhooks/wh-existing", None),
    ],
)
def test_webhook_management_cross_tenant_self_service_admin_forbidden(
    fake_state: dict[str, Any],
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    fake_state["db"].items[("TENANT#t-webhook", "METADATA")] = {
        "PK": "TENANT#t-webhook",
        "SK": "METADATA",
        "tenantId": "t-webhook",
        "appId": "app-webhook",
        "status": "active",
    }
    fake_state["db"].items[("TENANT#t-webhook", "WEBHOOK#wh-existing")] = {
        "PK": "TENANT#t-webhook",
        "SK": "WEBHOOK#wh-existing",
        "webhook_id": "wh-existing",
        "tenant_id": "t-webhook",
        "callback_url": "https://example.com/existing",
        "events": ["job.completed"],
        "status": "active",
    }
    event = _event(
        method=method,
        tenant_id="t-webhook",
        caller_tenant_id="t-attacker",
        roles=["SelfService.Admin"],
        body=body,
    )
    event["path"] = path

    response = _invoke(event)

    assert response["statusCode"] == 403
    assert _body(response)["error"]["code"] == "FORBIDDEN"
    assert ("TENANT#t-webhook", "WEBHOOK#wh-existing") in fake_state["db"].items
    assert len([key for key in fake_state["db"].items if key[1].startswith("WEBHOOK#")]) == 1


def test_webhook_management_cross_tenant_platform_actor_succeeds(
    fake_state: dict[str, Any],
) -> None:
    fake_state["db"].items[("TENANT#t-webhook", "METADATA")] = {
        "PK": "TENANT#t-webhook",
        "SK": "METADATA",
        "tenantId": "t-webhook",
        "appId": "app-webhook",
        "status": "active",
    }
    event = _event(
        method="POST",
        tenant_id="t-webhook",
        caller_tenant_id="platform",
        roles=["Platform.Operator"],
        body={
            "callbackUrl": "https://example.com/callback",
            "events": ["job.completed"],
        },
    )
    event["path"] = "/v1/tenants/t-webhook/webhooks"

    response = _invoke(event)

    assert response["statusCode"] == 201
    assert _body(response)["callbackUrl"] == "https://example.com/callback"


def test_webhook_registration_validation(fake_state: dict[str, Any]) -> None:
    fake_state["db"].items[("TENANT#t-val", "METADATA")] = {
        "PK": "TENANT#t-val",
        "SK": "METADATA",
        "tenant_id": "t-val",
        "app_id": "app-val",
        "status": "active",
    }

    test_cases = [
        ("Missing callbackUrl", {"events": ["job.completed"]}, 400),
        ("Invalid callbackUrl", {"callbackUrl": "not-a-url", "events": ["job.completed"]}, 422),
        ("Missing events", {"callbackUrl": "https://example.com"}, 400),
        ("Empty events", {"callbackUrl": "https://example.com", "events": []}, 400),
        (
            "Unsupported event",
            {"callbackUrl": "https://example.com", "events": ["bad.event"]},
            422,
        ),
        (
            "Duplicate events",
            {"callbackUrl": "https://example.com", "events": ["job.completed", "job.completed"]},
            400,
        ),
        (
            "Description too long",
            {
                "callbackUrl": "https://example.com",
                "events": ["job.completed"],
                "description": "a" * 257,
            },
            422,
        ),
    ]

    for name, payload, expected_status in test_cases:
        event = _event(
            method="POST",
            tenant_id="t-val",
            caller_tenant_id="t-val",
            roles=["SelfService.Admin"],
            body=payload,
        )
        event["path"] = "/v1/webhooks"
        response = _invoke(event)
        assert response["statusCode"] == expected_status, f"Test '{name}' failed"


def test_list_invites_succeeds(fake_state: dict[str, Any]) -> None:
    fake_state["db"].items[("TENANT#t-list-invites", "METADATA")] = {
        "PK": "TENANT#t-list-invites",
        "SK": "METADATA",
        "tenantId": "t-list-invites",
        "appId": "app-list-invites",
        "status": "active",
    }
    fake_state["db"].items[("TENANT#t-list-invites", "INVITE#inv-1")] = {
        "PK": "TENANT#t-list-invites",
        "SK": "INVITE#inv-1",
        "inviteId": "inv-1",
        "email": "user1@example.com",
        "status": "pending",
    }

    event = _event(
        method="GET",
        tenant_id="t-list-invites",
        caller_tenant_id="t-list-invites",
        roles=["Platform.Operator"],
    )
    event["path"] = "/v1/tenants/t-list-invites/users/invites"

    response = _invoke(event)
    assert response["statusCode"] == 200
    body = _body(response)
    assert len(body["items"]) == 1
    assert body["items"][0]["inviteId"] == "inv-1"


def test_list_invites_cross_tenant_platform_role_without_platform_actor_forbidden(
    fake_state: dict[str, Any],
) -> None:
    fake_state["db"].items[("TENANT#t-list-invites", "METADATA")] = {
        "PK": "TENANT#t-list-invites",
        "SK": "METADATA",
        "tenantId": "t-list-invites",
        "appId": "app-list-invites",
        "status": "active",
    }
    fake_state["db"].items[("TENANT#t-list-invites", "INVITE#inv-1")] = {
        "PK": "TENANT#t-list-invites",
        "SK": "INVITE#inv-1",
        "inviteId": "inv-1",
        "email": "user1@example.com",
        "status": "pending",
    }
    event = _event(
        method="GET",
        tenant_id="t-list-invites",
        caller_tenant_id="t-attacker",
        roles=["Platform.Admin"],
    )
    event["path"] = "/v1/tenants/t-list-invites/users/invites"

    response = _invoke(event)

    assert response["statusCode"] == 403
    assert _body(response)["error"]["code"] == "FORBIDDEN"


def test_list_invites_cross_tenant_platform_actor_succeeds(
    fake_state: dict[str, Any],
) -> None:
    fake_state["db"].items[("TENANT#t-list-invites", "METADATA")] = {
        "PK": "TENANT#t-list-invites",
        "SK": "METADATA",
        "tenantId": "t-list-invites",
        "appId": "app-list-invites",
        "status": "active",
    }
    fake_state["db"].items[("TENANT#t-list-invites", "INVITE#inv-1")] = {
        "PK": "TENANT#t-list-invites",
        "SK": "INVITE#inv-1",
        "inviteId": "inv-1",
        "email": "user1@example.com",
        "status": "pending",
    }
    event = _event(
        method="GET",
        tenant_id="t-list-invites",
        caller_tenant_id="platform",
        roles=["Platform.Admin"],
    )
    event["path"] = "/v1/tenants/t-list-invites/users/invites"

    response = _invoke(event)

    assert response["statusCode"] == 200
    assert _body(response)["items"][0]["inviteId"] == "inv-1"


def test_lambda_rollback_finds_previous_version_and_updates_alias(
    fake_state: dict[str, Any],
) -> None:
    event = _event(
        method="POST",
        body={"functionSuffix": "bridge", "aliasName": "live"},
        roles=["Platform.Admin"],
        caller_tenant_id="platform",
    )
    event["path"] = "/v1/platform/ops/lambda-rollback"

    response = _invoke(event)

    assert response["statusCode"] == 200
    body = _body(response)
    assert body["functionName"] == "platform-bridge-dev"
    assert body["fromVersion"] == "10"
    assert body["toVersion"] == "2"
    assert body["status"] == "rolled_back"

    update_calls = fake_state["deps"].awslambda.update_calls
    assert len(update_calls) == 1
    assert update_calls[0]["FunctionName"] == "platform-bridge-dev"
    assert update_calls[0]["FunctionVersion"] == "2"
    assert "Rollback from 10 to 2" in update_calls[0]["Description"]


def test_lambda_rollback_rejects_oldest_version(fake_state: dict[str, Any]) -> None:
    fake_state["deps"].awslambda.aliases["platform-bridge-dev"]["live"] = "1"
    event = _event(
        method="POST",
        body={"functionSuffix": "bridge", "aliasName": "live"},
        roles=["Platform.Admin"],
        caller_tenant_id="platform",
    )
    event["path"] = "/v1/platform/ops/lambda-rollback"

    response = _invoke(event)

    assert response["statusCode"] == 409
    assert _body(response)["error"]["code"] == "NO_PREVIOUS_VERSION"


def test_lambda_rollback_requires_admin_role(fake_state: dict[str, Any]) -> None:
    event = _event(
        method="POST",
        body={"functionSuffix": "bridge"},
        roles=["Platform.Operator"],  # Not sufficient for this route
        caller_tenant_id="platform",
    )
    event["path"] = "/v1/platform/ops/lambda-rollback"

    response = _invoke(event)
    assert response["statusCode"] == 403


def test_lambda_rollback_returns_404_on_missing_function(fake_state: dict[str, Any]) -> None:
    event = _event(
        method="POST",
        body={"functionSuffix": "non-existent"},
        roles=["Platform.Admin"],
        caller_tenant_id="platform",
    )
    event["path"] = "/v1/platform/ops/lambda-rollback"

    response = _invoke(event)
    assert response["statusCode"] == 404


def test_platform_register_agent_defaults_to_built(fake_state: dict[str, Any]) -> None:
    event = _event(
        method="POST",
        body={
            "agentName": "echo-agent",
            "version": "1.2.0",
            "ownerTeam": "platform",
            "tierMinimum": "basic",
            "layerHash": "hash-120",
            "layerS3Key": "layers/1.2.0.zip",
            "scriptS3Key": "scripts/1.2.0.zip",
            "invocationMode": "sync",
        },
        roles=["Platform.Admin"],
        caller_tenant_id="platform",
    )
    event["path"] = "/v1/platform/agents"

    response = _invoke(event)

    assert response["statusCode"] == 201
    item = fake_state["db"].items[("AGENT#echo-agent", "VERSION#1.2.0")]
    assert item["status"] == "built"
    assert "approved_by" not in item


def test_platform_register_agent_rejects_non_platform_admin_context(
    fake_state: dict[str, Any],
) -> None:
    event = _event(
        method="POST",
        body={
            "agentName": "echo-agent",
            "version": "1.2.11",
            "ownerTeam": "platform",
            "tierMinimum": "basic",
            "layerHash": "hash-1211",
            "layerS3Key": "layers/1.2.11.zip",
            "scriptS3Key": "scripts/1.2.11.zip",
            "invocationMode": "sync",
        },
        roles=["Platform.Admin"],
        caller_tenant_id="customer-admin",
    )
    event["path"] = "/v1/platform/agents"

    response = _invoke(event)

    assert response["statusCode"] == 403
    assert _body(response)["error"]["code"] == "FORBIDDEN"
    assert ("AGENT#echo-agent", "VERSION#1.2.11") not in fake_state["db"].items


@pytest.mark.parametrize("roles", [["Platform.Admin"], ["Platform.Operator"]])
def test_platform_list_agents_uses_control_plane_scan_client(
    fake_state: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    roles: list[str],
) -> None:
    from data_access import (
        ControlPlaneDynamoDB,
        TenantContext,
        TenantScopedDynamoDB,
        TenantTier,
    )

    from src.tenant_api import db_factory

    class ScanTable:
        def __init__(self) -> None:
            self.scan_calls: list[dict[str, Any]] = []

        def scan(self, **kwargs: Any) -> dict[str, Any]:
            self.scan_calls.append(kwargs)
            return {
                "Items": [
                    {
                        "PK": "AGENT#echo-agent",
                        "SK": "VERSION#1.2.0",
                        "agent_name": "echo-agent",
                        "version": "1.2.0",
                    }
                ]
            }

    class ScanResource:
        def __init__(self, table: ScanTable) -> None:
            self.table = table
            self.table_names: list[str] = []

        def Table(self, table_name: str) -> ScanTable:
            self.table_names.append(table_name)
            return self.table

    context = TenantContext(
        tenant_id="platform",
        app_id="platform-agent",
        tier=TenantTier.PREMIUM,
        sub="operator-123",
    )
    table = ScanTable()
    resource = ScanResource(table)
    tenant_scoped_db = TenantScopedDynamoDB(
        context,
        dynamodb_resource=resource,
        cloudwatch_client=object(),
    )
    control_plane_db = ControlPlaneDynamoDB(
        context,
        dynamodb_resource=resource,
        cloudwatch_client=object(),
    )
    monkeypatch.setattr(db_factory, "db_for_tenant", lambda **_kwargs: tenant_scoped_db)
    monkeypatch.setattr(db_factory, "control_plane_db", lambda *_args, **_kwargs: control_plane_db)

    event = _event(
        method="GET",
        roles=roles,
        caller_tenant_id="platform",
        app_id="platform-agent",
    )
    event["path"] = "/v1/platform/agents"

    response = _invoke(event)

    assert response["statusCode"] == 200
    body = _body(response)
    assert body["items"][0]["agent_name"] == "echo-agent"
    assert resource.table_names == ["platform-agents"]
    assert table.scan_calls == [{}]


@pytest.mark.parametrize("roles", [["Platform.Admin"], ["Platform.Operator"]])
def test_platform_list_agents_requires_platform_tenant_context(
    fake_state: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    roles: list[str],
) -> None:
    from src.tenant_api import db_factory

    def fail_control_plane_db(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("non-platform caller must not reach control-plane scan client")

    monkeypatch.setattr(db_factory, "control_plane_db", fail_control_plane_db)

    event = _event(
        method="GET",
        roles=roles,
        caller_tenant_id="tenant-admin",
        app_id="tenant-admin-app",
    )
    event["path"] = "/v1/platform/agents"

    response = _invoke(event)

    assert response["statusCode"] == 403
    assert _body(response)["error"]["code"] == "FORBIDDEN"


def test_platform_register_agent_rejects_async_invocation_mode(
    fake_state: dict[str, Any],
) -> None:
    event = _event(
        method="POST",
        body={
            "agentName": "echo-agent",
            "version": "1.2.7",
            "ownerTeam": "platform",
            "tierMinimum": "basic",
            "layerHash": "hash-127",
            "layerS3Key": "layers/1.2.7.zip",
            "scriptS3Key": "scripts/1.2.7.zip",
            "invocationMode": "async",
        },
        roles=["Platform.Admin"],
        caller_tenant_id="platform",
    )
    event["path"] = "/v1/platform/agents"

    response = _invoke(event)

    assert response["statusCode"] == 400
    assert ("AGENT#echo-agent", "VERSION#1.2.7") not in fake_state["db"].items


def test_platform_register_agent_persists_ag_ui_metadata(fake_state: dict[str, Any]) -> None:
    event = _event(
        method="POST",
        body={
            "agentName": "echo-agent",
            "version": "1.2.5",
            "ownerTeam": "platform",
            "tierMinimum": "basic",
            "layerHash": "hash-125",
            "layerS3Key": "layers/1.2.5.zip",
            "scriptS3Key": "scripts/1.2.5.zip",
            "invocationMode": "sync",
            "agUi": {
                "enabled": True,
                "transport": "sse",
                "endpoint": "https://ag-ui.example.com/connect",
            },
        },
        roles=["Platform.Admin"],
        caller_tenant_id="platform",
    )
    event["path"] = "/v1/platform/agents"

    response = _invoke(event)

    assert response["statusCode"] == 201
    item = fake_state["db"].items[("AGENT#echo-agent", "VERSION#1.2.5")]
    assert item["ag_ui_enabled"] is True
    assert item["ag_ui_transport"] == "sse"
    assert item["ag_ui_endpoint"] == "https://ag-ui.example.com/connect"


def test_platform_register_agent_rejects_non_built_initial_status(
    fake_state: dict[str, Any],
) -> None:
    event = _event(
        method="POST",
        body={
            "agentName": "echo-agent",
            "version": "1.2.1",
            "status": "promoted",
            "ownerTeam": "platform",
            "tierMinimum": "basic",
            "layerHash": "hash-121",
            "layerS3Key": "layers/1.2.1.zip",
            "scriptS3Key": "scripts/1.2.1.zip",
            "invocationMode": "sync",
        },
        roles=["Platform.Admin"],
        caller_tenant_id="platform",
    )
    event["path"] = "/v1/platform/agents"

    response = _invoke(event)

    assert response["statusCode"] == 400
    assert ("AGENT#echo-agent", "VERSION#1.2.1") not in fake_state["db"].items


def test_platform_register_agent_rejects_missing_zip_layer_metadata(
    fake_state: dict[str, Any],
) -> None:
    event = _event(
        method="POST",
        body={
            "agentName": "echo-agent",
            "version": "1.2.2",
            "ownerTeam": "platform",
            "tierMinimum": "basic",
            "layerHash": "",
            "layerS3Key": "",
            "scriptS3Key": "scripts/1.2.2.zip",
            "invocationMode": "sync",
        },
        roles=["Platform.Admin"],
        caller_tenant_id="platform",
    )
    event["path"] = "/v1/platform/agents"

    response = _invoke(event)

    assert response["statusCode"] == 400
    assert ("AGENT#echo-agent", "VERSION#1.2.2") not in fake_state["db"].items


def test_platform_register_agent_allows_container_without_zip_layer_metadata(
    fake_state: dict[str, Any],
) -> None:
    event = _event(
        method="POST",
        body={
            "agentName": "echo-agent",
            "version": "1.2.6",
            "ownerTeam": "platform",
            "tierMinimum": "basic",
            "layerHash": "",
            "layerS3Key": "",
            "scriptS3Key": "",
            "invocationMode": "sync",
        },
        roles=["Platform.Admin"],
        caller_tenant_id="platform",
    )
    event["path"] = "/v1/platform/agents"

    response = _invoke(event)

    assert response["statusCode"] == 201
    item = fake_state["db"].items[("AGENT#echo-agent", "VERSION#1.2.6")]
    assert item["layer_hash"] == ""
    assert item["layer_s3_key"] == ""


def test_platform_register_agent_persists_runtime_endpoint_metadata(
    fake_state: dict[str, Any],
) -> None:
    event = _event(
        method="POST",
        body={
            "agentName": "echo-agent",
            "version": "1.2.7",
            "ownerTeam": "platform",
            "tierMinimum": "basic",
            "layerHash": "",
            "layerS3Key": "",
            "scriptS3Key": "",
            "runtimeArn": (
                "arn:aws:bedrock-agentcore:eu-west-2:210987654321:runtime/"
                "PlatformdevRuntime-aaaaaaaaaa"
            ),
            "runtimeEndpointArn": (
                "arn:aws:bedrock-agentcore:eu-west-2:210987654321:runtime/"
                "PlatformdevRuntime-aaaaaaaaaa/runtime-endpoint/PlatformdevEndpoint-bbbbbbbbbb"
            ),
            "runtimeEndpointName": "PlatformdevEndpoint",
            "runtimeEndpointVersion": "7",
            "invocationMode": "sync",
        },
        roles=["Platform.Admin"],
        caller_tenant_id="platform",
    )
    event["path"] = "/v1/platform/agents"

    response = _invoke(event)

    assert response["statusCode"] == 201
    item = fake_state["db"].items[("AGENT#echo-agent", "VERSION#1.2.7")]
    assert item["runtime_endpoint_arn"].endswith(
        "runtime/PlatformdevRuntime-aaaaaaaaaa/runtime-endpoint/PlatformdevEndpoint-bbbbbbbbbb"
    )
    assert item["runtime_endpoint_name"] == "PlatformdevEndpoint"
    assert item["runtime_endpoint_version"] == "7"


def test_platform_register_agent_rejects_partial_runtime_endpoint_metadata(
    fake_state: dict[str, Any],
) -> None:
    event = _event(
        method="POST",
        body={
            "agentName": "echo-agent",
            "version": "1.2.8",
            "ownerTeam": "platform",
            "tierMinimum": "basic",
            "layerHash": "",
            "layerS3Key": "",
            "scriptS3Key": "",
            "runtimeArn": (
                "arn:aws:bedrock-agentcore:eu-west-2:210987654321:runtime/"
                "PlatformdevRuntime-aaaaaaaaaa"
            ),
            "runtimeEndpointName": "PlatformdevEndpoint",
            "invocationMode": "sync",
        },
        roles=["Platform.Admin"],
        caller_tenant_id="platform",
    )
    event["path"] = "/v1/platform/agents"

    response = _invoke(event)

    assert response["statusCode"] == 400
    assert ("AGENT#echo-agent", "VERSION#1.2.8") not in fake_state["db"].items


def test_platform_register_agent_requires_runtime_endpoint_metadata_with_runtime_arn(
    fake_state: dict[str, Any],
) -> None:
    event = _event(
        method="POST",
        body={
            "agentName": "echo-agent",
            "version": "1.2.10",
            "ownerTeam": "platform",
            "tierMinimum": "basic",
            "layerHash": "",
            "layerS3Key": "",
            "scriptS3Key": "",
            "runtimeArn": (
                "arn:aws:bedrock-agentcore:eu-west-2:210987654321:runtime/"
                "PlatformdevRuntime-aaaaaaaaaa"
            ),
            "invocationMode": "sync",
        },
        roles=["Platform.Admin"],
        caller_tenant_id="platform",
    )
    event["path"] = "/v1/platform/agents"

    response = _invoke(event)

    assert response["statusCode"] == 400
    assert ("AGENT#echo-agent", "VERSION#1.2.10") not in fake_state["db"].items


def test_platform_register_agent_rejects_runtime_endpoint_drift(
    fake_state: dict[str, Any],
) -> None:
    event = _event(
        method="POST",
        body={
            "agentName": "echo-agent",
            "version": "1.2.9",
            "ownerTeam": "platform",
            "tierMinimum": "basic",
            "layerHash": "",
            "layerS3Key": "",
            "scriptS3Key": "",
            "runtimeArn": (
                "arn:aws:bedrock-agentcore:eu-west-2:210987654321:runtime/"
                "PlatformdevRuntime-aaaaaaaaaa"
            ),
            "runtimeEndpointArn": (
                "arn:aws:bedrock-agentcore:eu-west-2:210987654321:runtime/"
                "OtherRuntime-aaaaaaaaaa/runtime-endpoint/PlatformdevEndpoint-bbbbbbbbbb"
            ),
            "runtimeEndpointName": "PlatformdevEndpoint",
            "runtimeEndpointVersion": "7",
            "invocationMode": "sync",
        },
        roles=["Platform.Admin"],
        caller_tenant_id="platform",
    )
    event["path"] = "/v1/platform/agents"

    response = _invoke(event)

    assert response["statusCode"] == 400
    assert ("AGENT#echo-agent", "VERSION#1.2.9") not in fake_state["db"].items


def test_platform_promote_agent_updates_metadata_and_emits_event(
    fake_state: dict[str, Any],
) -> None:
    _seed_agent_version(
        fake_state,
        agent_name="echo-agent",
        version="1.2.0",
        status="approved",
        extra={
            "approved_by": "approver-007",
            "approved_at": "2026-02-24T18:30:00Z",
            "release_notes": "operator approval evidence",
        },
    )
    event = _event(
        method="PATCH",
        body={"status": "promoted", "releaseNotes": "promotion executed by operator"},
        caller_tenant_id="platform",
        roles=["Platform.Admin"],
    )
    event["path"] = "/v1/platform/agents/echo-agent/versions/1.2.0"

    response = _invoke(event)

    assert response["statusCode"] == 200
    item = fake_state["db"].items[("AGENT#echo-agent", "VERSION#1.2.0")]
    assert item["status"] == "promoted"
    assert item["approved_by"] == "approver-007"
    assert item["approved_at"] == "2026-02-24T18:30:00Z"
    assert item["release_notes"] == "operator approval evidence"
    detail_type, detail = _last_event_detail(fake_state)
    assert detail_type == "platform.agent_version.promoted"
    assert detail["schemaVersion"] == 1
    assert detail["targetTenantId"] == "platform"
    assert detail["operation"] == "promotion"
    assert detail["operationType"] == "promotion"
    assert detail["outcome"] == "succeeded"
    assert detail["occurredAt"] == "2026-02-25T12:00:00Z"
    assert detail["actorTenantId"] == "platform"
    assert detail["actorAppId"] == "app-admin"
    assert detail["actorSub"] == "user-123"
    assert detail["releaseId"] == "echo-agent:1.2.0"
    assert detail["agentRecordPk"] == "AGENT#echo-agent"
    assert detail["agentRecordSk"] == "VERSION#1.2.0"
    assert detail["agentName"] == "echo-agent"
    assert detail["version"] == "1.2.0"
    assert detail["previousStatus"] == "approved"
    assert detail["status"] == "promoted"
    assert detail["approvedBy"] == "approver-007"
    assert detail["approvedAt"] == "2026-02-24T18:30:00Z"
    assert detail["releaseNotes"] == "promotion executed by operator"


def test_platform_approve_agent_records_immutable_approval_evidence(
    fake_state: dict[str, Any],
) -> None:
    _seed_agent_version(
        fake_state, agent_name="approve-agent", version="1.0.0", status="evaluation_passed"
    )
    event = _event(
        method="PATCH",
        body={"status": "approved", "releaseNotes": "two-person approval recorded"},
        roles=["Platform.Admin"],
        caller_tenant_id="platform",
    )
    event["path"] = "/v1/platform/agents/approve-agent/versions/1.0.0"

    response = _invoke(event)

    assert response["statusCode"] == 200
    item = fake_state["db"].items[("AGENT#approve-agent", "VERSION#1.0.0")]
    assert item["status"] == "approved"
    assert item["approved_by"] == "user-123"
    assert item["approved_at"] == "2026-02-25T12:00:00Z"
    assert item["release_notes"] == "two-person approval recorded"


def test_platform_rejects_invalid_agent_status_transition(fake_state: dict[str, Any]) -> None:
    _seed_agent_version(fake_state, agent_name="echo-agent", version="1.2.0", status="promoted")
    event = _event(
        method="PATCH",
        body={"status": "built"},
        roles=["Platform.Admin"],
        caller_tenant_id="platform",
    )
    event["path"] = "/v1/platform/agents/echo-agent/versions/1.2.0"

    response = _invoke(event)

    assert response["statusCode"] == 400


def test_platform_register_and_promote_with_evidence(fake_state: dict[str, Any]) -> None:
    # 1. Register as BUILT
    register_event = _event(
        method="POST",
        body={
            "agentName": "evidence-agent",
            "version": "1.0.0",
            "ownerTeam": "platform",
            "tierMinimum": "basic",
            "layerHash": "hash-100",
            "layerS3Key": "layers/1.0.0.zip",
            "scriptS3Key": "scripts/1.0.0.zip",
            "invocationMode": "sync",
            "status": "built",
            "commitSha": "abc12345",
            "pipelineUrl": "https://gitlab.com/pipeline/123",
            "jobId": "job-456",
        },
        roles=["Platform.Admin"],
        caller_tenant_id="platform",
    )
    register_event["path"] = "/v1/platform/agents"
    register_response = _invoke(register_event)
    assert register_response["statusCode"] == 201

    # 2. Advance to APPROVED (skipping intermediate for brevity)
    # Actually, I'll just seed it as APPROVED to test the final promotion metadata
    _seed_agent_version(
        fake_state,
        agent_name="evidence-agent",
        version="1.0.0",
        status="approved",
        extra={
            "approved_by": "release-admin",
            "approved_at": "2026-02-24T18:30:00Z",
            "release_notes": "approval evidence",
            "commit_sha": "abc12345",
            "pipeline_url": "https://gitlab.com/pipeline/123",
            "job_id": "job-456",
        },
    )

    # 3. Promote with evaluation metadata
    promote_event = _event(
        method="PATCH",
        body={
            "status": "promoted",
            "evaluationScore": 0.98,
            "evaluationReportUrl": "https://frankfurt.aws/eval/789",
            "releaseNotes": "Score: 0.98",
        },
        roles=["Platform.Admin"],
        caller_tenant_id="platform",
    )
    promote_event["path"] = "/v1/platform/agents/evidence-agent/versions/1.0.0"
    promote_response = _invoke(promote_event)
    assert promote_response["statusCode"] == 200

    from decimal import Decimal

    item = fake_state["db"].items[("AGENT#evidence-agent", "VERSION#1.0.0")]
    assert item["status"] == "promoted"
    assert item["evaluation_score"] == Decimal("0.98")
    assert item["evaluation_report_url"] == "https://frankfurt.aws/eval/789"
    assert item["approved_by"] == "release-admin"
    assert item["approved_at"] == "2026-02-24T18:30:00Z"
    assert item["release_notes"] == "approval evidence"

    # Verify event detail
    detail_type, detail = _last_event_detail(fake_state)
    assert detail_type == "platform.agent_version.promoted"
    assert detail["approvedBy"] == "release-admin"
    assert detail["approvedAt"] == "2026-02-24T18:30:00Z"
    assert detail["releaseNotes"] == "Score: 0.98"
    assert detail["evaluationScore"] == 0.98
    assert detail["evaluationReportUrl"] == "https://frankfurt.aws/eval/789"


def test_platform_promote_requires_existing_approval_evidence(fake_state: dict[str, Any]) -> None:
    _seed_agent_version(fake_state, agent_name="echo-agent", version="1.2.0", status="approved")

    event = _event(
        method="PATCH",
        body={"status": "promoted"},
        roles=["Platform.Admin"],
        caller_tenant_id="platform",
    )
    event["path"] = "/v1/platform/agents/echo-agent/versions/1.2.0"

    response = _invoke(event)

    assert response["statusCode"] == 400


def test_platform_rejects_immutable_agent_metadata_updates(fake_state: dict[str, Any]) -> None:
    _seed_agent_version(
        fake_state,
        agent_name="echo-agent",
        version="1.2.0",
        status="approved",
        extra={
            "approved_by": "release-admin",
            "approved_at": "2026-02-24T18:30:00Z",
            "release_notes": "approval evidence",
        },
    )

    event = _event(
        method="PATCH",
        body={"status": "promoted", "agUi": {"enabled": True, "endpoint": "https://example.com"}},
        roles=["Platform.Admin"],
        caller_tenant_id="platform",
    )
    event["path"] = "/v1/platform/agents/echo-agent/versions/1.2.0"

    response = _invoke(event)

    assert response["statusCode"] == 400


def test_platform_rollback_with_metadata(fake_state: dict[str, Any]) -> None:
    _seed_agent_version(
        fake_state,
        agent_name="rollback-agent",
        version="1.0.0",
        status="promoted",
        extra={
            "approved_by": "release-admin",
            "approved_at": "2026-02-24T18:30:00Z",
            "release_notes": "approval evidence",
        },
    )

    event = _event(
        method="PATCH",
        body={"status": "rolled_back", "releaseNotes": "runtime regression confirmed"},
        roles=["Platform.Admin"],
        caller_tenant_id="platform",
    )
    event["path"] = "/v1/platform/agents/rollback-agent/versions/1.0.0"

    response = _invoke(event)
    assert response["statusCode"] == 200

    item = fake_state["db"].items[("AGENT#rollback-agent", "VERSION#1.0.0")]
    assert item["status"] == "rolled_back"
    assert item["rolled_back_by"] == "user-123"
    assert item["rolled_back_at"] == "2026-02-25T12:00:00Z"
    assert item["release_notes"] == "approval evidence"

    # Verify event detail
    detail_type, detail = _last_event_detail(fake_state)
    assert detail_type == "platform.agent_version.rolled_back"
    assert detail["status"] == "rolled_back"
    assert detail["approvedBy"] == "release-admin"
    assert detail["approvedAt"] == "2026-02-24T18:30:00Z"
    assert detail["releaseNotes"] == "runtime regression confirmed"
    assert detail["rolledBackBy"] == "user-123"
    assert detail["rolledBackAt"] == "2026-02-25T12:00:00Z"


def test_platform_rollback_agent_emits_event(fake_state: dict[str, Any]) -> None:
    _seed_agent_version(fake_state, agent_name="echo-agent", version="1.2.0", status="promoted")
    fake_state["db"].items[("AGENT#echo-agent", "VERSION#1.2.0")]["approved_by"] = "release-admin"
    fake_state["db"].items[("AGENT#echo-agent", "VERSION#1.2.0")]["approved_at"] = (
        "2026-02-24T18:30:00Z"
    )
    fake_state["db"].items[("AGENT#echo-agent", "VERSION#1.2.0")]["release_notes"] = (
        "approval evidence"
    )
    event = _event(
        method="PATCH",
        body={"status": "rolled_back", "releaseNotes": "error rate spike"},
        caller_tenant_id="platform",
        roles=["Platform.Admin"],
    )
    event["path"] = "/v1/platform/agents/echo-agent/versions/1.2.0"

    response = _invoke(event)

    assert response["statusCode"] == 200
    item = fake_state["db"].items[("AGENT#echo-agent", "VERSION#1.2.0")]
    assert item["status"] == "rolled_back"
    detail_type, detail = _last_event_detail(fake_state)
    assert detail_type == "platform.agent_version.rolled_back"
    assert detail["schemaVersion"] == 1
    assert detail["targetTenantId"] == "platform"
    assert detail["operation"] == "rollback"
    assert detail["operationType"] == "rollback"
    assert detail["outcome"] == "succeeded"
    assert detail["occurredAt"] == "2026-02-25T12:00:00Z"
    assert detail["actorTenantId"] == "platform"
    assert detail["actorAppId"] == "app-admin"
    assert detail["actorSub"] == "user-123"
    assert detail["releaseId"] == "echo-agent:1.2.0"
    assert detail["agentRecordPk"] == "AGENT#echo-agent"
    assert detail["agentRecordSk"] == "VERSION#1.2.0"
    assert detail["agentName"] == "echo-agent"
    assert detail["version"] == "1.2.0"
    assert detail["previousStatus"] == "promoted"
    assert detail["status"] == "rolled_back"
    assert detail["approvedBy"] == "release-admin"
    assert detail["approvedAt"] == "2026-02-24T18:30:00Z"
    assert detail["releaseNotes"] == "error rate spike"


def test_platform_tenant_does_not_bypass_cross_tenant_reads_without_admin_role(
    fake_state: dict[str, Any],
) -> None:
    fake_state["db"].items[("TENANT#t-001", "METADATA")] = {
        "PK": "TENANT#t-001",
        "SK": "METADATA",
        "tenantId": "t-001",
        "status": "active",
        "appId": "app-001",
    }
    event = _event(method="GET", tenant_id="t-001", caller_tenant_id="platform", roles=[])

    response = _invoke(event)

    assert response["statusCode"] == 403
