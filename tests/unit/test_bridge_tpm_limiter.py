from __future__ import annotations

import logging
import socket
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "data-access-lib" / "src"))

from data_access.models import AgentRecord, InvocationMode, TenantContext, TenantTier

from src.bridge import tpm_limiter


class FakeCounterClient:
    def __init__(self) -> None:
        self.increments: list[tuple[str, int]] = []
        self.expiries: list[tuple[str, int]] = []

    def increment_windows(
        self,
        *,
        actual_key: str,
        actual_tokens: int,
        estimated_key: str,
        estimated_tokens: int,
        ttl_seconds: int,
    ) -> int:
        self.increments.extend([(actual_key, actual_tokens), (estimated_key, estimated_tokens)])
        self.expiries.extend([(actual_key, ttl_seconds), (estimated_key, ttl_seconds)])
        return actual_tokens


class FailingCounterClient:
    def increment_windows(
        self,
        *,
        actual_key: str,
        actual_tokens: int,
        estimated_key: str,
        estimated_tokens: int,
        ttl_seconds: int,
    ) -> int:
        del actual_key, actual_tokens, estimated_key, estimated_tokens, ttl_seconds
        raise RuntimeError("valkey unavailable")


class FakeSocket:
    def __init__(self, response: bytes) -> None:
        self.response = bytearray(response)
        self.sent = bytearray()

    def __enter__(self) -> FakeSocket:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def settimeout(self, timeout: float) -> None:
        del timeout

    def sendall(self, payload: bytes) -> None:
        self.sent.extend(payload)

    def recv(self, length: int) -> bytes:
        if not self.response:
            return b""
        chunk = self.response[:length]
        del self.response[:length]
        return bytes(chunk)


def _tenant_context() -> TenantContext:
    return TenantContext(
        tenant_id="tenant-123",
        app_id="app-123",
        tier=TenantTier.BASIC,
        sub="user-123",
    )


def _agent() -> AgentRecord:
    return AgentRecord(
        agent_name="echo-agent",
        version="1.0.0",
        owner_team="platform",
        tier_minimum=TenantTier.BASIC,
        layer_hash="layer-hash",
        layer_s3_key="layer.zip",
        script_s3_key="agent.py",
        deployed_at="2026-01-01T00:00:00Z",
        invocation_mode=InvocationMode.SYNC,
        streaming_enabled=False,
    )


def test_extract_token_usage_accepts_camel_and_snake_case() -> None:
    assert tpm_limiter.extract_token_usage(
        '{"usage":{"inputTokens":12,"outputTokens":8},"modelId":"claude"}'
    ) == tpm_limiter.TokenUsage(input_tokens=12, output_tokens=8, model_id="claude")
    assert tpm_limiter.extract_token_usage(
        '{"usage":{"input_tokens":3,"output_tokens":5},"model_id":"model-x"}'
    ) == tpm_limiter.TokenUsage(input_tokens=3, output_tokens=5, model_id="model-x")
    assert tpm_limiter.extract_token_usage('{"usage":{"totalTokens":21}}') == (
        tpm_limiter.TokenUsage(input_tokens=0, output_tokens=21, model_id=None)
    )
    assert tpm_limiter.extract_token_usage("data: not-json") == tpm_limiter.TokenUsage()


def test_estimate_tokens_from_prompt_uses_four_character_buckets() -> None:
    assert tpm_limiter.estimate_tokens_from_prompt("") == 0
    assert tpm_limiter.estimate_tokens_from_prompt("abcd") == 1
    assert tpm_limiter.estimate_tokens_from_prompt("abcde") == 2


