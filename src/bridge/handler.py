"""
bridge.handler — Agent invocation bridge Lambda.

Reads invocation_mode from agent registry, assumes tenant execution role,
and routes to AgentCore Runtime via sync, streaming, or async paths.

ADRs: ADR-003, ADR-005, ADR-010, ADR-023
"""

from __future__ import annotations

import json
import os
from typing import Any

from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.logging import correlation_paths
from aws_lambda_powertools.utilities.typing import LambdaContext
from data_access import ControlPlaneDynamoDB
from data_access.models import TenantContext, TenantTier

from src.bridge import (
    discovery_service,
    invocation_engine,
    role_resolver,
    route_adapter,
    runtime_calls,
    runtime_dependencies,
    telemetry,
)
from src.bridge.constants import (
    AGENTS_TABLE,
    JOB_RESULT_URL_EXPIRY_SECONDS,
    JOB_RESULTS_BUCKET,
    JOBS_TABLE,
)
from src.bridge.runtime_dependencies import (
    get_capability_client,
)

logger = Logger(service="bridge")
tracer = Tracer()

_ssm_client: Any | None = None
_sts_client: Any | None = None
_cloudwatch_client: Any | None = None
_config_cache: dict[str, Any] = {}
_config_cache_expiry = 0

get_config = runtime_dependencies.get_config
get_http_session = runtime_dependencies.get_http_session
get_runtime_client = runtime_dependencies.get_runtime_client
get_ssm = runtime_dependencies.get_ssm
get_sts = runtime_dependencies.get_sts
get_cloudwatch = runtime_dependencies.get_cloudwatch
_send_streaming_response = route_adapter.send_streaming_response
_mock_runtime_response_body = route_adapter.mock_runtime_response_body
_is_runtime_unavailable_error = route_adapter.is_runtime_unavailable_error


def error_response(status_code: int, code: str, message: str, request_id: str) -> dict[str, Any]:
    return route_adapter.error_response(status_code, code, message, request_id)


def _coerce_optional_string(val: Any) -> str | None:
    return runtime_calls.coerce_optional_string(val)


def get_jitter() -> str:
    return runtime_calls.get_jitter()


def _validate_execution_role_arn(role_arn: str, expected_account_id: str) -> str:
    return runtime_calls.validate_execution_role_arn(role_arn, expected_account_id)


