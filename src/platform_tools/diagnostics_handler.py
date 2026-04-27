"""
platform_tools.diagnostics_handler — Read-only platform diagnostics and runbook assistance.

Implements tools for the platform-diagnostics-agent to query platform health,
tenant status, recent errors, and runbook guidance.

Tools:
  - get_platform_health: Returns health signals for regions and services.
  - get_tenant_status: Returns status, tier, and recent metrics for a tenant.
  - get_recent_errors: Returns recent invocation errors from audit records.
  - get_runbook_guidance: Returns guidance from the operator runbooks.

Implemented in ISSUE-389.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime, timedelta
from typing import Any, TypedDict

import jwt
from aws_lambda_powertools import Logger
from boto3.dynamodb.conditions import Attr, Key
from data_access import ControlPlaneDynamoDB, TenantContext, TenantTier

from platform_config.runtime_topology import SERVING_RUNTIME_REGION
from src.platform_aws import boto3_client

logger = Logger(service="platform-diagnostics-tool")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TENANTS_TABLE = os.environ.get("TENANTS_TABLE_NAME", "platform-tenants")
INVOCATIONS_TABLE = os.environ.get("INVOCATIONS_TABLE_NAME", "platform-invocations")
RUNTIME_REGION_PARAM = os.environ.get("RUNTIME_REGION_PARAM", "/platform/config/runtime-region")
SCOPED_TOKEN_ISSUER = os.environ.get("SCOPED_TOKEN_ISSUER", "platform-gateway")
SCOPED_TOKEN_SIGNING_KEY_SECRET_ARN = (
    "SCOPED_TOKEN_SIGNING_KEY_SECRET_ARN"  # pragma: allowlist secret
)
SCOPED_TOKEN_SIGNING_KEY_ENV = "SCOPED_TOKEN_SIGNING_KEY"  # pragma: allowlist secret

_scoped_token_signing_key_cache: str | None = None
_scoped_token_signing_key_expiry: float = 0
HEALTH_WINDOW_MINUTES = 15
RECENT_ERRORS_WINDOW_MINUTES = 60
ERROR_STATUSES = frozenset({"error", "timeout", "throttled"})

# ---------------------------------------------------------------------------
# Runbook Data (Embedded for tool access)
# ---------------------------------------------------------------------------


class Runbook(TypedDict):
    title: str
    trigger: str
    steps: list[str]


RUNBOOKS: dict[str, Runbook] = {
    "RUNBOOK-001": {
        "title": "Runtime Region Degradation",
        "trigger": (
            "ServiceUnavailableException from the active runtime region "
            f"({SERVING_RUNTIME_REGION})."
        ),
        "steps": [
            "1. Verify regional outage via Service Health Dashboard or CloudWatch metrics.",
            "2. Confirm the platform is in degraded runtime mode.",
            "3. Pause tenant-impacting release activity while the outage is active.",
            "4. Track AWS recovery and update tenant communications.",
            "5. Verify new invocations succeed in the serving region after recovery.",
        ],
    },
    "RUNBOOK-002": {
        "title": "AgentCore Quota Monitoring",
        "trigger": "ConcurrentSessions utilisation > 70%.",
        "steps": [
            "1. Check /v1/platform/quota to see current regional utilisation.",
            "2. Identify if any single tenant is responsible for the surge.",
            "3. If utilisation > 80%, initiate RUNBOOK-004 (Quota Increase).",
            "4. If utilisation > 90% and approval is slow, consider 'Option B' (Account Split).",
        ],
    },
    "RUNBOOK-003": {
        "title": "Tenant Access Violation",
        "trigger": "TenantAccessViolation alarm or security event log.",
        "steps": [
            "1. Identify the caller tenant and the target tenant from the logs.",
            "2. Determine if the attempt was a misconfiguration or a malicious probe.",
            "3. Suspend the caller tenant if necessary via "
            "POST /v1/platform/ops/tenants/{id}/suspend.",
            "4. Page the security team if a persistent breach is suspected.",
        ],
    },
    "RUNBOOK-005": {
        "title": "DLQ Management",
        "trigger": "DLQ CloudWatch alarm.",
        "steps": [
            "1. Inspect the messages in the DLQ via GET /v1/platform/ops/dlq/{name}.",
            "2. Identify the root cause (e.g., timeout, downstream error).",
            "3. Fix the underlying issue.",
            "4. Redrive the messages via POST /v1/platform/ops/dlq/{name}/redrive.",
        ],
    },
    "RUNBOOK-007": {
        "title": "Deployment Rollback",
        "trigger": "Failed deployment or regression detected post-release.",
        "steps": [
            "1. Identify the failing function(s).",
            "2. Perform a Lambda alias rollback via POST /v1/platform/ops/lambda-rollback.",
            "3. Verify the previous version is stable.",
            "4. Update the issue and investigate the root cause.",
        ],
    },
}

# ---------------------------------------------------------------------------
# Tool Implementations
# ---------------------------------------------------------------------------


def _header_value(headers: Any, name: str) -> str | None:
    if not isinstance(headers, dict):
        return None
    for key, value in headers.items():
        if key.lower() == name.lower() and value is not None:
            return str(value)
    return None


def _bearer_token(headers: Any) -> str | None:
    auth_header = _header_value(headers, "Authorization")
    if not auth_header:
        return None
    prefix = "Bearer "
    if not auth_header.startswith(prefix):
        return None
    token = auth_header[len(prefix) :].strip()
    return token or None


def _get_scoped_token_signing_key() -> str | None:
    platform_env = os.environ.get("PLATFORM_ENV", "prod")

    explicit = os.environ.get(SCOPED_TOKEN_SIGNING_KEY_ENV)
    if explicit and platform_env == "local":
        if len(explicit) < 32:
            logger.warning("SCOPED_TOKEN_SIGNING_KEY is too short (min 32 bytes recommended)")
        return explicit

    secret_arn = os.environ.get(SCOPED_TOKEN_SIGNING_KEY_SECRET_ARN)
    if not secret_arn:
        logger.error("Scoped token signing key is not configured")
        return None

    global _scoped_token_signing_key_cache
    global _scoped_token_signing_key_expiry
    now = time.time()
    if _scoped_token_signing_key_cache and now < _scoped_token_signing_key_expiry:
        return _scoped_token_signing_key_cache

    try:
        response = boto3_client("secretsmanager").get_secret_value(SecretId=secret_arn)
        signing_key = response.get("SecretString")
    except Exception:
        logger.exception("Failed to fetch scoped token signing key from Secrets Manager")
        return None

    if not signing_key:
        logger.error("Scoped token signing key secret has no SecretString")
        return None

    _scoped_token_signing_key_cache = signing_key
    _scoped_token_signing_key_expiry = now + 300
    return signing_key


def _trusted_platform_context(
    headers: dict[str, Any],
    *,
    tool_name: str,
) -> tuple[str, str] | None:
    token = _bearer_token(headers)
    if not token:
        return None

    signing_key = _get_scoped_token_signing_key()
    if not signing_key:
        return None

    try:
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["HS256"],
            audience=f"tool:{tool_name}",
            issuer=os.environ.get("SCOPED_TOKEN_ISSUER", SCOPED_TOKEN_ISSUER),
            options={
                "require": ["exp", "iat", "tenantid", "appid", "scope_tool", "acting_sub"],
            },
        )
    except jwt.PyJWTError:
        logger.warning("Diagnostics request denied: invalid scoped token")
        return None

    tenant_id = claims.get("tenantid")
    app_id = claims.get("appid")
    scope_tool = claims.get("scope_tool")
    acting_sub = claims.get("acting_sub")
    if tenant_id != "platform" or not app_id or scope_tool != tool_name or not acting_sub:
        logger.warning(
            "Diagnostics request denied: scoped token claims not authorized",
            extra={
                "tenant_id": tenant_id,
                "app_id": app_id,
                "scope_tool": scope_tool,
            },
        )
        return None

    return str(tenant_id), str(app_id)


def _status_value(item: dict[str, Any]) -> str:
    return str(item.get("status", "")).lower()


def _tenant_ids(db: ControlPlaneDynamoDB) -> list[str]:
    tenants = db.scan_all(TENANTS_TABLE, filter_expression=Attr("SK").eq("METADATA"))
    tenant_ids: list[str] = []
    for item in tenants:
        raw = item.get("tenantId") or item.get("tenant_id")
        if not raw:
            pk = str(item.get("PK", ""))
            raw = pk.removeprefix("TENANT#") if pk.startswith("TENANT#") else None
        if raw and raw != "platform":
            tenant_ids.append(str(raw))
    return sorted(set(tenant_ids))


def _tenant_recent_invocations(
    db: ControlPlaneDynamoDB,
    *,
    tenant_id: str,
    since: datetime,
) -> list[dict[str, Any]]:
    return db.query_all(
        INVOCATIONS_TABLE,
        pk_value=f"TENANT#{tenant_id}",
        sk_condition=Key("SK").gt(f"INV#{since.isoformat()}"),
        scan_index_forward=False,
    )


def _recent_invocations(
    db: ControlPlaneDynamoDB,
    *,
    since: datetime,
) -> tuple[list[dict[str, Any]], list[str]]:
    tenant_ids = _tenant_ids(db)
    recent: list[dict[str, Any]] = []
    for tenant_id in tenant_ids:
        recent.extend(_tenant_recent_invocations(db, tenant_id=tenant_id, since=since))
    return recent, tenant_ids


def _recent_error_invocations(
    db: ControlPlaneDynamoDB,
    *,
    since: datetime,
    tenant_id: str | None = None,
) -> list[dict[str, Any]]:
    if tenant_id:
        candidates = _tenant_recent_invocations(db, tenant_id=tenant_id, since=since)
    else:
        candidates, _ = _recent_invocations(db, since=since)
    return [item for item in candidates if _status_value(item) in ERROR_STATUSES]


def _format_invocation_error(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "timestamp": item.get("timestamp"),
        "type": "invocation_error",
        "tenantId": item.get("tenant_id") or item.get("tenantId"),
        "appId": item.get("app_id") or item.get("appId"),
        "agentName": item.get("agent_name") or item.get("agentName"),
        "runtimeRegion": item.get("runtime_region") or item.get("runtimeRegion"),
        "status": _status_value(item),
        "errorCode": item.get("error_code") or item.get("errorCode"),
    }


def get_platform_health(
    db: ControlPlaneDynamoDB, log_context: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Return health derived from authoritative platform invocation records."""
    now = datetime.now(UTC)
    since = now - timedelta(minutes=HEALTH_WINDOW_MINUTES)
    source = "platform-invocations"
    try:
        recent, tenant_ids = _recent_invocations(db, since=since)
    except Exception as exc:
        logger.warning(
            "Platform invocation health signal unavailable",
            extra={**(log_context or {}), "error": str(exc)},
        )
        return {
            "status": "unknown",
            "source": source,
            "regions": [{"region": SERVING_RUNTIME_REGION, "status": "unknown"}],
            "services": {"RuntimeInvocations": "unknown"},
            "signals": [
                {
                    "name": "recent_invocations",
                    "source": source,
                    "state": "unknown",
                    "reason": "signal_unavailable",
                }
            ],
            "timestamp": now.isoformat(),
        }

    error_count = sum(1 for item in recent if _status_value(item) in ERROR_STATUSES)
    if not tenant_ids:
        status = "unknown"
        region_status = "unknown"
        reason = "not_configured"
    elif not recent:
        status = "unknown"
        region_status = "unknown"
        reason = "no_recent_invocations"
    elif error_count:
        status = "degraded"
        region_status = "degraded"
        reason = "recent_invocation_errors"
    else:
        status = "healthy"
        region_status = "operational"
        reason = "recent_invocations_successful"

    return {
        "status": status,
        "source": source,
        "regions": [{"region": SERVING_RUNTIME_REGION, "status": region_status}],
        "services": {"RuntimeInvocations": region_status},
        "signals": [
            {
                "name": "recent_invocations",
                "source": source,
                "state": region_status,
                "reason": reason,
            }
        ],
        "summary": {
            "configuredTenants": len(tenant_ids),
            "recentInvocations": len(recent),
            "errors": error_count,
            "windowMinutes": HEALTH_WINDOW_MINUTES,
        },
        "timestamp": now.isoformat(),
    }


