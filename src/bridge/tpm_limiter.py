from __future__ import annotations

import json
import math
import os
import socket
import ssl
import time
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

from aws_lambda_powertools import Logger
from data_access.models import AgentRecord, TenantContext

logger = Logger(service="bridge-tpm-limiter")

WINDOW_SECONDS = 60
COUNTER_TTL_SECONDS = 90
CHARS_PER_TOKEN_ESTIMATE = 4
DEFAULT_VALKEY_PORT = 6379


class CounterClient(Protocol):
    def increment_windows(
        self,
        *,
        actual_key: str,
        actual_tokens: int,
        estimated_key: str,
        estimated_tokens: int,
        ttl_seconds: int,
    ) -> int: ...


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    model_id: str | None = None

    @property
    def total_tokens(self) -> int:
        return max(0, self.input_tokens) + max(0, self.output_tokens)


@dataclass(frozen=True)
class TpmCounterResult:
    actual_tokens: int
    estimated_tokens: int
    window_expiry: int
    window_usage: int
    model_id: str
    skipped: bool


class SocketValkeyCounterClient:
    """Minimal Redis RESP client for Valkey INCRBY/EXPIRE without a package dependency."""

    def __init__(
        self,
        *,
        host: str,
        port: int = DEFAULT_VALKEY_PORT,
        use_tls: bool = True,
        timeout_seconds: float = 0.75,
    ) -> None:
        self._host = host
        self._port = port
        self._use_tls = use_tls
        self._timeout_seconds = timeout_seconds

    def incrby(self, key: str, amount: int) -> int:
        response = self._execute("INCRBY", key, str(amount))
        return int(response)

    def expire(self, key: str, seconds: int) -> None:
        self._execute("EXPIRE", key, str(seconds))

    def increment_windows(
        self,
        *,
        actual_key: str,
        actual_tokens: int,
        estimated_key: str,
        estimated_tokens: int,
        ttl_seconds: int,
    ) -> int:
        results = self._execute_transaction(
            ("INCRBY", actual_key, str(actual_tokens)),
            ("EXPIRE", actual_key, str(ttl_seconds)),
            ("INCRBY", estimated_key, str(estimated_tokens)),
            ("EXPIRE", estimated_key, str(ttl_seconds)),
        )
        if not isinstance(results, list) or len(results) < 1:
            raise RuntimeError("Valkey transaction returned no results")
        return int(results[0])

    def _execute(self, *parts: str) -> Any:
        command = _encode_resp_command(parts)
        with socket.create_connection(
            (self._host, self._port), timeout=self._timeout_seconds
        ) as raw_sock:
            if self._use_tls:
                context = ssl.create_default_context()
                with context.wrap_socket(raw_sock, server_hostname=self._host) as sock:
                    sock.settimeout(self._timeout_seconds)
                    sock.sendall(command)
                    return _read_resp(sock)
            raw_sock.settimeout(self._timeout_seconds)
            raw_sock.sendall(command)
            return _read_resp(raw_sock)

    def _execute_transaction(self, *commands: tuple[str, ...]) -> list[Any]:
        payload = b"".join(
            [_encode_resp_command(("MULTI",))]
            + [_encode_resp_command(command) for command in commands]
            + [_encode_resp_command(("EXEC",))]
        )
        with socket.create_connection(
            (self._host, self._port), timeout=self._timeout_seconds
        ) as raw_sock:
            if self._use_tls:
                context = ssl.create_default_context()
                with context.wrap_socket(raw_sock, server_hostname=self._host) as sock:
                    sock.settimeout(self._timeout_seconds)
                    sock.sendall(payload)
                    return self._read_transaction_response(sock, len(commands))
            raw_sock.settimeout(self._timeout_seconds)
            raw_sock.sendall(payload)
            return self._read_transaction_response(raw_sock, len(commands))

    @staticmethod
    def _read_transaction_response(sock: socket.socket, command_count: int) -> list[Any]:
        _read_resp(sock)
        for _ in range(command_count):
            _read_resp(sock)
        exec_response = _read_resp(sock)
        if not isinstance(exec_response, list):
            raise RuntimeError("Valkey EXEC did not return an array")
        return exec_response


