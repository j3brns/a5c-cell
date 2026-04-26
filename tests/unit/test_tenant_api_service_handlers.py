from __future__ import annotations

import json
from typing import Any

from src.tenant_api import admin_ops_handler, agent_registry_handler
from src.tenant_api import handler as tenant_api_handler


def _status(response: dict[str, Any]) -> int:
    return int(response["statusCode"])


def _body(response: dict[str, Any]) -> dict[str, Any]:
    return json.loads(str(response["body"]))


def test_agent_registry_handler_rejects_non_agent_platform_route(
    fake_state: dict[str, Any],
    tenant_api_event: Any,
) -> None:
    event = tenant_api_event(method="GET")
    event["path"] = "/v1/platform/quota"  # Non-matching path
    response = agent_registry_handler.handle_event(
        event,
        dependencies=fake_state["deps"],
    )

    assert _status(response) == 404
    assert _body(response)["error"]["code"] == "NOT_FOUND"


def test_admin_ops_handler_rejects_agent_registry_route(
    fake_state: dict[str, Any],
    tenant_api_event: Any,
) -> None:
    event = tenant_api_event(method="GET")
    event["path"] = "/v1/platform/agents"  # Non-matching path
    response = admin_ops_handler.handle_event(
        event,
        dependencies=fake_state["deps"],
    )

    assert _status(response) == 404
    assert _body(response)["error"]["code"] == "NOT_FOUND"


def test_tenant_mgmt_handler_rejects_non_tenant_route(
    fake_state: dict[str, Any],
    tenant_api_event: Any,
) -> None:
    from src.tenant_api import tenant_mgmt_handler

    event = tenant_api_event(method="GET")
    event["path"] = "/v1/platform/quota"  # Non-matching path
    response = tenant_mgmt_handler.handle_event(
        event,
        dependencies=fake_state["deps"],
    )

    assert _status(response) == 404
    assert _body(response)["error"]["code"] == "NOT_FOUND"


def test_webhook_handler_rejects_non_webhook_route(
    fake_state: dict[str, Any],
    tenant_api_event: Any,
) -> None:
    from src.tenant_api import webhook_registry_handler

    event = tenant_api_event(method="GET")
    event["path"] = "/v1/platform/quota"  # Non-matching path
    response = webhook_registry_handler.handle_event(
        event,
        dependencies=fake_state["deps"],
    )

    assert _status(response) == 404
    assert _body(response)["error"]["code"] == "NOT_FOUND"


def test_tenant_mgmt_handler_builds_runtime_and_dispatches_tenant_route(
    monkeypatch: Any,
    tenant_api_event: Any,
    fake_deps: tenant_api_handler.TenantApiDependencies,
) -> None:
    from src.tenant_api import tenant_lifecycle, tenant_mgmt_handler

    captured: dict[str, Any] = {}

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

    event = tenant_api_event(method="GET", tenant_id="Tenant-Acme-001")
    response = tenant_mgmt_handler.handle_event(event, dependencies=fake_deps)

    assert _status(response) == 200
    assert captured["path"] == "/v1/tenants/tenant-acme-001"
    assert captured["method"] == "GET"
    assert captured["tenant_id"] == "tenant-acme-001"
    assert captured["deps"] is fake_deps


def test_webhook_handler_builds_runtime_and_dispatches_webhook_route(
    monkeypatch: Any,
    tenant_api_event: Any,
    fake_deps: tenant_api_handler.TenantApiDependencies,
) -> None:
    from src.tenant_api import webhook_registry, webhook_registry_handler

    captured: dict[str, Any] = {}

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

    event = tenant_api_event(method="GET")
    event["path"] = "/v1/webhooks"
    response = webhook_registry_handler.handle_event(
        event,
        dependencies=fake_deps,
    )

    assert _status(response) == 200
    assert captured["path"] == "/v1/webhooks"
    assert captured["method"] == "GET"
    assert captured["deps"] is fake_deps
