from __future__ import annotations

import pytest
from data_access.models import TenantContext

from src.tenant_api import db_factory
from src.tenant_api.models import CallerIdentity


def _caller(*, tenant_id: str | None, roles: frozenset[str] = frozenset()) -> CallerIdentity:
    return CallerIdentity(
        tenant_id=tenant_id,
        app_id="app-caller",
        tier="premium",
        sub="user-123",
        roles=roles,
        usage_identifier_key=None,
    )


class _CapturedTenantScopedClient:
    contexts: list[TenantContext] = []

    def __init__(self, tenant_context: TenantContext) -> None:
        self.contexts.append(tenant_context)
        self.tenant_context = tenant_context


class _UnexpectedTenantScopedClient:
    def __init__(self, tenant_context: TenantContext) -> None:
        _ = tenant_context
        raise AssertionError("Tenant-scoped DAL client should not be constructed")


def test_db_for_tenant_allows_same_tenant_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    _CapturedTenantScopedClient.contexts = []
    monkeypatch.setattr(db_factory, "TenantScopedDynamoDB", _CapturedTenantScopedClient)

    client = db_factory.db_for_tenant(
        tenant_id="t-a",
        caller=_caller(tenant_id="t-a"),
        app_id=None,
    )

    assert isinstance(client, _CapturedTenantScopedClient)
    assert client.tenant_context.tenant_id == "t-a"
    assert client.tenant_context.app_id == "app-caller"


def test_db_for_tenant_rejects_non_platform_caller_target_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(db_factory, "TenantScopedDynamoDB", _UnexpectedTenantScopedClient)

    with pytest.raises(PermissionError, match="Tenant-scoped client target mismatch"):
        db_factory.db_for_tenant(
            tenant_id="t-b",
            caller=_caller(tenant_id="t-a"),
            app_id="app-target",
        )


def test_s3_for_tenant_rejects_non_platform_caller_target_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(db_factory, "TenantScopedS3", _UnexpectedTenantScopedClient)

    with pytest.raises(PermissionError, match="Tenant-scoped client target mismatch"):
        db_factory.s3_for_tenant(
            tenant_id="t-b",
            caller=_caller(tenant_id="t-a"),
            app_id="app-target",
        )


@pytest.mark.parametrize("factory_name", ["db_for_tenant", "s3_for_tenant"])
def test_tenant_scoped_factories_allow_explicit_platform_actor_target(
    monkeypatch: pytest.MonkeyPatch,
    factory_name: str,
) -> None:
    _CapturedTenantScopedClient.contexts = []
    constructor_name = (
        "TenantScopedDynamoDB" if factory_name == "db_for_tenant" else "TenantScopedS3"
    )
    monkeypatch.setattr(db_factory, constructor_name, _CapturedTenantScopedClient)

    factory = getattr(db_factory, factory_name)
    client = factory(
        tenant_id="t-b",
        caller=_caller(tenant_id="platform", roles=frozenset({"Platform.Admin"})),
        app_id="app-target",
    )

    assert isinstance(client, _CapturedTenantScopedClient)
    assert client.tenant_context.tenant_id == "t-b"
    assert client.tenant_context.app_id == "app-target"


def test_control_plane_db_remains_explicit_platform_scan_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _CapturedTenantScopedClient.contexts = []
    monkeypatch.setattr(db_factory, "ControlPlaneDynamoDB", _CapturedTenantScopedClient)

    client = db_factory.control_plane_db(
        _caller(tenant_id="platform", roles=frozenset({"Platform.Admin"}))
    )

    assert isinstance(client, _CapturedTenantScopedClient)
    assert client.tenant_context.tenant_id == "platform"
    assert client.tenant_context.app_id == "app-caller"
