from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from aws_lambda_powertools import Logger
from data_access import TenantScopedDynamoDB
from data_access.models import (
    AgentRecord,
    InvocationMode,
    InvocationRecord,
    InvocationStatus,
    JobRecord,
    TenantContext,
)

from src.bridge.constants import INVOCATION_TTL_SECONDS, INVOCATIONS_TABLE, JOBS_TABLE

logger = Logger(service="bridge-telemetry")


def record_invocation_metric(
    cloudwatch: Any,
    *,
    tenant_id: str,
    agent_name: str,
    status: InvocationStatus,
    latency_ms: float,
) -> None:
    try:
        cloudwatch.put_metric_data(
            Namespace="Platform/Bridge",
            MetricData=[
                {
                    "MetricName": "InvocationCount",
                    "Dimensions": [
                        {"Name": "TenantId", "Value": tenant_id},
                        {"Name": "AgentName", "Value": agent_name},
                        {"Name": "Status", "Value": status.value},
                    ],
                    "Value": 1,
                    "Unit": "Count",
                },
                {
                    "MetricName": "InvocationLatency",
                    "Dimensions": [
                        {"Name": "TenantId", "Value": tenant_id},
                        {"Name": "AgentName", "Value": agent_name},
                    ],
                    "Value": latency_ms,
                    "Unit": "Milliseconds",
                },
            ],
        )
    except Exception:
        logger.warning("Failed to record invocation metrics to CloudWatch")


def emit_invocation_metrics(
    cloudwatch: Any,
    tenant_context: TenantContext,
    agent: AgentRecord,
    status: InvocationStatus,
    latency_ms: int,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """Emit real-time invocation metrics to CloudWatch."""
    try:
        dimensions_sets = [
            [
                {"Name": "TenantId", "Value": tenant_context.tenant_id},
                {"Name": "AgentName", "Value": agent.agent_name},
            ],
            [
                {"Name": "TenantId", "Value": tenant_context.tenant_id},
            ],
        ]

        metric_data = []
        for dims in dimensions_sets:
            metric_data.extend(
                [
                    {
                        "MetricName": "Invocations",
                        "Value": 1.0,
                        "Unit": "Count",
                        "Dimensions": dims,
                    },
                    {
                        "MetricName": "Latency",
                        "Value": float(latency_ms),
                        "Unit": "Milliseconds",
                        "Dimensions": dims,
                    },
                    {
                        "MetricName": "InputTokens",
                        "Value": float(input_tokens),
                        "Unit": "Count",
                        "Dimensions": dims,
                    },
                    {
                        "MetricName": "OutputTokens",
                        "Value": float(output_tokens),
                        "Unit": "Count",
                        "Dimensions": dims,
                    },
                ]
            )
            if status != InvocationStatus.SUCCESS:
                metric_data.append(
                    {"MetricName": "Errors", "Value": 1.0, "Unit": "Count", "Dimensions": dims}
                )

        cloudwatch.put_metric_data(Namespace="Platform/Bridge", MetricData=metric_data)
    except Exception as exc:
        logger.warning("Failed to emit invocation metrics", extra={"error": str(exc)})


def emit_bedrock_throttle_metric(
    cloudwatch: Any,
    *,
    tenant_context: TenantContext,
    agent: AgentRecord,
    runtime_region: str,
) -> None:
    try:
        cloudwatch.put_metric_data(
            Namespace="Platform/Bridge",
            MetricData=[
                {
                    "MetricName": "Invocation.Throttled.Bedrock",
                    "Value": 1.0,
                    "Unit": "Count",
                    "Dimensions": [
                        {"Name": "TenantId", "Value": tenant_context.tenant_id},
                        {"Name": "AgentName", "Value": agent.agent_name},
                        {"Name": "RuntimeRegion", "Value": runtime_region},
                    ],
                }
            ],
        )
    except Exception as exc:
        logger.warning("Failed to emit Bedrock throttle metric", extra={"error": str(exc)})


def log_invocation(
    cloudwatch: Any,
    tenant_context: TenantContext,
    agent: AgentRecord,
    invocation_id: str,
    status: InvocationStatus,
    latency_ms: int,
    mode: InvocationMode,
    runtime_region: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    job_id: str | None = None,
    session_id: str | None = None,
    error_code: str | None = None,
    jitter: str | None = None,
) -> None:
    """Write invocation audit record to DynamoDB and emit metrics."""
    try:
        db = TenantScopedDynamoDB(tenant_context)
        now_iso = datetime.now(UTC).isoformat()
        now_ts = int(time.time())

        record = InvocationRecord(
            invocation_id=invocation_id,
            tenant_id=tenant_context.tenant_id,
            app_id=tenant_context.app_id,
            agent_name=agent.agent_name,
            agent_version=agent.version,
            session_id=session_id or "unknown-session",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            status=status,
            runtime_region=runtime_region,
            invocation_mode=mode,
            timestamp=now_iso,
            ttl=now_ts + INVOCATION_TTL_SECONDS,
            jitter=jitter,
            error_code=error_code,
            job_id=job_id,
        )

        item = {
            "PK": record.pk,
            "SK": record.sk,
            "invocation_id": record.invocation_id,
            "tenant_id": record.tenant_id,
            "app_id": record.app_id,
            "agent_name": record.agent_name,
            "agent_version": record.agent_version,
            "session_id": record.session_id,
            "input_tokens": record.input_tokens,
            "output_tokens": record.output_tokens,
            "latency_ms": record.latency_ms,
            "status": str(record.status),
            "runtime_region": record.runtime_region,
            "invocation_mode": str(record.invocation_mode),
            "timestamp": record.timestamp,
            "ttl": record.ttl,
        }
        if record.jitter:
            item["jitter"] = record.jitter
        if record.job_id:
            item["job_id"] = record.job_id
        if record.error_code:
            item["error_code"] = record.error_code

        db.put_item(INVOCATIONS_TABLE, item)
        emit_invocation_metrics(
            cloudwatch, tenant_context, agent, status, latency_ms, input_tokens, output_tokens
        )
    except Exception:
        logger.exception("Failed to log invocation")


def log_job(tenant_context: TenantContext, record: JobRecord) -> None:
    """Write job record to DynamoDB."""
    try:
        db = TenantScopedDynamoDB(tenant_context)
        item = {
            "PK": record.pk,
            "SK": record.sk,
            "job_id": record.job_id,
            "tenant_id": record.tenant_id,
            "app_id": record.app_id,
            "agent_name": record.agent_name,
            "status": str(record.status),
            "created_at": record.created_at,
            "ttl": record.ttl,
        }
        # Add optional fields if present
        for field in (
            "webhook_id",
            "webhook_url",
            "webhook_delivery_status",
            "webhook_delivery_error",
            "webhook_last_attempt_at",
            "started_at",
            "completed_at",
            "result_s3_key",
            "error_message",
        ):
            val = getattr(record, field, None)
            if val is not None:
                item[field] = val

        item["webhook_delivered"] = bool(getattr(record, "webhook_delivered", False))
        item["webhook_delivery_attempts"] = int(getattr(record, "webhook_delivery_attempts", 0))

        db.put_item(JOBS_TABLE, item)
    except Exception:
        logger.exception("Failed to log job")