def _build_runtime_payload(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return runtime_calls.build_runtime_payload(*args, **kwargs)


def _validate_runtime_arn(runtime_arn: str) -> Any:
    return runtime_calls.validate_runtime_arn(runtime_arn)


def get_agent_record(agent_name: str, agent_version: str | None = None):
    return runtime_dependencies.get_agent_record(agent_name, agent_version)


def get_tenant_record(tenant_context: TenantContext):
    return runtime_dependencies.get_tenant_record(tenant_context)


def _get_execution_role_arn_from_ssm(tenant_id: str) -> str | None:
    return role_resolver.resolve_tenant_execution_role(get_ssm(), tenant_id=tenant_id)


def assume_tenant_role(tenant_id: str, role_arn: str):
    return role_resolver.assume_tenant_role(
        get_sts(), role_arn=role_arn, session_name=f"invoke-{tenant_id[:8]}"
    )


def log_invocation(*args: Any, **kwargs: Any) -> None:
    telemetry.log_invocation(get_cloudwatch(), *args, jitter=runtime_calls.get_jitter(), **kwargs)


def emit_invocation_metrics(*args: Any, **kwargs: Any) -> None:
    telemetry.emit_invocation_metrics(get_cloudwatch(), *args, **kwargs)


def emit_bedrock_throttle_metric(**kwargs: Any) -> None:
    telemetry.emit_bedrock_throttle_metric(get_cloudwatch(), **kwargs)


def log_job(*args: Any, **kwargs: Any) -> None:
    telemetry.log_job(*args, **kwargs)


def _runtime_failure_response(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return runtime_calls.runtime_failure_response(
        *args,
        emit_bedrock_throttle_metric=emit_bedrock_throttle_metric,
        log_invocation=log_invocation,
        error_response=error_response,
        **kwargs,
    )


def invoke_real_runtime(*args: Any, **kwargs: Any):
    return runtime_calls.invoke_real_runtime(*args, **kwargs)


def invoke_mock_runtime(*args: Any, **kwargs: Any):
    return runtime_calls.invoke_mock_runtime(
        *args,
        get_http_session=get_http_session,
        build_runtime_payload=_build_runtime_payload,
        log_invocation=log_invocation,
        runtime_failure_response=_runtime_failure_response,
        **kwargs,
    )


def handle_streaming_invocation(*args: Any, **kwargs: Any):
    return route_adapter.handle_streaming_invocation(*args, **kwargs)


def invoke_agent(
    agent: Any,
    tenant_context: TenantContext,
    prompt: str,
    session_id: str | None,
    webhook_id: str | None,
    request_id: str,
    response_stream: Any | None,
    estimate: int = 0,
):
    return route_adapter.invoke_agent(
        agent=agent,
        tenant_context=tenant_context,
        prompt=prompt,
        session_id=session_id,
        webhook_id=webhook_id,
        request_id=request_id,
        response_stream=response_stream,
        estimate=estimate,
    )


def trigger_failover(current_region: str) -> str | None:
    from src.bridge.lock_manager import trigger_failover as _trigger_failover

    return _trigger_failover(current_region)


def get_job_status(
    path_params: dict[str, Any],
    request_id: str,
    tenant_context: TenantContext,
) -> dict[str, Any]:
    return discovery_service.get_job_status(
        tenant_context,
        path_params,
        request_id,
        jobs_table=JOBS_TABLE,
        job_results_bucket=JOB_RESULTS_BUCKET,
        job_result_url_expiry_seconds=JOB_RESULT_URL_EXPIRY_SECONDS,
        error_response=route_adapter.error_response,
    )


@tracer.capture_lambda_handler
@logger.inject_lambda_context(
    clear_state=True, log_event=True, correlation_id_path=correlation_paths.API_GATEWAY_REST
)
def handler(
    event: dict[str, Any],
    context: LambdaContext,
    response_stream: Any | None = None,
) -> dict[str, Any] | None:
    request_id = context.aws_request_id
    auth_map = route_adapter.get_authorizer_map(event)

    tenant_id = auth_map.get("tenantId") or auth_map.get("tenantid") or "unknown"
    app_id = auth_map.get("appId") or auth_map.get("appid") or "unknown"
    tier_raw = auth_map.get("tier") or "standard"
    try:
        tier = TenantTier(tier_raw.lower())
    except ValueError:
        tier = TenantTier.STANDARD

    tenant_context = TenantContext(
        tenant_id=tenant_id,
        app_id=app_id,
        tier=tier,
        sub=auth_map.get("sub") or "system",
    )

    path = event.get("path", "")
    path_params = event.get("pathParameters") or {}
    http_method = str(event.get("httpMethod", "")).upper()

    capability_client = get_capability_client()
    discovery_capability_policy = capability_client.fetch_policy()

    invoke_capability_policy = None
    if (
        os.environ.get("APPCONFIG_APPLICATION_ID")
        and os.environ.get("APPCONFIG_ENVIRONMENT_ID")
        and os.environ.get("APPCONFIG_PROFILE_ID")
    ):
        invoke_capability_policy = discovery_capability_policy

    agent_name = path_params.get("agentName")
    job_id = path_params.get("jobId")

    if http_method == "GET" and route_adapter.is_agents_list_path(path):
        result = discovery_service.list_agents(
            tenant_context,
            agents_table=AGENTS_TABLE,
            db_factory=discovery_service.ControlPlaneDynamoDB,
            capability_policy=discovery_capability_policy,
        )
    elif http_method == "GET" and route_adapter.is_agent_detail_path(path, agent_name):
        result = discovery_service.get_agent_detail(
            path_params,
            request_id,
            agents_table=AGENTS_TABLE,
            db_factory=discovery_service.ControlPlaneDynamoDB,
            error_response=route_adapter.error_response,
            tenant_context=tenant_context,
            capability_policy=discovery_capability_policy,
        )
    elif http_method == "POST" and route_adapter.is_agent_bootstrap_path(path, agent_name):
        result = route_adapter.bootstrap_agent_session(
            agent_name=agent_name or "",
            tenant_context=tenant_context,
            request_id=request_id,
        )
    elif http_method == "GET" and route_adapter.is_job_contract_path(path, job_id):
        result = get_job_status(path_params, request_id, tenant_context)
    else:
        result = invocation_engine.handle_invoke_request(
            event=event,
            request_id=request_id,
            tenant_context=tenant_context,
            path=path,
            path_params=path_params,
            response_stream=response_stream,
            error_response=route_adapter.error_response,
            parse_body=lambda e: json.loads(e.get("body") or "{}"),
            coerce_optional_string=runtime_calls.coerce_optional_string,
            is_invoke_contract_path=route_adapter.is_invoke_contract_path,
            get_agent_record=get_agent_record,
            capability_policy=invoke_capability_policy,
            invoke_agent=invoke_agent,
        )

    if response_stream is not None and isinstance(result, dict):
        route_adapter.send_streaming_response(
            response_stream,
            int(result.get("statusCode", 200)),
            str(result.get("body", "")).encode("utf-8"),
            dict(result.get("headers", {})),
        )
        return None

    return result
