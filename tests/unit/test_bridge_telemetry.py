from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

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


def _dimensions(metric: dict[str, Any]) -> dict[str, str]:
    return {dimension["Name"]: dimension["Value"] for dimension in metric["Dimensions"]}


def test_emit_invocation_metrics_includes_tenant_app_and_agent_dimensions() -> None:
    cloudwatch = MagicMock()

    telemetry.emit_invocation_metrics(
        cloudwatch,
        _tenant_context(),
        _agent(),
        InvocationStatus.SUCCESS,
        latency_ms=123,
        input_tokens=10,
        output_tokens=20,
    )

    cloudwatch.put_metric_data.assert_called_once()
    kwargs = cloudwatch.put_metric_data.call_args.kwargs
    assert kwargs["Namespace"] == "Platform/Bridge"
    metrics = kwargs["MetricData"]
    assert {metric["MetricName"] for metric in metrics} == {
        "Invocations",
        "Latency",
        "InputTokens",
        "OutputTokens",
    }
    dimension_sets = [_dimensions(metric) for metric in metrics]
    assert all(dimensions["TenantId"] == "tenant-123" for dimensions in dimension_sets)
    assert all(dimensions["AppId"] == "app-123" for dimensions in dimension_sets)
    assert {"TenantId": "tenant-123", "AppId": "app-123"} in dimension_sets
    assert {"TenantId": "tenant-123", "AppId": "app-123", "AgentName": "echo-agent"} in (
        dimension_sets
    )


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
            runtime_region="eu-west-2",
        )

    _assert_warning_record(
        caplog,
        message="Failed to emit Bedrock throttle metric",
        error="throttle metric unavailable",
    )


def test_emit_bedrock_throttle_metric_includes_app_id() -> None:
    cloudwatch = MagicMock()

    telemetry.emit_bedrock_throttle_metric(
        cloudwatch,
        tenant_context=_tenant_context(),
        agent=_agent(),
        runtime_region="eu-west-2",
    )

    cloudwatch.put_metric_data.assert_called_once()
    metric = cloudwatch.put_metric_data.call_args.kwargs["MetricData"][0]
    assert metric["MetricName"] == "Invocation.Throttled.Bedrock"
    assert _dimensions(metric) == {
        "TenantId": "tenant-123",
        "AppId": "app-123",
        "AgentName": "echo-agent",
        "RuntimeRegion": "eu-west-2",
    }


def test_emit_tpm_limit_exceeded_metric_includes_app_id() -> None:
    cloudwatch = MagicMock()

    telemetry.emit_tpm_limit_exceeded_metric(
        cloudwatch,
        tenant_context=_tenant_context(),
        agent_name="echo-agent",
    )

    cloudwatch.put_metric_data.assert_called_once()
    metric = cloudwatch.put_metric_data.call_args.kwargs["MetricData"][0]
    assert metric["MetricName"] == "event.name=tpm_limit_exceeded"
    assert _dimensions(metric) == {
        "TenantId": "tenant-123",
        "AppId": "app-123",
        "AgentName": "echo-agent",
    }


def test_log_invocation_persists_streaming_ttft_and_emits_metric() -> None:
    cloudwatch = MagicMock()
    db = MagicMock()
    agent = replace(_agent(), invocation_mode=InvocationMode.STREAMING, streaming_enabled=True)

    with patch("src.bridge.telemetry.TenantScopedDynamoDB", return_value=db):
        telemetry.log_invocation(
            cloudwatch,
            _tenant_context(),
            agent,
            "inv-ttft",
            InvocationStatus.SUCCESS,
            42,
            InvocationMode.STREAMING,
            runtime_region="eu-west-2",
            session_id="sess-ttft",
            ttft_ms=17,
        )

    _, item = db.put_item.call_args.args
    assert item["ttftMs"] == 17

    metric_names = [
        metric["MetricName"]
        for call in cloudwatch.put_metric_data.call_args_list
        for metric in call.kwargs["MetricData"]
    ]
    assert "gen_ai.ttft_ms" in metric_names

    ttft_metrics = [
        metric
        for call in cloudwatch.put_metric_data.call_args_list
        for metric in call.kwargs["MetricData"]
        if metric["MetricName"] == "gen_ai.ttft_ms"
    ]
    assert len(ttft_metrics) == 2
    for metric in ttft_metrics:
        assert metric["Value"] == 17
        assert metric["Unit"] == "Milliseconds"

    dimension_sets = [
        {dimension["Name"]: dimension["Value"] for dimension in metric["Dimensions"]}
        for metric in ttft_metrics
    ]
    assert {
        "AgentName": "echo-agent",
        "InvocationMode": "streaming",
        "RuntimeRegion": "eu-west-2",
    } in dimension_sets
    assert {
        "AgentName": "all",
        "InvocationMode": "streaming",
        "RuntimeRegion": "all",
    } in dimension_sets


@pytest.mark.parametrize("mode", [InvocationMode.SYNC, InvocationMode.ASYNC])
def test_log_invocation_persists_null_ttft_for_non_streaming_modes(mode: InvocationMode) -> None:
    cloudwatch = MagicMock()
    db = MagicMock()
    agent = replace(_agent(), invocation_mode=mode)

    with patch("src.bridge.telemetry.TenantScopedDynamoDB", return_value=db):
        telemetry.log_invocation(
            cloudwatch,
            _tenant_context(),
            agent,
            f"inv-{mode.value}",
            InvocationStatus.SUCCESS,
            42,
            mode,
            runtime_region="eu-west-2",
            session_id=f"sess-{mode.value}",
        )

    _, item = db.put_item.call_args.args
    assert item["ttftMs"] is None

    metric_names = [
        metric["MetricName"]
        for call in cloudwatch.put_metric_data.call_args_list
        for metric in call.kwargs["MetricData"]
    ]
    assert "gen_ai.ttft_ms" not in metric_names
