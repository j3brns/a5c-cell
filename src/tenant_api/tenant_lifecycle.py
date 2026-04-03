from __future__ import annotations

from datetime import timedelta
from typing import Any

from boto3.dynamodb.conditions import Key
from data_access.models import TenantStatus

try:
    from . import (
        auth,
        constants,
        db_factory,
        db_utils,
        events,
        http_utils,
        lifecycle_logic,
        models,
        secrets_manager,
        serialization,
        utils,
        validation,
    )
except (ImportError, ValueError):  # pragma: no cover
    from src.tenant_api import (
        auth,
        constants,
        db_factory,
        db_utils,
        events,
        http_utils,
        lifecycle_logic,
        models,
        secrets_manager,
        serialization,
        utils,
        validation,
    )


def handle_create(
    event: dict[str, Any],
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
) -> dict[str, Any]:
    auth.require_admin(caller)
    body = http_utils.require_json_body(event)
    required = ["tenantId", "appId", "displayName", "tier", "ownerEmail", "ownerTeam", "accountId"]
    missing = [field for field in required if utils.str_or_none(body.get(field)) is None]
    if missing:
        raise ValueError(f"Missing required field(s): {', '.join(missing)}")

    tenant_id = validation.canonical_tenant_id(body["tenantId"])
    app_id = str(body["appId"]).strip()
    now = utils.now_utc()
    tier = lifecycle_logic.normalize_tier(body.get("tier"))

    if db_utils.read_tenant_record(tenant_id=tenant_id, caller=caller, app_id=app_id) is not None:
        return http_utils.error(409, "CONFLICT", "Tenant already exists")

    memory_info = deps.memory_provisioner.provision(tenant_id=tenant_id, app_id=app_id) or {}
    api_key_secret_arn = secrets_manager.create_api_key_secret(
        deps, tenant_id=tenant_id, app_id=app_id
    )

    attributes: dict[str, Any] = {
        "tenantId": tenant_id,
        "appId": app_id,
        "displayName": str(body["displayName"]).strip(),
        "tier": tier,
        "status": TenantStatus.ACTIVE.value,
        "createdAt": utils.iso(now),
        "updatedAt": utils.iso(now),
        "provisioningStatus": "pending",
        "provisioningUpdatedAt": utils.iso(now),
        "ownerEmail": str(body["ownerEmail"]).strip(),
        "ownerTeam": str(body["ownerTeam"]).strip(),
        "accountId": str(body["accountId"]).strip(),
        "apiKeySecretArn": api_key_secret_arn,
    }
    if body.get("monthlyBudgetUsd") is not None:
        attributes["monthlyBudgetUsd"] = utils.as_float(
            body["monthlyBudgetUsd"], field="monthlyBudgetUsd"
        )

    # Save to DynamoDB
    db = db_factory.db_for_tenant(tenant_id=tenant_id, caller=caller, app_id=app_id)
    db.put_item(db_factory.tenants_table_name(), attributes)

    # Emit event
    events.put_event(
        deps,
        detail_type="platform.tenant.created",
        detail={
            "tenantId": tenant_id,
            "appId": app_id,
            "tier": tier,
            "accountId": attributes["accountId"],
            "memoryInfo": memory_info,
        },
    )

    return http_utils.response(201, serialization.serialize_tenant(attributes))


