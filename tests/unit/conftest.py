from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pytest

from src.tenant_api import handler as tenant_api_handler
from tests.unit.tenant_api_test_support import (
    FakeScopedDb,
    apply_common_tenant_api_env,
    build_tenant_api_dependencies,
    fixed_now_value,
)


@pytest.fixture
def fixed_now() -> datetime:
    return fixed_now_value()


@pytest.fixture
def tenant_api_env(monkeypatch: pytest.MonkeyPatch) -> None:
    apply_common_tenant_api_env(monkeypatch)


@pytest.fixture
def fake_db() -> FakeScopedDb:
    return FakeScopedDb()


@pytest.fixture
def fake_deps() -> tenant_api_handler.TenantApiDependencies:
    return build_tenant_api_dependencies()


@pytest.fixture
def fake_state(
    monkeypatch: pytest.MonkeyPatch,
    fixed_now: datetime,
    fake_db: FakeScopedDb,
) -> dict[str, Any]:
    from src.tenant_api import db_factory, db_utils, handler, utils

    deps = build_tenant_api_dependencies()
    apply_common_tenant_api_env(monkeypatch)

    monkeypatch.setattr(db_factory, "db_for_tenant", lambda **_kwargs: fake_db)
    monkeypatch.setattr(db_factory, "control_plane_db", lambda *_args, **_kwargs: fake_db)
    monkeypatch.setattr(db_utils, "db_for_tenant", lambda **_kwargs: fake_db)
    monkeypatch.setattr(db_utils, "control_plane_db", lambda *_args, **_kwargs: fake_db)

    monkeypatch.setattr(utils, "_OVERRIDE_NOW", fixed_now)
    # Also override in handler.utils just in case (as done in tenant_api_test_support.py)
    monkeypatch.setattr(handler.utils, "_OVERRIDE_NOW", fixed_now)

    return {"db": fake_db, "deps": deps}


@pytest.fixture
def module_state(
    monkeypatch: pytest.MonkeyPatch,
    fixed_now: datetime,
) -> dict[str, Any]:
    from tests.unit.tenant_api_test_support import build_module_state

    return build_module_state(monkeypatch, fixed_now)


@pytest.fixture
def tenant_api_caller():
    from src.tenant_api import handler as tenant_api_handler

    def _caller(
        *,
        tenant_id: str | None = "t-admin",
        roles: list[str] | None = None,
        app_id: str = "app-admin",
    ) -> tenant_api_handler.CallerIdentity:
        return tenant_api_handler.CallerIdentity(
            tenant_id=tenant_id,
            app_id=app_id,
            tier="premium",
            sub="user-123",
            roles=frozenset(roles or ["Platform.Admin"]),
            usage_identifier_key=None,
        )

    return _caller


@pytest.fixture
def invoke_tenant_api(fake_state: dict[str, Any]):
    def _invoke(event: dict[str, Any]) -> dict[str, Any]:
        return tenant_api_handler.handle_event(event, dependencies=fake_state["deps"])

    return _invoke


@pytest.fixture
def tenant_api_event():
    def _event(
        *,
        method: str,
        tenant_id: str | None = None,
        body: dict[str, Any] | None = None,
        caller_tenant_id: str | None = "t-admin",
        roles: str | list[str] = "Platform.Admin",
        app_id: str = "app-admin",
        usage_identifier_key: str | None = None,
    ) -> dict[str, Any]:
        path_params = None
        if tenant_id is not None:
            path_params = {"tenantId": tenant_id}
        authorizer: dict[str, Any] = {
            "tenantid": caller_tenant_id,
            "appid": app_id,
            "tier": "premium",
            "sub": "user-123",
            "roles": roles,
        }
        if usage_identifier_key is not None:
            authorizer["usageIdentifierKey"] = usage_identifier_key

        path = "/v1/tenants"
        if tenant_id is not None:
            path = f"/v1/tenants/{tenant_id}"

        return {
            "httpMethod": method,
            "path": path,
            "pathParameters": path_params,
            "body": None if body is None else json.dumps(body),
            "requestContext": {"authorizer": authorizer},
        }

    return _event
