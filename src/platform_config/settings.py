"""Pydantic settings models for platform scripts and local tooling."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, cast

from pydantic import AliasChoices, BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = str(value).strip()
    return stripped or None


class AwsSettings(BaseModel):
    """Shared AWS execution context."""

    region: str
    default_region: str | None = None
    control_account_id: str | None = None
    runtime_account_id: str | None = None
    access_key_id: str | None = None
    secret_access_key: str | None = None


class GitLabSettings(BaseModel):
    """GitLab API and CI context used by release checks."""

    project_id: str | None = None
    ci_api_v4_url: str | None = None
    ci_project_id: str | None = None
    protected_env_api_token: str | None = None
    commit_sha: str | None = None
    pipeline_url: str | None = None
    job_id: str | None = None


class AgentSettings(BaseModel):
    """Agent build, deploy, and registry settings."""

    execution_role_arn: str | None = None
    ecr_repository_uri: str | None = None
    platform_layer_bucket: str | None = None
    agent_layer_bucket: str | None = None
    layer_artifact_bucket: str | None = None
    api_base_url: str | None = None
    vite_api_base_url: str | None = None
    platform_access_token: str | None = None
    ops_access_token: str | None = None

    @property
    def layer_bucket_candidates(self) -> tuple[str, ...]:
        return tuple(
            value
            for value in (
                self.platform_layer_bucket,
                self.agent_layer_bucket,
                self.layer_artifact_bucket,
            )
            if value
        )

    @property
    def resolved_api_base_url(self) -> str | None:
        return self.api_base_url or self.vite_api_base_url

    @property
    def resolved_access_token(self) -> str | None:
        return self.platform_access_token or self.ops_access_token


class BillingSettings(BaseModel):
    """Billing table and event configuration."""

    tenants_table_name: str | None = None
    invocations_table_name: str | None = None
    event_bus_name: str | None = None


class OpsSettings(BaseModel):
    """Operator CLI and failover-lock settings."""

    credentials_path: str | None = None
    failover_lock_token_path: str | None = None
    platform_ops_locks_table: str | None = None
    user: str | None = None
    username: str | None = None
    shell: str | None = None
    worktree_ready_wait_seconds: str | None = None
    worktree_gitnexus_cli: str | None = None
    worktree_gitnexus_timeout_seconds: str | None = None
    issue_tracker_remote: str | None = None
    can_list_top_tenants: str | None = None
    can_list_sessions: str | None = None
    can_get_invocation_report: str | None = None
    can_list_security_events: str | None = None
    can_inspect_dlq: str | None = None
    can_redrive_dlq: str | None = None
    can_get_error_rate: str | None = None
    can_get_service_health: str | None = None


class PlatformSettings(BaseSettings):
    """Root settings model.

    `.env.example` is the documented local baseline. Real environment variables
    and developer env files override it in pydantic-settings order.
    """

    model_config = SettingsConfigDict(
        env_file=(
            REPO_ROOT / ".env.example",
            REPO_ROOT / ".env",
            REPO_ROOT / ".env.local",
            REPO_ROOT / ".env.test",
        ),
        extra="ignore",
    )

    aws_region: str | None = Field(
        validation_alias=AliasChoices("AWS_REGION", "AWS_DEFAULT_REGION")
    )
    aws_default_region: str | None = Field(default=None, validation_alias="AWS_DEFAULT_REGION")
    aws_control_account_id: str | None = None
    aws_runtime_account_id: str | None = None
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None

    gitlab_project_id: str | None = None
    ci_api_v4_url: str | None = None
    ci_project_id: str | None = None
    gitlab_protected_env_api_token: str | None = None
    ci_commit_sha: str | None = None
    ci_pipeline_url: str | None = None
    ci_job_id: str | None = None

    bedrock_agentcore_execution_role_arn: str | None = None
    bedrock_agentcore_ecr_repository_uri: str | None = None
    platform_layer_bucket: str | None = None
    agent_layer_bucket: str | None = None
    layer_artifact_bucket: str | None = None
    api_base_url: str | None = None
    vite_api_base_url: str | None = None
    platform_access_token: str | None = None
    ops_access_token: str | None = None

    tenants_table_name: str | None = None
    invocations_table_name: str | None = None
    event_bus_name: str | None = None

    platform_credentials_path: str | None = None
    failover_lock_token_path: str | None = None
    platform_ops_locks_table: str | None = None
    user: str | None = None
    username: str | None = None
    shell: str | None = None
    worktree_ready_wait_seconds: str | None = None
    worktree_gitnexus_cli: str | None = None
    worktree_gitnexus_timeout_seconds: str | None = None
    issue_tracker_remote: str | None = None
    platform_ops_can_list_top_tenants: str | None = None
    platform_ops_can_list_sessions: str | None = None
    platform_ops_can_get_invocation_report: str | None = None
    platform_ops_can_list_security_events: str | None = None
    platform_ops_can_inspect_dlq: str | None = None
    platform_ops_can_redrive_dlq: str | None = None
    platform_ops_can_get_error_rate: str | None = None
    platform_ops_can_get_service_health: str | None = None

    @field_validator("*", mode="before")
    @classmethod
    def normalize_blank_strings(cls, value: Any) -> Any:
        if isinstance(value, str):
            return _blank_to_none(value)
        return value

    @field_validator("aws_region")
    @classmethod
    def require_aws_region(cls, value: str | None) -> str:
        stripped = (value or "").strip()
        if not stripped:
            raise ValueError("AWS_REGION must be set")
        return stripped

    @property
    def aws(self) -> AwsSettings:
        return AwsSettings(
            region=cast(str, self.aws_region),
            default_region=self.aws_default_region,
            control_account_id=self.aws_control_account_id,
            runtime_account_id=self.aws_runtime_account_id,
            access_key_id=self.aws_access_key_id,
            secret_access_key=self.aws_secret_access_key,
        )

    @property
    def gitlab(self) -> GitLabSettings:
        return GitLabSettings(
            project_id=self.gitlab_project_id,
            ci_api_v4_url=self.ci_api_v4_url,
            ci_project_id=self.ci_project_id,
            protected_env_api_token=self.gitlab_protected_env_api_token,
            commit_sha=self.ci_commit_sha,
            pipeline_url=self.ci_pipeline_url,
            job_id=self.ci_job_id,
        )

    @property
    def agents(self) -> AgentSettings:
        return AgentSettings(
            execution_role_arn=self.bedrock_agentcore_execution_role_arn,
            ecr_repository_uri=self.bedrock_agentcore_ecr_repository_uri,
            platform_layer_bucket=self.platform_layer_bucket,
            agent_layer_bucket=self.agent_layer_bucket,
            layer_artifact_bucket=self.layer_artifact_bucket,
            api_base_url=self.api_base_url,
            vite_api_base_url=self.vite_api_base_url,
            platform_access_token=self.platform_access_token,
            ops_access_token=self.ops_access_token,
        )

    @property
    def billing(self) -> BillingSettings:
        return BillingSettings(
            tenants_table_name=self.tenants_table_name,
            invocations_table_name=self.invocations_table_name,
            event_bus_name=self.event_bus_name,
        )

    @property
    def ops(self) -> OpsSettings:
        return OpsSettings(
            credentials_path=self.platform_credentials_path,
            failover_lock_token_path=self.failover_lock_token_path,
            platform_ops_locks_table=self.platform_ops_locks_table,
            user=self.user,
            username=self.username,
            shell=self.shell,
            worktree_ready_wait_seconds=self.worktree_ready_wait_seconds,
            worktree_gitnexus_cli=self.worktree_gitnexus_cli,
            worktree_gitnexus_timeout_seconds=self.worktree_gitnexus_timeout_seconds,
            issue_tracker_remote=self.issue_tracker_remote,
            can_list_top_tenants=self.platform_ops_can_list_top_tenants,
            can_list_sessions=self.platform_ops_can_list_sessions,
            can_get_invocation_report=self.platform_ops_can_get_invocation_report,
            can_list_security_events=self.platform_ops_can_list_security_events,
            can_inspect_dlq=self.platform_ops_can_inspect_dlq,
            can_redrive_dlq=self.platform_ops_can_redrive_dlq,
            can_get_error_rate=self.platform_ops_can_get_error_rate,
            can_get_service_health=self.platform_ops_can_get_service_health,
        )

    def optional(self, name: str, default: str | None = None) -> str | None:
        value = _blank_to_none(os.environ.get(name))
        if value is not None:
            return value

        model_value = getattr(self, name.lower(), None)
        if isinstance(model_value, str):
            return _blank_to_none(model_value) or default
        return default

    def required(self, name: str) -> str:
        value = self.optional(name)
        if value is None:
            raise RuntimeError(f"{name} must be set")
        return value

    def first(self, *names: str) -> str | None:
        for name in names:
            value = self.optional(name)
            if value is not None:
                return value
        return None

    def truthy(self, name: str) -> bool:
        value = self.optional(name)
        return value is not None and value.lower() not in {"0", "false", "no", "off"}


def get_settings() -> PlatformSettings:
    """Return fresh settings so tests that monkeypatch env observe current values."""
    return PlatformSettings()  # type: ignore[call-arg]


def env_optional(name: str, default: str | None = None) -> str | None:
    return get_settings().optional(name, default)


def process_env_optional(name: str, default: str | None = None) -> str | None:
    value = _blank_to_none(os.environ.get(name))
    return value if value is not None else default


def process_env_required(name: str) -> str:
    value = process_env_optional(name)
    if value is None:
        raise RuntimeError(f"{name} must be set")
    return value


def env_required(name: str) -> str:
    return get_settings().required(name)


def env_first(*names: str) -> str | None:
    return get_settings().first(*names)


def env_truthy(name: str) -> bool:
    return get_settings().truthy(name)


class SettingsProxy:
    """Compatibility object for `from platform_config import settings`."""

    def __getattr__(self, name: str) -> Any:
        return getattr(get_settings(), name)


settings = SettingsProxy()