def get_tenant_status(db: ControlPlaneDynamoDB, tenant_id: str) -> dict[str, Any]:
    """Return the current status and metadata for a specific tenant."""
    tenant = db.get_item(TENANTS_TABLE, {"PK": f"TENANT#{tenant_id}", "SK": "METADATA"})
    if not tenant:
        return {"error": f"Tenant {tenant_id} not found"}

    # Recent invocation summary (last 1 hour)
    now = datetime.now(UTC)
    hour_ago = (now - timedelta(hours=1)).isoformat()

    # Note: Scanning invocations by tenant_id is slow in prod,
    # but for a platform tool it's acceptable with a small limit.
    recent_invocations = db.query(
        INVOCATIONS_TABLE,
        pk_value=f"TENANT#{tenant_id}",
        sk_condition=Key("SK").gt(f"INV#{hour_ago}"),
        limit=20,
        scan_index_forward=False,
    )

    return {
        "tenantId": tenant_id,
        "displayName": tenant.get("displayName") or tenant.get("display_name", "Unknown"),
        "status": tenant.get("status", "active"),
        "tier": tenant.get("tier", "basic"),
        "recentInvocations": len(recent_invocations.items),
        "lastUpdated": tenant.get("updatedAt") or tenant.get("updated_at"),
    }


