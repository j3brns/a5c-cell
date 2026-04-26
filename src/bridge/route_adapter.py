from __future__ import annotations

import json
import os
import sys
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import boto3
from botocore.exceptions import (
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)
from data_access import ControlPlaneDynamoDB, TenantScopedDynamoDB
from data_access.models import (
    AgentRecord,
    InvocationMode,
    InvocationStatus,
    JobStatus,
    TenantContext,
)

from src.bridge import runtime_calls, telemetry, tpm_limiter
from src.bridge.constants import (
    AG_UI_SCOPE_NAME,
    BFF_SESSION_KEEPALIVE_PATH,
    BFF_TOKEN_REFRESH_PATH,
    ENTRA_AUDIENCE,
    JOBS_TABLE,
    SESSIONS_TABLE,
)
from src.bridge.runtime_dependencies import (
    get_agent_record,
    get_cloudwatch,
    get_config,
    get_http_session,
    get_limiter,
    get_platform_context,
    get_webhook_registration,
)
from src.platform_utils import coerce_optional_string as _coerce_optional_string

_DEFAULT_GET_HTTP_SESSION = get_http_session
_DEFAULT_GET_CLOUDWATCH = get_cloudwatch
_DEFAULT_GET_CONFIG = get_config


def _handler_dependency(name: str, fallback: Any) -> Any:
    handler_module = sys.modules.get("src.bridge.handler")
    if handler_module is not None and hasattr(handler_module, name):
        return getattr(handler_module, name)
    return fallback


def _local_or_handler_dependency(name: str, default: Any) -> Any:
    current = globals().get(name, default)
    if current is not default:
        return current
    return _handler_dependency(name, default)


def error_response(status_code: int, code: str, message: str, request_id: str) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json", "x-amzn-RequestId": request_id},
        "body": json.dumps({"error": {"code": code, "message": message, "requestId": request_id}}),
    }


def get_authorizer_map(event: dict[str, Any]) -> dict[str, str]:
    request_context = event.get("requestContext", {})
    authorizer = request_context.get("authorizer", {})
    if not isinstance(authorizer, dict):
        return {}
    if "lambda" in authorizer and isinstance(authorizer["lambda"], dict):
        return authorizer["lambda"]
    return authorizer


def is_invoke_contract_path(path: str, agent_name: str | None) -> bool:
    if not agent_name:
        return False
    return path.endswith(f"/agents/{agent_name}/invoke")


def send_streaming_response(
    response_stream: Any,
    status_code: int,
    body: bytes,
    headers: dict[str, str],
) -> None:
    preamble = json.dumps({"statusCode": status_code, "headers": headers}).encode("utf-8") + b"\0"
    response_stream.write(preamble)
    if body:
        response_stream.write(body)


def handle_streaming_invocation(
    *,
    url: str,
    headers: dict[str, str] | None = None,
    payload: dict[str, Any],
    agent: AgentRecord,
    tenant_context: TenantContext,
    invocation_id: str,
    start_time: float,
    response_stream: Any | None,
    request_id: str,
    session_id: str | None,
) -> dict[str, Any] | None:
    if response_stream is None:
        return error_response(
            500,
            "INTERNAL_ERROR",
            "Streaming invocation requires a response stream",
            request_id,
        )

    stream_started = False
    ttft_ms: int | None = None
    http_session_factory = _local_or_handler_dependency(
        "get_http_session", _DEFAULT_GET_HTTP_SESSION
    )
    log_result = _handler_dependency("log_invocation", None)
    try:
        with http_session_factory().post(
            url.rstrip("/"),
            headers=headers or {},
            json=payload,
            stream=True,
            timeout=5,
        ) as response:
            response.raise_for_status()
            send_streaming_response(
                response_stream,
                200,
                b"",
                {"Content-Type": "text/event-stream"},
            )
            stream_started = True
            for raw_line in response.iter_lines():
                if not raw_line:
                    continue
                if ttft_ms is None:
                    ttft_ms = max(1, int((time.time() - start_time) * 1000))
                response_stream.write(raw_line + b"\n\n")
            latency_ms = int((time.time() - start_time) * 1000)
            if log_result is not None:
                log_result(
                    tenant_context,
                    agent,
                    invocation_id,
                    InvocationStatus.SUCCESS,
                    latency_ms,
                    agent.invocation_mode,
                    session_id=session_id or "mock-session-id",
                    runtime_region="mock-runtime",
                    ttft_ms=ttft_ms,
                )
            else:
                telemetry.log_invocation(
                    get_cloudwatch(),
                    tenant_context,
                    agent,
                    invocation_id,
                    InvocationStatus.SUCCESS,
                    latency_ms,
                    agent.invocation_mode,
                    session_id=session_id or "mock-session-id",
                    runtime_region="mock-runtime",
                    jitter=runtime_calls.get_jitter(),
                    ttft_ms=ttft_ms,
                )
            return None
    except Exception as exc:
        if not stream_started:
            raise
        return runtime_calls.runtime_failure_response(
            tenant_context,
            agent,
            invocation_id,
            start_time,
            agent.invocation_mode,
            "mock-runtime",
            request_id,
            exc,
            session_id=session_id,
        )


