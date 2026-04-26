from __future__ import annotations

import base64
import json
import os
import secrets
from datetime import timedelta
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from data_access.models import TenantStatus

try:
    from . import (
        auth,
        constants,
        db_factory,
        db_utils,
        events,
        http_utils,
        models,
        secrets_manager,
        serialization,
        tenant_audit_exports,
        tenant_invites,
        tenant_records,
        tenant_sessions,
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
        models,
        secrets_manager,
        serialization,
        tenant_audit_exports,
        tenant_invites,
        tenant_records,
        tenant_sessions,
        utils,
        validation,
    )


def _tenant_create_input_from_event(event: dict[str, Any]) -> models.TenantCreateInput:
    body = http_utils.require_json_body(event)
    required = ["tenantId", "appId", "displayName", "tier", "ownerEmail", "ownerTeam", "accountId"]
    missing = [field for field in required if utils.str_or_none(body.get(field)) is None]
    if missing:
        raise ValueError(f"Missing required field(s): {', '.join(missing)}")

    return models.TenantCreateInput(
        tenant_id=validation.canonical_tenant_id(body["tenantId"]),
        app_id=str(body["appId"]).strip(),
        display_name=str(body["displayName"]).strip(),
        tier=str(body["tier"]).strip(),
        owner_email=str(body["ownerEmail"]).strip(),
        owner_team=str(body["ownerTeam"]).strip(),
        account_id=str(body["accountId"]).strip(),
        monthly_budget_usd=body.get("monthlyBudgetUsd"),
    )


def _encode_pagination_token(last_evaluated_key: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(last_evaluated_key, default=str).encode()).decode()


def _decode_pagination_token(token: str) -> dict[str, Any]:
    return json.loads(base64.urlsafe_b64decode(token.encode()))


def _tenant_list_input_from_event(event: dict[str, Any]) -> models.TenantListInput:
    query = event.get("queryStringParameters") or {}
    if not isinstance(query, dict):
        query = {}
    limit: int | None = None
    limit_raw = utils.str_or_none(query.get("limit"))
    if limit_raw is not None:
        try:
            limit = int(limit_raw)
        except (ValueError, TypeError):
            pass
    return models.TenantListInput(
        status_filter=utils.str_or_none(query.get("status")),
        tier_filter=utils.str_or_none(query.get("tier")),
        limit=limit,
        next_token=utils.str_or_none(query.get("nextToken")),
    )


def _tenant_update_input_from_event(event: dict[str, Any]) -> models.TenantUpdateInput:
    body = http_utils.require_json_body(event)
    return models.TenantUpdateInput(
        provided_fields=frozenset(body),
        display_name=body.get("displayName"),
        status=body.get("status"),
        tier=body.get("tier"),
        monthly_budget_usd=body.get("monthlyBudgetUsd"),
    )


def handle_create(
    event: dict[str, Any],
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
) -> dict[str, Any]:
    response = tenant_records.handle_create(_tenant_create_input_from_event(event), caller, deps)
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
    request: models.TenantListInput,
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

    limit = request.limit
    if limit is not None:
        if limit < 1 or limit > constants.TENANT_LIST_MAX_PAGE_SIZE:
            return http_utils.error(
                400,
                "INVALID_LIMIT",
                f"limit must be between 1 and {constants.TENANT_LIST_MAX_PAGE_SIZE}",
            )
    else:
        limit = constants.TENANT_LIST_DEFAULT_PAGE_SIZE

    exclusive_start_key: dict[str, Any] | None = None
    if request.next_token:
        try:
            exclusive_start_key = _decode_pagination_token(request.next_token)
        except Exception:
            return http_utils.error(400, "INVALID_TOKEN", "Invalid pagination token")

    db = db_factory.control_plane_db(caller)
    result = db.scan(
        db_factory.tenants_table_name(),
        limit=limit,
        exclusive_start_key=exclusive_start_key,
    )

    records = [
        serialization.serialize_tenant(item)
        for item in result.items
        if item.get("SK") == "METADATA"
    ]
    if request.status_filter:
        records = [r for r in records if r.get("status") == request.status_filter]
    if request.tier_filter:
        records = [r for r in records if r.get("tier") == request.tier_filter]

    next_token = (
        _encode_pagination_token(result.last_evaluated_key) if result.last_evaluated_key else None
    )
    return http_utils.response(200, {"items": records, "nextToken": next_token})


