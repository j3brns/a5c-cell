import re
from pathlib import Path

CI_FILE = Path(__file__).resolve().parents[2] / ".gitlab-ci.yml"
AGENTS_DIR = Path(__file__).resolve().parents[2] / "agents"
AGENT_CI_FILE = Path(__file__).resolve().parents[2] / "agents" / ".gitlab-ci-agent.yml"
TASKS_FILE = Path(__file__).resolve().parents[2] / "docs" / "TASKS.md"


def _job_block(name: str, content: str) -> str:
    pattern = rf"(?ms)^{re.escape(name)}:\n(.*?)(?=^[A-Za-z0-9_.-]+:\n|\Z)"
    match = re.search(pattern, content)
    assert match is not None, f"Missing job block: {name}"
    return match.group(1)


def _configured_agent_matrix(content: str) -> set[str]:
    return {
        agent
        for match in re.finditer(r"AGENT_NAME:\s*\[([^\]]+)\]", content)
        for agent in re.findall(r'"([^"]+)"', match.group(1))
    }


def test_canary_policy_variables_are_explicit_per_environment() -> None:
    content = CI_FILE.read_text(encoding="utf-8")

    assert 'CANARY_POLICY_DEV: "all-at-once"' in content
    assert 'CANARY_POLICY_STAGING: "canary-10%-30m"' in content
    assert 'CANARY_POLICY_PROD: "canary-10%-15m"' in content
    assert 'STAGING_ROLLOUT_WINDOW_MINUTES: "30"' in content
    assert 'PROD_ROLLOUT_WINDOW_MINUTES: "15"' in content
    assert 'GITLAB_PROTECTED_ENVIRONMENT_NAME: "prod"' in content
    assert 'GITLAB_PROTECTED_ENV_REQUIRED_APPROVALS: "2"' in content


def test_ci_test_matrix_covers_unit_integration_and_cdk() -> None:
    content = CI_FILE.read_text(encoding="utf-8")
    for name in ("test-unit", "test-integration", "test-cdk"):
        block = _job_block(name, content)
        assert "extends: .test_job_base" in block or "extends: .aws_auth_base" in block


def test_workflow_pauses_pipelines_by_default_and_preserves_opt_in_deduping() -> None:
    content = CI_FILE.read_text(encoding="utf-8")
    workflow = _job_block("workflow", content)

    assert 'GITLAB_PIPELINES_ENABLED: "0"' in content
    assert (
        'if: $GITLAB_PIPELINES_ENABLED == "1" && $CI_PIPELINE_SOURCE == "merge_request_event"'
    ) in workflow
    assert 'if: $GITLAB_PIPELINES_ENABLED == "1" && $CI_COMMIT_BRANCH' in workflow
    assert "GitLab CI is paused by default" in workflow
    assert (
        'if: $GITLAB_PIPELINES_ENABLED == "1" && $CI_COMMIT_BRANCH && $CI_OPEN_MERGE_REQUESTS'
    ) in workflow
    assert "when: never" in workflow


def test_expensive_jobs_are_gated_by_relevant_file_changes() -> None:
    content = CI_FILE.read_text(encoding="utf-8")

    expected_rule_refs = {
        "validate": ".platform_change_rules",
        "test-unit": ".python_change_rules",
        "test-integration": ".python_change_rules",
        "test-cdk": ".cdk_change_rules",
        "test-spa": ".spa_change_rules",
        "plan-infra": ".deployable_change_rules",
    }
    for job, rule_ref in expected_rule_refs.items():
        block = _job_block(job, content)
        assert f"!reference [{rule_ref}, rules]" in block

    deploy_base = _job_block(".deploy_job_base", content)
    assert "!reference [.main_deployable_change_rules, rules]" in deploy_base


def test_deploy_jobs_require_appconfig_extension_layer_arn() -> None:
    content = CI_FILE.read_text(encoding="utf-8")

    assert "# APPCONFIG_EXTENSION_LAYER_ARN" in content
    deploy_base = _job_block(".deploy_job_base", content)
    assert "APPCONFIG_EXTENSION_LAYER_ARN" in deploy_base
    assert "AWS AppConfig ARM64 Lambda extension layer ARN" in deploy_base


