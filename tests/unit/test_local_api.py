"""Unit tests for scripts/local_api.py."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

import scripts.local_api as local_api


def _jwt(payload: dict[str, Any]) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
    return f"header.{encoded.rstrip('=')}.signature"


def test_build_event_maps_route_and_authorizer_claims() -> None:
    token = _jwt(
        {
            "tenantid": "t-basic-001",
            "appid": "app-001",
            "tier": "basic",
            "sub": "u-001",
        }
    )
    event = local_api.build_event(
        path="/v1/agents/echo-agent/invoke",
        agent_name="echo-agent",
        headers={
            "authorization": f"Bearer {token}",
            "x-tenant-id": "ignored",
        },
        body='{"input": "Hello"}',
    )

    assert event["httpMethod"] == "POST"
    assert event["headers"]["authorization"] == "Bearer <redacted>"
    assert event["pathParameters"] == {"agentName": "echo-agent"}
    assert event["requestContext"]["authorizer"] == {
        "tenantid": "t-basic-001",
        "appid": "app-001",
        "tier": "basic",
        "sub": "u-001",
    }
    assert event["body"] == '{"input": "Hello"}'


def test_build_event_uses_tenant_header_when_token_is_missing() -> None:
    event = local_api.build_event(
        path="/v1/agents/echo-agent/invoke",
        agent_name="echo-agent",
        headers={"x-tenant-id": "t-basic-001"},
        body="{}",
    )

    assert event["requestContext"]["authorizer"]["tenantid"] == "t-basic-001"
    assert event["requestContext"]["authorizer"]["appid"] == "platform-local"
    assert event["requestContext"]["authorizer"]["tier"] == "basic"


def test_post_invocation_passes_lambda_response(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def _fake_bridge_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
        seen["event"] = event
        seen["request_id"] = context.aws_request_id
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "text/event-stream; charset=utf-8"},
            "body": "data: [DONE]\n\n",
        }

    monkeypatch.setattr(local_api, "bridge_handler", _fake_bridge_handler)
    handler = local_api.LocalApiHandler.__new__(local_api.LocalApiHandler)
    handler.path = "/v1/agents/echo-agent/invoke"
    handler.headers = {"Content-Length": "18", "x-tenant-id": "t-basic-001"}
    handler.rfile = Mock()
    handler.rfile.read.return_value = b'{"input": "Hello"}'
    handler._send = Mock()

    handler.do_POST()

    assert seen["event"]["path"] == "/v1/agents/echo-agent/invoke"
    assert seen["event"]["pathParameters"] == {"agentName": "echo-agent"}
    assert seen["event"]["requestContext"]["authorizer"]["tenantid"] == "t-basic-001"
    assert seen["request_id"]
    handler._send.assert_called_once_with(
        200,
        {"Content-Type": "text/event-stream; charset=utf-8"},
        "data: [DONE]\n\n",
    )


def test_script_is_importable_from_repo_root() -> None:
    assert Path(local_api.__file__).name == "local_api.py"