def estimate_tokens_from_prompt(prompt: str) -> int:
    if not prompt:
        return 0
    return int(math.ceil(len(prompt) / CHARS_PER_TOKEN_ESTIMATE))


def extract_token_usage(body_text: str) -> TokenUsage:
    try:
        payload = json.loads(body_text)
    except (TypeError, ValueError):
        return TokenUsage()
    if not isinstance(payload, dict):
        return TokenUsage()

    usage = payload.get("usage")
    if not isinstance(usage, dict):
        usage = payload

    input_tokens = _coerce_int(usage.get("inputTokens", usage.get("input_tokens")))
    output_tokens = _coerce_int(usage.get("outputTokens", usage.get("output_tokens")))
    if input_tokens == 0 and output_tokens == 0:
        total_tokens = _coerce_int(usage.get("totalTokens", usage.get("total_tokens")))
        output_tokens = total_tokens

    model_id = payload.get("modelId", payload.get("model_id"))
    if model_id is not None:
        model_id = str(model_id)
    return TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens, model_id=model_id)


def record_log_only_tpm(
    cloudwatch: Any,
    *,
    tenant_context: TenantContext,
    agent: AgentRecord,
    actual_tokens: int,
    estimated_tokens: int,
    model_id: str | None = None,
    counter_client: CounterClient | None = None,
    now: float | None = None,
) -> TpmCounterResult:
    model = _metric_model_id(model_id, agent)
    actual = max(0, int(actual_tokens))
    estimated = max(0, int(estimated_tokens))
    window_expiry = _window_expiry(now or time.time())
    actual_key = _counter_key(tenant_context.tenant_id, model, "tpm", window_expiry)
    estimated_key = _counter_key(tenant_context.tenant_id, model, "tpm_estimated", window_expiry)

    try:
        client = counter_client if counter_client is not None else _default_counter_client()
        if client is None:
            _log_counter_skipped(
                tenant_context, agent, model, actual, estimated, "valkey_not_configured"
            )
            return TpmCounterResult(actual, estimated, window_expiry, 0, model, skipped=True)
        window_usage = client.increment_windows(
            actual_key=actual_key,
            actual_tokens=actual,
            estimated_key=estimated_key,
            estimated_tokens=estimated,
            ttl_seconds=COUNTER_TTL_SECONDS,
        )
    except Exception as exc:
        _log_valkey_unavailable(tenant_context, agent, model, actual, estimated, exc)
        _log_counter_skipped(tenant_context, agent, model, actual, estimated, str(exc))
        return TpmCounterResult(actual, estimated, window_expiry, 0, model, skipped=True)

    _emit_tpm_metric(cloudwatch, tenant_context, agent, model, window_usage)
    logger.info(
        "TPM usage recorded",
        extra={
            "event.name": "tpm_counter_recorded",
            "tenantid": tenant_context.tenant_id,
            "appid": tenant_context.app_id,
            "agent.name": agent.agent_name,
            "model.id": model,
            "rate_limit.tpm_used": actual,
            "rate_limit.tpm_estimated": estimated,
            "rate_limit.tpm_window_usage": window_usage,
            "rate_limit.tpm_window_expiry": window_expiry,
            "gen_ai.tpm_window_usage": window_usage,
        },
    )
    return TpmCounterResult(actual, estimated, window_expiry, window_usage, model, skipped=False)


def _coerce_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _metric_model_id(model_id: str | None, agent: AgentRecord) -> str:
    if model_id and model_id.strip():
        return model_id.strip()
    return agent.agent_name


