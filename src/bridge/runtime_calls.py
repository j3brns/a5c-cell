from __future__ import annotations

import json
import sys
import time
import uuid
from typing import Any

from botocore.exceptions import ClientError
from data_access.models import AgentRecord, InvocationMode, InvocationStatus, TenantContext

from src.bridge import role_resolver, telemetry, tpm_limiter
from src.bridge.constants import IAM_ROLE_ARN_PATTERN, RUNTIME_ARN_PATTERN
from src.bridge.runtime_dependencies import (
    get_cloudwatch,
)
from src.bridge.runtime_dependencies import (
    get_http_session as default_get_http_session,
)
from src.bridge.runtime_dependencies import (
    get_runtime_client as default_get_runtime_client,
)
from src.bridge.runtime_dependencies import (
    get_ssm as default_get_ssm,
)
from src.bridge.runtime_dependencies import (
    get_sts as default_get_sts,
)
from src.bridge.runtime_dependencies import (
    get_tenant_record as default_get_tenant_record,
)
from src.platform_utils import coerce_optional_string as _coerce_optional_string
from src.platform_utils import get_hex_jitter as _get_hex_jitter

_DEFAULT_GET_CLOUDWATCH = get_cloudwatch


def _handler_dependency(name: str, fallback: Any) -> Any:
    handler_module = sys.modules.get("src.bridge.handler")
    if handler_module is not None and hasattr(handler_module, name):
        return getattr(handler_module, name)
    return fallback


def _cloudwatch_dependency() -> Any:
    route_adapter = sys.modules.get("src.bridge.route_adapter")
    route_adapter_cloudwatch = getattr(route_adapter, "get_cloudwatch", None)
    if (
        route_adapter_cloudwatch is not None
        and route_adapter_cloudwatch is not _DEFAULT_GET_CLOUDWATCH
    ):
        return route_adapter_cloudwatch
    return _handler_dependency("get_cloudwatch", get_cloudwatch)


def coerce_optional_string(val: Any) -> str | None:
    return _coerce_optional_string(val)


def get_jitter() -> str:
    return _get_hex_jitter()


def validate_execution_role_arn(role_arn: str, expected_account_id: str) -> str:
    match = IAM_ROLE_ARN_PATTERN.fullmatch(role_arn)
    if not match:
        raise ValueError("Tenant execution role ARN is malformed")
    if match.group("account_id") != expected_account_id:
        raise ValueError("Tenant execution role ARN account mismatch")
    return role_arn


def build_runtime_payload(
    agent: AgentRecord,
    tenant_context: TenantContext,
    prompt: str,
    session_id: str | None = None,
) -> dict[str, Any]:
    payload = {
        "prompt": prompt,
        "input": prompt,
        "mode": agent.invocation_mode.value,
        "appid": tenant_context.app_id,
        "tenantId": tenant_context.tenant_id,
        "agentName": agent.agent_name,
        "agentVersion": agent.version,
    }
    if session_id:
        payload["sessionId"] = session_id
    return payload


def validate_runtime_arn(runtime_arn: str) -> Any:
    match = RUNTIME_ARN_PATTERN.fullmatch(runtime_arn)
    if not match:
        raise ValueError("Agent runtime ARN is malformed")
    return match


