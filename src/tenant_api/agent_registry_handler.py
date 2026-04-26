from __future__ import annotations

from typing import Any

from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.typing import LambdaContext

try:
    from . import composition, http_utils
except (ImportError, ValueError):  # pragma: no cover
    from src.tenant_api import composition, http_utils
from src.tenant_api.models import TenantApiDependencies

logger = Logger(service="tenant-api-agent-registry-handler")


@logger.inject_lambda_context(clear_state=True, log_event=False)
def lambda_handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    _ = context
    return handle_event(event)


def handle_event(
    event: dict[str, Any],
    *,
    dependencies: TenantApiDependencies | None = None,
) -> dict[str, Any]:
    try:
        runtime = composition.build_runtime(event, dependencies=dependencies)
        deps = runtime.deps
        caller = runtime.caller
        logger.append_keys(appid=caller.app_id or "unknown", tenantid=caller.tenant_id or "unknown")

        try:
            from . import agent_registry
        except (ImportError, ValueError):
            from src.tenant_api import agent_registry

        response = agent_registry.dispatch_routes(runtime.path, runtime.method, event, caller, deps)
        if response:
            return response

        return http_utils.error(404, "NOT_FOUND", "Route not found")
    except PermissionError as exc:
        return http_utils.error(403, "FORBIDDEN", str(exc))
    except ValueError as exc:
        return http_utils.error(400, "BAD_REQUEST", str(exc))
    except Exception:
        logger.exception("Unhandled error in agent registry handler")
        return http_utils.error(500, "INTERNAL_ERROR", "Internal server error")
