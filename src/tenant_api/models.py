from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.tenant_api.constants import ADMIN_ROLES


@dataclass(frozen=True)
class CallerIdentity:
    tenant_id: str | None
    app_id: str | None
    tier: str | None
    sub: str | None
    roles: frozenset[str]
    usage_identifier_key: str | None

    @property
    def is_admin(self) -> bool:
        return bool(self.roles & ADMIN_ROLES)

    @property
    def is_platform_actor(self) -> bool:
        from src.tenant_api.constants import PLATFORM_TENANT_ID

        return self.tenant_id == PLATFORM_TENANT_ID


@dataclass(frozen=True)
class TenantApiDependencies:
    secretsmanager: Any
    events: Any
    ssm: Any
    awslambda: Any
    usage_client: Any
    memory_provisioner: Any
    platform_quota_client: Any


@dataclass(frozen=True)
class TenantCreateInput:
    tenant_id: str
    app_id: str
    display_name: str
    tier: str
    owner_email: str
    owner_team: str
    account_id: str
    monthly_budget_usd: Any | None = None


@dataclass(frozen=True)
class TenantListInput:
    status_filter: str | None = None
    tier_filter: str | None = None


@dataclass(frozen=True)
class TenantUpdateInput:
    provided_fields: frozenset[str] = field(default_factory=frozenset)
    display_name: Any | None = None
    status: Any | None = None
    tier: Any | None = None
    monthly_budget_usd: Any | None = None

    def __post_init__(self) -> None:
        known_values = {
            "displayName": self.display_name,
            "status": self.status,
            "tier": self.tier,
            "monthlyBudgetUsd": self.monthly_budget_usd,
        }
        provided = self.provided_fields
        if not provided:
            provided = frozenset(
                field_name for field_name, value in known_values.items() if value is not None
            )
            object.__setattr__(self, "provided_fields", provided)

        unmarked = [
            field_name
            for field_name, value in known_values.items()
            if value is not None and field_name not in provided
        ]
        if unmarked:
            raise ValueError(f"Unmarked update field(s): {', '.join(unmarked)}")

    def includes(self, field_name: str) -> bool:
        return field_name in self.provided_fields
