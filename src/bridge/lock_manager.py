from __future__ import annotations

import os
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from aws_lambda_powertools import Logger
from data_access import ControlPlaneDynamoDB, TenantContext, TenantTier

from src.bridge.constants import (
    FAILOVER_LOCK_NAME,
    OPS_LOCKS_TABLE,
    RUNTIME_REGION_PARAM,
)
from src.bridge.runtime_dependencies import get_config, get_ssm

logger = Logger(service="bridge-lock-manager")


def _platform_context() -> TenantContext:
    return TenantContext(
        tenant_id="platform",
        app_id="bridge-lock-manager",
        tier=TenantTier.PREMIUM,
        sub="bridge-lock-manager",
    )


def acquire_lock(
    db: ControlPlaneDynamoDB,
    *,
    lock_name: str,
    identity: str,
    ttl_seconds: int = 300,
) -> str | None:
    """Acquire a distributed lock in DynamoDB."""
    lock_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    ttl = int(now.timestamp()) + ttl_seconds

    try:
        db.put_item(
            OPS_LOCKS_TABLE,
            item={
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
    db: ControlPlaneDynamoDB,
    *,
    lock_name: str,
    lock_id: str,
) -> bool:
    """Release a distributed lock in DynamoDB."""
    try:
        db.delete_item(
            OPS_LOCKS_TABLE,
            key={"PK": f"LOCK#{lock_name}", "SK": "METADATA"},
            ConditionExpression="lock_id = :lock_id",
            ExpressionAttributeValues={":lock_id": lock_id},
        )
        return True
    except Exception:
        return False


def trigger_failover(
    current_region: str,
    *,
    ssm: Any | None = None,
    get_config_fn: Any | None = None,
    runtime_region_param: str = RUNTIME_REGION_PARAM,
) -> str:
    """Failover from eu-west-1 to eu-central-1 (or vice versa).

    Uses distributed lock to ensure only one Lambda instance performs the update.
    Returns the new active region.
    """
    new_region = "eu-central-1" if current_region == "eu-west-1" else "eu-west-1"
    lock_name = FAILOVER_LOCK_NAME
    identity = f"bridge-lambda-{os.environ.get('AWS_LAMBDA_LOG_STREAM_NAME', 'local')}"
    db = ControlPlaneDynamoDB(_platform_context())

    lock_id = acquire_lock(db, lock_name=lock_name, identity=identity)
    if not lock_id:
        logger.info("Failover in progress by another instance, skipping update")
        # Wait a bit and re-fetch config
        from src.bridge.utils import get_retry_jitter

        time.sleep(get_retry_jitter(2.0))
        config_fetcher = get_config_fn or get_config
        config = config_fetcher(force_refresh=True)
        return config.get("runtime_region", current_region)

    try:
        ssm_client = ssm or get_ssm()
        # Re-fetch config to ensure we still need to failover
        param_response = ssm_client.get_parameter(Name=runtime_region_param)
        current_ssm_region = str(param_response.get("Parameter", {}).get("Value", current_region))

        if current_ssm_region != current_region:
            logger.info(
                "Region already changed by another instance",
                extra={"ssm_region": current_ssm_region},
            )
            return current_ssm_region

        logger.warning(
            "Triggering region failover", extra={"from": current_region, "to": new_region}
        )
        ssm_client.put_parameter(
            Name=runtime_region_param, Value=new_region, Type="String", Overwrite=True
        )

        return new_region
    except Exception:
        logger.exception("Failed to trigger failover")
        return current_region
    finally:
        release_lock(db, lock_name=lock_name, lock_id=lock_id)
