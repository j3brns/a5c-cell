"""
ops.py — Platform operations CLI for the administrative control plane.

Communicates with the Platform API (tenant-api) using operator credentials
stored in ~/.platform/credentials (seeded via Entra login).

This tool is for infrastructure operators. Tenant-level management is
handled via the tenant-api directly by customers.

Usage:
    uv run python scripts/ops.py login --env <env>
    uv run python scripts/ops.py top-tenants --env <env>
    uv run python scripts/ops.py suspend-tenant --tenant <id> --reason <r> --env <env>

Implemented in Phase 5 — Operations & Governance.
ADRs: ADR-011, ADR-023
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from platform_config import get_settings

DEFAULT_ENV = "dev"
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_TOKEN_TTL_SECONDS = 3600

logger = logging.getLogger("ops")
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


class OpsCliError(RuntimeError):
    """Domain error for operator CLI failures."""


class ApiRequestError(OpsCliError):
    """Raised when an API call fails."""

    def __init__(
        self,
        *,
        message: str,
        status_code: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload or {}


@dataclass(frozen=True)
class OperatorProfile:
    env: str
    api_base_url: str
    access_token: str
    expires_at: int


@dataclass(frozen=True)
class ApiOperation:
    method: str
    path: str
    body: dict[str, Any] | None = None
    query_params: dict[str, str] | None = None


@dataclass(frozen=True)
class ApiResponse:
    status_code: int
    payload: dict[str, Any]


def _credentials_path() -> Path:
    return Path(
        get_settings().ops.credentials_path or str(Path.home() / ".platform" / "credentials")
    )


def _load_credentials_store(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"profiles": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("Malformed credentials file at %s; ignoring", path)
        return {"profiles": {}}


def _save_credentials_store(path: Path, store: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


def _profile_for_env(store: dict[str, Any], env: str) -> OperatorProfile | None:
    profile_data = store.get("profiles", {}).get(env)
    if not profile_data:
        return None
    return OperatorProfile(
        env=env,
        api_base_url=profile_data["apiBaseUrl"],
        access_token=profile_data["accessToken"],
        expires_at=profile_data["expiresAt"],
    )


def _handle_login(args: argparse.Namespace) -> int:
    """Mock Entra login: accepts an explicit token or opens browser for OIDC flow.

    For this MVP, we accept an explicit --token and --api-base-url and store
    them in the local profile.
    """
    if not args.token or not args.api_base_url:
        print("ERROR: Login via OIDC browser flow not yet implemented.", file=sys.stderr)
        print("Provide --token and --api-base-url for manual profile seeding.", file=sys.stderr)
        return 1

    path = _credentials_path()
    store = _load_credentials_store(path)
    store["profiles"][args.env] = {
        "apiBaseUrl": args.api_base_url.rstrip("/"),
        "accessToken": args.token,
        "expiresAt": int(time.time()) + args.ttl_seconds,
    }
    _save_credentials_store(path, store)
    logger.info("Stored credentials for profile '%s' in %s", args.env, path)
    return 0


def _resolve_api_base_url(explicit: str | None, profile: OperatorProfile | None) -> str:
    url = (
        explicit
        or (profile.api_base_url if profile else None)
        or get_settings().agents.api_base_url
    )
    if not url:
        raise OpsCliError("API base URL not set")
    return url.rstrip("/")


def _resolve_token(explicit: str | None, profile: OperatorProfile | None) -> str:
    token = (
        explicit
        or (profile.access_token if profile else None)
        or get_settings().agents.platform_access_token
    )
    if not token:
        raise OpsCliError("No access token provided")
    return token


def _jwt_payload(token: str) -> dict[str, Any]:
    """Base64 decode the middle part of a JWT (claims)."""
    try:
        _, payload_b64, _ = token.split(".", 2)
        # Pad with '=' for correct base64 decoding
        payload_b64 += "=" * ((4 - len(payload_b64) % 4) % 4)
        return json.loads(base64.urlsafe_b64decode(payload_b64).decode("utf-8"))
    except Exception:
        return {}


def _token_subject(claims: dict[str, Any]) -> str:
    return str(claims.get("preferred_username") or claims.get("sub", "unknown"))


def _token_roles(claims: dict[str, Any]) -> list[str]:
    roles = claims.get("roles", [])
    if isinstance(roles, str):
        return [roles]
    return list(roles)


def _build_url(base_url: str, path: str, query_params: dict[str, str] | None) -> str:
    url = f"{base_url.rstrip('/')}{path}"
    if query_params:
        from urllib.parse import urlencode

        params = {k: v for k, v in query_params.items() if v is not None}
        if params:
            url += f"?{urlencode(params)}"
    return url


def _command_to_operation(args: argparse.Namespace) -> ApiOperation:
    match args.command:
        case "top-tenants":
            if not get_settings().ops.can_list_top_tenants:
                raise OpsCliError("Command `top-tenants` is disabled")
            return ApiOperation(method="GET", path="/v1/platform/reports/top-tenants")
        case "tenant-sessions":
            if not get_settings().ops.can_list_sessions:
                raise OpsCliError("Command `tenant-sessions` is disabled")
            return ApiOperation(method="GET", path=f"/v1/platform/tenants/{args.tenant}/sessions")
        case "suspend-tenant":
            return ApiOperation(
                method="PATCH",
                path=f"/v1/platform/tenants/{args.tenant}",
                body={"status": "suspended", "statusReason": args.reason},
            )
        case "reinstate-tenant":
            return ApiOperation(
                method="PATCH",
                path=f"/v1/platform/tenants/{args.tenant}",
                body={"status": "active", "statusReason": "reinstated by operator"},
            )
        case "quota-report":
            return ApiOperation(method="GET", path="/v1/platform/reports/quota-utilisation")
        case "invocation-report":
            if not get_settings().ops.can_get_invocation_report:
                raise OpsCliError("Command `invocation-report` is disabled")
            return ApiOperation(
                method="GET", path=f"/v1/platform/reports/invocations/tenants/{args.tenant}"
            )
        case "security-events":
            if not get_settings().ops.can_list_security_events:
                raise OpsCliError("Command `security-events` is disabled")
            return ApiOperation(method="GET", path="/v1/platform/security/events")
        case "dlq-inspect":
            if not get_settings().ops.can_inspect_dlq:
                raise OpsCliError("Command `dlq-inspect` is disabled")
            return ApiOperation(method="GET", path=f"/v1/platform/ops/queues/{args.queue}/dlq")
        case "dlq-redrive":
            if not get_settings().ops.can_redrive_dlq:
                raise OpsCliError("Command `dlq-redrive` is disabled")
            return ApiOperation(method="POST", path=f"/v1/platform/ops/queues/{args.queue}/redrive")
        case "error-rate":
            if not get_settings().ops.can_get_error_rate:
                raise OpsCliError("Command `error-rate` is disabled")
            return ApiOperation(method="GET", path="/v1/platform/ops/health/errors")
        case "notify-tenant":
            return ApiOperation(
                method="POST",
                path=f"/v1/platform/tenants/{args.tenant}/notifications",
                body={"template": args.template},
            )
        case "service-health":
            if not get_settings().ops.can_get_service_health:
                raise OpsCliError("Command `service-health` is disabled")
            return ApiOperation(method="GET", path="/v1/platform/ops/health/services")
        case "billing-status":
            return ApiOperation(method="GET", path="/v1/platform/billing/status")
        case "update-tenant-budget":
            return ApiOperation(
                method="PATCH",
                path=f"/v1/platform/tenants/{args.tenant}/billing",
                body={"monthlyBudget": args.budget},
            )
        case "fail-job":
            return ApiOperation(
                method="PATCH",
                path=f"/v1/platform/jobs/{args.job}",
                body={"status": "failed", "failureReason": args.reason},
            )
        case "audit-export":
            return ApiOperation(
                method="GET",
                path=f"/v1/platform/tenants/{args.tenant}/audit/export",
                query_params={"start": args.start, "end": args.end},
            )
        case "page-security":
            return ApiOperation(
                method="POST",
                path="/v1/platform/security/page",
                body={"incident": args.incident, "tenantId": args.tenant},
            )
        case "lambda-rollback":
            return ApiOperation(
                method="POST",
                path=f"/v1/platform/ops/lambdas/{args.function}/rollback",
                body={"functionSuffix": args.function, "aliasName": args.alias},
            )
        case _:
            raise OpsCliError(f"Unsupported command mapping: {args.command}")


def _request_api(
    base_url: str,
    token: str,
    operation: ApiOperation,
    timeout_seconds: int,
) -> ApiResponse:
    url = _build_url(base_url, operation.path, operation.query_params)
    data = json.dumps(operation.body).encode("utf-8") if operation.body else None

    req = Request(url, data=data, method=operation.method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "platform-ops-cli/0.1.0")

    try:
        with urlopen(req, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return ApiResponse(status_code=response.status, payload=payload)
    except HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            error_payload = json.loads(body)
        except json.JSONDecodeError:
            error_payload = {"error": body}
        raise ApiRequestError(
            message=f"API returned {exc.code} {exc.reason}",
            status_code=exc.code,
            payload=error_payload,
        ) from exc
    except URLError as exc:
        raise OpsCliError(f"Failed to connect to API at {url}: {exc.reason}") from exc


def _print_payload(payload: Any, stream: Any = sys.stdout) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True), file=stream)


def _add_api_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--env", default=DEFAULT_ENV)
    parser.add_argument("--api-base-url", default=None)
    parser.add_argument("--token", default=None)
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ops.py",
        description="Platform operations CLI (Admin REST API only).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    login = subparsers.add_parser("login", help="Store operator access token for API calls.")
    login.add_argument("--env", default=DEFAULT_ENV)
    login.add_argument("--api-base-url", default=None)
    login.add_argument("--token", default=None)
    login.add_argument("--ttl-seconds", type=int, default=DEFAULT_TOKEN_TTL_SECONDS)

    top_tenants = subparsers.add_parser("top-tenants", help="List top tenants by token usage.")
    _add_api_common_arguments(top_tenants)
    top_tenants.add_argument("--n", type=int, default=10)

    tenant_sessions = subparsers.add_parser(
        "tenant-sessions",
        help="List active sessions for a tenant.",
    )
    _add_api_common_arguments(tenant_sessions)
    tenant_sessions.add_argument("--tenant", required=True)

    suspend_tenant = subparsers.add_parser("suspend-tenant", help="Suspend a tenant.")
    _add_api_common_arguments(suspend_tenant)
    suspend_tenant.add_argument("--tenant", required=True)
    suspend_tenant.add_argument("--reason", required=True)

    reinstate_tenant = subparsers.add_parser("reinstate-tenant", help="Reinstate a tenant.")
    _add_api_common_arguments(reinstate_tenant)
    reinstate_tenant.add_argument("--tenant", required=True)

    quota_report = subparsers.add_parser("quota-report", help="Get AgentCore quota report.")
    _add_api_common_arguments(quota_report)

    invocation_report = subparsers.add_parser(
        "invocation-report",
        help="Get tenant invocation report.",
    )
    _add_api_common_arguments(invocation_report)
    invocation_report.add_argument("--tenant", required=True)
    invocation_report.add_argument("--days", type=int, default=7)

    security_events = subparsers.add_parser(
        "security-events",
        help="List tenant access violation events.",
    )
    _add_api_common_arguments(security_events)
    security_events.add_argument("--hours", type=int, default=24)

    dlq_inspect = subparsers.add_parser("dlq-inspect", help="Inspect a DLQ.")
    _add_api_common_arguments(dlq_inspect)
    dlq_inspect.add_argument("--queue", required=True)

    dlq_redrive = subparsers.add_parser("dlq-redrive", help="Redrive a DLQ.")
    _add_api_common_arguments(dlq_redrive)
    dlq_redrive.add_argument("--queue", required=True)

    error_rate = subparsers.add_parser("error-rate", help="Get error rate.")
    _add_api_common_arguments(error_rate)
    error_rate.add_argument("--minutes", type=int, default=5)

    notify_tenant = subparsers.add_parser("notify-tenant", help="Notify tenant owner.")
    _add_api_common_arguments(notify_tenant)
    notify_tenant.add_argument("--tenant", required=True)
    notify_tenant.add_argument("--template", required=True)

    service_health = subparsers.add_parser("service-health", help="Check service health.")
    _add_api_common_arguments(service_health)

    billing_status = subparsers.add_parser("billing-status", help="Get billing status.")
    _add_api_common_arguments(billing_status)

    update_tenant_budget = subparsers.add_parser(
        "update-tenant-budget",
        help="Update tenant monthly budget.",
    )
    _add_api_common_arguments(update_tenant_budget)
    update_tenant_budget.add_argument("--tenant", required=True)
    update_tenant_budget.add_argument("--budget", type=float, required=True)

    fail_job = subparsers.add_parser("fail-job", help="Mark async job as failed.")
    _add_api_common_arguments(fail_job)
    fail_job.add_argument("--job", required=True)
    fail_job.add_argument("--reason", required=True)

    audit_export = subparsers.add_parser("audit-export", help="Get tenant audit export URL.")
    _add_api_common_arguments(audit_export)
    audit_export.add_argument("--tenant", required=True)
    audit_export.add_argument("--start", default=None)
    audit_export.add_argument("--end", default=None)

    page_security = subparsers.add_parser("page-security", help="Page security team.")
    _add_api_common_arguments(page_security)
    page_security.add_argument("--incident", required=True)
    page_security.add_argument("--tenant", required=True)

    lambda_rollback = subparsers.add_parser(
        "lambda-rollback",
        help="Roll back a platform Lambda to its previous version.",
    )
    _add_api_common_arguments(lambda_rollback)
    lambda_rollback.add_argument(
        "--function", required=True, help="Function suffix (e.g. bridge, bff, tenant-api)."
    )
    lambda_rollback.add_argument("--alias", default="live", help="Alias name (default: live).")

    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def handle_login(env: str = DEFAULT_ENV) -> int:
    # Use a dummy namespace that has all required fields for _handle_login
    return _handle_login(argparse.Namespace(env=env, token=None, api_base_url=None))


def run_api_command(
    command: str,
    env: str = DEFAULT_ENV,
    api_base_url: str | None = None,
    token: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    **kwargs: Any,
) -> int:
    args_dict = {
        "command": command,
        "env": env,
        "api_base_url": api_base_url,
        "token": token,
        "timeout_seconds": timeout_seconds,
    }
    args_dict.update(kwargs)
    args = argparse.Namespace(**args_dict)

    creds_store = _load_credentials_store(_credentials_path())
    profile = _profile_for_env(creds_store, args.env)
    resolved_api_base_url = _resolve_api_base_url(explicit=args.api_base_url, profile=profile)
    resolved_token = _resolve_token(args.token, profile)
    operation = _command_to_operation(args)

    response = _request_api(
        base_url=resolved_api_base_url,
        token=resolved_token,
        operation=operation,
        timeout_seconds=args.timeout_seconds,
    )
    _print_payload(response.payload)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "login":
            return _handle_login(args)
        return run_api_command(**vars(args))
    except ApiRequestError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        _print_payload(exc.payload, stream=sys.stderr)
        return 1
    except OpsCliError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