def test_record_log_only_tpm_increments_actual_and_estimated_windows(
    caplog: pytest.LogCaptureFixture,
) -> None:
    cloudwatch = MagicMock()
    counter = FakeCounterClient()

    with caplog.at_level(logging.INFO):
        result = tpm_limiter.record_log_only_tpm(
            cloudwatch,
            tenant_context=_tenant_context(),
            agent=_agent(),
            actual_tokens=20,
            estimated_tokens=12,
            model_id="anthropic.test-model",
            counter_client=counter,
            now=1_800_000_001,
        )

    assert result.skipped is False
    assert result.window_usage == 20
    assert counter.increments == [
        ("LIMITER/tenant-123:anthropic.test-model:tpm/1800000060", 20),
        ("LIMITER/tenant-123:anthropic.test-model:tpm_estimated/1800000060", 12),
    ]
    assert counter.expiries == [
        ("LIMITER/tenant-123:anthropic.test-model:tpm/1800000060", 90),
        ("LIMITER/tenant-123:anthropic.test-model:tpm_estimated/1800000060", 90),
    ]
    cloudwatch.put_metric_data.assert_called_once()
    _, kwargs = cloudwatch.put_metric_data.call_args
    assert kwargs["Namespace"] == "Platform/Bridge"
    assert kwargs["MetricData"][0]["MetricName"] == "gen_ai.tpm_window_usage"
    assert kwargs["MetricData"][0]["Value"] == 20.0
    assert {"Name": "TenantId", "Value": "tenant-123"} in kwargs["MetricData"][0]["Dimensions"]
    assert {"Name": "AppId", "Value": "app-123"} in kwargs["MetricData"][0]["Dimensions"]
    assert {"Name": "ModelId", "Value": "anthropic.test-model"} in kwargs["MetricData"][0][
        "Dimensions"
    ]

    records = [record for record in caplog.records if record.message == "TPM usage recorded"]
    assert len(records) == 1
    assert records[0].__dict__["rate_limit.tpm_used"] == 20
    assert records[0].__dict__["rate_limit.tpm_estimated"] == 12


def test_record_log_only_tpm_fails_open_when_valkey_is_unavailable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        result = tpm_limiter.record_log_only_tpm(
            MagicMock(),
            tenant_context=_tenant_context(),
            agent=_agent(),
            actual_tokens=20,
            estimated_tokens=12,
            counter_client=FailingCounterClient(),
            now=1_800_000_001,
        )

    assert result.skipped is True
    event_names = {record.__dict__.get("event.name") for record in caplog.records}
    assert "valkey_unavailable" in event_names
    assert "tpm_counter_skipped" in event_names


def test_record_log_only_tpm_fails_open_when_valkey_endpoint_is_malformed(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VALKEY_ENDPOINT", "rediss://")

    with caplog.at_level(logging.WARNING):
        result = tpm_limiter.record_log_only_tpm(
            MagicMock(),
            tenant_context=_tenant_context(),
            agent=_agent(),
            actual_tokens=20,
            estimated_tokens=12,
            now=1_800_000_001,
        )

    assert result.skipped is True
    event_names = {record.__dict__.get("event.name") for record in caplog.records}
    assert "valkey_unavailable" in event_names
    assert "tpm_counter_skipped" in event_names


def test_socket_valkey_counter_client_updates_windows_in_one_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = (
        b"+OK\r\n+QUEUED\r\n+QUEUED\r\n+QUEUED\r\n+QUEUED\r\n*4\r\n:20\r\n:1\r\n:12\r\n:1\r\n"
    )
    fake_socket = FakeSocket(response)

    def fake_create_connection(
        address: tuple[str, int], timeout: float | None = None
    ) -> FakeSocket:
        assert address == ("valkey.local", 6379)
        assert timeout == 0.75
        return fake_socket

    monkeypatch.setattr(socket, "create_connection", fake_create_connection)
    client = tpm_limiter.SocketValkeyCounterClient(host="valkey.local", use_tls=False)

    assert (
        client.increment_windows(
            actual_key="actual-key",
            actual_tokens=20,
            estimated_key="estimated-key",
            estimated_tokens=12,
            ttl_seconds=90,
        )
        == 20
    )
    assert fake_socket.sent.count(b"MULTI") == 1
    assert fake_socket.sent.count(b"EXEC") == 1
    assert fake_socket.sent.count(b"INCRBY") == 2
    assert fake_socket.sent.count(b"EXPIRE") == 2