def test_deployable_changes_exclude_docs_only_pipeline_noise() -> None:
    content = CI_FILE.read_text(encoding="utf-8")
    deployable_rules = _job_block(".deployable_change_rules", content)

    assert "src/**/*" in deployable_rules
    assert "infra/**/*" in deployable_rules
    assert "spa/**/*" in deployable_rules
    assert "docs/openapi.yaml" in deployable_rules
    assert "docs/**/*" not in deployable_rules


def test_validate_pipeline_policy_runs_ci_contract_and_protection_script_tests() -> None:
    content = CI_FILE.read_text(encoding="utf-8")
    validate = _job_block("validate-pipeline-policy", content)
    assert "tests/unit/test_issue_110_ci_policy.py" in validate
    assert "tests/unit/test_check_gitlab_protected_environment.py" in validate


def test_aws_backed_validation_and_plan_jobs_fail_closed_on_missing_oidc() -> None:
    content = CI_FILE.read_text(encoding="utf-8")

    auth_base = _job_block(".aws_auth_base", content)
    assert "ERROR: AWS role ARN not set." in auth_base
    assert "ERROR: GITLAB_OIDC_TOKEN not issued for this job." in auth_base
    assert "AWS_AUTH_OPTIONAL" not in auth_base

    integration = _job_block("test-integration", content)
    assert "extends: .aws_auth_base" in integration
    assert "Skipping integration tests" not in integration

    plan = _job_block("plan-infra", content)
    assert "extends: .aws_auth_base" in plan
    assert "Skipping infra plan" not in plan
    assert "skipped: no validate role configured" not in plan


def test_root_pipeline_explicitly_triggers_agent_child_pipelines() -> None:
    content = CI_FILE.read_text(encoding="utf-8")

    rules = _job_block(".agent_pipeline_change_rules", content)
    assert 'if: $CI_PIPELINE_SOURCE == "merge_request_event"' in rules
    assert "changes: *agent_pipeline_changes" in rules

    rules = _job_block(".main_agent_pipeline_change_rules", content)
    assert 'if: $CI_COMMIT_BRANCH == "main"' in rules
    assert "agents/.gitlab-ci-agent.yml" in rules
    assert "agents/**/*" in rules

    validate = _job_block("validate-agent-pipelines", content)
    assert "stage: test" in validate
    assert "local: agents/.gitlab-ci-agent.yml" in validate
    assert "strategy: mirror" in validate
    assert 'AGENT_PIPELINE_MODE: ["validate"]' in validate
    assert "!reference [.agent_pipeline_change_rules, rules]" in validate

    trigger = _job_block("deploy-agent-pipelines", content)
    assert "stage: deploy-dev" in trigger
    assert "local: agents/.gitlab-ci-agent.yml" in trigger
    assert "strategy: mirror" in trigger
    assert 'AGENT_PIPELINE_MODE: ["deploy-dev"]' in trigger
    assert "!reference [.main_agent_pipeline_change_rules, rules]" in trigger

    staging_promotion = _job_block("promote-agent-staging", content)
    assert "stage: deploy-staging" in staging_promotion
    assert "local: agents/.gitlab-ci-agent.yml" in staging_promotion
    assert "strategy: mirror" in staging_promotion
    assert 'AGENT_PIPELINE_MODE: ["promote-staging"]' in staging_promotion
    assert "!reference [.main_agent_pipeline_change_rules, rules]" in staging_promotion

    prod_promotion = _job_block("promote-agent-prod", content)
    assert "stage: deploy-prod" in prod_promotion
    assert 'needs: ["promote-agent-staging"]' in prod_promotion
    assert "local: agents/.gitlab-ci-agent.yml" in prod_promotion
    assert "strategy: mirror" in prod_promotion
    assert 'AGENT_PIPELINE_MODE: ["promote-prod"]' in prod_promotion
    assert "!reference [.main_agent_pipeline_change_rules, rules]" in prod_promotion


def test_agent_child_pipeline_matrix_matches_checked_in_agents() -> None:
    content = CI_FILE.read_text(encoding="utf-8")

    checked_in_agents = {manifest.parent.name for manifest in AGENTS_DIR.glob("*/pyproject.toml")}
    assert _configured_agent_matrix(content) == checked_in_agents


