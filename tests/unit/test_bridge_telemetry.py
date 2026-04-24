from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "data-access-lib" / "src"))

from data_access.models import (
    AgentRecord,
    InvocationMode,
    InvocationStatus,
    TenantContext,
    TenantTier,
)

from src.bridge import telemetry


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


def _assert_warning_record(
    caplog: pytest.LogCaptureFixture,
    *,
    message: str,
    error: str,
) -> None:
    matching_records = [record for record in caplog.records if record.message == message]
    assert len(matching_records) == 1
    assert getattr(matching_records[0], "error", None) == error


def test_emit_invocation_metrics_logs_structured_warning_on_cloudwatch_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    cloudwatch = MagicMock()
    cloudwatch.put_metric_data.side_effect = RuntimeError("metrics unavailable")

    with caplog.at_level("WARNING"):
        telemetry.emit_invocation_metrics(
            cloudwatch,
            _tenant_context(),
            _agent(),
            InvocationStatus.ERROR,
            latency_ms=123,
            input_tokens=10,
            output_tokens=20,
        )

    _assert_warning_record(
        caplog,
        message="Failed to emit invocation metrics",
        error="metrics unavailable",
    )


def test_emit_bedrock_throttle_metric_logs_structured_warning_on_cloudwatch_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    cloudwatch = MagicMock()
    cloudwatch.put_metric_data.side_effect = RuntimeError("throttle metric unavailable")

    with caplog.at_level("WARNING"):
        telemetry.emit_bedrock_throttle_metric(
            cloudwatch,
            tenant_context=_tenant_context(),
            agent=_agent(),
            runtime_region="eu-west-1",
        )

    _assert_warning_record(
        caplog,
        message="Failed to emit Bedrock throttle metric",
        error="throttle metric unavailable",
    )
