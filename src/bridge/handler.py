"""
bridge.handler — Agent invocation bridge Lambda.

Reads invocation_mode from agent registry, assumes tenant execution role,
and routes to AgentCore Runtime via sync, streaming, or async paths.

ADRs: ADR-003, ADR-005, ADR-009, ADR-010
"""

from __future__ import annotations

import json
import os
import re
import secrets
import time
import urllib.parse
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
import requests
from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.logging import correlation_paths
from aws_lambda_powertools.utilities.typing import LambdaContext
from boto3.dynamodb.conditions import Key
from botocore.config import Config
from botocore.exceptions import (
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)
from data_access import (
    ControlPlaneDynamoDB,
    TenantCapabilityClient,
    TenantScopedDynamoDB,
    TenantScopedS3,
)
from data_access.models import (
    SESSION_TTL_SECONDS,
    AgentAgUiConfig,
    AgentRecord,
    AgentStatus,
    AgUiTransport,
    InvocationMode,
    InvocationRecord,
    InvocationStatus,
    JobRecord,
    JobStatus,
    SessionRecord,
    SessionStatus,
    TenantContext,
    TenantTier,
    is_invokable_agent_status,
    normalize_agent_status,
)

from src.bridge import (
    config_provider,
    constants,
    lock_manager,
    role_resolver,
    telemetry,
)
from src.bridge.config_provider import (
    ConfigProvider,
    config_defaults,
    fetch_ssm_config,
)
from src.bridge.constants import (
    AG_UI_SCOPE_NAME,
    AGENTCORE_RUNTIME_CONNECT_TIMEOUT_SECONDS,
    AGENTCORE_RUNTIME_READ_TIMEOUT_SECONDS,
    AGENTS_TABLE,
    BFF_SESSION_KEEPALIVE_PATH,
    BFF_TOKEN_REFRESH_PATH,
    ENTRA_AUDIENCE,
    IAM_ROLE_ARN_PATTERN,
    INVOCATION_TTL_SECONDS,
    INVOCATIONS_TABLE,
    JOB_RESULT_URL_EXPIRY_SECONDS,
    JOB_RESULTS_BUCKET,
    JOB_TTL_SECONDS,
    JOBS_TABLE,
    MOCK_RUNTIME_URL_PARAM,
    OPS_LOCKS_TABLE,
    RUNTIME_ARN_PATTERN,
    RUNTIME_REGION_PARAM,
    SESSIONS_TABLE,
    TENANT_EXECUTION_ROLE_PARAM_TEMPLATE,
    TENANTS_TABLE,
    VALID_WEBHOOK_EVENTS,
)

from .discovery_service import (
    _agent_record_sort_key,
)
from .discovery_service import (
    get_agent_detail as discovery_get_agent_detail,
)
from .discovery_service import (
    get_job_status as discovery_get_job_status,
)
from .discovery_service import (
    list_agents as discovery_list_agents,
)
from .invocation_engine import handle_invoke_request
from .runtime_invoker import RuntimeInvoker
from .runtime_orchestrator import build_runtime_orchestrator

logger = Logger(service="bridge")
tracer = Tracer()

# Backward-compatibility aliases for existing submodules/logic
_acquire_lock = lock_manager.acquire_lock
_release_lock = lock_manager.release_lock
_log_invocation = telemetry.log_invocation
_emit_invocation_metrics = telemetry.emit_invocation_metrics
_emit_bedrock_throttle_metric = telemetry.emit_bedrock_throttle_metric
_log_job = telemetry.log_job
_resolve_tenant_execution_role = role_resolver.resolve_tenant_execution_role
_assume_tenant_role = role_resolver.assume_tenant_role

# ---------------------------------------------------------------------------
# Global clients/cache
# ---------------------------------------------------------------------------
_ssm_client = None
_sts_client = None
_dynamodb_resource = None
_cloudwatch_client = None
_capability_client = None
_http_session = None

_config_cache: dict[str, Any] = {}
_config_cache_expiry: float = 0
_config_provider_instance: ConfigProvider | None = None


def _aws_region() -> str:
    return os.environ["AWS_REGION"]


def get_capability_client():
    global _capability_client
    if _capability_client is None:
        _capability_client = TenantCapabilityClient()
    return _capability_client


def get_ssm():
    global _ssm_client
    if _ssm_client is None:
        _ssm_client = boto3.client("ssm", region_name=_aws_region())
    return _ssm_client


def get_sts():
    global _sts_client
    if _sts_client is None:
        _sts_client = boto3.client("sts", region_name=_aws_region())
    return _sts_client


