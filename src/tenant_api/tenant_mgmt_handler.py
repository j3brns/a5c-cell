from __future__ import annotations

from typing import Any

from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.typing import LambdaContext

try:
    from . import composition, http_utils, utils
except (ImportError, ValueError):  # pragma: no cover
    from src.tenant_api import composition, http_utils, utils
from src.tenant_api.models import TenantApiDependencies

logger = Logger(service="tenant-api-mgmt")


@logger.inject_lambda_context(clear_state=True, log_event=False)
def lambda_handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    _ = context
    return handle_event(event)


def handle_event(
    event: dict[str, Any],
    *,
    dependencies: TenantApiDependencies | None = None,
) -> dict[str, Any]:
    detail_type = utils.str_or_none(event.get("detail-type"))
    source = utils.str_or_none(event.get("source"))

    try:
        runtime = composition.build_runtime(event, dependencies=dependencies)
        deps = runtime.deps

        if detail_type and source == "platform.tenant_provisioner":
            detail = event.get("detail") or {}
            tenant_id = (
                utils.str_or_none(detail.get("tenantId")) if isinstance(detail, dict) else None
            )
            app_id = utils.str_or_none(detail.get("appId")) if isinstance(detail, dict) else None
            logger.append_keys(appid=app_id or "unknown", tenantid=tenant_id or "unknown")
            try:
                from . import tenant_lifecycle
            except (ImportError, ValueError):
                from src.tenant_api import tenant_lifecycle
            return tenant_lifecycle.handle_tenant_provisioning_event(event, deps)

        caller = runtime.caller
        logger.append_keys(appid=caller.app_id or "unknown", tenantid=caller.tenant_id or "unknown")

        try:
            from . import tenant_lifecycle
        except (ImportError, ValueError):
            from src.tenant_api import tenant_lifecycle

        response = tenant_lifecycle.dispatch_routes(
            runtime.path, runtime.method, event, caller, deps, runtime.tenant_id
        )
        if response:
            return response

        return http_utils.error(404, "NOT_FOUND", "Route not found")
    except PermissionError as exc:
        return http_utils.error(403, "FORBIDDEN", str(exc))
    except ValueError as exc:
        return http_utils.error(400, "BAD_REQUEST", str(exc))
    except Exception:
        logger.exception("Unhandled error in tenant mgmt handler")
        return http_utils.error(500, "INTERNAL_ERROR", "Internal server error")
