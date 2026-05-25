from __future__ import annotations

from typing import Any

PLATFORM_DIAGNOSTICS_TOOLS = frozenset(
    {
        "get_platform_health",
        "get_tenant_status",
        "get_recent_errors",
        "get_runbook_guidance",
    }
)
PLATFORM_DIAGNOSTICS_ROLES = frozenset({"Platform.Admin", "Platform.Operator"})


def _roles_from_payload(payload: dict[str, Any]) -> set[str]:
    roles = payload.get("roles", [])
    if isinstance(roles, str):
        return {roles}
    if isinstance(roles, list):
        return {str(role) for role in roles}
    return set()


def _extract_tool_name(method: str, body: dict[str, Any]) -> str | None:
    if method != "tools/call":
        return None
    params = body.get("params", {})
    if not isinstance(params, dict):
        return None
    name = params.get("name") or params.get("toolName")
    return str(name) if name else None


def _effective_tenant_id(*, tenant_id: str, tool_name: str | None, roles: set[str]) -> str:
    if tool_name in PLATFORM_DIAGNOSTICS_TOOLS and roles.intersection(PLATFORM_DIAGNOSTICS_ROLES):
        return "platform"
    return tenant_id


def _safe_header_value(name: str, value: str) -> str:
    if "\r" in value or "\n" in value:
        raise ValueError(f"Invalid newline in injected header {name}")
    return value


def process_request(
    event: dict[str, Any],
    *,
    parse_body: Any,
    normalized_headers: Any,
    get_header: Any,
    error_response: Any,
    validate_bearer_token: Any,
    validate_tool_access: Any,
    issue_scoped_token: Any,
    logger: Any,
    jwt_module: Any,
) -> dict[str, Any]:
    mcp = event.get("mcp", {})
    if not isinstance(mcp, dict):
        mcp = {}
    gateway_request = mcp.get("gatewayRequest", {})
    if not isinstance(gateway_request, dict):
        gateway_request = {}
    request_body = parse_body(gateway_request.get("body"))
    request_headers = normalized_headers(gateway_request.get("headers", {}))
    jsonrpc_id = request_body.get("id")
    authorization = get_header(request_headers, "Authorization")
    if not authorization or not authorization.startswith("Bearer "):
        return error_response(
            gateway_request=gateway_request,
            request_id=jsonrpc_id,
            status_code=401,
            code=-32001,
            message="Missing or invalid Bearer token",
        )
    user_token = authorization.split(" ", 1)[1]
    try:
        payload = validate_bearer_token(user_token)
    except jwt_module.ExpiredSignatureError:
        return error_response(
            gateway_request=gateway_request,
            request_id=jsonrpc_id,
            status_code=401,
            code=-32001,
            message="Bearer token expired",
        )
    except jwt_module.InvalidTokenError:
        return error_response(
            gateway_request=gateway_request,
            request_id=jsonrpc_id,
            status_code=401,
            code=-32001,
            message="Bearer token validation failed",
        )
    except Exception:
        logger.exception("Unexpected JWT validation error")
        return error_response(
            gateway_request=gateway_request,
            request_id=jsonrpc_id,
            status_code=401,
            code=-32001,
            message="Bearer token validation failed",
        )
    if payload is None:
        return error_response(
            gateway_request=gateway_request,
            request_id=jsonrpc_id,
            status_code=401,
            code=-32001,
            message="Bearer token validation failed",
        )
    tenant_id = str(payload.get("tenantid") or "")
    app_id = str(payload.get("appid") or "")
    tier = str(payload.get("tier") or "basic")
    acting_sub = str(payload.get("sub") or "unknown")
    roles = _roles_from_payload(payload)
    if not tenant_id or not app_id:
        return error_response(
            gateway_request=gateway_request,
            request_id=jsonrpc_id,
            status_code=401,
            code=-32001,
            message="Missing tenant context in token",
        )
    method = str(request_body.get("method") or "")
    tool_name = _extract_tool_name(method, request_body)
    tenant_id = _effective_tenant_id(tenant_id=tenant_id, tool_name=tool_name, roles=roles)
    logger.append_keys(tenant_id=tenant_id, app_id=app_id)
    tool_name, tool_error = validate_tool_access(
        method=method,
        request_body=request_body,
        gateway_request=gateway_request,
        request_id=jsonrpc_id,
        tenant_id=tenant_id,
        tier=tier,
    )
    if tool_error is not None:
        return tool_error
    scope_tool = tool_name if tool_name else method
    scoped_token = issue_scoped_token(
        tenant_id=tenant_id,
        app_id=app_id,
        tier=tier,
        acting_sub=acting_sub,
        scope_tool=scope_tool,
        mcp_session_id=get_header(request_headers, "Mcp-Session-Id"),
        mcp_request_id=jsonrpc_id,
    )
    try:
        injected_headers = {
            "Authorization": f"Bearer {_safe_header_value('Authorization', scoped_token)}",
            "x-tenant-id": _safe_header_value("x-tenant-id", tenant_id),
            "x-app-id": _safe_header_value("x-app-id", app_id),
            "x-tier": _safe_header_value("x-tier", tier),
            "x-acting-sub": _safe_header_value("x-acting-sub", acting_sub),
        }
    except ValueError:
        logger.warning("Rejected unsafe injected gateway header value")
        return error_response(
            gateway_request=gateway_request,
            request_id=jsonrpc_id,
            status_code=401,
            code=-32001,
            message="Invalid tenant context in token",
        )
    transformed_headers = {
        key: value for key, value in request_headers.items() if key.lower() != "authorization"
    }
    transformed_headers.update(injected_headers)
    transformed_request = dict(gateway_request)
    transformed_request["headers"] = transformed_headers
    transformed_request["body"] = request_body
    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {"transformedGatewayRequest": transformed_request},
    }