def mock_runtime_response_body(response: Any, session_id: str | None) -> tuple[str, str | None]:
    return runtime_calls.mock_runtime_response_body(response, session_id)


def is_runtime_unavailable_error(exc: Exception) -> bool:
    if isinstance(exc, ClientError):
        return str(exc.response.get("Error", {}).get("Code", "")) == "ServiceUnavailableException"
    return isinstance(exc, (EndpointConnectionError, ConnectTimeoutError, ReadTimeoutError))


def invoke_agent(
    agent: AgentRecord,
    tenant_context: TenantContext,
    prompt: str,
    session_id: str | None,
    webhook_id: str | None,
    request_id: str,
    response_stream: Any | None,
    estimate: int = 0,
) -> Any:
    get_runtime_config = _local_or_handler_dependency("get_config", _DEFAULT_GET_CONFIG)
    call_real_runtime = _handler_dependency(
        "invoke_real_runtime", runtime_calls.invoke_real_runtime
    )
    if globals().get("get_cloudwatch") is not _DEFAULT_GET_CLOUDWATCH:
        log_result = None
    else:
        log_result = _handler_dependency("log_invocation", None)

    # Wrap log_result to include TPM estimate (TASK-903)
    def log_result_with_tpm(*args: Any, **kwargs: Any) -> None:
        kwargs["estimated_tokens"] = estimate
        if log_result is not None:
            log_result(*args, **kwargs)
        else:
            telemetry.log_invocation(get_cloudwatch(), *args, **kwargs)

    do_failover = _handler_dependency("trigger_failover", None)

    config = get_runtime_config()
    mock_url = _coerce_optional_string(config.get("mock_runtime_url"))
    runtime_region = str(config["runtime_region"])
    invocation_id = str(uuid.uuid4())
    start_time = time.time()

    if agent.invocation_mode == InvocationMode.ASYNC:
        webhook_record = None
        if webhook_id:
            webhook_record = get_webhook_registration(tenant_context, webhook_id)
            if webhook_record is None:
                return error_response(
                    404,
                    "NOT_FOUND",
                    f"Webhook '{webhook_id}' not found",
                    request_id,
                )

        job_id = str(uuid.uuid4())
        TenantScopedDynamoDB(tenant_context).put_item(
            JOBS_TABLE,
            {
                "PK": f"TENANT#{tenant_context.tenant_id}",
                "SK": f"JOB#{job_id}",
                "job_id": job_id,
                "tenant_id": tenant_context.tenant_id,
                "app_id": tenant_context.app_id,
                "agent_name": agent.agent_name,
                "status": JobStatus.PENDING.value,
                "created_at": datetime.now(UTC).isoformat(),
                "webhook_id": webhook_id,
                "webhook_url": _coerce_optional_string(
                    webhook_record.get("callback_url") if webhook_record else None
                ),
            },
        )
        return {
            "statusCode": 202,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(
                {
                    "status": "accepted",
                    "jobId": job_id,
                    "webhookDelivery": "registered" if webhook_record else "none",
                }
            ),
        }

    if mock_url:
        if response_stream is not None and agent.invocation_mode == InvocationMode.STREAMING:
            return handle_streaming_invocation(
                url=mock_url,
                payload=runtime_calls.build_runtime_payload(
                    agent,
                    tenant_context,
                    prompt,
                    session_id=session_id,
                ),
                agent=agent,
                tenant_context=tenant_context,
                invocation_id=invocation_id,
                start_time=start_time,
                response_stream=response_stream,
                request_id=request_id,
                session_id=session_id,
            )
        response = runtime_calls.invoke_mock_runtime(
            mock_url,
            agent,
            tenant_context,
            prompt,
            session_id,
            webhook_id,
            request_id,
            response_stream,
            invocation_id,
            start_time,
            estimate=estimate,
            get_http_session=get_http_session,
            build_runtime_payload=runtime_calls.build_runtime_payload,
            log_invocation=log_result_with_tpm,
        )
        if response is not None and response.get("statusCode") == 200:
            _perform_tpm_correction(agent, tenant_context, estimate, response)
        return response

    try:
        response = call_real_runtime(
            runtime_region,
            agent,
            tenant_context,
            prompt,
            session_id,
            webhook_id,
            request_id,
            response_stream,
            invocation_id,
            start_time,
            estimate=estimate,
            log_invocation=log_result_with_tpm,
        )
        if response is not None and response.get("statusCode") == 200:
            _perform_tpm_correction(agent, tenant_context, estimate, response)
        return response
    except ClientError as exc:
        error_code = str(exc.response.get("Error", {}).get("Code", ""))
        if error_code == "ThrottlingException":
            latency_ms = int((time.time() - start_time) * 1000)
            log_result_with_tpm(
                tenant_context,
                agent,
                invocation_id,
                InvocationStatus.ERROR,
                latency_ms,
                agent.invocation_mode,
                session_id=session_id,
                error_code="THROTTLED",
                runtime_region=runtime_region,
            )
            response = error_response(429, "THROTTLED", "Agent runtime throttled", request_id)
            response["headers"]["Retry-After"] = "1"
            return response
        if is_runtime_unavailable_error(exc):
            if do_failover is None:
                from .lock_manager import trigger_failover as do_failover

            new_region = do_failover(runtime_region)
            if new_region is None:
                return runtime_calls.runtime_failure_response(
                    tenant_context,
                    agent,
                    invocation_id,
                    start_time,
                    agent.invocation_mode,
                    runtime_region,
                    request_id,
                    exc,
                    session_id=session_id,
                    tpm_estimated=estimate,
                    log_invocation=log_result_with_tpm,
                )
            return call_real_runtime(
                new_region,
                agent,
                tenant_context,
                prompt,
                session_id,
                webhook_id,
                request_id,
                response_stream,
                invocation_id,
                start_time,
                estimate=estimate,
                log_invocation=log_result_with_tpm,
            )
        return runtime_calls.runtime_failure_response(
            tenant_context,
            agent,
            invocation_id,
            start_time,
            agent.invocation_mode,
            runtime_region,
            request_id,
            exc,
            session_id=session_id,
            tpm_estimated=estimate,
            log_invocation=log_result_with_tpm,
        )
    except Exception as exc:
        if is_runtime_unavailable_error(exc):
            if do_failover is None:
                from .lock_manager import trigger_failover as do_failover

            new_region = do_failover(runtime_region)
            if new_region is None:
                return runtime_calls.runtime_failure_response(
                    tenant_context,
                    agent,
                    invocation_id,
                    start_time,
                    agent.invocation_mode,
                    runtime_region,
                    request_id,
                    exc,
                    session_id=session_id,
                    tpm_estimated=estimate,
                    log_invocation=log_result_with_tpm,
                )
            return call_real_runtime(
                new_region,
                agent,
                tenant_context,
                prompt,
                session_id,
                webhook_id,
                request_id,
                response_stream,
                invocation_id,
                start_time,
                estimate=estimate,
                log_invocation=log_result_with_tpm,
            )
        return runtime_calls.runtime_failure_response(
            tenant_context,
            agent,
            invocation_id,
            start_time,
            agent.invocation_mode,
            runtime_region,
            request_id,
            exc,
            session_id=session_id,
            tpm_estimated=estimate,
            log_invocation=log_result_with_tpm,
        )


