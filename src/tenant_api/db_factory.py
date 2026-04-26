from __future__ import annotations

import os
from typing import TYPE_CHECKING

from data_access import ControlPlaneDynamoDB, TenantContext, TenantScopedDynamoDB, TenantScopedS3
from data_access.models import TenantTier

from src.tenant_api.constants import (
    AUDIT_EXPORT_BUCKET_ENV,
    INVOCATIONS_TABLE_ENV,
    TENANTS_TABLE_ENV,
)

if TYPE_CHECKING:
    from src.tenant_api.models import CallerIdentity


def tenants_table_name() -> str:
    return os.environ.get(TENANTS_TABLE_ENV, "platform-tenants")


def agents_table_name() -> str:
    return os.environ.get("AGENTS_TABLE_NAME", "platform-agents")


def invocations_table_name() -> str:
    return os.environ.get(INVOCATIONS_TABLE_ENV, "platform-invocations")


def audit_export_bucket_name() -> str:
    return os.environ.get(AUDIT_EXPORT_BUCKET_ENV, "platform-audit-exports")


def runtime_region_param_name() -> str:
    from src.tenant_api.constants import DEFAULT_RUNTIME_REGION_PARAM, RUNTIME_REGION_PARAM_ENV

    return os.environ.get(RUNTIME_REGION_PARAM_ENV, DEFAULT_RUNTIME_REGION_PARAM)


def _tenant_context_for_scope(
    *,
    tenant_id: str,
    caller: CallerIdentity,
    app_id: str | None,
) -> TenantContext:
    tier_raw = (caller.tier or TenantTier.STANDARD.value).lower()
    try:
        tier = TenantTier(tier_raw)
    except ValueError:
        tier = TenantTier.STANDARD
    return TenantContext(
        tenant_id=tenant_id,
        app_id=app_id or caller.app_id or "unknown-app",
        tier=tier,
        sub=caller.sub or "system",
    )


def db_for_tenant(
    *,
    tenant_id: str,
    caller: CallerIdentity,
    app_id: str | None,
) -> TenantScopedDynamoDB:
    tenant_context = _tenant_context_for_scope(
        tenant_id=tenant_id,
        caller=caller,
        app_id=app_id,
    )
    return TenantScopedDynamoDB(tenant_context)


def s3_for_tenant(
    *,
    tenant_id: str,
    caller: CallerIdentity,
    app_id: str | None,
) -> TenantScopedS3:
    tenant_context = _tenant_context_for_scope(
        tenant_id=tenant_id,
        caller=caller,
        app_id=app_id,
    )
    return TenantScopedS3(tenant_context)


def control_plane_db(caller: CallerIdentity) -> ControlPlaneDynamoDB:
    """Return the explicit scan-capable client for audited platform/admin routes.

    Tenant API code must use this factory rather than constructing
    ControlPlaneDynamoDB directly, so scan-capable access stays visible and
    reviewable at the service boundary.
    """
    tenant_context = _tenant_context_for_scope(
        tenant_id=caller.tenant_id or "control-plane",
        caller=caller,
        app_id=caller.app_id or "control-plane",
    )
    return ControlPlaneDynamoDB(tenant_context)