def test_agent_pipeline_uses_valid_oidc_token_syntax() -> None:
    content = AGENT_CI_FILE.read_text(encoding="utf-8")
    default = _job_block("default", content)

    assert "\nid_token:" not in content
    assert "id_tokens:" in default
    assert "GITLAB_OIDC_TOKEN:" in default
    assert "aud: sts.amazonaws.com" in default


def test_agent_aws_jobs_fail_closed_before_deploy_or_validation() -> None:
    content = AGENT_CI_FILE.read_text(encoding="utf-8")

    auth_base = _job_block(".agent-aws-auth-base", content)
    assert "ERROR: agent pipeline AWS role ARN not set." in auth_base
    assert "ERROR: GITLAB_OIDC_TOKEN not issued for this agent job." in auth_base
    assert "assume-role-with-web-identity" in auth_base

    for job in ("validate", "push-dev", "promote-staging", "promote-prod"):
        block = _job_block(job, content)
        assert "extends: .agent-aws-auth-base" in block

    test = _job_block("test", content)
    assert "extends: .agent-base" in test

    validate = _job_block("validate", content)
    assert '$AGENT_PIPELINE_MODE == "validate"' in validate
    assert '$AGENT_PIPELINE_MODE == "deploy-dev"' in validate
    assert '$AGENT_PIPELINE_MODE == "promote-staging"' in validate
    assert '$AGENT_PIPELINE_MODE == "promote-prod"' in validate

    push_dev = _job_block("push-dev", content)
    assert 'if: $CI_COMMIT_BRANCH == "main" && $AGENT_PIPELINE_MODE == "deploy-dev"' in push_dev
    assert 'if: $CI_PIPELINE_SOURCE == "push"' not in push_dev

    staging = _job_block("promote-staging", content)
    assert 'if: $CI_COMMIT_BRANCH == "main" && $AGENT_PIPELINE_MODE == "promote-staging"' in staging
    assert "deployment_tier: staging" in staging

    prod = _job_block("promote-prod", content)
    assert 'if: $CI_COMMIT_BRANCH == "main" && $AGENT_PIPELINE_MODE == "promote-prod"' in prod
    assert "uv run python scripts/check_gitlab_protected_environment.py" in prod
    assert "deployment_tier: production" in prod
    assert '--environment "${GITLAB_PROTECTED_ENVIRONMENT_NAME}"' in prod
    assert '--min-approvals "${GITLAB_PROTECTED_ENV_REQUIRED_APPROVALS}"' in prod


def test_task_044_plan_stage_claim_matches_artifact_only_pipeline() -> None:
    tasks = TASKS_FILE.read_text(encoding="utf-8")
    content = CI_FILE.read_text(encoding="utf-8")
    plan = _job_block("plan-infra", content)

    assert "plan: cdk diff stored as artifacts for review" in tasks
    assert "plan: cdk diff posted as MR comment" not in tasks
    assert "dev-diff.txt" in plan
    assert "staging-diff.txt" in plan
    assert "prod-diff.txt" in plan


def test_staging_and_prod_gates_have_manual_approvals_and_rollout_windows() -> None:
    content = CI_FILE.read_text(encoding="utf-8")

    staging = _job_block("deploy-staging", content)
    assert "when: manual" in staging
    assert "deployment_tier: staging" in staging

    staging_window = _job_block("staging-rollout-window", content)
    assert "when: delayed" in staging_window
    assert "start_in: 30 minutes" in staging_window
    assert 'needs: ["deploy-staging"]' in staging_window

    prod = _job_block("deploy-prod", content)
    assert 'needs: ["staging-rollout-window"]' in prod
    assert "when: manual" in prod
    assert "deployment_tier: production" in prod
    assert "uv run python scripts/check_gitlab_protected_environment.py" in prod
    assert '--environment "${GITLAB_PROTECTED_ENVIRONMENT_NAME}"' in prod
    assert '--min-approvals "${GITLAB_PROTECTED_ENV_REQUIRED_APPROVALS}"' in prod
    assert 'test "${PROD_APPROVAL_MODE}"' not in prod

    prod_window = _job_block("prod-rollout-window", content)
    assert "when: delayed" in prod_window
    assert "start_in: 15 minutes" in prod_window
    assert 'needs: ["deploy-prod"]' in prod_window
