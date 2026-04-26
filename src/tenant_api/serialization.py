from __future__ import annotations

from typing import Any


def serialize_tenant(item: dict[str, Any]) -> dict[str, Any]:
    record = {
        "tenantId": str(item.get("tenantId", "")),
        "appId": str(item.get("appId", "")),
        "displayName": str(item.get("displayName", "")),
        "tier": str(item.get("tier", "")),
        "status": str(item.get("status", "")),
        "createdAt": item.get("createdAt"),
        "updatedAt": item.get("updatedAt"),
        "ownerEmail": item.get("ownerEmail"),
        "ownerTeam": item.get("ownerTeam"),
        "accountId": item.get("accountId"),
    }
    optional_fields = (
        "executionRoleArn",
        "memoryStoreArn",
        "runtimeRegion",
        "provisioningStatus",
        "provisioningUpdatedAt",
        "provisioningError",
        "apiKeySecretArn",
        "monthlyBudgetUsd",
        "deletedAt",
        "purgeAtEpochSeconds",
    )
    for field in optional_fields:
        if field in item and item[field] is not None:
            record[field] = item[field]
    return record
