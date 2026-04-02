from __future__ import annotations
import uuid
from datetime import UTC, datetime
from typing import Any
from aws_lambda_powertools import Logger
from src.bridge.constants import OPS_LOCKS_TABLE

logger = Logger(service="bridge-lock-manager")

def acquire_lock(
    dynamodb: Any,
    *,
    lock_name: str,
    identity: str,
    ttl_seconds: int = 300,
) -> str | None:
    """Acquire a distributed lock in DynamoDB."""
    lock_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    ttl = int(now.timestamp()) + ttl_seconds
    table = dynamodb.Table(OPS_LOCKS_TABLE)

    try:
        table.put_item(
            Item={
                "PK": f"LOCK#{lock_name}",
                "SK": "METADATA",
                "lock_id": lock_id,
                "identity": identity,
                "acquired_at": now.isoformat(),
                "expires_at": ttl,
            },
            ConditionExpression="attribute_not_exists(PK) OR expires_at < :now",
            ExpressionAttributeValues={":now": int(now.timestamp())},
        )
        return lock_id
    except Exception:
        return None

def release_lock(
    dynamodb: Any,
    *,
    lock_name: str,
    lock_id: str,
) -> bool:
    """Release a distributed lock in DynamoDB."""
    table = dynamodb.Table(OPS_LOCKS_TABLE)
    try:
        table.delete_item(
            Key={"PK": f"LOCK#{lock_name}", "SK": "METADATA"},
            ConditionExpression="lock_id = :lock_id",
            ExpressionAttributeValues={":lock_id": lock_id},
        )
        return True
    except Exception:
        return False
