from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import jwt
import pytest

from src.platform_tools import diagnostics_handler
from src.platform_tools.diagnostics_handler import lambda_handler

SCOPED_TOKEN_SIGNING_KEY = "unit-test-signing-key-with-32-bytes-minimum"
SCOPED_TOKEN_ISSUER = "platform-gateway"


@pytest.fixture
def mock_db():
    with patch("src.platform_tools.diagnostics_handler.ControlPlaneDynamoDB") as mock:
        yield mock


@pytest.fixture(autouse=True)
def scoped_token_env(monkeypatch):
    monkeypatch.setenv("PLATFORM_ENV", "local")
    monkeypatch.setenv("SCOPED_TOKEN_SIGNING_KEY", SCOPED_TOKEN_SIGNING_KEY)
    monkeypatch.setenv("SCOPED_TOKEN_ISSUER", SCOPED_TOKEN_ISSUER)
    monkeypatch.delenv("SCOPED_TOKEN_SIGNING_KEY_SECRET_ARN", raising=False)
    diagnostics_handler._scoped_token_signing_key_cache = None
    diagnostics_handler._scoped_token_signing_key_expiry = 0


def _scoped_headers(tool_name: str) -> dict[str, str]:
    token = jwt.encode(
        {
            "iss": SCOPED_TOKEN_ISSUER,
            "aud": f"tool:{tool_name}",
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(minutes=5),
            "tenantid": "platform",
            "appid": "admin-ui",
            "tier": "premium",
            "acting_sub": "operator-123",
            "scope_tool": tool_name,
        },
        SCOPED_TOKEN_SIGNING_KEY,
        algorithm="HS256",
    )
    return {
        "Authorization": f"Bearer {token}",
        "x-tenant-id": "platform",
        "x-app-id": "admin-ui",
    }


def test_get_platform_health(mock_db):
    event = {
        "method": "tools/call",
        "params": {"name": "get_platform_health", "arguments": {}},
        "headers": _scoped_headers("get_platform_health"),
        "id": "1",
    }

    response = lambda_handler(event, None)

    assert response["id"] == "1"
    assert "result" in response
    assert response["result"]["status"] == "healthy"
    assert response["result"]["regions"] == [
        {"region": "eu-west-2", "status": "operational", "latency_ms": 0}
    ]


def test_get_tenant_status(mock_db):
    # Setup mock DB response
    mock_instance = mock_db.return_value
    mock_instance.get_item.return_value = {
        "tenant_id": "t-test-001",
        "display_name": "Test Tenant",
        "status": "active",
        "tier": "basic",
    }
    mock_instance.query.return_value = MagicMock(items=[])

    event = {
        "method": "tools/call",
        "params": {"name": "get_tenant_status", "arguments": {"tenant_id": "t-test-001"}},
        "headers": _scoped_headers("get_tenant_status"),
        "id": "2",
    }

    response = lambda_handler(event, None)

    assert response["id"] == "2"
    assert response["result"]["tenantId"] == "t-test-001"
    assert response["result"]["status"] == "active"
    mock_instance.get_item.assert_called_once()
    query_kwargs = mock_instance.query.call_args.kwargs
    assert query_kwargs["pk_value"] == "TENANT#t-test-001"
    assert query_kwargs["sk_condition"].get_expression()["values"][1].startswith("INV#")
    assert query_kwargs["limit"] == 20
    assert query_kwargs["scan_index_forward"] is False


def test_get_tenant_status_reads_canonical_tenant_metadata(mock_db):
    mock_instance = mock_db.return_value
    mock_instance.get_item.return_value = {
        "tenantId": "t-test-001",
        "displayName": "Test Tenant",
        "status": "active",
        "tier": "basic",
        "updatedAt": "2026-01-01T00:00:00+00:00",
    }
    mock_instance.query.return_value = MagicMock(items=[{"SK": "TIME#2026-01-01T00:00:00"}])

    event = {
        "method": "tools/call",
        "params": {"name": "get_tenant_status", "arguments": {"tenant_id": "t-test-001"}},
        "headers": _scoped_headers("get_tenant_status"),
        "id": "2-canonical",
    }

    response = lambda_handler(event, None)

    assert response["result"]["displayName"] == "Test Tenant"
    assert response["result"]["lastUpdated"] == "2026-01-01T00:00:00+00:00"
    assert response["result"]["recentInvocations"] == 1
    query_kwargs = mock_instance.query.call_args.kwargs
    assert query_kwargs["pk_value"] == "TENANT#t-test-001"
    assert query_kwargs["sk_condition"].get_expression()["values"][1].startswith("INV#")


def test_get_runbook_guidance(mock_db):
    event = {
        "method": "tools/call",
        "params": {"name": "get_runbook_guidance", "arguments": {"runbook_id": "RUNBOOK-001"}},
        "headers": _scoped_headers("get_runbook_guidance"),
        "id": "3",
    }

    response = lambda_handler(event, None)

    assert response["id"] == "3"
    assert response["result"]["runbookId"] == "RUNBOOK-001"
    assert "steps" in response["result"]


def test_access_denied_for_non_platform_tenant(mock_db):
    event = {
        "method": "tools/call",
        "params": {"name": "get_platform_health"},
        "headers": {"x-tenant-id": "t-test-001", "x-app-id": "some-app"},
        "id": "4",
    }

    response = lambda_handler(event, None)

    assert "error" in response
    assert response["error"]["code"] == -32003
    assert "Access denied" in response["error"]["message"]


