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
