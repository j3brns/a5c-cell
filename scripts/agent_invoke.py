"""Developer CLI for direct deployed-agent invocation via the Bridge Lambda."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import boto3
from botocore.exceptions import ClientError

DEFAULT_ENV = "dev"
DEFAULT_APP_ID = "platform-cli"
DEFAULT_TIER = "premium"
DEFAULT_SUB = "developer"


class AgentInvokeError(RuntimeError):
    """Domain error for CLI usage and invocation failures."""


def build_payload(
    prompt: str, session_id: str | None = None, webhook_id: str | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {"input": prompt}
    if session_id:
        payload["sessionId"] = session_id
    if webhook_id:
        payload["webhookId"] = webhook_id
    return payload


def build_event(
    agent: str,
    tenant: str,
    prompt: str,
    session_id: str | None = None,
    webhook_id: str | None = None,
) -> dict[str, Any]:
    """Construct a bridge-compatible API Gateway event payload."""
    return {
        "httpMethod": "POST",
        "path": f"/v1/agents/{agent}/invoke",
        "pathParameters": {"agentName": agent},
        "body": json.dumps(build_payload(prompt, session_id, webhook_id)),
        "requestContext": {
            "authorizer": {
                "lambda": {
                    "tenantid": tenant,
                    "appid": DEFAULT_APP_ID,
                    "tier": DEFAULT_TIER,
                    "sub": DEFAULT_SUB,
                }
            }
        },
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Invoke a deployed agent directly via the Bridge Lambda."
    )
    parser.add_argument("--agent", required=True, help="Agent name")
    parser.add_argument("--tenant", required=True, help="Tenant ID")
    parser.add_argument("--prompt", default="Hello", help="Input prompt")
    parser.add_argument(
        "--mode",
        choices=("sync", "streaming", "async"),
        default="sync",
        help="Requested invocation mode for the payload contract",
    )
    parser.add_argument(
        "--env",
        default=DEFAULT_ENV,
        help="Deployment environment (dev, staging, prod). Use dev-invoke for local.",
    )
    parser.add_argument("--session-id", help="Optional session identifier")
    parser.add_argument("--webhook-id", help="Optional webhook identifier")
    return parser.parse_args(argv)


def _print_payload(payload: Any) -> None:
    if isinstance(payload, (dict, list)):
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    print(payload)


def invoke_remote(
    agent: str,
    tenant: str,
    prompt: str = "Hello",
    env: str = DEFAULT_ENV,
    mode: str = "sync",
    session_id: str | None = None,
    webhook_id: str | None = None,
) -> int:
    """Invoke the deployed Bridge Lambda on AWS."""
    if env == "local":
        raise AgentInvokeError("ENV=local is not supported here. Use `make dev-invoke` instead.")

    function_name = f"platform-{env}-bridge"
    client = boto3.client("lambda")

    try:
        payload_bytes = json.dumps(
            build_event(agent, tenant, prompt, session_id, webhook_id)
        ).encode("utf-8")
        response = client.invoke(
            FunctionName=function_name,
            InvocationType="RequestResponse",
            Payload=payload_bytes,
        )
    except ClientError as exc:
        raise AgentInvokeError(f"AWS Lambda invocation failed: {exc}") from exc

    payload_stream = response.get("Payload")
    if payload_stream is None:
        raise AgentInvokeError("AWS Lambda invocation returned no payload.")

    raw_payload = payload_stream.read()
    try:
        payload = json.loads(raw_payload.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise AgentInvokeError("AWS Lambda invocation returned invalid JSON.") from exc

    _print_payload(payload)
    return 0 if payload.get("statusCode", 500) < 400 else 1


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        return invoke_remote(
            agent=args.agent,
            tenant=args.tenant,
            prompt=args.prompt,
            env=args.env,
            mode=args.mode,
            session_id=args.session_id,
            webhook_id=args.webhook_id,
        )
    except AgentInvokeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