def get_dynamodb():
    global _dynamodb_resource
    if _dynamodb_resource is None:
        _dynamodb_resource = boto3.resource("dynamodb", region_name=_aws_region())
    return _dynamodb_resource


def get_cloudwatch():
    global _cloudwatch_client
    if _cloudwatch_client is None:
        _cloudwatch_client = boto3.client("cloudwatch", region_name=_aws_region())
    return _cloudwatch_client


def get_http_session():
    global _http_session
    if _http_session is None:
        _http_session = requests.Session()
    return _http_session


def _get_config_provider() -> ConfigProvider:
    global _config_provider_instance
    if _config_provider_instance is None:
        _config_provider_instance = ConfigProvider(
            fetcher=lambda: fetch_ssm_config(get_ssm(), get_http_session()),
            fallback_factory=config_defaults,
            ttl_seconds=60,
        )
    return _config_provider_instance


def get_config(force_refresh: bool = False) -> dict[str, Any]:
    """Fetch and cache configuration from SSM."""
    global _config_cache, _config_cache_expiry
    provider = _get_config_provider()
    config = provider.get(force_refresh=force_refresh)
    _config_cache = dict(config)
    _config_cache_expiry = provider.expires_at
    return config


def get_runtime_client(region: str, credentials: dict[str, Any] | None = None) -> Any:
    session_kwargs: dict[str, Any] = {"region_name": region}
    if credentials:
        session_kwargs.update(
            {
                "aws_access_key_id": credentials["AccessKeyId"],
                "aws_secret_access_key": credentials["SecretAccessKey"],
                "aws_session_token": credentials["SessionToken"],
            }
        )

    session = boto3.Session(**session_kwargs)
    client_kwargs: dict[str, Any] = {
        "service_name": "bedrock-agentcore",
        "region_name": region,
        "config": Config(
            connect_timeout=AGENTCORE_RUNTIME_CONNECT_TIMEOUT_SECONDS,
            read_timeout=AGENTCORE_RUNTIME_READ_TIMEOUT_SECONDS,
            retries={"max_attempts": 1, "mode": "standard"},
        ),
    }
    if os.environ.get("BEDROCK_AGENTCORE_DP_ENDPOINT"):
        client_kwargs["endpoint_url"] = os.environ.get("BEDROCK_AGENTCORE_DP_ENDPOINT")
    return session.client(**client_kwargs)


def trigger_failover(current_region: str) -> str:
    """Failover from eu-west-1 to eu-central-1 (or vice versa)."""
    return lock_manager.trigger_failover(
        dynamodb=get_dynamodb(),
        ssm=get_ssm(),
        current_region=current_region,
        get_config_fn=get_config,
        runtime_region_param=RUNTIME_REGION_PARAM,
    )


_trigger_failover = trigger_failover


def get_tenant_record(tenant_context: TenantContext) -> dict[str, Any] | None:
    """Fetch tenant metadata from the registry."""
    try:
        db = TenantScopedDynamoDB(tenant_context)
        return db.get_item(
            TENANTS_TABLE, {"PK": f"TENANT#{tenant_context.tenant_id}", "SK": "METADATA"}
        )
    except Exception:
        logger.exception("Failed to fetch tenant record")
        return None


def get_agent_record(agent_name: str, agent_version: str | None = None) -> AgentRecord | None:
    """Resolve an agent record by name and version."""
    return discovery_get_agent_detail(get_dynamodb(), agent_name, agent_version)


def get_webhook_registration(
    tenant_context: TenantContext, webhook_id: str
) -> dict[str, Any] | None:
    """Resolve a webhook registration for a tenant."""
    try:
        db = TenantScopedDynamoDB(tenant_context)
        return db.get_item(
            TENANTS_TABLE,
            {"PK": f"TENANT#{tenant_context.tenant_id}", "SK": f"WEBHOOK#{webhook_id}"},
        )
    except Exception:
        logger.exception("Failed to fetch webhook registration")
        return None


def error_response(status_code: int, code: str, message: str, request_id: str) -> dict[str, Any]:
    """Return a standard error response."""
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json", "x-amzn-RequestId": request_id},
        "body": json.dumps({"error": {"code": code, "message": message, "requestId": request_id}}),
    }


def _coerce_optional_string(val: Any) -> str | None:
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None


def _coerce_optional_int(val: Any) -> int | None:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def get_jitter() -> float:
    """Return a random jitter value for hot-partition mitigation."""
    return secrets.SystemRandom().uniform(0, 0.1)