def normalize_contract_path(path: str) -> str:
    parts = [part for part in path.split("/") if part]
    if len(parts) >= 2 and parts[0] != "v1" and parts[1] == "v1":
        parts = parts[1:]
    return "/" + "/".join(parts)


def is_job_contract_path(path: str, job_id: str | None) -> bool:
    if not job_id:
        return False
    return normalize_contract_path(path) == f"/v1/jobs/{job_id}"


def is_agent_detail_path(path: str, agent_name: str | None) -> bool:
    if not agent_name:
        return False
    return normalize_contract_path(path) == f"/v1/agents/{agent_name}"


def is_agents_list_path(path: str) -> bool:
    return normalize_contract_path(path) == "/v1/agents"


def is_agent_bootstrap_path(path: str, agent_name: str | None) -> bool:
    if not agent_name:
        return False
    return normalize_contract_path(path) == f"/v1/agents/{agent_name}/bootstrap"


def bootstrap_agent_session(
    *,
    agent_name: str,
    tenant_context: TenantContext,
    request_id: str,
) -> dict[str, Any]:
    agent = get_agent_record(agent_name)
    if not agent:
        return error_response(404, "NOT_FOUND", f"Agent '{agent_name}' not found", request_id)

    if not agent.ag_ui or not agent.ag_ui.enabled or not agent.ag_ui.endpoint:
        return error_response(
            404, "NOT_FOUND", f"Agent '{agent_name}' is not AG-UI enabled", request_id
        )

    session_id = str(uuid.uuid4())
    runtime_session_id = str(uuid.uuid4())
    session_item = {
        "PK": f"TENANT#{tenant_context.tenant_id}",
        "SK": f"SESSION#{session_id}",
        "tenant_id": tenant_context.tenant_id,
        "app_id": tenant_context.app_id,
        "session_id": session_id,
        "runtime_session_id": runtime_session_id,
        "bootstrap_type": "ag_ui",
        "created_at": datetime.now(UTC).isoformat(),
    }
    try:
        dynamodb_resource = boto3.resource("dynamodb", region_name=os.environ["AWS_REGION"])
        ControlPlaneDynamoDB(
            get_platform_context(),
            dynamodb_resource=dynamodb_resource,
        ).put_item(SESSIONS_TABLE, session_item)
    except Exception:
        pass

    scope = f"{ENTRA_AUDIENCE}/{AG_UI_SCOPE_NAME}" if ENTRA_AUDIENCE else AG_UI_SCOPE_NAME
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(
            {
                "agentName": agent.agent_name,
                "sessionId": session_id,
                "runtimeSessionId": runtime_session_id,
                "transport": agent.ag_ui.transport.value,
                "connectUrl": agent.ag_ui.endpoint,
                "tokenRefreshPath": BFF_TOKEN_REFRESH_PATH,
                "sessionKeepalivePath": BFF_SESSION_KEEPALIVE_PATH,
                "auth": {"scopes": [scope]},
            }
        ),
    }


def _perform_tpm_correction(
    agent: AgentRecord,
    tenant_context: TenantContext,
    estimate: int,
    response: dict[str, Any],
) -> None:
    """Correct estimated usage with actual usage for enforcement counter (non-streaming)."""
    if agent.invocation_mode == InvocationMode.STREAMING:
        return

    body_text = response.get("body", "{}")
    token_usage = tpm_limiter.extract_token_usage(body_text)
    actual = token_usage.total_tokens

    if actual > 0 or estimate > 0:
        limiter = get_limiter()
        limiter.correct_usage(
            tenant_context.tenant_id,
            agent.model_id or agent.agent_name,
            estimate,
            actual,
        )