def get_recent_errors(
    db: ControlPlaneDynamoDB,
    tenant_id: str | None = None,
    log_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return recent invocation errors from authoritative audit records."""
    since = datetime.now(UTC) - timedelta(minutes=RECENT_ERRORS_WINDOW_MINUTES)
    source = "platform-invocations"
    try:
        events = [
            _format_invocation_error(item)
            for item in _recent_error_invocations(db, since=since, tenant_id=tenant_id)
        ]
    except Exception as exc:
        logger.warning(
            "Recent error signal unavailable",
            extra={**(log_context or {}), "error": str(exc)},
        )
        return {
            "events": [],
            "count": 0,
            "source": source,
            "status": "unknown",
            "reason": "signal_unavailable",
        }

    return {"events": events, "count": len(events), "source": source, "status": "ok"}


def get_runbook_guidance(query: str | None = None, runbook_id: str | None = None) -> dict[str, Any]:
    """Return guidance from the operator runbooks based on a query or ID."""
    if runbook_id:
        guidance = RUNBOOKS.get(runbook_id.upper())
        if guidance:
            return {"runbookId": runbook_id.upper(), **guidance}
        return {"error": f"Runbook {runbook_id} not found"}

    if query:
        # Simple keyword match
        query_lower = query.lower()
        matches = []
        for rid, data in RUNBOOKS.items():
            if query_lower in data["title"].lower() or query_lower in rid.lower():
                matches.append({"runbookId": rid, "title": data["title"]})

        if matches:
            return {"matches": matches}

    return {
        "availableRunbooks": [
            {"runbookId": rid, "title": data["title"]} for rid, data in RUNBOOKS.items()
        ]
    }


# ---------------------------------------------------------------------------
# Lambda Handler
# ---------------------------------------------------------------------------


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Tool entrypoint — handles JSON-RPC from Gateway."""
    headers = event.get("headers") or {}
    log_context = {
        "method": event.get("method"),
        "tenantid": "unknown",
        "appid": "unknown",
    }

    logger.info("Diagnostics tool invoked", extra=log_context)

    # 1. Parse request (Gateway Tool Call format)
    method = event.get("method")
    params = event.get("params", {})

    # If it's a tools/call, the tool name is in params.name
    if method == "tools/call":
        tool_name = params.get("name")
        tool_params = params.get("arguments", {})
    else:
        # Fallback for direct calls
        tool_name = method
        tool_params = params

    if not isinstance(tool_name, str) or not tool_name:
        return {
            "jsonrpc": "2.0",
            "id": event.get("id"),
            "error": {"code": -32601, "message": f"Method not found: {tool_name}"},
        }

    trusted_context = _trusted_platform_context(headers, tool_name=tool_name)
    if trusted_context is None:
        logger.warning("Diagnostics tool access denied", extra=log_context)
        return {
            "jsonrpc": "2.0",
            "id": event.get("id"),
            "error": {"code": -32003, "message": "Access denied: trusted scoped token required"},
        }
    tenant_id, app_id = trusted_context
    log_context = {
        "method": event.get("method"),
        "tenantid": tenant_id,
        "appid": app_id,
    }

    # 2. Initialize dependencies
    ctx = TenantContext(
        tenant_id=tenant_id, app_id=app_id, tier=TenantTier.PREMIUM, sub="platform-diagnostics"
    )
    db = ControlPlaneDynamoDB(ctx)

    # 3. Dispatch tool
    result: Any = None
    try:
        if tool_name == "get_platform_health":
            result = get_platform_health(db, log_context)
        elif tool_name == "get_tenant_status":
            tid = tool_params.get("tenant_id") or tool_params.get("tenantId")
            if not tid:
                raise ValueError("tenant_id is required")
            result = get_tenant_status(db, tid)
        elif tool_name == "get_recent_errors":
            tid = tool_params.get("tenant_id") or tool_params.get("tenantId")
            result = get_recent_errors(db, tid, log_context)
        elif tool_name == "get_runbook_guidance":
            query = tool_params.get("query")
            rid = tool_params.get("runbook_id") or tool_params.get("runbookId")
            result = get_runbook_guidance(query, rid)
        else:
            return {
                "jsonrpc": "2.0",
                "id": event.get("id"),
                "error": {"code": -32601, "message": f"Method not found: {tool_name}"},
            }

        return {"jsonrpc": "2.0", "id": event.get("id"), "result": result}
    except Exception as exc:
        logger.exception("Tool execution failed", extra=log_context)
        return {
            "jsonrpc": "2.0",
            "id": event.get("id"),
            "error": {"code": -32603, "message": str(exc)},
        }
