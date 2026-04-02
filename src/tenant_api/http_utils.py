from __future__ import annotations

import json
from typing import Any

from src.tenant_api.models import CallerIdentity
from src.tenant_api.utils import json_default, str_or_none


def response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=json_default),
    }

def error(status_code: int, code: str, message: str) -> dict[str, Any]:
    return response(status_code, {"error": {"code": code, "message": message}})

def require_json_body(event: dict[str, Any]) -> dict[str, Any]:
    raw_body = event.get("body")
    if raw_body is None:
        raise ValueError("Request body is required")
    if not isinstance(raw_body, str):
        raise ValueError("Request body must be a JSON string")
    try:
        body = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise ValueError("Malformed JSON body") from exc
    if not isinstance(body, dict):
        raise ValueError("JSON body must be an object")
    return body

def get_authorizer_map(event: dict[str, Any]) -> dict[str, Any]:
    request_context = event.get("requestContext", {})
    authorizer = request_context.get("authorizer", {})
    if not isinstance(authorizer, dict):
        return {}
    if "lambda" in authorizer and isinstance(authorizer["lambda"], dict):
        return authorizer["lambda"]
    return authorizer

def parse_roles(value: Any) -> frozenset[str]:
    if value is None:
        return frozenset()
    if isinstance(value, list):
        return frozenset(str(v).strip() for v in value if str(v).strip())
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            if isinstance(decoded, list):
                return frozenset(str(v).strip() for v in decoded if str(v).strip())
        except json.JSONDecodeError:
            pass
        normalized = value.replace(",", " ").split()
        return frozenset(part.strip() for part in normalized if part.strip())
    return frozenset()

def caller_identity(event: dict[str, Any]) -> CallerIdentity:
    auth = get_authorizer_map(event)
    return CallerIdentity(
        tenant_id=str_or_none(auth.get("tenantid") or auth.get("tenantId")),
        app_id=str_or_none(auth.get("appid") or auth.get("appId")),
        tier=str_or_none(auth.get("tier")),
        sub=str_or_none(auth.get("sub")),
        roles=parse_roles(auth.get("roles")),
        usage_identifier_key=str_or_none(
            auth.get("usageIdentifierKey") or auth.get("usage_identifier_key")
        ),
    )
