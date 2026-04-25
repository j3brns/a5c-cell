"""Unit tests for scripts/ops.py (TASK-029)."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request

import pytest


def _load_ops_module() -> Any:
    repo_root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("ops_script", repo_root / "scripts" / "ops.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


ops = _load_ops_module()


def _jwt(payload: dict[str, Any]) -> str:
    import base64

    header = {"alg": "none", "typ": "JWT"}
    head = base64.urlsafe_b64encode(json.dumps(header).encode("utf-8")).decode("utf-8").rstrip("=")
    body = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("utf-8").rstrip("=")
    return f"{head}.{body}.signature"


class _FakeHeaders:
    def get_content_charset(self, default: str = "utf-8") -> str:
        return default


class _FakeResponse:
    def __init__(self, *, status: int, payload: Any) -> None:
        self.status = status
        self._payload = payload

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_args: Any) -> bool:
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def _write_creds(path: Path, *, env_name: str, token: str, api_base_url: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    store = {
        "version": 1,
        "profiles": {
            env_name: {
                "apiBaseUrl": api_base_url,
                "accessToken": token,
                "expiresAt": int(sys.maxsize),
            }
        },
    }
    path.write_text(json.dumps(store), encoding="utf-8")


def _write_failover_lock_token(path: Path, *, lock_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "lockId": lock_id,
        "tableName": "platform-ops-locks",
        "lockName": "platform-runtime-failover",
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_parse_args_top_tenants_defaults() -> None:
    args = ops.parse_args(["top-tenants", "--env", "prod"])
    assert args.command == "top-tenants"
    assert args.env == "prod"
    assert args.n == 10


def test_login_persists_profile(monkeypatch, tmp_path: Path, capsys) -> None:
    creds_path = tmp_path / ".platform" / "credentials"
    monkeypatch.setenv("PLATFORM_CREDENTIALS_PATH", str(creds_path))

    token = _jwt(
        {
            "sub": "u123",
            "preferred_username": "operator@example.com",
            "roles": ["Platform.Operator"],
        }
    )

    rc = ops.main(
        [
            "login",
            "--env",
            "dev",
            "--api-base-url",
            "https://ops.example.com",
            "--token",
            token,
        ]
    )

    assert rc == 0
    assert creds_path.exists()
    profile = json.loads(creds_path.read_text())["profiles"]["dev"]
    assert profile["apiBaseUrl"] == "https://ops.example.com"
    assert profile["accessToken"] == token


def _make_creds(monkeypatch: Any, tmp_path: Path) -> Path:
    creds_path = tmp_path / ".platform" / "credentials"
    monkeypatch.setenv("PLATFORM_CREDENTIALS_PATH", str(creds_path))
    _write_creds(creds_path, env_name="dev", token="tk", api_base_url="https://api.example.com")
    return creds_path


def _capture_request(monkeypatch: Any) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def _fake_urlopen(request: Request, timeout: int) -> _FakeResponse:
        seen["url"] = request.full_url
        seen["method"] = request.get_method()
        seen["timeout"] = timeout
        data = request.data
        if data:
            seen["body"] = json.loads(data.decode("utf-8"))
        return _FakeResponse(status=200, payload={"ok": True})

    monkeypatch.setattr(ops, "urlopen", _fake_urlopen)
    return seen


@pytest.mark.parametrize(
    ("argv", "expected_message"),
    [
        (
            ["top-tenants", "--env", "dev", "--n", "5"],
            "top-tenants` is disabled",
        ),
        (
            ["tenant-sessions", "--env", "dev", "--tenant", "t-123"],
            "tenant-sessions` is disabled",
        ),
        (
            ["invocation-report", "--env", "dev", "--tenant", "t-inv", "--days", "14"],
            "invocation-report` is disabled",
        ),
        (
            ["security-events", "--env", "dev", "--hours", "48"],
            "security-events` is disabled",
        ),
        (
            ["dlq-inspect", "--env", "dev", "--queue", "bridge-dlq"],
            "dlq-inspect` is disabled",
        ),
        (
            ["dlq-redrive", "--env", "dev", "--queue", "bridge-dlq"],
            "dlq-redrive` is disabled",
        ),
        (
            ["error-rate", "--env", "dev", "--minutes", "15"],
            "error-rate` is disabled",
        ),
        (
            ["service-health", "--env", "dev"],
            "service-health` is disabled",
        ),
    ],
)
def test_non_authoritative_ops_commands_fail_fast(
    monkeypatch, tmp_path: Path, capsys, argv: list[str], expected_message: str
) -> None:
    _make_creds(monkeypatch, tmp_path)
    seen = _capture_request(monkeypatch)

    rc = ops.main(argv)

    assert rc == 2
    assert seen == {}
    assert expected_message in capsys.readouterr().err


def test_update_tenant_budget_uses_patch_and_json_body(monkeypatch, tmp_path: Path) -> None:
    creds_path = tmp_path / ".platform" / "credentials"
    monkeypatch.setenv("PLATFORM_CREDENTIALS_PATH", str(creds_path))
    _write_creds(
        creds_path,
        env_name="dev",
        token="t1",
        api_base_url="https://ops.example.com",
    )
    seen = _capture_request(monkeypatch)

    rc = ops.main(["update-tenant-budget", "--tenant", "t-123", "--budget", "500.0"])

    assert rc == 0
    assert seen["method"] == "PATCH"
    assert seen["url"] == "https://ops.example.com/v1/platform/tenants/t-123/billing"
    assert seen["body"] == {"monthlyBudget": 500.0}


def test_set_runtime_region_uses_failover_api_contract(monkeypatch, tmp_path: Path) -> None:
    creds_path = tmp_path / ".platform" / "credentials"
    monkeypatch.setenv("PLATFORM_CREDENTIALS_PATH", str(creds_path))
    _write_creds(
        creds_path,
        env_name="dev",
        token="t1",
        api_base_url="https://ops.example.com",
    )
    seen = _capture_request(monkeypatch)

    rc = ops.main(["set-runtime-region", "--region", "eu-central-1", "--lock-id", "l1"])

    assert rc == 0
    assert seen["method"] == "PUT"
    assert seen["url"] == "https://ops.example.com/v1/platform/ops/runtime-region"
    assert seen["body"] == {"targetRegion": "eu-central-1", "lockId": "l1"}


def test_set_runtime_region_accepts_explicit_lock_id(monkeypatch, tmp_path: Path) -> None:
    creds_path = tmp_path / ".platform" / "credentials"
    monkeypatch.setenv("PLATFORM_CREDENTIALS_PATH", str(creds_path))
    _write_creds(
        creds_path,
        env_name="prod",
        token="failover-token",
        api_base_url="https://ops.example.com",
    )
    seen = _capture_request(monkeypatch)

    rc = ops.main(
        [
            "set-runtime-region",
            "--env",
            "prod",
            "--region",
            "eu-central-1",
            "--lock-id",
            "lock-explicit",
        ]
    )

    assert rc == 0
    assert seen["body"] == {"targetRegion": "eu-central-1", "lockId": "lock-explicit"}


def test_set_runtime_region_requires_lock_id_when_no_saved_token(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    creds_path = tmp_path / ".platform" / "credentials"
    monkeypatch.setenv("PLATFORM_CREDENTIALS_PATH", str(creds_path))
    monkeypatch.delenv("FAILOVER_LOCK_TOKEN_PATH", raising=False)
    _write_creds(
        creds_path,
        env_name="prod",
        token="failover-token",
        api_base_url="https://ops.example.com",
    )

    rc = ops.main(["set-runtime-region", "--env", "prod", "--region", "eu-central-1"])

    assert rc == 2
    assert "Failover lock id required" in capsys.readouterr().err


def test_api_error_returns_nonzero_and_prints_error(monkeypatch, tmp_path: Path, capsys) -> None:
    creds_path = tmp_path / ".platform" / "credentials"
    monkeypatch.setenv("PLATFORM_CREDENTIALS_PATH", str(creds_path))
    _write_creds(
        creds_path,
        env_name="dev",
        token="err-token",
        api_base_url="https://api.example.com",
    )

    def _fake_urlopen(request: Request, timeout: int) -> _FakeResponse:
        del timeout
        raise HTTPError(
            url=request.full_url,
            code=403,
            msg="Forbidden",
            hdrs=_FakeHeaders(),
            fp=io.BytesIO(b'{"error":{"code":"FORBIDDEN","message":"nope"}}'),
        )

    monkeypatch.setattr(ops, "urlopen", _fake_urlopen)

    rc = ops.main(["quota-report", "--env", "dev"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "API returned 403 Forbidden" in captured.err
    assert "FORBIDDEN" in captured.err


def test_lambda_rollback_calls_expected_endpoint(monkeypatch, tmp_path: Path) -> None:
    creds_path = tmp_path / ".platform" / "credentials"
    monkeypatch.setenv("PLATFORM_CREDENTIALS_PATH", str(creds_path))
    _write_creds(
        creds_path,
        env_name="prod",
        token="rollback-token",
        api_base_url="https://ops.example.com",
    )
    seen = _capture_request(monkeypatch)

    rc = ops.main(
        [
            "lambda-rollback",
            "--env",
            "prod",
            "--function",
            "bridge",
            "--alias",
            "live",
        ]
    )
    assert rc == 0
    assert seen["method"] == "POST"
    assert seen["url"] == "https://ops.example.com/v1/platform/ops/lambdas/bridge/rollback"
    assert seen["body"] == {"functionSuffix": "bridge", "aliasName": "live"}


def test_suspend_tenant_calls_correct_endpoint(monkeypatch, tmp_path: Path) -> None:
    _make_creds(monkeypatch, tmp_path)
    seen = _capture_request(monkeypatch)

    rc = ops.main(["suspend-tenant", "--env", "dev", "--tenant", "t-abc", "--reason", "abuse"])

    assert rc == 0
    assert seen["method"] == "PATCH"
    assert "/v1/platform/tenants/t-abc" in seen["url"]
    assert seen["body"] == {"status": "suspended", "statusReason": "abuse"}


def test_reinstate_tenant_calls_correct_endpoint(monkeypatch, tmp_path: Path) -> None:
    _make_creds(monkeypatch, tmp_path)
    seen = _capture_request(monkeypatch)

    rc = ops.main(["reinstate-tenant", "--env", "dev", "--tenant", "t-xyz"])

    assert rc == 0
    assert seen["method"] == "PATCH"
    assert "/v1/platform/tenants/t-xyz" in seen["url"]
    assert seen["body"]["status"] == "active"


def test_notify_tenant_calls_correct_endpoint(monkeypatch, tmp_path: Path) -> None:
    _make_creds(monkeypatch, tmp_path)
    seen = _capture_request(monkeypatch)

    rc = ops.main(
        ["notify-tenant", "--env", "dev", "--tenant", "t-notify", "--template", "budget_exceeded"]
    )

    assert rc == 0
    assert seen["method"] == "POST"
    assert "/v1/platform/tenants/t-notify/notifications" in seen["url"]
    assert seen["body"] == {"template": "budget_exceeded"}


def test_audit_export_with_date_range(monkeypatch, tmp_path: Path) -> None:
    _make_creds(monkeypatch, tmp_path)
    seen = _capture_request(monkeypatch)

    rc = ops.main(
        [
            "audit-export",
            "--env",
            "dev",
            "--tenant",
            "t-audit",
            "--start",
            "2026-01-01",
            "--end",
            "2026-01-31",
        ]
    )

    assert rc == 0
    assert seen["method"] == "GET"
    assert "/v1/platform/tenants/t-audit/audit/export" in seen["url"]
    assert "start=2026-01-01" in seen["url"]
    assert "end=2026-01-31" in seen["url"]


def test_audit_export_without_date_range(monkeypatch, tmp_path: Path) -> None:
    _make_creds(monkeypatch, tmp_path)
    seen = _capture_request(monkeypatch)

    rc = ops.main(["audit-export", "--env", "dev", "--tenant", "t-audit"])

    assert rc == 0
    assert "start" not in seen["url"]
    assert "end" not in seen["url"]


def test_fail_job_calls_correct_endpoint(monkeypatch, tmp_path: Path) -> None:
    _make_creds(monkeypatch, tmp_path)
    seen = _capture_request(monkeypatch)

    rc = ops.main(["fail-job", "--env", "dev", "--job", "job-001", "--reason", "timed out"])

    assert rc == 0
    assert seen["method"] == "PATCH"
    assert "/v1/platform/jobs/job-001" in seen["url"]
    assert seen["body"] == {"status": "failed", "failureReason": "timed out"}


def test_page_security_calls_correct_endpoint(monkeypatch, tmp_path: Path) -> None:
    _make_creds(monkeypatch, tmp_path)
    seen = _capture_request(monkeypatch)

    rc = ops.main(["page-security", "--env", "dev", "--incident", "leak", "--tenant", "t-leak"])

    assert rc == 0
    assert seen["method"] == "POST"
    assert "/v1/platform/security/page" in seen["url"]
    assert seen["body"] == {"incident": "leak", "tenantId": "t-leak"}


def test_resolve_token_falls_back_to_env(monkeypatch) -> None:
    monkeypatch.setenv("PLATFORM_ACCESS_TOKEN", "env-tk")
    token = ops._resolve_token(None, None)
    assert token == "env-tk"


def test_resolve_token_falls_back_to_profile() -> None:
    profile = ops.OperatorProfile(
        env="dev", api_base_url="x", access_token="prof-tk", expires_at=int(sys.maxsize)
    )
    token = ops._resolve_token(None, profile)
    assert token == "prof-tk"


def test_resolve_token_no_token_raises() -> None:
    with pytest.raises(ops.OpsCliError, match="No access token"):
        ops._resolve_token(None, None)


def test_resolve_api_base_url_uses_profile() -> None:
    profile = ops.OperatorProfile(
        env="dev", api_base_url="https://p.com", access_token="x", expires_at=0
    )
    url = ops._resolve_api_base_url(explicit=None, profile=profile)
    assert url == "https://p.com"


def test_resolve_api_base_url_raises_when_no_source() -> None:
    with pytest.raises(ops.OpsCliError, match="API base URL not set"):
        ops._resolve_api_base_url(explicit=None, profile=None)


def test_jwt_payload_extracts_claims() -> None:
    token = _jwt({"sub": "u1", "roles": ["Admin"]})
    assert ops._jwt_payload(token) == {"sub": "u1", "roles": ["Admin"]}


def test_jwt_payload_invalid_token_returns_empty() -> None:
    assert ops._jwt_payload("not.a.valid") == {}


def test_token_subject_preferred_username_takes_priority() -> None:
    claims = {"sub": "123", "preferred_username": "ops@example.com"}
    assert ops._token_subject(claims) == "ops@example.com"


def test_token_subject_falls_back_to_sub() -> None:
    claims = {"sub": "abc123"}
    assert ops._token_subject(claims) == "abc123"


def test_token_subject_unknown_when_empty() -> None:
    assert ops._token_subject({}) == "unknown"


def test_token_roles_list() -> None:
    claims = {"roles": ["Platform.Operator", "Platform.Admin"]}
    assert ops._token_roles(claims) == ["Platform.Operator", "Platform.Admin"]


def test_token_roles_string_format() -> None:
    claims = {"roles": "Platform.Operator"}
    roles = ops._token_roles(claims)
    assert "Platform.Operator" in roles


def test_token_roles_empty_when_missing() -> None:
    assert ops._token_roles({}) == []


def test_build_url_without_query() -> None:
    url = ops._build_url("https://api.example.com", "/v1/tenants", None)
    assert url == "https://api.example.com/v1/tenants"


def test_build_url_with_query() -> None:
    url = ops._build_url("https://api.example.com", "/v1/tenants", {"n": "5"})
    assert url == "https://api.example.com/v1/tenants?n=5"


def test_build_url_trailing_slash_in_base() -> None:
    url = ops._build_url("https://api.example.com/", "/v1/resource", None)
    assert url == "https://api.example.com/v1/resource"