def _validate_execution_role_arn(role_arn: str, expected_account_id: str) -> str:
    match = IAM_ROLE_ARN_PATTERN.fullmatch(role_arn)
    if not match:
        raise ValueError("Tenant execution role ARN is malformed")
    if match.group("account_id") != expected_account_id:
        raise ValueError("Tenant execution role is in an untrusted account")
    return role_arn


def _build_runtime_payload(
    agent: AgentRecord,
    tenant_context: TenantContext,
    prompt: str,
    session_id: str | None = None,
) -> dict[str, Any]:
    payload = {
        "agentName": agent.agent_name,
        "agentVersion": agent.version,
        "prompt": prompt,
        "tenantId": tenant_context.tenant_id,
        "appId": tenant_context.app_id,
    }
    if session_id:
        payload["sessionId"] = session_id
    return payload


def _validate_runtime_arn(runtime_arn: str) -> re.Match[str]:
    match = RUNTIME_ARN_PATTERN.fullmatch(runtime_arn)
    if not match:
        raise ValueError("Agent runtime ARN is malformed")
    return match


def _runtime_failure_response(
    status_code: int,
    code: str,
    message: str,
    request_id: str,
    invocation_id: str,
    agent: AgentRecord,
    tenant_context: TenantContext,
    start_time: float,
    invocation_mode: InvocationMode,
    error_status: InvocationStatus,
    session_id: str | None = None,
) -> dict[str, Any]:
    latency_ms = int((time.time() - start_time) * 1000)
    log_invocation(
        tenant_context,
        agent,
        invocation_id,
        error_status,
        latency_ms,
        invocation_mode,
        session_id=session_id,
        error_code=code,
    )
    return error_response(status_code, code, message, request_id)


def log_invocation(
    tenant_context: TenantContext,
    agent: AgentRecord,
    invocation_id: str,
    status: InvocationStatus,
    latency_ms: int,
    mode: InvocationMode,
    input_tokens: int = 0,
    output_tokens: int = 0,
    job_id: str | None = None,
    session_id: str | None = None,
    error_code: str | None = None,
    runtime_region: str | None = None,
) -> None:
    # Delegate to modular telemetry
    telemetry.log_invocation(
        get_cloudwatch(),
        tenant_context,
        agent,
        invocation_id,
        status,
        latency_ms,
        mode,
        runtime_region=runtime_region or get_config()["runtime_region"],
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        job_id=job_id,
        session_id=session_id,
        error_code=error_code,
        jitter=get_jitter(),
    )


def emit_invocation_metrics(
    tenant_context: TenantContext,
    agent: AgentRecord,
    status: InvocationStatus,
    latency_ms: int,
    input_tokens: int,
    output_tokens: int,
) -> None:
    telemetry.emit_invocation_metrics(
        get_cloudwatch(),
        tenant_context,
        agent,
        status,
        latency_ms,
        input_tokens,
        output_tokens,
    )


def emit_bedrock_throttle_metric(
    *,
    tenant_context: TenantContext,
    agent: AgentRecord,
    runtime_region: str,
) -> None:
    telemetry.emit_bedrock_throttle_metric(
        get_cloudwatch(),
        tenant_context=tenant_context,
        agent=agent,
        runtime_region=runtime_region,
    )


def log_job(tenant_context: TenantContext, record: JobRecord) -> None:
    telemetry.log_job(tenant_context, record)