def runtime_failure_response(
    tenant_context: TenantContext,
    agent: AgentRecord,
    invocation_id: str,
    start_time: float,
    invocation_mode: InvocationMode,
    runtime_region: str,
    request_id: str,
    exc: Exception,
    *,
    session_id: str | None,
    emit_bedrock_throttle_metric: Any | None = None,
    log_invocation: Any | None = None,
    error_response: Any | None = None,
) -> dict[str, Any]:
    from .route_adapter import error_response as default_error_response

    emit_throttle = emit_bedrock_throttle_metric or (
        lambda **kwargs: telemetry.emit_bedrock_throttle_metric(
            _cloudwatch_dependency()(), **kwargs
        )
    )
    log_result = log_invocation or (
        lambda *args, **kwargs: telemetry.log_invocation(
            _cloudwatch_dependency()(),
            *args,
            jitter=get_jitter(),
            **kwargs,
        )
    )
    build_error_response = error_response or default_error_response

    status_code = 502
    error_code = "RUNTIME_INVOCATION_FAILED"
    message = "Agent runtime invocation failed"

    if isinstance(exc, ClientError):
        err = exc.response.get("Error", {})
        error_code = str(err.get("Code") or error_code)
        message = str(err.get("Message") or message)
        status_code = int(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 502))
        if error_code == "ThrottlingException":
            emit_throttle(
                tenant_context=tenant_context,
                agent=agent,
                runtime_region=runtime_region,
            )
    else:
        message = str(exc) or message

    latency_ms = int((time.time() - start_time) * 1000)
    log_result(
        tenant_context,
        agent,
        invocation_id,
        InvocationStatus.ERROR,
        latency_ms,
        invocation_mode,
        session_id=session_id,
        error_code=error_code,
        runtime_region=runtime_region,
    )
    return build_error_response(status_code, error_code, message, request_id)


def read_runtime_response_body(
    response_body: Any,
    *,
    start_time: float,
) -> tuple[bytes, int | None]:
    """Read a streaming runtime body and capture TTFT from its first non-empty chunk."""
    if hasattr(response_body, "iter_chunks"):
        chunks: list[bytes] = []
        ttft_ms: int | None = None
        for chunk in response_body.iter_chunks():
            if not chunk:
                continue
            if ttft_ms is None:
                ttft_ms = max(1, int((time.time() - start_time) * 1000))
            if isinstance(chunk, (bytes, bytearray)):
                chunks.append(bytes(chunk))
            else:
                chunks.append(str(chunk).encode("utf-8"))
        return b"".join(chunks), ttft_ms

    if hasattr(response_body, "read"):
        body_bytes = response_body.read()
        ttft_ms = max(1, int((time.time() - start_time) * 1000)) if body_bytes else None
        return bytes(body_bytes) if isinstance(body_bytes, (bytes, bytearray)) else b"", ttft_ms

    return bytes(response_body) if isinstance(response_body, (bytes, bytearray)) else b"", None


def mock_runtime_response_body(response: Any, session_id: str | None) -> tuple[str, str | None]:
    text = getattr(response, "text", None)
    if isinstance(text, str):
        return text, session_id

    runtime_session_id = session_id
    parts: list[str] = []
    for raw_line in response.iter_lines():
        if not raw_line:
            continue
        line = (
            raw_line.decode("utf-8") if isinstance(raw_line, (bytes, bytearray)) else str(raw_line)
        )
        payload = line[5:].strip() if line.startswith("data:") else line.strip()
        if payload == "[DONE]":
            continue
        try:
            data = json.loads(payload)
        except ValueError:
            continue
        if data.get("type") == "session" and data.get("sessionId"):
            runtime_session_id = str(data["sessionId"])
        if data.get("type") == "text":
            parts.append(str(data.get("content", "")))

    body: dict[str, Any] = {
        "output": "".join(parts),
        "usage": {"inputTokens": 0, "outputTokens": 0},
    }
    if runtime_session_id:
        body["sessionId"] = runtime_session_id
    return json.dumps(body), runtime_session_id


