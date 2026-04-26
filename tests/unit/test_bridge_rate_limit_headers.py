"""Tests for x-ratelimit-* headers on northbound Bridge responses (TASK-905)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "data-access-lib" / "src"))

from data_access.models import AgentRecord, InvocationMode, TenantContext, TenantTier

from src.bridge import tpm_limiter
from src.bridge.tpm_limiter import TpmCounterResult, build_rate_limit_headers

_RATE_LIMIT_HEADER_NAMES = {
    "x-ratelimit-limit-tpm",
    "x-ratelimit-used-tpm",
    "x-ratelimit-limit-rpm",
    "x-ratelimit-used-rpm",
    "x-ratelimit-reset",
}


# ---------------------------------------------------------------------------
# build_rate_limit_headers unit tests
# ---------------------------------------------------------------------------


def test_build_rate_limit_headers_unlimited_when_no_limit_and_no_counter() -> None:
    headers = build_rate_limit_headers(tpm_result=None, now=1_800_000_000.0)
    assert headers["x-ratelimit-limit-tpm"] == "unlimited"
    assert headers["x-ratelimit-used-tpm"] == "0"
    assert headers["x-ratelimit-limit-rpm"] == "unlimited"
    assert headers["x-ratelimit-used-rpm"] == "0"
    assert headers["x-ratelimit-reset"] == "1800000060"


def test_build_rate_limit_headers_tpm_limit_configured() -> None:
    headers = build_rate_limit_headers(tpm_result=None, tpm_limit=50_000, now=1_800_000_000.0)
    assert headers["x-ratelimit-limit-tpm"] == "50000"
    assert headers["x-ratelimit-limit-rpm"] == "unlimited"


def test_build_rate_limit_headers_rpm_limit_configured() -> None:
    headers = build_rate_limit_headers(tpm_result=None, rpm_limit=50, now=1_800_000_000.0)
    assert headers["x-ratelimit-limit-rpm"] == "50"
    assert headers["x-ratelimit-limit-tpm"] == "unlimited"


def test_build_rate_limit_headers_zero_limits_treated_as_unlimited() -> None:
    headers = build_rate_limit_headers(
        tpm_result=None, tpm_limit=0, rpm_limit=0, now=1_800_000_000.0
    )
    assert headers["x-ratelimit-limit-tpm"] == "unlimited"
    assert headers["x-ratelimit-limit-rpm"] == "unlimited"


def test_build_rate_limit_headers_uses_counter_window_usage() -> None:
    counter_result = TpmCounterResult(
        actual_tokens=120,
        estimated_tokens=30,
        window_expiry=1_800_000_060,
        window_usage=500,
        model_id="claude",
        skipped=False,
    )
    headers = build_rate_limit_headers(tpm_result=counter_result, tpm_limit=10_000)
    assert headers["x-ratelimit-used-tpm"] == "500"
    assert headers["x-ratelimit-reset"] == "1800000060"


def test_build_rate_limit_headers_skipped_counter_shows_zero() -> None:
    skipped_result = TpmCounterResult(
        actual_tokens=0,
        estimated_tokens=0,
        window_expiry=1_800_000_060,
        window_usage=0,
        model_id="claude",
        skipped=True,
    )
    headers = build_rate_limit_headers(tpm_result=skipped_result, now=1_800_000_000.0)
    assert headers["x-ratelimit-used-tpm"] == "0"
    # reset falls back to computed window when counter skipped
    assert headers["x-ratelimit-reset"] == "1800000060"


def test_build_rate_limit_headers_ignores_non_tpmcounterresult_objects() -> None:
    """A MagicMock (as seen in tests that patch record_log_only_tpm) must not cause errors."""
    mock_result = MagicMock()
    headers = build_rate_limit_headers(tpm_result=mock_result, now=1_800_000_000.0)
    assert headers["x-ratelimit-used-tpm"] == "0"
    assert headers["x-ratelimit-reset"] == "1800000060"


def test_build_rate_limit_headers_rpm_used_passthrough() -> None:
    headers = build_rate_limit_headers(tpm_result=None, rpm_used=7, now=1_800_000_000.0)
    assert headers["x-ratelimit-used-rpm"] == "7"


def test_build_rate_limit_headers_returns_all_five_keys() -> None:
    headers = build_rate_limit_headers(tpm_result=None, now=1_800_000_000.0)
    assert set(headers.keys()) == _RATE_LIMIT_HEADER_NAMES


# ---------------------------------------------------------------------------
# invoke_mock_runtime: headers on 200 response
# ---------------------------------------------------------------------------


def _agent(mode: InvocationMode = InvocationMode.SYNC) -> AgentRecord:
    return AgentRecord(
        agent_name="echo-agent",
        version="1.0.0",
        owner_team="platform",
        tier_minimum=TenantTier.BASIC,
        layer_hash="h",
        layer_s3_key="k",
        script_s3_key="s",
        deployed_at="2026-01-01T00:00:00Z",
        invocation_mode=mode,
        streaming_enabled=False,
    )


def _tenant() -> TenantContext:
    return TenantContext(tenant_id="t-1", app_id="a-1", tier=TenantTier.BASIC, sub="u-1")


class _FakeCounter:
    def increment_windows(
        self, *, actual_key, actual_tokens, estimated_key, estimated_tokens, ttl_seconds
    ):
        return actual_tokens + 100  # simulate previous window usage


def test_invoke_mock_runtime_200_response_includes_all_rate_limit_headers() -> None:
    from src.bridge import runtime_calls

    mock_http = MagicMock()
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.status_code = 200
    mock_resp.headers = {"Content-Type": "application/json"}
    mock_resp.iter_lines.return_value = []
    mock_resp.text = json.dumps({"output": "hi", "usage": {"inputTokens": 10, "outputTokens": 5}})
    mock_http.return_value.post.return_value = mock_resp

    mock_cw = MagicMock()

    def _log(*args, **kwargs):
        return tpm_limiter.record_log_only_tpm(
            mock_cw,
            tenant_context=_tenant(),
            agent=_agent(),
            actual_tokens=kwargs.get("input_tokens", 0) + kwargs.get("output_tokens", 0),
            estimated_tokens=kwargs.get("estimated_tokens", 0),
            model_id=kwargs.get("model_id"),
            counter_client=_FakeCounter(),
            now=1_800_000_001.0,
        )

    result = runtime_calls.invoke_mock_runtime(
        "http://localhost:8765",
        _agent(),
        _tenant(),
        "hello",
        None,
        None,
        "req-1",
        None,
        "inv-1",
        0.0,
        get_http_session=mock_http,
        build_runtime_payload=runtime_calls.build_runtime_payload,
        log_invocation=_log,
    )

    assert result is not None
    assert result["statusCode"] == 200
    headers = result["headers"]
    assert _RATE_LIMIT_HEADER_NAMES.issubset(set(headers.keys()))
    assert headers["x-ratelimit-limit-tpm"] == "unlimited"
    assert headers["x-ratelimit-used-tpm"] == "115"  # 10+5+100 from counter
    assert headers["x-ratelimit-reset"].isdigit()


def test_invoke_mock_runtime_error_response_includes_rate_limit_headers() -> None:
    """Even when the mock HTTP request fails, error response carries rate limit headers."""
    from src.bridge import runtime_calls

    mock_http = MagicMock()
    mock_http.return_value.post.side_effect = ConnectionError("runtime down")

    mock_log = MagicMock(return_value=None)
    mock_failure_response = MagicMock(
        return_value={
            "statusCode": 500,
            "headers": {"Content-Type": "application/json"},
            "body": "{}",
        }
    )

    result = runtime_calls.invoke_mock_runtime(
        "http://localhost:8765",
        _agent(),
        _tenant(),
        "hello",
        None,
        None,
        "req-2",
        None,
        "inv-2",
        0.0,
        get_http_session=mock_http,
        build_runtime_payload=runtime_calls.build_runtime_payload,
        log_invocation=mock_log,
        runtime_failure_response=mock_failure_response,
    )

    assert result is not None
    headers = result.get("headers", {})
    assert _RATE_LIMIT_HEADER_NAMES.issubset(set(headers.keys()))
    assert headers["x-ratelimit-limit-tpm"] == "unlimited"
    assert headers["x-ratelimit-used-tpm"] == "0"


# ---------------------------------------------------------------------------
# handle_streaming_invocation: headers in preamble
# ---------------------------------------------------------------------------


def test_handle_streaming_invocation_preamble_includes_rate_limit_headers() -> None:
    from src.bridge.route_adapter import handle_streaming_invocation

    mock_stream = MagicMock()
    mock_response = MagicMock()
    mock_response.iter_lines.return_value = []
    mock_http = MagicMock()
    mock_http.return_value.post.return_value.__enter__ = lambda s: mock_response
    mock_http.return_value.post.return_value.__exit__ = MagicMock(return_value=False)
    mock_log = MagicMock(return_value=None)

    with patch("src.bridge.route_adapter.get_http_session", mock_http):
        with patch("src.bridge.handler.log_invocation", mock_log):
            handle_streaming_invocation(
                url="http://runtime:8080",
                headers={},
                payload={},
                agent=_agent(InvocationMode.STREAMING),
                tenant_context=_tenant(),
                invocation_id="inv-s",
                start_time=0.0,
                response_stream=mock_stream,
                request_id="req-s",
                session_id=None,
            )

    preamble_bytes = mock_stream.write.call_args_list[0][0][0]
    preamble = json.loads(preamble_bytes.decode("utf-8").rstrip("\0"))
    headers = preamble["headers"]
    assert _RATE_LIMIT_HEADER_NAMES.issubset(set(headers.keys()))
    assert headers["x-ratelimit-limit-tpm"] == "unlimited"
    assert headers["x-ratelimit-used-tpm"] == "0"
    assert headers["x-ratelimit-reset"].isdigit()


# ---------------------------------------------------------------------------
# 429 ThrottlingException response includes rate limit headers
# ---------------------------------------------------------------------------


def test_invoke_agent_throttle_429_includes_rate_limit_headers() -> None:
    from botocore.exceptions import ClientError

    from src.bridge.route_adapter import invoke_agent

    throttle_exc = ClientError(
        {
            "Error": {"Code": "ThrottlingException", "Message": "too many requests"},
            "ResponseMetadata": {"HTTPStatusCode": 429},
        },
        "InvokeAgentRuntime",
    )

    fake_call = MagicMock(side_effect=throttle_exc)
    mock_log = MagicMock(return_value=None)
    mock_config = MagicMock(return_value={"runtime_region": "eu-west-1", "mock_runtime_url": None})
    mock_cw = MagicMock(return_value=MagicMock())

    with (
        patch("src.bridge.route_adapter._handler_dependency", return_value=None),
        patch("src.bridge.route_adapter._local_or_handler_dependency") as mock_dep,
        patch("src.bridge.route_adapter.get_cloudwatch", mock_cw),
    ):
        # Only the config dep is used by invoke_agent directly
        mock_dep.side_effect = lambda name, default: (
            mock_config() if name == "get_config" else default
        )
        with patch("src.bridge.route_adapter._handler_dependency") as mock_hdep:
            mock_hdep.side_effect = lambda name, fallback: (
                fake_call
                if name == "invoke_real_runtime"
                else mock_log
                if name == "log_invocation"
                else None
            )
            with patch("src.bridge.route_adapter._local_or_handler_dependency") as mock_ldep:
                mock_ldep.side_effect = lambda name, default: (
                    mock_config if name == "get_config" else default
                )

                result = invoke_agent(
                    _agent(),
                    _tenant(),
                    "hello",
                    None,
                    None,
                    "req-t",
                    None,
                )

    assert result is not None
    assert result["statusCode"] == 429
    headers = result.get("headers", {})
    assert _RATE_LIMIT_HEADER_NAMES.issubset(set(headers.keys()))
    assert headers["x-ratelimit-limit-tpm"] == "unlimited"


# ---------------------------------------------------------------------------
# Non-model routes: no rate limit headers
# ---------------------------------------------------------------------------


def test_non_model_routes_have_no_rate_limit_headers() -> None:
    """GET /v1/agents and GET /v1/jobs/:id do not carry rate limit headers."""
    import os
    from unittest.mock import patch

    import boto3
    from moto import mock_aws

    os.environ["AWS_ACCESS_KEY_ID"] = "testing"  # pragma: allowlist secret
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"  # pragma: allowlist secret
    os.environ["AWS_DEFAULT_REGION"] = "eu-west-2"
    os.environ["AWS_REGION"] = "eu-west-2"

    class _Ctx:
        aws_request_id = "req-list"
        function_name = "bridge"
        memory_limit_in_mb = 128
        invoked_function_arn = "arn:aws:lambda:eu-west-2:111111111111:function:bridge"

    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="eu-west-2")
        ddb.create_table(
            TableName="platform-agents",
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        event = {
            "httpMethod": "GET",
            "path": "/v1/agents",
            "pathParameters": {},
            "requestContext": {
                "authorizer": {"tenantid": "t-1", "appid": "a-1", "tier": "basic", "sub": "u-1"}
            },
        }

        with patch("src.bridge.handler.get_capability_client") as mock_cap:
            mock_client = MagicMock()
            mock_cap.return_value = mock_client
            policy = MagicMock()
            policy.is_enabled.return_value = True
            mock_client.fetch_policy.return_value = policy

            from src.bridge.handler import handler

            response = handler(event, _Ctx())

        assert response["statusCode"] == 200
        headers = response.get("headers", {})
        for name in _RATE_LIMIT_HEADER_NAMES:
            assert name not in headers, f"Unexpected rate limit header {name!r} on non-model route"