def invoke_real_runtime(
    region: str,
    runtime_arn: str,
    tenant_context: TenantContext,
    agent: AgentRecord,
    prompt: str,
    invocation_id: str,
    start_time: float,
    request_id: str,
    invocation_mode: InvocationMode,
    runtime_credentials: dict[str, Any] | None = None,
    session_id: str | None = None,
    response_stream: Any | None = None,
    webhook_id: str | None = None,
) -> Any:
    """Invoke the real AgentCore Runtime."""
    # Ensure runtime ARN is valid
    _validate_runtime_arn(runtime_arn)

    # Resolve active runtime credentials if not provided
    if not runtime_credentials:
        tenant_record = get_tenant_record(tenant_context)
        if not tenant_record:
            return error_response(404, "NOT_FOUND", "Tenant metadata not found", request_id)

        role_arn = _coerce_optional_string(tenant_record.get("executionRoleArn"))
        if not role_arn:
            return error_response(
                403, "FORBIDDEN", "Tenant execution role not configured", request_id
            )

        runtime_credentials = _assume_tenant_role(
            get_sts(), role_arn=role_arn, session_name=f"invoke-{invocation_id[:8]}"
        )

    # 1. Prepare Request
    payload = _build_runtime_payload(agent, tenant_context, prompt, session_id)
    runtime_client = get_runtime_client(region, credentials=runtime_credentials)

    # 2. Invoke via Orchestrator (Orchestrator handles the sync/streaming/async dispatch)
    orchestrator = build_runtime_orchestrator(
        invocation_mode=invocation_mode,
        runtime_client=runtime_client,
        runtime_arn=runtime_arn,
        payload=payload,
        invocation_id=invocation_id,
        request_id=request_id,
        response_stream=response_stream,
        webhook_id=webhook_id,
        tenant_context=tenant_context,
        agent=agent,
        start_time=start_time,
        log_invocation_fn=log_invocation,
        log_job_fn=log_job,
        emit_throttle_metric_fn=lambda r: emit_bedrock_throttle_metric(
            tenant_context=tenant_context, agent=agent, runtime_region=r
        ),
    )

    return orchestrator.invoke()


def invoke_mock_runtime(
    url: str,
    agent: AgentRecord,
    tenant_context: TenantContext,
    prompt: str,
    invocation_id: str,
    start_time: float,
    request_id: str,
    session_id: str | None = None,
    response_stream: Any | None = None,
    webhook_id: str | None = None,
) -> dict[str, Any] | None:
    """Invoke a mock runtime (local development or testing)."""
    headers = {
        "x-amzn-RequestId": request_id,
        "x-platform-invocation-id": invocation_id,
        "x-platform-tenant-id": tenant_context.tenant_id,
        "Content-Type": "application/json",
    }
    payload = _build_runtime_payload(agent, tenant_context, prompt, session_id)

    if agent.invocation_mode == InvocationMode.STREAMING:
        return handle_streaming_invocation(
            url,
            headers,
            payload,
            agent,
            tenant_context,
            invocation_id,
            start_time,
            response_stream,
            request_id,
            session_id,
        )
    elif agent.invocation_mode == InvocationMode.ASYNC:
        return handle_async_invocation(
            url,
            headers,
            payload,
            agent,
            tenant_context,
            invocation_id,
            start_time,
            webhook_id,
            request_id,
            session_id,
        )
    else:
        return handle_sync_invocation(
            url, headers, payload, agent, tenant_context, invocation_id, start_time, session_id
        )