def _window_expiry(now: float) -> int:
    return ((int(now) // WINDOW_SECONDS) + 1) * WINDOW_SECONDS


def _counter_key(tenant_id: str, model_id: str, counter_name: str, window_expiry: int) -> str:
    return f"LIMITER/{tenant_id}:{model_id}:{counter_name}/{window_expiry}"


def _default_counter_client() -> CounterClient | None:
    endpoint = os.environ.get("VALKEY_ENDPOINT") or os.environ.get("TPM_VALKEY_ENDPOINT")
    if not endpoint:
        return None
    parsed = _parse_endpoint(endpoint)
    return SocketValkeyCounterClient(
        host=parsed["host"],
        port=int(parsed["port"]),
        use_tls=parsed["use_tls"],
    )


def _parse_endpoint(endpoint: str) -> dict[str, Any]:
    value = endpoint.strip()
    if "://" not in value:
        value = f"rediss://{value}"
    parsed = urlparse(value)
    host = parsed.hostname
    if not host:
        raise ValueError("Valkey endpoint host is missing")
    return {
        "host": host,
        "port": parsed.port or DEFAULT_VALKEY_PORT,
        "use_tls": parsed.scheme != "redis",
    }


def _emit_tpm_metric(
    cloudwatch: Any,
    tenant_context: TenantContext,
    agent: AgentRecord,
    model_id: str,
    window_usage: int,
) -> None:
    try:
        cloudwatch.put_metric_data(
            Namespace="Platform/Bridge",
            MetricData=[
                {
                    "MetricName": "gen_ai.tpm_window_usage",
                    "Value": float(window_usage),
                    "Unit": "Count",
                    "Dimensions": [
                        {"Name": "TenantId", "Value": tenant_context.tenant_id},
                        {"Name": "ModelId", "Value": model_id},
                        {"Name": "AgentName", "Value": agent.agent_name},
                    ],
                }
            ],
        )
    except Exception as exc:
        logger.warning(
            "Failed to emit TPM metric",
            extra={
                "tenantid": tenant_context.tenant_id,
                "appid": tenant_context.app_id,
                "agent.name": agent.agent_name,
                "model.id": model_id,
                "error": str(exc),
            },
        )


def _log_valkey_unavailable(
    tenant_context: TenantContext,
    agent: AgentRecord,
    model_id: str,
    actual_tokens: int,
    estimated_tokens: int,
    exc: Exception,
) -> None:
    logger.warning(
        "Valkey unavailable for TPM counter",
        extra={
            "event.name": "valkey_unavailable",
            "tenantid": tenant_context.tenant_id,
            "appid": tenant_context.app_id,
            "agent.name": agent.agent_name,
            "model.id": model_id,
            "rate_limit.tpm_used": actual_tokens,
            "rate_limit.tpm_estimated": estimated_tokens,
            "error": str(exc),
        },
    )


def _log_counter_skipped(
    tenant_context: TenantContext,
    agent: AgentRecord,
    model_id: str,
    actual_tokens: int,
    estimated_tokens: int,
    reason: str,
) -> None:
    logger.warning(
        "TPM counter skipped",
        extra={
            "event.name": "tpm_counter_skipped",
            "tenantid": tenant_context.tenant_id,
            "appid": tenant_context.app_id,
            "agent.name": agent.agent_name,
            "model.id": model_id,
            "rate_limit.tpm_used": actual_tokens,
            "rate_limit.tpm_estimated": estimated_tokens,
            "reason": reason,
        },
    )


def _encode_resp_command(parts: tuple[str, ...]) -> bytes:
    encoded = [f"*{len(parts)}\r\n".encode()]
    for part in parts:
        data = part.encode("utf-8")
        encoded.append(f"${len(data)}\r\n".encode())
        encoded.append(data + b"\r\n")
    return b"".join(encoded)


def _read_resp(sock: socket.socket) -> Any:
    prefix = _read_exact(sock, 1)
    if prefix == b"+":
        return _read_line(sock).decode("utf-8")
    if prefix == b":":
        return int(_read_line(sock))
    if prefix == b"-":
        raise RuntimeError(_read_line(sock).decode("utf-8"))
    if prefix == b"$":
        length = int(_read_line(sock))
        if length < 0:
            return None
        payload = _read_exact(sock, length)
        _read_exact(sock, 2)
        return payload.decode("utf-8")
    if prefix == b"*":
        length = int(_read_line(sock))
        if length < 0:
            return None
        return [_read_resp(sock) for _ in range(length)]
    raise RuntimeError(f"Unsupported Valkey response prefix: {prefix!r}")


def _read_line(sock: socket.socket) -> bytes:
    chunks = bytearray()
    while not chunks.endswith(b"\r\n"):
        chunks.extend(_read_exact(sock, 1))
    return bytes(chunks[:-2])


def _read_exact(sock: socket.socket, length: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < length:
        chunk = sock.recv(length - len(chunks))
        if not chunk:
            raise RuntimeError("Valkey connection closed")
        chunks.extend(chunk)
    return bytes(chunks)
