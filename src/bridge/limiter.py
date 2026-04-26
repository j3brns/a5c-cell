from __future__ import annotations

import time
from typing import Any, NamedTuple, cast

from aws_lambda_powertools import Logger
from redis import Redis
from redis.exceptions import RedisError

logger = Logger(service="bridge-limiter")

# Lua script for atomic pre-request check and increment.
# KEYS[1]: counter key
# ARGV[1]: limit (number)
# ARGV[2]: estimate (number)
# ARGV[3]: expiry seconds (number)
# Returns: {current_value_after_incr, success_flag}
# success_flag: 1 if allowed and incremented, 0 if over limit
PRE_REQUEST_SCRIPT = """
local current = redis.call('GET', KEYS[1])
local limit = tonumber(ARGV[1])
local estimate = tonumber(ARGV[2])
local expiry = tonumber(ARGV[3])

if limit > 0 and current and (tonumber(current) + estimate) > limit then
    return {tonumber(current), 0}
end

local newVal = redis.call('INCRBY', KEYS[1], estimate)
if newVal == estimate then
    redis.call('EXPIRE', KEYS[1], expiry)
end
return {newVal, 1}
"""

# Lua script for post-request correction.
# KEYS[1]: counter key
# ARGV[1]: estimated tokens used in pre-request (number)
# ARGV[2]: actual tokens used (number)
# Returns: new value after correction
POST_REQUEST_SCRIPT = """
local estimate = tonumber(ARGV[1])
local actual = tonumber(ARGV[2])
local diff = actual - estimate
return redis.call('INCRBY', KEYS[1], diff)
"""


class RateLimitResult(NamedTuple):
    allowed: bool
    limit: int
    used: int
    reset_seconds: int


class TokenLimiter:
    def __init__(self, redis_client: Redis | None = None):
        self._redis = redis_client
        self._pre_request_sha = None
        self._post_request_sha = None

    def _get_client(self) -> Redis | None:
        return self._redis

    def check_and_increment(
        self,
        tenant_id: str,
        model_id: str,
        limit: int,
        estimate: int,
    ) -> RateLimitResult:
        """
        Perform pre-request TPM check and increment.

        If limit is 0, it's considered unlimited but we still track usage.
        """
        client = self._get_client()
        now = int(time.time())
        window_start = now // 60 * 60
        reset_seconds = 60 - (now % 60)
        key = f"LIMITER/{tenant_id}:{model_id}:tpm/{window_start}"

        if not client:
            return RateLimitResult(True, limit or -1, estimate, reset_seconds)

        try:
            if self._pre_request_sha is None:
                self._pre_request_sha = str(client.script_load(PRE_REQUEST_SCRIPT))

            # Script returns [new_val, success]
            # Use -1 for unlimited in Lua script if we want to handle it there,
            # but here we pass limit directly. 0 means unlimited.
            result = cast(
                list[Any],
                client.evalsha(self._pre_request_sha, 1, key, limit, estimate, 90),
            )
            used, success = result

            return RateLimitResult(
                allowed=bool(success),
                limit=limit if limit > 0 else -1,
                used=int(used),
                reset_seconds=reset_seconds,
            )
        except RedisError as exc:
            logger.warning(
                "Valkey unavailable during pre-request check, failing open",
                extra={"event.name": "valkey_unavailable", "error": str(exc)},
            )
            return RateLimitResult(True, limit or -1, estimate, reset_seconds)

    def correct_usage(
        self,
        tenant_id: str,
        model_id: str,
        estimate: int,
        actual: int,
    ) -> int:
        """Correct estimated usage with actual usage after response."""
        client = self._get_client()
        if not client:
            return actual

        now = int(time.time())
        window_start = now // 60 * 60
        key = f"LIMITER/{tenant_id}:{model_id}:tpm/{window_start}"

        try:
            if self._post_request_sha is None:
                self._post_request_sha = str(client.script_load(POST_REQUEST_SCRIPT))

            new_val = client.evalsha(self._post_request_sha, 1, key, estimate, actual)
            return int(cast(Any, new_val))
        except RedisError as exc:
            logger.warning(
                "Valkey unavailable during post-request correction",
                extra={"event.name": "valkey_unavailable", "error": str(exc)},
            )
            return actual
