from __future__ import annotations

import os
from typing import Any

from aws_lambda_powertools import Logger
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

try:
    from . import (
        auth,
        bootstrap,
        db_factory,
        http_utils,
        lifecycle_logic,
        models,
        utils,
    )
except (ImportError, ValueError):  # pragma: no cover
    from src.tenant_api import (
        auth,
        bootstrap,
        db_factory,
        http_utils,
        lifecycle_logic,
        models,
        utils,
    )
logger = Logger(service="tenant-api-ops")


PLATFORM_ADMIN_PATHS = {
    "/v1/platform/failover",
    "/v1/platform/quota",
    "/v1/platform/quota/split-accounts",
    "/v1/platform/service-health",
    "/v1/platform/billing/status",
}

READ_ONLY_PLATFORM_DIAGNOSTIC_ROUTES = frozenset(
    {
        ("GET", "/v1/platform/agents"),
        ("GET", "/v1/platform/quota"),
        ("GET", "/v1/platform/billing/status"),
    }
)


def handle_platform_failover(
    event: dict[str, Any],
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
) -> dict[str, Any]:
    auth.require_admin(caller)
    body = http_utils.require_json_body(event)
    target_region = utils.str_or_none(body.get("targetRegion"))
    lock_id = utils.str_or_none(body.get("lockId"))

    if not target_region or not lock_id:
        raise ValueError("targetRegion and lockId are required")

    _ = deps, target_region, lock_id
    return http_utils.error(
        409,
        "RUNTIME_FAILOVER_DISABLED",
        "Runtime regional failover is disabled for the v0.2 topology",
    )


def handle_platform_quota(
    event: dict[str, Any],
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
) -> dict[str, Any]:
    _ = event
    auth.require_admin(caller)

    ssm = deps.ssm
    active_region = bootstrap.required_ssm_parameter(ssm, db_factory.runtime_region_param_name())
    # Get real-time utilization from CloudWatch/Service Quotas
    quotas = deps.platform_quota_client.get_utilisation(
        active_region=active_region,
    )

    return lifecycle_logic.platform_control_response(
        200,
        {"utilisation": quotas},
        caller=caller,
        operation_type="quota_report",
    )


def handle_platform_billing_status(
    event: dict[str, Any],
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
) -> dict[str, Any]:
    _ = deps
    auth.require_admin(caller)
    year_month = utils.now_utc().strftime("%Y-%m")
    db = db_factory.control_plane_db(caller)
    summaries = db.scan_all(
        db_factory.tenants_table_name(),
        filter_expression=Key("SK").eq(f"BILLING#{year_month}"),
    )

    return http_utils.response(
        200,
        {
            "yearMonth": year_month,
            "summaries": [
                {
                    "tenantId": s.get("tenantId") or s.get("tenant_id"),
                    "totalInputTokens": int(
                        s.get("totalInputTokens", s.get("total_input_tokens", 0))
                    ),
                    "totalOutputTokens": int(
                        s.get("totalOutputTokens", s.get("total_output_tokens", 0))
                    ),
                    "totalCostUsd": float(s.get("totalCostUsd", s.get("total_cost_usd", 0.0))),
                    "lastUpdated": s.get("updatedAt") or s.get("last_updated"),
                }
                for s in summaries
            ],
        },
    )


def handle_service_health(
    event: dict[str, Any],
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
) -> dict[str, Any]:
    _ = event
    _ = deps
    auth.require_admin(caller)
    return http_utils.response(
        200,
        {
            "status": "unknown",
            "services": {},
            "signals": [
                {
                    "name": "service_health",
                    "source": "none",
                    "state": "unknown",
                    "reason": "no_authoritative_signal",
                }
            ],
            "timestamp": utils.iso(utils.now_utc()),
        },
    )


def handle_platform_split_accounts(
    event: dict[str, Any],
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
) -> dict[str, Any]:
    _ = deps
    if "Platform.Admin" not in caller.roles:
        raise PermissionError("Platform.Admin role required")

    body = http_utils.require_json_body(event)
    lifecycle_logic.normalize_tier(body.get("tier"))
    target_account_id = utils.str_or_none(body.get("targetAccountId"))

    import re

    if target_account_id is None or not re.fullmatch(r"^[0-9]{12}$", target_account_id):
        raise ValueError("targetAccountId must match ^[0-9]{12}$")

    return http_utils.response(
        202,
        {"status": "initiated", "jobId": f"job-split-{int(utils.now_utc().timestamp())}"},
    )


def handle_lambda_rollback(
    event: dict[str, Any],
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
) -> dict[str, Any]:
    if "Platform.Admin" not in caller.roles:
        raise PermissionError("Platform.Admin role required")

    body = http_utils.require_json_body(event)
    function_suffix = utils.str_or_none(body.get("functionSuffix"))
    alias_name = utils.str_or_none(body.get("aliasName")) or "live"
    if function_suffix is None:
        raise ValueError("functionSuffix is required")

    function_name = f"platform-{function_suffix}-{os.environ.get('PLATFORM_ENV', 'dev')}"
    try:
        alias = deps.awslambda.get_alias(FunctionName=function_name, Name=alias_name)
        current_version = str(alias["FunctionVersion"])
        versions: list[str] = []
        paginator = deps.awslambda.get_paginator("list_versions_by_function")
        for page in paginator.paginate(FunctionName=function_name):
            versions.extend(
                str(item.get("Version"))
                for item in page.get("Versions", [])
                if str(item.get("Version")) != "$LATEST"
            )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
            return http_utils.error(404, "NOT_FOUND", "Lambda function not found")
        raise

    ordered_versions = sorted(set(versions), key=lambda value: int(value))
    if current_version not in ordered_versions:
        return http_utils.error(404, "NOT_FOUND", "Alias version not found")
    current_index = ordered_versions.index(current_version)
    if current_index == 0:
        return http_utils.error(409, "NO_PREVIOUS_VERSION", "No previous published version")

    previous_version = ordered_versions[current_index - 1]
    deps.awslambda.update_alias(
        FunctionName=function_name,
        Name=alias_name,
        FunctionVersion=previous_version,
        Description=f"Rollback from {current_version} to {previous_version}",
    )
    return http_utils.response(
        200,
        {
            "functionName": function_name,
            "fromVersion": current_version,
            "toVersion": previous_version,
            "status": "rolled_back",
        },
    )


def dispatch_platform_admin_routes(
    path: str,
    method: str,
    event: dict[str, Any],
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
) -> dict[str, Any] | None:
    if path == "/v1/platform/failover" and method == "POST":
        return handle_platform_failover(event, caller, deps)
    if path == "/v1/platform/quota" and method == "GET":
        return handle_platform_quota(event, caller, deps)
    if path == "/v1/platform/quota/split-accounts" and method == "POST":
        return handle_platform_split_accounts(event, caller, deps)
    if path == "/v1/platform/billing/status" and method == "GET":
        return handle_platform_billing_status(event, caller, deps)
    if path == "/v1/platform/service-health" and method == "GET":
        return handle_service_health(event, caller, deps)
    return None


def dispatch_ops_routes(
    path: str,
    method: str,
    event: dict[str, Any],
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
) -> dict[str, Any] | None:
    if path == "/v1/platform/ops/lambda-rollback" and method == "POST":
        return handle_lambda_rollback(event, caller, deps)

    if path.startswith("/v1/platform/ops"):
        return http_utils.error(405, "METHOD_NOT_ALLOWED", "Method not allowed for this route")

    return None
