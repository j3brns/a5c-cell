from __future__ import annotations

from typing import Any

from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.typing import LambdaContext

try:
    import handler as shared

    from . import http_utils
except (ImportError, ValueError):  # pragma: no cover
    from src.tenant_api import handler as shared
    from src.tenant_api import http_utils

logger = Logger(service="tenant-api-admin-ops")


@logger.inject_lambda_context(clear_state=True, log_event=False)
def lambda_handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    _ = context
    method = str(event.get("httpMethod") or "GET").upper()
    path = str(event.get("path") or "").rstrip("/")

    try:
        deps = shared._dependencies()
        caller = http_utils.caller_identity(event)
        logger.append_keys(appid=caller.app_id or "unknown", tenantid=caller.tenant_id or "unknown")

        if path == "/v1/platform/failover" and method == "POST":
            try:
                from . import ops_control
            except (ImportError, ValueError):
                from src.tenant_api import ops_control
            return ops_control.handle_platform_failover(event, caller, deps)

        if path == "/v1/platform/quota" and method == "GET":
            try:
                from . import ops_control
            except (ImportError, ValueError):
                from src.tenant_api import ops_control
            return ops_control.handle_platform_quota(event, caller, deps)

        return http_utils.error(404, "NOT_FOUND", "Route not found")
    except PermissionError as exc:
        return http_utils.error(403, "FORBIDDEN", str(exc))
    except ValueError as exc:
        return http_utils.error(400, "BAD_REQUEST", str(exc))
    except Exception:
        logger.exception("Unhandled error in admin ops handler")
        return http_utils.error(500, "INTERNAL_ERROR", "Internal server error")
