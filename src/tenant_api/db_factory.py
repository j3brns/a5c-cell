from __future__ import annotations
import os
from typing import Any
from data_access import ControlPlaneDynamoDB, TenantContext, TenantScopedDynamoDB, TenantScopedS3
from data_access.models import TenantStatus, TenantTier
from src.tenant_api.constants import (
    TENANTS_TABLE_ENV,
    INVOCATIONS_TABLE_ENV,
    AUDIT_EXPORT_BUCKET_ENV,
)

def tenants_table_name() -> str:
    return os.environ.get(TENANTS_TABLE_ENV, "platform-tenants")

def invocations_table_name() -> str:
    return os.environ.get(INVOCATIONS_TABLE_ENV, "platform-invocations")

def audit_export_bucket_name() -> str:
    return os.environ.get(AUDIT_EXPORT_BUCKET_ENV, "platform-audit-exports")

def ops_locks_table_name() -> str:
    from src.tenant_api.constants import OPS_LOCKS_TABLE_ENV, DEFAULT_OPS_LOCKS_TABLE
    return os.environ.get(OPS_LOCKS_TABLE_ENV, DEFAULT_OPS_LOCKS_TABLE)

def db_for_tenant(*, tenant_id: str, app_id: str, tier: str) -> TenantScopedDynamoDB:
    ctx = TenantContext(
        tenant_id=tenant_id,
        app_id=app_id,
        tier=TenantTier(tier),
        status=TenantStatus.ACTIVE,
    )
    return TenantScopedDynamoDB(ctx)

def s3_for_tenant(*, tenant_id: str, app_id: str, tier: str) -> TenantScopedS3:
    ctx = TenantContext(
        tenant_id=tenant_id,
        app_id=app_id,
        tier=TenantTier(tier),
        status=TenantStatus.ACTIVE,
    )
    return TenantScopedS3(ctx)

def control_plane_db() -> ControlPlaneDynamoDB:
    return ControlPlaneDynamoDB()