def handle_read(
    caller: models.CallerIdentity,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    if not auth.can_read_tenant(caller, tenant_id):
        raise PermissionError("Access denied")

    item = db_utils.read_tenant_record(tenant_id=tenant_id, caller=caller)
    if item is None:
        return http_utils.error(404, "NOT_FOUND", f"Tenant '{tenant_id}' not found")

    return http_utils.response(200, serialization.serialize_tenant(item))


def handle_update(
    event: dict[str, Any],
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    auth.require_admin(caller)
    body = http_utils.require_json_body(event)

    item = db_utils.read_tenant_record(tenant_id=tenant_id, caller=caller)
    if item is None:
        return http_utils.error(404, "NOT_FOUND", f"Tenant '{tenant_id}' not found")

    now = utils.now_utc()
    updates: dict[str, Any] = {"updatedAt": utils.iso(now)}

    if "displayName" in body:
        updates["displayName"] = str(body["displayName"]).strip()
    if "status" in body:
        updates["status"] = lifecycle_logic.normalize_status(body["status"])
    if "tier" in body:
        updates["tier"] = lifecycle_logic.normalize_tier(body["tier"])
    if "monthlyBudgetUsd" in body:
        updates["monthlyBudgetUsd"] = utils.as_float(
            body["monthlyBudgetUsd"], field="monthlyBudgetUsd"
        )

    expression, names, values = db_utils.build_update_expression(updates)
    db = db_factory.db_for_tenant(tenant_id=tenant_id, caller=caller, app_id=None)
    db.update_item(
        db_factory.tenants_table_name(),
        db_utils.tenant_key(tenant_id),
        expression,
        values,
        expression_attribute_names=names,
    )

    # Re-fetch for response
    updated_item = db_utils.read_tenant_record(tenant_id=tenant_id, caller=caller)
    return http_utils.response(200, serialization.serialize_tenant(updated_item or item))


def handle_delete(
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    auth.require_admin(caller)

    item = db_utils.read_tenant_record(tenant_id=tenant_id, caller=caller)
    if item is None:
        return http_utils.error(404, "NOT_FOUND", f"Tenant '{tenant_id}' not found")

    now = utils.now_utc()
    # Soft delete: update status and set purge date
    updates = {
        "status": "deleted",
        "deletedAt": utils.iso(now),
        "purgeAtEpochSeconds": int(
            (now + timedelta(days=constants.DELETE_RETENTION_DAYS)).timestamp()
        ),
        "updatedAt": utils.iso(now),
    }

    expression, names, values = db_utils.build_update_expression(updates)
    db = db_factory.db_for_tenant(tenant_id=tenant_id, caller=caller, app_id=None)
    db.update_item(
        db_factory.tenants_table_name(),
        db_utils.tenant_key(tenant_id),
        expression,
        values,
        expression_attribute_names=names,
    )

    return http_utils.response(204, {})


def handle_tenant_provisioning_event(
    event: dict[str, Any],
    deps: models.TenantApiDependencies,
) -> dict[str, Any]:
    detail = event.get("detail", {})
    tenant_id = utils.str_or_none(detail.get("tenantId"))
    app_id = utils.str_or_none(detail.get("appId"))
    if not tenant_id:
        raise ValueError("tenantId missing in provisioning event")

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
        if "executionRoleArn" in detail:
            updates["executionRoleArn"] = str(detail["executionRoleArn"])
        if "memoryStoreArn" in detail:
            updates["memoryStoreArn"] = str(detail["memoryStoreArn"])
    elif status == "failed":
        updates["provisioningError"] = str(detail.get("error", "Unknown error"))

    # System update (no caller identity required for EventBridge trigger)
    # But db_for_tenant expects a caller for context derivation.
    # Using a dummy system caller.
    system_caller = models.CallerIdentity(
        tenant_id=None,
        app_id=None,
        tier=None,
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

    return {"status": "updated", "tenantId": tenant_id, "provisioningStatus": status}


def handle_health(deps: models.TenantApiDependencies) -> dict[str, Any]:
    # Basic service health
    _ = deps
    return http_utils.response(
        200,
        {
            "status": "operational",
            "timestamp": utils.iso(utils.now_utc()),
        },
    )


def handle_sessions(
    event: dict[str, Any],
    caller: models.CallerIdentity,
) -> dict[str, Any]:
    # Placeholder for session listing
    _ = event
    _ = caller
    return http_utils.response(200, {"items": []})


def handle_list_invites(
    caller: models.CallerIdentity,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    if not auth.can_read_tenant(caller, tenant_id) or not auth.can_manage_tenant_self_service(
        caller, tenant_id
    ):
        raise PermissionError("Access denied")

    db = db_factory.db_for_tenant(tenant_id=tenant_id, caller=caller, app_id=None)
    item = db.get_item(db_factory.tenants_table_name(), db_utils.tenant_key(tenant_id))
    if item is None:
        return http_utils.error(404, "NOT_FOUND", f"Tenant '{tenant_id}' not found")

    results = db.query(
        db_factory.tenants_table_name(),
        sk_condition=Key("SK").begins_with("INVITE#"),
    )

    invites = [
        {
            "inviteId": str(invite.get("inviteId", "")),
            "tenantId": tenant_id,
            "email": str(invite.get("email", "")),
            "role": str(invite.get("role", "Agent.Invoke")),
            "status": str(invite.get("status", "")),
            "expiresAt": invite.get("expiresAt"),
        }
        for invite in results.items
    ]
    return http_utils.response(200, {"items": invites})


def dispatch_routes(
    path: str,
    method: str,
    event: dict[str, Any],
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
    tenant_id: str | None,
) -> dict[str, Any] | None:
    if path == "/v1/tenants" and method == "POST":
        return handle_create(event, caller, deps)
    if tenant_id:
        if path == f"/v1/tenants/{tenant_id}" and method == "GET":
            return handle_read(caller, tenant_id=tenant_id)
        if path == f"/v1/tenants/{tenant_id}" and method == "PATCH":
            return handle_update(event, caller, deps, tenant_id=tenant_id)
        if path == f"/v1/tenants/{tenant_id}" and method == "DELETE":
            return handle_delete(caller, deps, tenant_id=tenant_id)
        if path == f"/v1/tenants/{tenant_id}/users/invites" and method == "GET":
            return handle_list_invites(caller, tenant_id=tenant_id)

        # Dispatch sub-resources (webhooks, etc.)
        if path.startswith(f"/v1/tenants/{tenant_id}/webhooks"):
            try:
                from src.tenant_api import webhook_registry
            except (ImportError, ValueError):
                from . import webhook_registry
            return webhook_registry.dispatch_routes(path, method, event, caller, deps, tenant_id)

    return None
