"""Local HTTP adapter for the bridge Lambda handler.

This server is for developer runs only. It mirrors the contracted API route
that `scripts/dev_invoke.py` calls, then builds the API Gateway event shape the
bridge handler already expects.
"""

from __future__ import annotations

import base64
import json
import re
import sys
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from platform_config import env_optional
from src.bridge.handler import handler as bridge_handler

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080
_AGENT_INVOKE_RE = re.compile(r"^/v1/agents/(?P<agent>[^/]+)/invoke/?$")


class _LocalLambdaContext:
    function_name = "bridge-local-api"
    memory_limit_in_mb = 256
    invoked_function_arn = "arn:aws:lambda:eu-west-2:000000000000:function:bridge-local-api"

    def __init__(self) -> None:
        self.aws_request_id = str(uuid.uuid4())


def _json_response(status_code: int, payload: dict[str, Any]) -> tuple[int, dict[str, str], str]:
    return (
        status_code,
        {"Content-Type": "application/json"},
        json.dumps(payload, sort_keys=True),
    )


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload.encode("ascii"))
        value = json.loads(decoded.decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _normalise_headers(headers: Any) -> dict[str, str]:
    return {key.lower(): value for key, value in headers.items()}


def _authorizer_from_headers(headers: dict[str, str]) -> dict[str, str]:
    auth_header = headers.get("authorization", "")
    token = auth_header.removeprefix("Bearer ").strip()
    claims = _decode_jwt_payload(token) if token else {}

    tenant_id = (
        str(claims.get("tenantid") or claims.get("tenantId") or "").strip()
        or headers.get("x-tenant-id", "").strip()
        or "t-test-001"
    )
    app_id = str(claims.get("appid") or claims.get("appId") or "").strip() or "platform-local"
    tier = str(claims.get("tier") or "").strip() or "basic"
    subject = str(claims.get("sub") or "").strip() or "local-user"
    return {"tenantid": tenant_id, "appid": app_id, "tier": tier, "sub": subject}


def build_event(
    *,
    path: str,
    agent_name: str,
    headers: dict[str, str],
    body: str,
) -> dict[str, Any]:
    event_headers = dict(headers)
    if "authorization" in event_headers:
        event_headers["authorization"] = "Bearer <redacted>"
    return {
        "httpMethod": "POST",
        "path": path,
        "headers": event_headers,
        "pathParameters": {"agentName": agent_name},
        "requestContext": {"authorizer": _authorizer_from_headers(headers)},
        "body": body,
        "isBase64Encoded": False,
    }


class LocalApiHandler(BaseHTTPRequestHandler):
    server_version = "platform-local-api/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}", file=sys.stderr)

    def do_GET(self) -> None:
        if urlparse(self.path).path == "/health":
            self._send(*_json_response(HTTPStatus.OK, {"status": "ok"}))
            return
        self._send(*_json_response(HTTPStatus.NOT_FOUND, {"message": "Not found"}))

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        match = _AGENT_INVOKE_RE.match(parsed.path)
        if not match:
            self._send(*_json_response(HTTPStatus.NOT_FOUND, {"message": "Not found"}))
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send(
                *_json_response(
                    HTTPStatus.BAD_REQUEST,
                    {"message": "Invalid Content-Length header"},
                )
            )
            return

        body = self.rfile.read(content_length).decode("utf-8") if content_length else ""
        event = build_event(
            path=parsed.path,
            agent_name=match.group("agent"),
            headers=_normalise_headers(self.headers),
            body=body,
        )
        response = bridge_handler(event, _LocalLambdaContext())
        status_code = int(response.get("statusCode", HTTPStatus.OK))
        response_headers = {
            str(key): str(value) for key, value in response.get("headers", {}).items()
        }
        response_body = str(response.get("body", ""))
        self._send(status_code, response_headers, response_body)

    def _send(self, status_code: int, headers: dict[str, str], body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status_code)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def main() -> int:
    host = env_optional("LOCAL_API_HOST") or DEFAULT_HOST
    port = int(env_optional("LOCAL_API_PORT") or str(DEFAULT_PORT))
    server = ThreadingHTTPServer((host, port), LocalApiHandler)
    print(f"Local API listening on http://{host}:{port}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Local API stopped", file=sys.stderr)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
