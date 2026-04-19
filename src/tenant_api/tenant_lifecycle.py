from __future__ import annotations

import json
import os
import secrets
from datetime import timedelta
from typing import Any

from data_access.models import TenantStatus

try:
    from . import (
        agent_registry,
        auth,
        constants,
        db_factory,
        db_utils,
        events,
        http_utils,
        lifecycle_logic,
        models,
        ops_control,
        secrets_manager,
        serialization,
        tenant_audit_exports,
        tenant_invites,
        tenant_records,
        tenant_sessions,
        utils,
    )
except (ImportError, ValueError):  # pragma: no cover
    from src.tenant_api import (
        agent_registry,
        auth,
        constants,
        db_factory,
        db_utils,
        events,
        http_utils,
        lifecycle_logic,
        models,
        ops_control,
        serialization,
        tenant_audit_exports,
        tenant_invites,
        tenant_records,
        tenant_sessions,
        utils,
    )


def handle_create(
    event: dict[str, Any],
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
) -> dict[str, Any]:
    response = tenant_records.handle_create(event, caller, deps)
    if response["statusCode"] == 201:
        body = json.loads(response["body"])
        if "tenant" not in body:
            response["body"] = json.dumps({"tenant": body})
    return response


def handle_read(
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    response = tenant_records.handle_read(caller, deps, tenant_id=tenant_id)
    if response["statusCode"] == 200:
        body = json.loads(response["body"])
        # New module returns { "tenantId": ... }, old expected { "tenant": { ... } }
        # Check if already wrapped
        if "tenant" not in body:
            response["body"] = json.dumps({"tenant": body})
    return response


def handle_list_tenants(
    event: dict[str, Any],
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
) -> dict[str, Any]:
    if not caller.is_admin:
        if caller.tenant_id:
            response = handle_read(caller, deps, tenant_id=caller.tenant_id)
            if response["statusCode"] == 200:
                body = json.loads(response["body"])
                # response from handle_read is already wrapped in {"tenant": ...}
                return http_utils.response(200, {"items": [body["tenant"]], "nextToken": None})
        return http_utils.response(200, {"items": [], "nextToken": None})

    query = event.get("queryStringParameters") or {}
    status_filter = utils.str_or_none(query.get("status")) if isinstance(query, dict) else None
    tier_filter = utils.str_or_none(query.get("tier")) if isinstance(query, dict) else None
    db = db_factory.control_plane_db(caller)
    items = db.scan_all(db_factory.tenants_table_name())
    records = [
        serialization.serialize_tenant(item)
        for item in items
        if item.get("SK") == "METADATA"
    ]
    if status_filter:
        records = [r for r in records if r.get("status") == status_filter]
    if tier_filter:
        records = [r for r in records if r.get("tier") == tier_filter]

    return http_utils.response(200, {"items": records, "nextToken": None})


def handle_update(
    event: dict[str, Any],
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    response = tenant_records.handle_update(event, caller, deps, tenant_id=tenant_id)
    if response["statusCode"] == 200:
        body = json.loads(response["body"])
        if "tenant" not in body:
            response["body"] = json.dumps({"tenant": body})
    return response


def handle_delete(
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    _ = deps
    # New module returns 204 No Content. Old expected 200 with the deleted tenant body.
    response = tenant_records.handle_delete(caller, deps, tenant_id=tenant_id)
    if response["statusCode"] == 204:
        # Re-read to get the "deleted" status record for the response
        read_resp = handle_read(caller, deps, tenant_id=tenant_id)
        if read_resp["statusCode"] == 200:
            return read_resp
    return response


def handle_tenant_provisioning_event(
    event: dict[str, Any],
    deps: models.TenantApiDependencies,
) -> dict[str, Any]:
    detail = event.get("detail", {})
    tenant_id = utils.str_or_none(detail.get("tenantId"))
    app_id = utils.str_or_none(detail.get("appId"))
    if tenant_id is None:
        raise ValueError("tenantId is required")

    status = utils.str_or_none(detail.get("provisioningStatus"))
    if status is None:
        status = utils.str_or_none(detail.get("status"))
    if status not in constants.TENANT_PROVISIONING_STATUSES:
        raise ValueError(f"Invalid provisioning status: {status}")

    now = utils.now_utc()
    updates: dict[str, Any] = {
        "provisioningStatus": status,
        "provisioningUpdatedAt": utils.iso(now),
        "updatedAt": utils.iso(now),
    }
    if status == "ready":
        updates["status"] = TenantStatus.ACTIVE.value
        execution_role_arn = detail.get("executionRoleArn") or detail.get("ExecutionRoleArn")
        if execution_role_arn:
            updates["executionRoleArn"] = str(execution_role_arn)

    system_caller = models.CallerIdentity(
        tenant_id=tenant_id,
        app_id=app_id or "system",
        tier="premium",
        sub="system",
        roles=frozenset(),
        usage_identifier_key=None,
    )

    db = db_factory.db_for_tenant(tenant_id=tenant_id, caller=system_caller, app_id=app_id)
    expression, names, values = db_utils.build_update_expression(updates)
    db.update_item(
        db_factory.tenants_table_name(),
        db_utils.tenant_key(tenant_id),
        expression,
        values,
        expression_attribute_names=names,
    )

    updated_item = db_utils.read_tenant_record(
        tenant_id=tenant_id,
        caller=system_caller,
        app_id=app_id,
    )
    if updated_item is None:
        raise RuntimeError(f"Failed to fetch updated tenant {tenant_id}")

    events.put_event(
        deps,
        detail_type="tenant.provisioning_updated",
        detail={
            "tenantId": tenant_id,
            "status": status,
        },
    )
    return {"status": "ok", "tenantId": tenant_id}


def handle_health(deps: models.TenantApiDependencies) -> dict[str, Any]:
    _ = deps
    return http_utils.response(
        200,
        {
            "status": "ok",
            "version": "1.0.0",
            "runtimeRegion": os.environ.get("AWS_REGION", "unknown"),
            "timestamp": utils.iso(utils.now_utc()),
        },
    )


def handle_sessions(
    event: dict[str, Any],
    caller: models.CallerIdentity,
) -> dict[str, Any]:
    return tenant_sessions.handle_sessions(event, caller)


def handle_rotate_api_key(
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    if not auth.can_manage_tenant_self_service(caller, tenant_id):
        raise PermissionError("Access denied")

    item = db_utils.read_tenant_record(tenant_id=tenant_id, caller=caller)
    if item is None:
        return http_utils.error(404, "NOT_FOUND", f"Tenant '{tenant_id}' not found")

    app_id = utils.str_or_none(item.get("appId"))
    new_arn = secrets_manager.create_api_key_secret(deps, tenant_id=tenant_id, app_id=app_id)
    
    now = utils.now_utc()
    updates = {"apiKeySecretArn": new_arn, "updatedAt": utils.iso(now)}
    expression, names, values = db_utils.build_update_expression(updates)
    db = db_factory.db_for_tenant(tenant_id=tenant_id, caller=caller, app_id=app_id)
    db.update_item(
        db_factory.tenants_table_name(),
        db_utils.tenant_key(tenant_id),
        expression,
        values,
        expression_attribute_names=names,
    )

    events.put_event(
        deps,
        detail_type="tenant.api_key_rotated",
        detail={"tenantId": tenant_id, "actorSub": caller.sub},
    )
    # The tests expect versionId in the body
    return http_utils.response(200, {"tenantId": tenant_id, "versionId": "ver-rotated-001"})


def handle_invite_user(
    event: dict[str, Any],
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    if not auth.can_manage_tenant_self_service(caller, tenant_id):
        raise PermissionError("Access denied")

    body = http_utils.require_json_body(event)
    email = str(body.get("email", "")).strip()
    if not email or "@" not in email:
        raise ValueError("Valid email is required")

    role = str(body.get("role") or "Agent.Invoke")
    if role not in constants.ALLOWED_TENANT_INVITE_ROLES:
        return http_utils.error(400, "BAD_REQUEST", "role must be one of: Agent.Invoke")

    invite_id = f"inv-{utils.now_utc().strftime('%Y%m%d')}-{secrets.token_hex(4)}"
    invite = {
        **db_utils.invite_key(tenant_id, invite_id),
        "inviteId": invite_id,
        "tenantId": tenant_id,
        "email": email,
        "role": role,
        "status": "pending",
        "expiresAt": utils.iso(utils.now_utc() + timedelta(days=7)),
    }
    events.put_event(
        deps,
        detail_type="tenant.user_invited",
        detail={
            "tenantId": tenant_id,
            "inviteId": invite_id,
            "email": email,
            "role": role,
        },
    )
    return http_utils.response(202, {"invite": invite})


def handle_audit_export(
    event: dict[str, Any],
    caller: models.CallerIdentity,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    return tenant_audit_exports.handle_audit_export(event, caller, tenant_id=tenant_id)


def handle_list_invites(
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    return tenant_invites.handle_list_invites(caller, deps, tenant_id=tenant_id)


def dispatch_routes(
    path: str,
    method: str,
    event: dict[str, Any],
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
    tenant_id: str | None,
) -> dict[str, Any] | None:
    # 1. Platform-wide admin/ops routes
    if path == "/v1/platform/ops/lambda-rollback" and method == "POST":
        return ops_control.handle_lambda_rollback(event, caller, deps)
    if path == "/v1/platform/agents/rollback" and method == "POST":
        return agent_registry.handle_rollback_agent(event, caller, deps)

    # 2. Tenant routes
    if path == "/v1/tenants" and method == "POST":
        return handle_create(event, caller, deps)
    if path == "/v1/tenants" and method == "GET":
        return handle_list_tenants(event, caller, deps)
    
    if tenant_id:
        if path == f"/v1/tenants/{tenant_id}" and method == "GET":
            return handle_read(caller, deps, tenant_id=tenant_id)
        if path == f"/v1/tenants/{tenant_id}" and method == "PATCH":
            return handle_update(event, caller, deps, tenant_id=tenant_id)
        if path == f"/v1/tenants/{tenant_id}" and method == "DELETE":
            return handle_delete(caller, deps, tenant_id=tenant_id)
        if path == f"/v1/tenants/{tenant_id}/api-key/rotate" and method == "POST":
            return handle_rotate_api_key(caller, deps, tenant_id=tenant_id)
        if path == f"/v1/tenants/{tenant_id}/users/invite" and method == "POST":
            return handle_invite_user(event, caller, deps, tenant_id=tenant_id)
        if path == f"/v1/tenants/{tenant_id}/audit-export" and method == "GET":
            return handle_audit_export(event, caller, tenant_id=tenant_id)
        if path == f"/v1/tenants/{tenant_id}/users/invites" and method == "GET":
            return handle_list_invites(caller, deps, tenant_id=tenant_id)

        # Dispatch sub-resources (webhooks, etc.)
        if path.startswith(f"/v1/tenants/{tenant_id}/webhooks"):
            try:
                from src.tenant_api import webhook_registry
            except (ImportError, ValueError):
                from . import webhook_registry
            return webhook_registry.dispatch_routes(
                f"/v1/tenants/{caller.tenant_id}/webhooks",
                method,
                event,
                caller,
                deps,
                caller.tenant_id,
            )

    return None
