from typing import Any

from aws_lambda_powertools import Logger

logger = Logger(service="bridge-lock-manager")


def trigger_failover(
    current_region: str,
    *,
    ssm: Any | None = None,
    get_config_fn: Any | None = None,
    runtime_region_param: str | None = None,
) -> str | None:
    """Report the disabled ADR-023 runtime failover policy."""
    _ = ssm, get_config_fn, runtime_region_param
    logger.warning(
        "Runtime failover is disabled by the v0.2 topology",
        extra={"current_region": current_region},
    )
    return None
