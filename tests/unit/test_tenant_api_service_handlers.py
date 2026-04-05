from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.tenant_api import admin_ops_handler, agent_registry_handler


class _Context:
    function_name = "test-fn"
    function_version = "$LATEST"
    invoked_function_arn = "arn:aws:lambda:eu-west-2:111111111111:function:test-fn"
    memory_limit_in_mb = 128
    aws_request_id = "req-123"


def _event(path: str, method: str = "GET") -> dict[str, Any]:
    return {
        "path": path,
        "httpMethod": method,
        "requestContext": {
            "authorizer": {
                "claims": {
                    "sub": "user-123",
                    "tid": "tenant-123",
                    "tenant_id": "tenant-123",
                    "appid": "app-123",
                    "roles": "Platform.Admin",
                }
            }
        },
    }


def _status(response: dict[str, Any]) -> int:
    return int(response["statusCode"])


def _body(response: dict[str, Any]) -> dict[str, Any]:
    return json.loads(str(response["body"]))


def test_agent_registry_handler_rejects_non_agent_platform_route() -> None:
    import os

    os.environ["AWS_REGION"] = "eu-west-2"
    response = agent_registry_handler.lambda_handler(_event("/v1/platform/quota"), _Context())

    assert _status(response) == 404
    assert _body(response)["error"]["code"] == "NOT_FOUND"


def test_admin_ops_handler_rejects_agent_registry_route() -> None:
    import os

    os.environ["AWS_REGION"] = "eu-west-2"
    response = admin_ops_handler.lambda_handler(_event("/v1/platform/agents"), _Context())

    assert _status(response) == 404
    assert _body(response)["error"]["code"] == "NOT_FOUND"


def test_tenant_mgmt_handler_rejects_non_tenant_route(monkeypatch) -> None:
    import os

    from src.tenant_api import tenant_mgmt_handler

    os.environ["AWS_REGION"] = "eu-west-2"
    monkeypatch.setattr(
        tenant_mgmt_handler.bootstrap,
        "build_runtime",
        lambda _incoming_event: tenant_mgmt_handler.bootstrap.TenantApiRuntime(
            deps=object(),
            caller=tenant_mgmt_handler.http_utils.caller_identity(_event("/v1/platform/quota")),
            method="GET",
            path="/v1/platform/quota",
            tenant_id=None,
            detail_type=None,
            source=None,
            detail=None,
        ),
    )

    response = tenant_mgmt_handler.lambda_handler(_event("/v1/platform/quota"), _Context())

    assert _status(response) == 404
    assert _body(response)["error"]["code"] == "NOT_FOUND"


def test_webhook_handler_rejects_non_webhook_route(monkeypatch) -> None:
    import os

    from src.tenant_api import webhook_registry_handler

    os.environ["AWS_REGION"] = "eu-west-2"
    monkeypatch.setattr(
        webhook_registry_handler.bootstrap,
        "build_runtime",
        lambda _incoming_event: webhook_registry_handler.bootstrap.TenantApiRuntime(
            deps=object(),
            caller=webhook_registry_handler.http_utils.caller_identity(
                _event("/v1/platform/quota")
            ),
            method="GET",
            path="/v1/platform/quota",
            tenant_id=None,
            detail_type=None,
            source=None,
            detail=None,
        ),
    )

    response = webhook_registry_handler.lambda_handler(_event("/v1/platform/quota"), _Context())

    assert _status(response) == 404
    assert _body(response)["error"]["code"] == "NOT_FOUND"


def test_tenant_mgmt_handler_builds_runtime_and_dispatches_tenant_route(monkeypatch) -> None:
    import os

    from src.tenant_api import tenant_lifecycle, tenant_mgmt_handler

    os.environ["AWS_REGION"] = "eu-west-2"
    fake_deps = object()
    captured: dict[str, Any] = {}

    def _build_deps(*, region: str):
        captured["region"] = region
        return fake_deps

    monkeypatch.setattr(
        tenant_mgmt_handler.bootstrap.dependency_factories,
        "build_tenant_api_dependencies",
        _build_deps,
    )
    monkeypatch.setattr(
        tenant_lifecycle,
        "dispatch_routes",
        lambda path, method, event, caller, deps, tenant_id: (
            captured.update(
                {
                    "path": path,
                    "method": method,
                    "caller": caller,
                    "deps": deps,
                    "tenant_id": tenant_id,
                }
            )
            or {"statusCode": 200, "body": json.dumps({"ok": True})}
        ),
    )

    event = _event("/v1/tenants/Tenant-Acme-001")
    event["pathParameters"] = {"tenantId": "Tenant-Acme-001"}
    response = tenant_mgmt_handler.lambda_handler(event, _Context())

    assert _status(response) == 200
    assert captured["region"] == "eu-west-2"
    assert captured["path"] == "/v1/tenants/tenant-acme-001"
    assert captured["method"] == "GET"
    assert captured["tenant_id"] == "tenant-acme-001"
    assert captured["deps"] is fake_deps


def test_webhook_handler_builds_runtime_and_dispatches_webhook_route(monkeypatch) -> None:
    import os

    from src.tenant_api import webhook_registry, webhook_registry_handler

    os.environ["AWS_REGION"] = "eu-west-2"
    fake_deps = object()
    captured: dict[str, Any] = {}

    def _build_deps(*, region: str):
        captured["region"] = region
        return fake_deps

    monkeypatch.setattr(
        webhook_registry_handler.bootstrap.dependency_factories,
        "build_tenant_api_dependencies",
        _build_deps,
    )
    monkeypatch.setattr(
        webhook_registry,
        "dispatch_routes",
        lambda path, method, event, caller, deps: (
            captured.update(
                {
                    "path": path,
                    "method": method,
                    "caller": caller,
                    "deps": deps,
                }
            )
            or {"statusCode": 200, "body": json.dumps({"ok": True})}
        ),
    )

    response = webhook_registry_handler.lambda_handler(_event("/v1/webhooks"), _Context())

    assert _status(response) == 200
    assert captured["region"] == "eu-west-2"
    assert captured["path"] == "/v1/webhooks"
    assert captured["method"] == "GET"
    assert captured["deps"] is fake_deps