def invoke_real_runtime(
    region: str,
    agent: AgentRecord,
    tenant_context: TenantContext,
    prompt: str,
    session_id: str | None,
    webhook_id: str | None,
    request_id: str,
    response_stream: Any | None = None,
    invocation_id: str | None = None,
    start_time: float | None = None,
    runtime_credentials: dict[str, Any] | None = None,
    *,
    coerce_optional_string: Any | None = None,
    validate_runtime_arn: Any | None = None,
    get_tenant_record: Any | None = None,
    resolve_tenant_execution_role: Any | None = None,
    get_ssm: Any | None = None,
    validate_execution_role_arn: Any | None = None,
    get_sts: Any | None = None,
    assume_tenant_role: Any | None = None,
    get_runtime_client: Any | None = None,
    build_runtime_payload: Any | None = None,
    log_invocation: Any | None = None,
    runtime_failure_response: Any | None = None,
    error_response: Any | None = None,
) -> Any:
    from .route_adapter import error_response as default_error_response

    coerce = coerce_optional_string or _coerce_optional_string
    validate_arn = validate_runtime_arn or globals()["validate_runtime_arn"]
    fetch_tenant_record = get_tenant_record or _handler_dependency(
        "get_tenant_record", default_get_tenant_record
    )
    handler_resolve = _handler_dependency("_get_execution_role_arn_from_ssm", None)

    def resolve_role(_ssm: Any, *, tenant_id: str) -> str | None:
        if resolve_tenant_execution_role is not None:
            return resolve_tenant_execution_role(_ssm, tenant_id=tenant_id)
        if handler_resolve is not None:
            return handler_resolve(tenant_id)
        return role_resolver.resolve_tenant_execution_role(_ssm, tenant_id=tenant_id)

    fetch_ssm = get_ssm or _handler_dependency("get_ssm", default_get_ssm)
    validate_role = validate_execution_role_arn or globals()["validate_execution_role_arn"]
    fetch_sts = get_sts or _handler_dependency("get_sts", default_get_sts)
    if assume_tenant_role is not None:
        assume_role = assume_tenant_role
    elif (handler_assume := _handler_dependency("assume_tenant_role", None)) is not None:

        def assume_role(_sts: Any, role_arn: str, session_name: str) -> dict[str, Any]:
            del _sts, session_name
            return handler_assume(tenant_context.tenant_id, role_arn)
    else:

        def assume_role(_sts: Any, role_arn: str, session_name: str) -> dict[str, Any]:
            return role_resolver.assume_tenant_role(
                _sts, role_arn=role_arn, session_name=session_name
            )

    runtime_client_factory = get_runtime_client or _handler_dependency(
        "get_runtime_client", default_get_runtime_client
    )
    build_payload = build_runtime_payload or globals()["build_runtime_payload"]
    log_result = log_invocation or (
        lambda *args, **kwargs: telemetry.log_invocation(
            _cloudwatch_dependency()(),
            *args,
            jitter=get_jitter(),
            **kwargs,
        )
    )
    failure_response = runtime_failure_response or globals()["runtime_failure_response"]
    build_error_response = error_response or default_error_response

    del webhook_id
    del response_stream

    runtime_arn = coerce(agent.runtime_arn)
    if not runtime_arn:
        return build_error_response(
            500, "INVALID_RUNTIME", "Agent runtime ARN not configured", request_id
        )
    runtime_arn_match = validate_arn(runtime_arn)
    runtime_arn_region = runtime_arn_match.group("region")
    if runtime_arn_region != region:
        runtime_arn = runtime_arn.replace(f":{runtime_arn_region}:", f":{region}:", 1)
    invocation_id = invocation_id or str(uuid.uuid4())
    start_time = start_time or time.time()

    if not runtime_credentials:
        tenant_record = fetch_tenant_record(tenant_context)
        if not tenant_record:
            return build_error_response(404, "NOT_FOUND", "Tenant metadata not found", request_id)

        role_arn = coerce(
            tenant_record.get("executionRoleArn") or tenant_record.get("execution_role_arn")
        )
        if not role_arn:
            role_arn = resolve_role(fetch_ssm(), tenant_id=tenant_context.tenant_id)
        if not role_arn:
            return build_error_response(
                500, "INVALID_RUNTIME", "Tenant execution role ARN not configured", request_id
            )

        expected_account_id = (
            coerce(tenant_record.get("accountId") or tenant_record.get("account_id")) or ""
        )
        if expected_account_id:
            try:
                role_arn = validate_role(role_arn, expected_account_id)
            except ValueError as exc:
                return build_error_response(500, "INVALID_RUNTIME", str(exc), request_id)

        runtime_credentials = assume_role(
            fetch_sts(),
            role_arn=role_arn,
            session_name=f"invoke-{invocation_id[:8]}",
        )

    try:
        runtime_client = runtime_client_factory(region, credentials=runtime_credentials)
        runtime_response = runtime_client.invoke_agent_runtime(
            agentRuntimeArn=runtime_arn,
            payload=json.dumps(
                build_payload(agent, tenant_context, prompt, session_id=session_id)
            ).encode("utf-8"),
        )
        response_body = runtime_response.get("response")
        ttft_ms: int | None = None
        if agent.invocation_mode == InvocationMode.STREAMING:
            body_bytes, ttft_ms = read_runtime_response_body(
                response_body,
                start_time=start_time,
            )
        else:
            if hasattr(response_body, "read"):
                raw_body = response_body.read()
                body_bytes = bytes(raw_body) if isinstance(raw_body, (bytes, bytearray)) else b""
            else:
                body_bytes = response_body if isinstance(response_body, (bytes, bytearray)) else b""
        body_text = body_bytes.decode("utf-8") if isinstance(body_bytes, (bytes, bytearray)) else ""
        token_usage = tpm_limiter.extract_token_usage(body_text)
        latency_ms = int((time.time() - start_time) * 1000)
        log_result(
            tenant_context,
            agent,
            invocation_id,
            InvocationStatus.SUCCESS,
            latency_ms,
            agent.invocation_mode,
            session_id=session_id,
            runtime_region=region,
            ttft_ms=ttft_ms,
            input_tokens=token_usage.input_tokens,
            output_tokens=token_usage.output_tokens,
            estimated_tokens=tpm_limiter.estimate_tokens_from_prompt(prompt),
            model_id=token_usage.model_id,
        )
        headers = {"Content-Type": str(runtime_response.get("contentType", "application/json"))}
        runtime_session_id = coerce(runtime_response.get("runtimeSessionId"))
        if runtime_session_id:
            headers["x-runtime-session-id"] = runtime_session_id
        return {
            "statusCode": int(runtime_response.get("statusCode", 200)),
            "headers": headers,
            "body": body_text,
        }
    except Exception as exc:
        return failure_response(
            tenant_context,
            agent,
            invocation_id,
            start_time,
            agent.invocation_mode,
            region,
            request_id,
            exc,
            session_id=session_id,
        )


