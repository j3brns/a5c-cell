from __future__ import annotations

import sys
from typing import Any

from aws_lambda_powertools import Logger

from src.bridge.constants import TENANT_EXECUTION_ROLE_PARAM_TEMPLATE

logger = Logger(service="bridge-role-resolver")


def resolve_tenant_execution_role(
    ssm: Any,
    *,
    tenant_id: str,
) -> str | None:
    """Resolve the execution role ARN for a tenant via SSM or metadata."""
    param_name = TENANT_EXECUTION_ROLE_PARAM_TEMPLATE.format(tenant_id=tenant_id)
    try:
        response = ssm.get_parameter(Name=param_name)
        return response.get("Parameter", {}).get("Value")
    except Exception:
        logger.warning(
            "Failed to resolve tenant execution role via SSM", extra={"tenant_id": tenant_id}
        )
        return None


def assume_tenant_role(
    sts: Any,
    role_arn_arg: str | None = None,
    *,
    role_arn: str | None = None,
    session_name: str | None = None,
    external_id: str | None = None,
) -> dict[str, Any]:
    """Assume a tenant execution role via STS."""
    if role_arn is None and role_arn_arg is not None and isinstance(sts, str):
        handler_module = sys.modules.get("src.bridge.handler")
        get_sts = getattr(handler_module, "get_sts", None)
        if get_sts is None:
            raise TypeError("legacy assume_tenant_role requires src.bridge.handler.get_sts")
        tenant_id = sts
        sts = get_sts()
        role_arn = role_arn_arg
        session_name = f"invoke-{tenant_id[:8]}"
    if role_arn is None or session_name is None:
        raise TypeError("role_arn and session_name are required")

    kwargs = {
        "RoleArn": role_arn,
        "RoleSessionName": session_name[:64],  # STS limit
    }
    if external_id:
        kwargs["ExternalId"] = external_id

    try:
        response = sts.assume_role(**kwargs)
        return response["Credentials"]
    except Exception as exc:
        logger.error("Failed to assume tenant role", extra={"role_arn": role_arn})
        raise exc
