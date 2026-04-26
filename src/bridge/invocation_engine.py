from __future__ import annotations

import json
from typing import Any

from data_access.models import TenantContext, TenantTier

from src.bridge import route_adapter, telemetry
from src.bridge.runtime_dependencies import get_agent_record as default_get_agent_record
from src.bridge.runtime_dependencies import (
    get_cloudwatch,
    get_limiter,
)
from src.bridge.utils import estimate_tokens
from src.platform_utils import coerce_optional_string as _coerce_optional_string


def handle_invoke_request(
    *,
    event: dict[str, Any],
    request_id: str,
    tenant_context: TenantContext,
    path: str,
    path_params: dict[str, Any],
    response_stream: Any,
    error_response: Any | None = None,
    parse_body: Any | None = None,
    coerce_optional_string: Any | None = None,
    is_invoke_contract_path: Any | None = None,
    get_agent_record: Any | None = None,
    capability_policy: Any = None,
    invoke_agent: Any | None = None,
) -> Any:
    build_error_response = error_response or route_adapter.error_response
    parse_request_body = parse_body or (lambda e: json.loads(e.get("body") or "{}"))
    coerce = coerce_optional_string or _coerce_optional_string
    is_contract_path = is_invoke_contract_path or route_adapter.is_invoke_contract_path
    fetch_agent = get_agent_record or default_get_agent_record
    call_agent = invoke_agent or route_adapter.invoke_agent

    agent_name = coerce(path_params.get("agentName"))
    if path and not is_contract_path(path, agent_name):
        return build_error_response(404, "NOT_FOUND", "Route not found", request_id)
    if not agent_name:
        return build_error_response(400, "INVALID_REQUEST", "Missing agentName in path", request_id)

    try:
        body = parse_request_body(event)
    except ValueError:
        return build_error_response(
            400, "INVALID_REQUEST", "Invalid JSON in request body", request_id
        )

    prompt = coerce(body.get("input"))
    if not prompt:
        return build_error_response(
            400, "INVALID_REQUEST", "Missing 'input' in request body", request_id
        )

    session_id = coerce(body.get("sessionId"))
    webhook_id = coerce(body.get("webhookId"))

    agent = fetch_agent(agent_name)
    if not agent:
        return build_error_response(404, "NOT_FOUND", f"Agent '{agent_name}' not found", request_id)

    tier_order = {TenantTier.BASIC: 0, TenantTier.STANDARD: 1, TenantTier.PREMIUM: 2}
    if tier_order[tenant_context.tier] < tier_order[agent.tier_minimum]:
        return build_error_response(
            403, "FORBIDDEN", "Tenant tier insufficient for this agent", request_id
        )

    if capability_policy is not None:
        if not capability_policy.is_enabled(
            "agents.invoke",
            tenant_id=tenant_context.tenant_id,
            tenant_tier=tenant_context.tier,
        ):
            return build_error_response(
                403,
                "FORBIDDEN",
                "Agent invocation capability disabled",
                request_id,
            )

        if not capability_policy.is_enabled(
            f"agents.{agent_name}",
            tenant_id=tenant_context.tenant_id,
            tenant_tier=tenant_context.tier,
        ):
            return build_error_response(
                403,
                "FORBIDDEN",
                f"Access to agent '{agent_name}' is not enabled for this tenant",
                request_id,
            )

    # TPM Check (TASK-904)
    estimate = estimate_tokens(prompt)
    model_id = agent.model_id or "unknown"
    tpm_limit = 0
    if capability_policy is not None:
        try:
            tpm_limit = int(capability_policy.get_tpm_limit(model_id, tenant_context.tier))
        except (ValueError, TypeError, AttributeError):
            tpm_limit = 0

    if tpm_limit > 0 or get_limiter()._redis is not None:
        limiter = get_limiter()
        result = limiter.check_and_increment(
            tenant_context.tenant_id, model_id, tpm_limit, estimate
        )
        if not result.allowed:
            telemetry.emit_tpm_limit_exceeded_metric(
                get_cloudwatch(), tenant_context=tenant_context, agent_name=agent_name or "unknown"
            )
            return {
                "statusCode": 429,
                "headers": {
                    "Content-Type": "application/json",
                    "x-amzn-RequestId": request_id,
                    "X-RateLimit-Limit-TPM": str(result.limit),
                    "X-RateLimit-Used-TPM": str(result.used),
                    "X-RateLimit-Reset": str(result.reset_seconds),
                },
                "body": json.dumps(
                    {
                        "error": {
                            "code": "THROTTLED_TPM",
                            "message": f"TPM limit exceeded for model '{model_id}'",
                            "requestId": request_id,
                        }
                    }
                ),
            }

    return call_agent(
        agent,
        tenant_context,
        prompt,
        session_id,
        webhook_id,
        request_id,
        response_stream,
        estimate=estimate,
    )