def invoke_mock_runtime(
    url: str,
    agent: AgentRecord,
    tenant_context: TenantContext,
    prompt: str,
    session_id: str | None,
    webhook_id: str | None,
    request_id: str,
    response_stream: Any | None = None,
    invocation_id: str | None = None,
    start_time: float | None = None,
    *,
    get_http_session: Any | None = None,
    build_runtime_payload: Any | None = None,
    log_invocation: Any | None = None,
    runtime_failure_response: Any | None = None,
) -> dict[str, Any] | None:
    del webhook_id
    del response_stream

    invocation_id = invocation_id or str(uuid.uuid4())
    start_time = start_time or time.time()
    http_session_factory = get_http_session or _handler_dependency(
        "get_http_session", default_get_http_session
    )
    build_payload = build_runtime_payload or globals()["build_runtime_payload"]
    log_result = log_invocation or (
        lambda *args, **kwargs: telemetry.log_invocation(
            _cloudwatch_dependency()(),
            *args,
            jitter=get_jitter(),
            **kwargs,
        )
    )
    failure_response = runtime_failure_response or globals()["runtime_failure_response"]
    try:
        response = http_session_factory().post(
            url.rstrip("/"),
            json=build_payload(agent, tenant_context, prompt, session_id=session_id),
            timeout=5,
        )
        latency_ms = int((time.time() - start_time) * 1000)
        status = InvocationStatus.SUCCESS if response.ok else InvocationStatus.ERROR
        response_text, resolved_session_id = mock_runtime_response_body(response, session_id)
        token_usage = tpm_limiter.extract_token_usage(response_text)
        log_result(
            tenant_context,
            agent,
            invocation_id,
            status,
            latency_ms,
            agent.invocation_mode,
            session_id=resolved_session_id,
            runtime_region="mock-runtime",
            input_tokens=token_usage.input_tokens,
            output_tokens=token_usage.output_tokens,
            estimated_tokens=tpm_limiter.estimate_tokens_from_prompt(prompt),
            model_id=token_usage.model_id,
        )
        return {
            "statusCode": response.status_code,
            "headers": {"Content-Type": response.headers.get("Content-Type", "application/json")},
            "body": response_text,
        }
    except Exception as exc:
        return failure_response(
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