def handle_update(
    event: dict[str, Any],
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    response = tenant_records.handle_update(
        _tenant_update_input_from_event(event), caller, deps, tenant_id=tenant_id
    )
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

    detail_type = event.get("detail-type")
    status = utils.str_or_none(detail.get("provisioningStatus"))
    if status is None:
        status = utils.str_or_none(detail.get("status"))

    if status is None:
        if detail_type == "tenant.provisioning_failed":
            status = "failed"
        elif detail_type == "tenant.provisioned":
            status = "ready"

    if status not in constants.TENANT_PROVISIONING_STATUSES:
        raise ValueError(f"Invalid provisioning status: {status}")

    detail_account_id = utils.str_or_none(detail.get("accountId"))
    if detail_account_id is not None:
        validation.require_platform_home_account_id(detail_account_id)

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
        memory_store_arn = detail.get("memoryStoreArn") or detail.get("MemoryStoreArn")
        if memory_store_arn:
            updates["memoryStoreArn"] = str(memory_store_arn)

    if status == "failed":
        reason = (
            detail.get("reason")
            or detail.get("provisioningError")
            or detail.get("ProvisioningError")
        )
        if reason:
            updates["provisioningError"] = str(reason)

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
    return http_utils.response(200, {"tenant": serialization.serialize_tenant(updated_item)})


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

    app_id = str(item.get("appId", ""))
    secret_arn = str(item.get("apiKeySecretArn", ""))
    version_id = secrets_manager.rotate_api_key_secret(
        deps, secret_arn=secret_arn, tenant_id=tenant_id, app_id=app_id
    )

    now = utils.now_utc()
    updates = {"updatedAt": utils.iso(now)}
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
    return http_utils.response(200, {"tenantId": tenant_id, "versionId": version_id})


def handle_invite_user(
    event: dict[str, Any],
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    def _is_conditional_failure(exc: ClientError) -> bool:
        return exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"

    def _invite_from_lookup(existing_lookup: dict[str, Any]) -> dict[str, Any]:
        return {
            **db_utils.invite_key(tenant_id, str(existing_lookup["inviteId"])),
            "inviteId": str(existing_lookup["inviteId"]),
            "tenantId": tenant_id,
            "email": str(existing_lookup.get("email", normalized_email)),
            "normalizedEmail": str(existing_lookup.get("normalizedEmail", normalized_email)),
            "role": str(existing_lookup.get("role", "Agent.Invoke")),
            "status": str(existing_lookup.get("status", "pending")),
            "expiresAt": existing_lookup.get("expiresAt"),
            "ttl": existing_lookup.get("ttl"),
            "createdAt": existing_lookup.get("createdAt"),
            "updatedAt": existing_lookup.get("updatedAt"),
            "actorSub": existing_lookup.get("actorSub"),
            "actorAppId": existing_lookup.get("actorAppId"),
            "notificationStatus": str(existing_lookup.get("notificationStatus", "pending")),
            "notificationError": existing_lookup.get("notificationError"),
            "notificationFailedAt": existing_lookup.get("notificationFailedAt"),
            "notifiedAt": existing_lookup.get("notifiedAt"),
        }

    def _deliver_invite_notification(
        db: Any,
        invite: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            event_response = (
                events.put_event(
                    deps,
                    detail_type="tenant.user_invited",
                    detail={
                        "tenantId": tenant_id,
                        "inviteId": str(invite["inviteId"]),
                        "email": str(invite["email"]),
                        "role": str(invite["role"]),
                    },
                )
                or {}
            )
        except (BotoCoreError, ClientError) as exc:
            return _notification_failure_response(
                db,
                invite,
                getattr(exc, "response", {})
                .get("Error", {})
                .get(
                    "Code",
                    type(exc).__name__,
                ),
            )

        if int(event_response.get("FailedEntryCount", 0)) > 0:
            first_failed = (event_response.get("Entries") or [{}])[0]
            error_code = str(first_failed.get("ErrorCode") or "EventBridgeDeliveryFailed")
            return _notification_failure_response(db, invite, error_code)

        sent_time = utils.iso(utils.now_utc())
        db.update_item(
            db_factory.tenants_table_name(),
            db_utils.invite_key(tenant_id, str(invite["inviteId"])),
            (
                "SET notificationStatus = :status, notificationError = :error, "
                "notifiedAt = :notified_at, updatedAt = :updated_at"
            ),
            {
                ":status": "sent",
                ":error": None,
                ":notified_at": sent_time,
                ":updated_at": sent_time,
            },
            condition_expression="attribute_exists(PK)",
        )
        db.update_item(
            db_factory.tenants_table_name(),
            lookup_key,
            (
                "SET notificationStatus = :status, notificationError = :error, "
                "notifiedAt = :notified_at, updatedAt = :updated_at"
            ),
            {
                ":status": "sent",
                ":error": None,
                ":notified_at": sent_time,
                ":updated_at": sent_time,
            },
            condition_expression="attribute_exists(PK)",
        )
        invite["notificationStatus"] = "sent"
        invite["notificationError"] = None
        invite.pop("notificationFailedAt", None)
        invite["notifiedAt"] = sent_time
        invite["updatedAt"] = sent_time
        return http_utils.response(202, {"invite": invite})

    def _handle_existing_lookup(existing_lookup: dict[str, Any] | None) -> dict[str, Any] | None:
        if existing_lookup is None:
            return None
        if str(existing_lookup.get("status", "")) != "pending":
            db.delete_item(db_factory.tenants_table_name(), lookup_key)
            return None
        existing_invite = db.get_item(
            db_factory.tenants_table_name(),
            db_utils.invite_key(tenant_id, str(existing_lookup.get("inviteId", ""))),
        )
        if existing_invite is None:
            existing_invite = _invite_from_lookup(existing_lookup)
            try:
                db.put_item(
                    db_factory.tenants_table_name(),
                    existing_invite,
                    condition_expression="attribute_not_exists(PK)",
                )
            except ClientError as exc:
                if not _is_conditional_failure(exc):
                    raise
                existing_invite = db.get_item(
                    db_factory.tenants_table_name(),
                    db_utils.invite_key(tenant_id, str(existing_lookup.get("inviteId", ""))),
                )
                if existing_invite is None:
                    return http_utils.error(409, "CONFLICT", "Pending invite already exists")
            return _deliver_invite_notification(db, existing_invite)
        if str(existing_invite.get("status", "")) != "pending":
            db.delete_item(db_factory.tenants_table_name(), lookup_key)
            return None
        if str(existing_invite.get("notificationStatus", "")) == "failed":
            return _deliver_invite_notification(db, existing_invite)
        return http_utils.response(202, {"invite": existing_invite})

    def _notification_failure_response(
        db: Any,
        invite: dict[str, Any],
        error_code: str,
    ) -> dict[str, Any]:
        failure_time = utils.iso(utils.now_utc())
        db.update_item(
            db_factory.tenants_table_name(),
            db_utils.invite_key(tenant_id, str(invite["inviteId"])),
            (
                "SET notificationStatus = :status, notificationError = :error, "
                "notificationFailedAt = :failed_at, updatedAt = :updated_at"
            ),
            {
                ":status": "failed",
                ":error": error_code,
                ":failed_at": failure_time,
                ":updated_at": failure_time,
            },
            condition_expression="attribute_exists(PK)",
        )
        db.update_item(
            db_factory.tenants_table_name(),
            lookup_key,
            (
                "SET notificationStatus = :status, notificationError = :error, "
                "notificationFailedAt = :failed_at, updatedAt = :updated_at"
            ),
            {
                ":status": "failed",
                ":error": error_code,
                ":failed_at": failure_time,
                ":updated_at": failure_time,
            },
            condition_expression="attribute_exists(PK)",
        )
        invite["notificationStatus"] = "failed"
        invite["notificationError"] = error_code
        invite["notificationFailedAt"] = failure_time
        invite["updatedAt"] = failure_time
        return http_utils.error(
            502,
            "EVENT_DELIVERY_FAILED",
            "Invite notification failed after persistence",
        )

    if not auth.can_manage_tenant_self_service(caller, tenant_id):
        raise PermissionError("Access denied")

    body = http_utils.require_json_body(event)
    normalized_email = str(body.get("email", "")).strip().lower()
    if not normalized_email or "@" not in normalized_email:
        raise ValueError("Valid email is required")

    role = str(body.get("role") or "Agent.Invoke")
    if role not in constants.ALLOWED_TENANT_INVITE_ROLES:
        return http_utils.error(400, "BAD_REQUEST", "role must be one of: Agent.Invoke")

    if db_utils.read_tenant_record(tenant_id=tenant_id, caller=caller, app_id=None) is None:
        return http_utils.error(404, "NOT_FOUND", f"Tenant '{tenant_id}' not found")

    db = db_factory.db_for_tenant(tenant_id=tenant_id, caller=caller, app_id=None)
    lookup_key = db_utils.invite_email_lookup_key(tenant_id, normalized_email)
    existing_response = _handle_existing_lookup(
        db.get_item(db_factory.tenants_table_name(), lookup_key)
    )
    if existing_response is not None:
        return existing_response

    now = utils.now_utc()
    expires_at = now + timedelta(days=7)
    invite_id = f"inv-{now.strftime('%Y%m%d')}-{secrets.token_hex(4)}"
    invite = {
        **db_utils.invite_key(tenant_id, invite_id),
        "inviteId": invite_id,
        "tenantId": tenant_id,
        "email": normalized_email,
        "normalizedEmail": normalized_email,
        "role": role,
        "status": "pending",
        "expiresAt": utils.iso(expires_at),
        "ttl": int(expires_at.timestamp()),
        "createdAt": utils.iso(now),
        "updatedAt": utils.iso(now),
        "actorSub": caller.sub,
        "actorAppId": caller.app_id,
        "notificationStatus": "pending",
        "notificationError": None,
    }
    lookup_item = {
        **invite,
        **lookup_key,
    }
    try:
        db.put_item(
            db_factory.tenants_table_name(),
            lookup_item,
            condition_expression="attribute_not_exists(PK)",
        )
    except ClientError as exc:
        if not _is_conditional_failure(exc):
            raise
        existing_response = _handle_existing_lookup(
            db.get_item(db_factory.tenants_table_name(), lookup_key)
        )
        if existing_response is not None:
            return existing_response
        db.put_item(
            db_factory.tenants_table_name(),
            lookup_item,
            condition_expression="attribute_not_exists(PK)",
        )

    try:
        db.put_item(
            db_factory.tenants_table_name(),
            invite,
            condition_expression="attribute_not_exists(PK)",
        )
    except ClientError as exc:
        if _is_conditional_failure(exc):
            existing_response = _handle_existing_lookup(
                db.get_item(db_factory.tenants_table_name(), lookup_key)
            )
            if existing_response is not None:
                return existing_response
        db.delete_item(db_factory.tenants_table_name(), lookup_key)
        raise
    except Exception:
        db.delete_item(db_factory.tenants_table_name(), lookup_key)
        raise

    return _deliver_invite_notification(db, invite)


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
    # 2. Tenant routes
    if path == "/v1/tenants" and method == "POST":
        return handle_create(event, caller, deps)
    if path == "/v1/tenants" and method == "GET":
        return handle_list_tenants(_tenant_list_input_from_event(event), caller, deps)

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
