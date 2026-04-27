from __future__ import annotations

import json
import subprocess
from pathlib import Path


def _run_guard(template: dict[str, object]) -> subprocess.CompletedProcess[str]:
    repo_root = Path(__file__).resolve().parents[2]
    rules_path = repo_root / "infra/guard/platform-security.guard"
    fixture_path = repo_root / ".build" / "tmp" / "guard-cloudwatch-template.json"
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_path.write_text(json.dumps(template), encoding="utf-8")
    return subprocess.run(
        ["cfn-guard", "validate", "--rules", str(rules_path), "--data", str(fixture_path)],
        capture_output=True,
        text=True,
        check=False,
    )


def _put_metric_policy_statement(*, namespace: str | None) -> dict[str, object]:
    statement: dict[str, object] = {
        "Effect": "Allow",
        "Action": "cloudwatch:PutMetricData",
        "Resource": "*",
    }
    if namespace is not None:
        statement["Condition"] = {
            "StringEquals": {
                "cloudwatch:namespace": namespace,
            }
        }
    return statement


def _policy_statement(
    action: str | list[str],
    *,
    condition: dict[str, object] | None = None,
) -> dict[str, object]:
    statement: dict[str, object] = {
        "Effect": "Allow",
        "Action": action,
        "Resource": "*",
    }
    if condition is not None:
        statement["Condition"] = condition
    return statement


def _template_with_statement(statement: dict[str, object]) -> dict[str, object]:
    return {
        "Resources": {
            "MetricPolicy": {
                "Type": "AWS::IAM::Policy",
                "Properties": {
                    "PolicyName": "metric-policy",
                    "Roles": ["bridge-role"],
                    "PolicyDocument": {
                        "Version": "2012-10-17",
                        "Statement": [statement],
                    },
                },
            }
        }
    }


def _runtime_vpc_template(*, endpoint_services: list[str]) -> dict[str, object]:
    resources: dict[str, object] = {}
    for index, service_name in enumerate(endpoint_services, start=1):
        service_name_property: object
        if service_name.endswith(".s3"):
            service_name_property = {
                "Fn::Join": [
                    "",
                    [
                        "com.amazonaws.",
                        {"Ref": "AWS::Region"},
                        ".s3",
                    ],
                ]
            }
        else:
            service_name_property = service_name
        resources[f"Endpoint{index}"] = {
            "Type": "AWS::EC2::VPCEndpoint",
            "Properties": {
                "ServiceName": service_name_property,
            },
        }

    return {"Resources": resources}


def test_guard_allows_namespace_scoped_put_metric_data() -> None:
    for namespace in ("Platform/Bridge", "Platform/Billing"):
        result = _run_guard(
            _template_with_statement(_put_metric_policy_statement(namespace=namespace))
        )
        assert result.returncode == 0, result.stderr or result.stdout


def test_guard_rejects_unconditioned_put_metric_data() -> None:
    result = _run_guard(_template_with_statement(_put_metric_policy_statement(namespace=None)))
    assert result.returncode != 0
    assert "cloudwatch:namespace" in (result.stderr or result.stdout)


def test_guard_rejects_put_metric_data_outside_platform_namespaces() -> None:
    result = _run_guard(
        _template_with_statement(_put_metric_policy_statement(namespace="Custom/Other"))
    )
    assert result.returncode != 0
    assert "Platform/Bridge" in (result.stderr or result.stdout)


def test_guard_allows_service_quota_listing_without_resource_scope() -> None:
    result = _run_guard(
        _template_with_statement(_policy_statement("servicequotas:ListServiceQuotas"))
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_guard_allows_cdk_oidc_provider_custom_resource_actions() -> None:
    result = _run_guard(
        _template_with_statement(
            _policy_statement(
                [
                    "iam:CreateOpenIDConnectProvider",
                    "iam:DeleteOpenIDConnectProvider",
                    "iam:UpdateOpenIDConnectProviderThumbprint",
                    "iam:AddClientIDToOpenIDConnectProvider",
                    "iam:RemoveClientIDFromOpenIDConnectProvider",
                ]
            )
        )
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_guard_allows_agentcore_memory_creation_with_managed_tag_gate() -> None:
    result = _run_guard(
        _template_with_statement(
            _policy_statement(
                "bedrock-agentcore:CreateMemory",
                condition={
                    "StringEquals": {"aws:RequestTag/TenantManaged": "true"},
                    "ForAnyValue:StringEquals": {"aws:TagKeys": "TenantManaged"},
                },
            )
        )
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_guard_rejects_agentcore_memory_creation_without_tag_key_gate() -> None:
    result = _run_guard(
        _template_with_statement(
            _policy_statement(
                "bedrock-agentcore:CreateMemory",
                condition={"StringEquals": {"aws:RequestTag/TenantManaged": "true"}},
            )
        )
    )
    assert result.returncode != 0
    assert "aws:TagKeys" in (result.stderr or result.stdout)


def test_guard_rejects_unapproved_wildcard_resource_statement() -> None:
    result = _run_guard(_template_with_statement(_policy_statement("s3:ListBucket")))
    assert result.returncode != 0
    assert "s3:ListBucket" in (result.stderr or result.stdout)


def test_guard_rejects_agentcore_memory_management_on_wildcard_resource() -> None:
    result = _run_guard(_template_with_statement(_policy_statement("bedrock-agentcore:GetMemory")))
    assert result.returncode != 0
    assert "bedrock-agentcore:GetMemory" in (result.stderr or result.stdout)


def test_guard_requires_no_internet_agentcore_runtime_endpoints() -> None:
    required_services = [
        "com.amazonaws.eu-west-2.s3",
        "com.amazonaws.eu-west-2.logs",
        "com.amazonaws.eu-west-2.ecr.api",
        "com.amazonaws.eu-west-2.ecr.dkr",
    ]

    passing_result = _run_guard(_runtime_vpc_template(endpoint_services=required_services))
    assert passing_result.returncode == 0, passing_result.stderr or passing_result.stdout

    failing_result = _run_guard(
        _runtime_vpc_template(
            endpoint_services=[
                "com.amazonaws.eu-west-2.s3",
                "com.amazonaws.eu-west-2.ecr.api",
                "com.amazonaws.eu-west-2.ecr.dkr",
            ]
        )
    )
    assert failing_result.returncode != 0
    assert "agentcore_runtime_no_internet_endpoints_present" in (
        failing_result.stderr or failing_result.stdout
    )
