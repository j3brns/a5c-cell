from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from src.tenant_api.constants import (
    AWS_ACCOUNT_ID_PATTERN,
    RESERVED_TENANT_IDS,
    TENANT_ID_MAX_LENGTH,
    TENANT_ID_MIN_LENGTH,
    TENANT_ID_PATTERN,
)
from src.tenant_api.utils import str_or_none


def canonical_tenant_id(value: Any, *, allow_reserved: bool = False) -> str:
    tenant_id = str_or_none(value)
    if tenant_id is None:
        raise ValueError("tenantId is required")

    normalized = tenant_id.lower()
    if len(normalized) < TENANT_ID_MIN_LENGTH or len(normalized) > TENANT_ID_MAX_LENGTH:
        raise ValueError("tenantId must be 3-32 characters")
    if "--" in normalized:
        raise ValueError("tenantId must not contain consecutive hyphens")
    if normalized in RESERVED_TENANT_IDS and not (allow_reserved and normalized == "platform"):
        raise ValueError("tenantId is reserved")
    if not TENANT_ID_PATTERN.fullmatch(normalized):
        raise ValueError("tenantId must match ^[a-z](?:[a-z0-9-]{1,30}[a-z0-9])$")
    return normalized


def require_aws_account_id(value: Any, *, field: str) -> str:
    account_id = str_or_none(value)
    if account_id is None:
        raise ValueError(f"{field} is required")
    if not AWS_ACCOUNT_ID_PATTERN.fullmatch(account_id):
        raise ValueError(f"{field} must match ^[0-9]{{12}}$")
    return account_id


def parse_utc_timestamp(value: Any, *, field: str) -> datetime:
    text = str_or_none(value)
    if text is None:
        raise ValueError(f"{field} must be an ISO 8601 UTC timestamp")
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO 8601 UTC timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(UTC)


def parse_optional_utc_timestamp(value: Any, *, field: str) -> datetime | None:
    if str_or_none(value) is None:
        return None
    return parse_utc_timestamp(value, field=field)