def handle_sync_invocation(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    agent: AgentRecord,
    tenant_context: TenantContext,
    invocation_id: str,
    start_time: float,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Handle synchronous invocation."""
    response = get_http_session().post(
        f"{url}/invocations", headers=headers, json=payload, timeout=900
    )
    response.raise_for_status()

    # Mock runtime returns SSE, collect into full text
    full_text = ""
    effective_session_id = session_id or "mock-session-id"

    for line in response.iter_lines():
        if line:
            decoded_line = line.decode("utf-8")
            if decoded_line.startswith("data: "):
                data = decoded_line[6:]
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                    if chunk.get("type") == "text":
                        full_text += chunk.get("content", "")
                    elif chunk.get("type") == "session":
                        effective_session_id = chunk.get("sessionId", effective_session_id)
                except json.JSONDecodeError:
                    pass

    latency_ms = int((time.time() - start_time) * 1000)

    # Log invocation
    log_invocation(
        tenant_context,
        agent,
        invocation_id,
        InvocationStatus.SUCCESS,
        latency_ms,
        InvocationMode.SYNC,
        input_tokens=0,
        output_tokens=0,
        session_id=effective_session_id,
    )

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(
            {
                "invocationId": invocation_id,
                "agentName": agent.agent_name,
                "agentVersion": agent.version,
                "mode": InvocationMode.SYNC,
                "status": InvocationStatus.SUCCESS,
                "output": full_text,
                "sessionId": effective_session_id,
                "timestamp": datetime.now(UTC).isoformat(),
                "usage": {"inputTokens": 0, "outputTokens": 0, "latencyMs": latency_ms},
            }
        ),
    }


def handle_streaming_invocation(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    agent: AgentRecord,
    tenant_context: TenantContext,
    invocation_id: str,
    start_time: float,
    response_stream: Any,
    request_id: str,
    session_id: str | None = None,
) -> Any:
    """Handle streaming invocation using Lambda Response Streaming."""
    if not response_stream:
        logger.error("Streaming requested but response_stream not available")
        return error_response(
            500, "INTERNAL_ERROR", "Response streaming not enabled for this Lambda", request_id
        )

    effective_session_id = session_id or "mock-session-id"

    # Send preamble for streaming
    preamble = {
        "statusCode": 200,
        "headers": {"Content-Type": "text/event-stream"},
    }
    response_stream.write(json.dumps(preamble).encode("utf-8") + b"\0")

    with get_http_session().post(
        f"{url}/invocations", headers=headers, json=payload, stream=True, timeout=900
    ) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if line:
                response_stream.write(line + b"\n\n")

    latency_ms = int((time.time() - start_time) * 1000)

    # Log invocation (after stream closes)
    log_invocation(
        tenant_context,
        agent,
        invocation_id,
        InvocationStatus.SUCCESS,
        latency_ms,
        InvocationMode.STREAMING,
        input_tokens=0,
        output_tokens=0,
        session_id=effective_session_id,
    )
    return None


def handle_async_invocation(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    agent: AgentRecord,
    tenant_context: TenantContext,
    invocation_id: str,
    start_time: float,
    webhook_id: str | None,
    request_id: str,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Handle async invocation."""
    job_id = str(uuid.uuid4())
    now_iso = datetime.now(UTC).isoformat()
    now_ts = int(time.time())
    webhook_url: str | None = None

    if webhook_id:
        registration = get_webhook_registration(tenant_context, webhook_id)
        if registration is None:
            return error_response(404, "NOT_FOUND", f"Webhook '{webhook_id}' not found", request_id)
        webhook_url = _coerce_optional_string(registration.get("callback_url"))
        if webhook_url is None:
            return error_response(
                500, "INTERNAL_ERROR", "Webhook registration missing callback URL", request_id
            )

    # 1. Create JOB record in DynamoDB (platform-jobs)
    job_record = JobRecord(
        job_id=job_id,
        tenant_id=tenant_context.tenant_id,
        app_id=tenant_context.app_id,
        agent_name=agent.agent_name,
        status=JobStatus.PENDING,
        created_at=now_iso,
        ttl=now_ts + JOB_TTL_SECONDS,
        webhook_id=webhook_id,
        webhook_url=webhook_url,
    )
    log_job(tenant_context, job_record)

    # 2. Trigger Runtime
    try:
        response = get_http_session().post(
            f"{url}/invocations", headers=headers, json=payload, timeout=2
        )
        response.raise_for_status()
    except requests.exceptions.ReadTimeout:
        # Expected for async trigger if it's fire-and-forget
        pass

    latency_ms = int((time.time() - start_time) * 1000)

    # 3. Log invocation
    log_invocation(
        tenant_context,
        agent,
        invocation_id,
        InvocationStatus.SUCCESS,
        latency_ms,
        InvocationMode.ASYNC,
        input_tokens=0,
        output_tokens=0,
        job_id=job_id,
        session_id=session_id or "async-session",
    )

    return {
        "statusCode": 202,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(
            {
                "jobId": job_id,
                "status": "accepted",
                "mode": "async",
                "pollUrl": f"/v1/jobs/{job_id}",
                "webhookDelivery": "registered" if webhook_url else "not_registered",
            }
        ),
    }


@tracer.capture_lambda_handler
@logger.inject_lambda_context(
    clear_state=True, log_event=True, correlation_id_path=correlation_paths.API_GATEWAY_REST
)
def handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    """Lambda entry point."""
    return handle_invoke_request(
        event,
        context,
        orchestrator_factory=lambda **kwargs: build_runtime_orchestrator(
            get_config_fn=get_config,
            discovery_list_agents_fn=discovery_list_agents,
            discovery_get_agent_detail_fn=discovery_get_agent_detail,
            discovery_get_job_status_fn=discovery_get_job_status,
            acquire_lock_fn=_acquire_lock,
            release_lock_fn=_release_lock,
            trigger_failover_fn=_trigger_failover,
            log_invocation_fn=log_invocation,
            log_job_fn=log_job,
            emit_bedrock_throttle_metric_fn=emit_bedrock_throttle_metric,
            get_sts_client_fn=get_sts,
            get_ssm_client_fn=get_ssm,
            get_dynamodb_resource_fn=get_dynamodb,
            get_http_session_fn=get_http_session,
            get_runtime_client_fn=get_runtime_client,
            invoke_real_runtime_fn=invoke_real_runtime,
            invoke_mock_runtime_fn=invoke_mock_runtime,
            error_response_fn=error_response,
            **kwargs,
        ),
    )
