from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from botocore.exceptions import ClientError

from src.tenant_api import db_factory, http_utils, utils, validation
from src.tenant_api.models import CallerIdentity, TenantApiDependencies


@dataclass(frozen=True)
class TenantApiRuntime:
    deps: TenantApiDependencies
    caller: CallerIdentity
    method: str
    path: str
    tenant_id: str | None
    detail_type: str | None
    source: str | None
    detail: dict[str, Any] | None


def db_for_tenant(*, tenant_id: str, caller: CallerIdentity, app_id: str | None):
    return db_factory.db_for_tenant(tenant_id=tenant_id, caller=caller, app_id=app_id)


def control_plane_db(caller: CallerIdentity):
    return db_factory.control_plane_db(caller)


def now_utc():
    return utils.now_utc()


def optional_ssm_parameter(ssm: Any, name: str) -> str | None:
    try:
        response = ssm.get_parameter(Name=name)
        return utils.str_or_none(response.get("Parameter", {}).get("Value"))
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ParameterNotFound":
            return None
        raise


def required_ssm_parameter(ssm: Any, name: str) -> str:
    val = optional_ssm_parameter(ssm, name)
    if val is None:
        raise ValueError(f"SSM parameter {name} is empty")
    return val


def build_runtime(
    event: dict[str, Any],
    *,
    dependencies: TenantApiDependencies,
) -> TenantApiRuntime:
    caller = http_utils.caller_identity(event)
    method = str(
        event.get("httpMethod")
        or event.get("requestContext", {}).get("http", {}).get("method")
        or "GET"
    ).upper()
    path = str(
        event.get("path") or event.get("requestContext", {}).get("http", {}).get("path") or ""
    ).rstrip("/")
    detail_type = utils.str_or_none(event.get("detail-type"))
    source = utils.str_or_none(event.get("source"))
    detail = event.get("detail") if isinstance(event.get("detail"), dict) else None

    path_params = event.get("pathParameters") or {}
    tenant_id = (
        validation.canonical_tenant_id(path_params.get("tenantId"), allow_reserved=caller.is_admin)
        if path_params.get("tenantId")
        else None
    )
    if tenant_id and isinstance(path_params, dict):
        raw_tenant_id = path_params.get("tenantId")
        if raw_tenant_id:
            path = path.replace(str(raw_tenant_id), tenant_id)

    return TenantApiRuntime(
        deps=dependencies,
        caller=caller,
        method=method,
        path=path,
        tenant_id=tenant_id,
        detail_type=detail_type,
        source=source,
        detail=detail,
    )
