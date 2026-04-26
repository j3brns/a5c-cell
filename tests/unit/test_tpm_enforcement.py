from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest
from data_access.models import InvocationMode, TenantCapabilityPolicy, TenantContext, TenantTier

from src.bridge.invocation_engine import handle_invoke_request
from src.bridge.limiter import RateLimitResult


class TestTPMEnforcement:
    @pytest.fixture(autouse=True)
    def aws_env(self):
        with patch.dict(os.environ, {"AWS_REGION": "eu-west-1"}):
            yield

    @pytest.fixture
    def tenant_context(self):
        return TenantContext(tenant_id="t1", app_id="a1", tier=TenantTier.STANDARD, sub="s1")

    @pytest.fixture
    def agent_record(self):
        agent = MagicMock()
        agent.agent_name = "agent1"
        agent.version = "v1"
        agent.invocation_mode = InvocationMode.SYNC
        agent.model_id = "m1"
        agent.tier_minimum = TenantTier.STANDARD
        return agent

    @pytest.fixture
    def capability_policy(self):
        policy = MagicMock(spec=TenantCapabilityPolicy)
        policy.tpm_limits = {"m1": {TenantTier.STANDARD: 100}}
        policy.get_tpm_limit.side_effect = lambda m, t: policy.tpm_limits.get(m, {}).get(t, 0)
        policy.is_enabled.return_value = True
        return policy

    @patch("src.bridge.invocation_engine.get_limiter")
    @patch("src.bridge.invocation_engine.telemetry")
    def test_handle_invoke_request_tpm_throttled(
        self,
        mock_telemetry,
        mock_get_limiter,
        tenant_context,
        agent_record,
        capability_policy,
    ):
        mock_limiter = MagicMock()
        mock_get_limiter.return_value = mock_limiter

        # Throttled result
        mock_limiter.check_and_increment.return_value = RateLimitResult(
            allowed=False, limit=100, used=95, reset_seconds=45
        )

        event = {"body": json.dumps({"input": "test prompt"})}

        result = handle_invoke_request(
            event=event,
            request_id="req1",
            tenant_context=tenant_context,
            path="/v1/agents/agent1/invoke",
            path_params={"agentName": "agent1"},
            response_stream=None,
            error_response=lambda *args: {"statusCode": args[0], "headers": {}, "body": args[1]},
            parse_body=lambda e: json.loads(e["body"]),
            coerce_optional_string=lambda x: x,
            is_invoke_contract_path=lambda p, a: True,
            get_agent_record=lambda *args: agent_record,
            capability_policy=capability_policy,
            invoke_agent=MagicMock(),
        )

        assert result["statusCode"] == 429
        assert result["headers"]["X-RateLimit-Limit-TPM"] == "100"
        assert result["headers"]["X-RateLimit-Used-TPM"] == "95"
        assert result["headers"]["X-RateLimit-Reset"] == "45"
        mock_telemetry.emit_tpm_limit_exceeded_metric.assert_called_once()

    @patch("src.bridge.invocation_engine.get_limiter")
    def test_handle_invoke_request_tpm_allowed(
        self, mock_get_limiter, tenant_context, agent_record, capability_policy
    ):
        mock_limiter = MagicMock()
        mock_get_limiter.return_value = mock_limiter
        mock_limiter.check_and_increment.return_value = RateLimitResult(
            allowed=True, limit=100, used=10, reset_seconds=60
        )

        mock_invoke = MagicMock()
        mock_invoke.return_value = {"statusCode": 200, "body": "OK"}

        event = {"body": json.dumps({"input": "test prompt"})}

        result = handle_invoke_request(
            event=event,
            request_id="req1",
            tenant_context=tenant_context,
            path="/v1/agents/agent1/invoke",
            path_params={"agentName": "agent1"},
            response_stream=None,
            error_response=lambda *args: {"statusCode": args[0]},
            parse_body=lambda e: json.loads(e["body"]),
            coerce_optional_string=lambda x: x,
            is_invoke_contract_path=lambda p, a: True,
            get_agent_record=lambda *args: agent_record,
            capability_policy=capability_policy,
            invoke_agent=mock_invoke,
        )

        assert result["statusCode"] == 200
        mock_invoke.assert_called_once()
        # Estimate for "test prompt" (11 chars) should be 11 // 4 = 2
        assert mock_invoke.call_args[1]["estimate"] == 2

    @patch("src.bridge.invocation_engine.get_limiter")
    def test_handle_invoke_request_no_limit(self, mock_get_limiter, tenant_context, agent_record):
        # Empty policy (no limit for m1)
        policy = MagicMock(spec=TenantCapabilityPolicy)
        policy.get_tpm_limit.return_value = 0
        policy.is_enabled.return_value = True

        mock_limiter = MagicMock()
        mock_get_limiter.return_value = mock_limiter
        # Simulate Redis available but no limit
        mock_limiter._redis = MagicMock()
        mock_limiter.check_and_increment.return_value = RateLimitResult(
            allowed=True, limit=-1, used=1, reset_seconds=60
        )

        mock_invoke = MagicMock()
        mock_invoke.return_value = {"statusCode": 200}

        event = {"body": json.dumps({"input": "test"})}

        handle_invoke_request(
            event=event,
            request_id="req1",
            tenant_context=tenant_context,
            path="/v1/agents/agent1/invoke",
            path_params={"agentName": "agent1"},
            response_stream=None,
            error_response=MagicMock(),
            parse_body=lambda e: json.loads(e["body"]),
            coerce_optional_string=lambda x: x,
            is_invoke_contract_path=lambda p, a: True,
            get_agent_record=lambda *args: agent_record,
            capability_policy=policy,
            invoke_agent=mock_invoke,
        )

        mock_limiter.check_and_increment.assert_called_with("t1", "m1", 0, 1)
