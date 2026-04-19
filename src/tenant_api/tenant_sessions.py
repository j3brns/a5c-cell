from __future__ import annotations

from typing import Any

try:
    from . import http_utils, models
except (ImportError, ValueError):  # pragma: no cover
    from src.tenant_api import http_utils, models


def handle_sessions(
    event: dict[str, Any],
    caller: models.CallerIdentity,
) -> dict[str, Any]:
    _ = caller
    query = event.get("queryStringParameters") or {}
    raw_limit = query.get("limit") if isinstance(query, dict) else None
    if raw_limit is not None:
        try:
            int(str(raw_limit))
        except (TypeError, ValueError):
            # Using a request ID here would be better but requires more context
            return http_utils.error(400, "BAD_REQUEST", "limit must be an integer")
    return http_utils.error(
        501,
        "NOT_IMPLEMENTED",
        "tenant-backed session tracking is not implemented",
    )
