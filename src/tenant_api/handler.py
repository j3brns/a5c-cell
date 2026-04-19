"""
tenant_api.handler — Tenant management REST API Lambda.

Handles CRUD for tenants: create, read, update, soft-delete.
Uses data-access-lib exclusively. Publishes EventBridge events on mutations.

Implemented in TASK-017.
ADRs: ADR-012
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import UTC, datetime
from typing import Any

from aws_lambda_powertools import Logger
from aws_lambda_powertools.logging import correlation_paths
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from data_access import (
    ControlPlaneDynamoDB,
    TenantContext,
    TenantScopedDynamoDB,
    TenantScopedS3,
)
from data_access.models import (
    AgentStatus,
    TenantStatus,
    TenantTier,
    normalize_agent_status,
)

from src.tenant_api import (
    agent_logic,
    auth,
    bootstrap,
    constants,
    db_factory,
    db_utils,
    dependency_factories,
    events,
    http_utils,
    lifecycle_logic,
    models,
    secrets_manager,
    serialization,
    utils,
    validation,
)
from src.tenant_api.constants import (
    ADMIN_ROLES,
    TENANT_PROVISIONING_STATUSES,
)
from src.tenant_api.models import CallerIdentity, TenantApiDependencies

logger = Logger(service="tenant-api")

_NoopUsageClient = dependency_factories._NoopUsageClient
_NoopMemoryProvisioner = dependency_factories._NoopMemoryProvisioner
_AwsPlatformQuotaClient = dependency_factories._AwsPlatformQuotaClient


def _dependencies() -> TenantApiDependencies:
    return dependency_factories.build_tenant_api_dependencies(region=os.environ["AWS_REGION"])


_parse_roles = http_utils.parse_roles


def _db_for_tenant(*, tenant_id: str, caller: CallerIdentity, app_id: str | None):
    return db_factory.db_for_tenant(tenant_id=tenant_id, caller=caller, app_id=app_id)


def _control_plane_db(caller: CallerIdentity):
    return db_factory.control_plane_db(caller)


def _now_utc():
    return utils.now_utc()


def _optional_ssm_parameter(ssm: Any, name: str) -> str | None:
    try:
        response = ssm.get_parameter(Name=name)
        return utils.str_or_none(response.get("Parameter", {}).get("Value"))
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ParameterNotFound":
            return None
        raise


def _required_ssm_parameter(ssm: Any, name: str) -> str:
    val = _optional_ssm_parameter(ssm, name)
    if val is None:
        raise ValueError(f"SSM parameter {name} is empty")
    return val


def _dispatch_platform_routes(
    path: str,
    method: str,
    event: dict[str, Any],
    caller: CallerIdentity,
    deps: TenantApiDependencies,
) -> dict[str, Any] | None:
    if path in {
        "/v1/platform/failover",
        "/v1/platform/quota",
        "/v1/platform/quota/split-accounts",
        "/v1/platform/service-health",
        "/v1/platform/billing/status",
    }:
        try:
            from . import ops_control
        except (ImportError, ValueError):
            from src.tenant_api import ops_control
        return ops_control.dispatch_platform_admin_routes(path, method, event, caller, deps)

    if path.startswith("/v1/platform/agents"):
        try:
            from . import agent_registry
        except (ImportError, ValueError):
            from src.tenant_api import agent_registry
        return agent_registry.dispatch_routes(path, method, event, caller, deps)

    return None


def _dispatch_ops_routes(
    path: str,
    method: str,
    event: dict[str, Any],
    caller: CallerIdentity,
    deps: TenantApiDependencies,
) -> dict[str, Any] | None:
    try:
        from . import ops_control
    except (ImportError, ValueError):
        from src.tenant_api import ops_control
    return ops_control.dispatch_ops_routes(path, method, event, caller, deps)


def _dispatch_webhook_routes(
    path: str,
    method: str,
    event: dict[str, Any],
    caller: CallerIdentity,
    deps: TenantApiDependencies,
) -> dict[str, Any] | None:
    try:
        from src.tenant_api import webhook_registry
    except (ImportError, ValueError):
        from . import webhook_registry
    return webhook_registry.dispatch_routes(path, method, event, caller, deps)


def _dispatch_tenant_routes(
    path: str,
    method: str,
    event: dict[str, Any],
    caller: CallerIdentity,
    deps: TenantApiDependencies,
    tenant_id: str | None,
) -> dict[str, Any] | None:
    # 1. Try platform admin routes (platform-wide, no tenant context)
    from src.tenant_api import ops_control

    is_platform_route = path.startswith("/v1/platform") and not path.startswith(
        "/v1/platform/agents"
    )
    if is_platform_route and not caller.is_platform_actor:
        raise PermissionError("Platform tenant context required")

    resp = ops_control.dispatch_platform_admin_routes(path, method, event, caller, deps)
    if resp is not None:
        return resp

    # 2. Try generic ops routes (rollback, etc. - may be platform or tenant scoped)
    resp = ops_control.dispatch_ops_routes(path, method, event, caller, deps)
    if resp is not None:
        return resp

    # 3. Try agent registry routes
    if path.startswith("/v1/platform/agents"):
        try:
            from . import agent_registry
        except (ImportError, ValueError):
            from src.tenant_api import agent_registry
        return agent_registry.dispatch_routes(path, method, event, caller, deps)

    # 4. Try webhook routes (can be platform or tenant scoped)
    if "/webhooks" in path:
        try:
            from . import webhook_registry
        except (ImportError, ValueError):
            from src.tenant_api import webhook_registry
        return webhook_registry.dispatch_routes(path, method, event, caller, deps, tenant_id)

    # 5. Try tenant lifecycle routes
    try:
        from . import tenant_lifecycle
    except (ImportError, ValueError):
        from src.tenant_api import tenant_lifecycle
    return tenant_lifecycle.dispatch_routes(path, method, event, caller, deps, tenant_id)


@logger.inject_lambda_context(clear_state=True, log_event=False)
def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    try:
        runtime = bootstrap.build_runtime(event, dependency_builder=lambda region: _dependencies())
        deps = runtime.deps

        # 1. Dispatch routes (admin, ops, tenant) - HIGHEST priority
        response = _dispatch_tenant_routes(
            runtime.path, runtime.method, event, runtime.caller, deps, runtime.tenant_id
        )
        if response is not None:
            return response

        # 2. Handle provisioning events (if not already handled by dispatch)
        if runtime.detail_type and runtime.source == "platform.tenant_provisioner":
            try:
                from . import tenant_lifecycle
            except (ImportError, ValueError):
                from src.tenant_api import tenant_lifecycle
            return tenant_lifecycle.handle_tenant_provisioning_event(event, deps)

        # 3. Setup logging context
        caller = runtime.caller
        logger.append_keys(appid=caller.app_id or "unknown", tenantid=caller.tenant_id or "unknown")

        # 4. Handle built-in health/sessions (lower priority than specialized dispatch)
        if runtime.path == "/v1/health" and runtime.method == "GET":
            try:
                from . import tenant_lifecycle
            except (ImportError, ValueError):
                from src.tenant_api import tenant_lifecycle
            return tenant_lifecycle.handle_health(deps)

        if runtime.path == "/v1/sessions" and runtime.method == "GET":
            try:
                from . import tenant_lifecycle
            except (ImportError, ValueError):
                from src.tenant_api import tenant_lifecycle
            return tenant_lifecycle.handle_sessions(event, caller)

        return http_utils.error(
            404, "NOT_FOUND", f"Route not found: {runtime.method} {runtime.path}"
        )
    except PermissionError as exc:
        return http_utils.error(403, "FORBIDDEN", str(exc))
    except ValueError as exc:
        return http_utils.error(400, "BAD_REQUEST", str(exc))
    except ClientError as exc:
        logger.exception("AWS client error in tenant API handler")
        return http_utils.error(
            502, "AWS_CLIENT_ERROR", exc.response.get("Error", {}).get("Code", "Unknown")
        )
    except Exception:
        logger.exception("Unhandled tenant API handler error")
        return http_utils.error(500, "INTERNAL_ERROR", "Internal server error")
