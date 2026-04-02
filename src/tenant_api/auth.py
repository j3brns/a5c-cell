from __future__ import annotations
from src.tenant_api.models import CallerIdentity
from src.tenant_api.constants import SELF_SERVICE_ADMIN_ROLES

def require_admin(caller: CallerIdentity) -> None:
    if not caller.is_admin:
        raise PermissionError("Platform.Admin or Platform.Operator role required")

def require_platform_actor(caller: CallerIdentity) -> None:
    if not caller.is_platform_actor:
        raise PermissionError("Platform tenant context required")

def can_read_tenant(caller: CallerIdentity, tenant_id: str) -> bool:
    return caller.is_admin or caller.tenant_id == tenant_id

def is_self_service_admin(caller: CallerIdentity) -> bool:
    return bool(caller.roles & SELF_SERVICE_ADMIN_ROLES)

def can_manage_tenant_self_service(caller: CallerIdentity, tenant_id: str) -> bool:
    return is_self_service_admin(caller)