def test_access_denied_for_spoofed_platform_header_without_scoped_token(mock_db):
    event = {
        "method": "tools/call",
        "params": {"name": "get_platform_health"},
        "headers": {"x-tenant-id": "platform", "x-app-id": "admin-ui"},
        "id": "5",
    }

    response = lambda_handler(event, None)

    assert response["id"] == "5"
    assert response["error"]["code"] == -32003
    assert "trusted scoped token" in response["error"]["message"]
    mock_db.assert_not_called()


def test_spoofed_platform_header_without_token_does_not_fetch_signing_key(mock_db):
    event = {
        "method": "tools/call",
        "params": {"name": "get_platform_health"},
        "headers": {"x-tenant-id": "platform", "x-app-id": "admin-ui"},
        "id": "5-no-auth",
    }

    with patch("src.platform_tools.diagnostics_handler.boto3_client") as mock_boto3_client:
        response = lambda_handler(event, None)

    assert response["error"]["code"] == -32003
    mock_boto3_client.assert_not_called()
    mock_db.assert_not_called()


@pytest.mark.parametrize("headers", [None, "x-tenant-id: platform"])
def test_malformed_headers_fail_closed_without_fetching_signing_key(mock_db, headers):
    event = {
        "method": "tools/call",
        "params": {"name": "get_platform_health"},
        "headers": headers,
        "id": "malformed-headers",
    }

    with patch("src.platform_tools.diagnostics_handler.boto3_client") as mock_boto3_client:
        response = lambda_handler(event, None)

    assert response["id"] == "malformed-headers"
    assert response["error"]["code"] == -32003
    mock_boto3_client.assert_not_called()
    mock_db.assert_not_called()


def test_access_denied_when_scoped_token_audience_does_not_match_tool(mock_db):
    event = {
        "method": "tools/call",
        "params": {"name": "get_tenant_status", "arguments": {"tenant_id": "t-test-001"}},
        "headers": _scoped_headers("get_platform_health"),
        "id": "6",
    }

    response = lambda_handler(event, None)

    assert response["id"] == "6"
    assert response["error"]["code"] == -32003
    mock_db.assert_not_called()


def test_prod_rejects_direct_signing_key_without_secret_arn(mock_db, monkeypatch):
    monkeypatch.setenv("PLATFORM_ENV", "prod")
    monkeypatch.setenv("SCOPED_TOKEN_SIGNING_KEY", SCOPED_TOKEN_SIGNING_KEY)
    monkeypatch.delenv("SCOPED_TOKEN_SIGNING_KEY_SECRET_ARN", raising=False)

    event = {
        "method": "tools/call",
        "params": {"name": "get_platform_health"},
        "headers": _scoped_headers("get_platform_health"),
        "id": "7",
    }

    response = lambda_handler(event, None)

    assert response["id"] == "7"
    assert response["error"]["code"] == -32003
    mock_db.assert_not_called()


def test_prod_accepts_scoped_token_signed_with_secret_manager_key(mock_db, monkeypatch):
    monkeypatch.setenv("PLATFORM_ENV", "prod")
    monkeypatch.delenv("SCOPED_TOKEN_SIGNING_KEY", raising=False)
    monkeypatch.setenv(
        "SCOPED_TOKEN_SIGNING_KEY_SECRET_ARN",
        "arn:aws:secretsmanager:eu-west-2:111111111111:secret:scoped-token",
    )

    secret_client = MagicMock()
    secret_client.get_secret_value.return_value = {"SecretString": SCOPED_TOKEN_SIGNING_KEY}

    event = {
        "method": "tools/call",
        "params": {"name": "get_platform_health", "arguments": {}},
        "headers": _scoped_headers("get_platform_health"),
        "id": "8",
    }

    with patch(
        "src.platform_tools.diagnostics_handler.boto3_client",
        return_value=secret_client,
    ):
        response = lambda_handler(event, None)

    assert response["id"] == "8"
    assert response["result"]["status"] == "healthy"
    secret_client.get_secret_value.assert_called_once()


def test_prod_secret_manager_key_is_cached(mock_db, monkeypatch):
    monkeypatch.setenv("PLATFORM_ENV", "prod")
    monkeypatch.delenv("SCOPED_TOKEN_SIGNING_KEY", raising=False)
    monkeypatch.setenv(
        "SCOPED_TOKEN_SIGNING_KEY_SECRET_ARN",
        "arn:aws:secretsmanager:eu-west-2:111111111111:secret:scoped-token",
    )

    secret_client = MagicMock()
    secret_client.get_secret_value.return_value = {"SecretString": SCOPED_TOKEN_SIGNING_KEY}
    event = {
        "method": "tools/call",
        "params": {"name": "get_platform_health", "arguments": {}},
        "headers": _scoped_headers("get_platform_health"),
        "id": "9",
    }

    with patch(
        "src.platform_tools.diagnostics_handler.boto3_client",
        return_value=secret_client,
    ):
        first = lambda_handler(event, None)
        second = lambda_handler(event, None)

    assert first["result"]["status"] == "healthy"
    assert second["result"]["status"] == "healthy"
    secret_client.get_secret_value.assert_called_once()
