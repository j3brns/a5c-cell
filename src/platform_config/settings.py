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
    """Operator CLI settings."""

    credentials_path: str | None = None
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


class BffSettings(BaseModel):
    """BFF authentication and runtime wiring."""

    entra_client_id: str | None = None
    entra_client_secret: str | None = None
    entra_tenant_id: str | None = None
    entra_token_endpoint: str | None = None
    entra_audience: str | None = None
    entra_client_id_secret_arn: str | None = None
    entra_client_secret_secret_arn: str | None = None
    runtime_ping_url: str | None = None


class BridgeSettings(BaseModel):
    """Bridge runtime and resource settings."""

    appconfig_application_id: str | None = None
    appconfig_environment_id: str | None = None
    appconfig_profile_id: str | None = None
    agents_table_name: str | None = None
    jobs_table_name: str | None = None
    sessions_table_name: str | None = None
    job_results_bucket: str | None = None
    ag_ui_scope_name: str = "Agent.AgUi.Connect"
    runtime_region_param: str = "/platform/config/runtime-region"
    mock_runtime_url_param: str = "/platform/config/mock-runtime-url"
    valkey_endpoint_param: str = "/platform/config/valkey-endpoint"
    tenant_execution_role_param_template: str = "/platform/tenants/{tenant_id}/execution-role-arn"
    job_result_url_expiry_seconds: int = 3600
    runtime_connect_timeout_seconds: int = 5
    runtime_read_timeout_seconds: int = 900
    bedrock_agentcore_dp_endpoint: str | None = None
    valkey_endpoint: str | None = None


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

    tenants_table_name: str = Field(
        default="platform-tenants", validation_alias=AliasChoices("TENANTS_TABLE", "tenants_table_name")
    )
    invocations_table_name: str = Field(
        default="platform-invocations",
        validation_alias=AliasChoices("INVOCATIONS_TABLE", "invocations_table_name"),
    )
    agents_table_name: str = Field(
        default="platform-agents", validation_alias=AliasChoices("AGENTS_TABLE", "agents_table_name")
    )
    jobs_table_name: str = Field(
        default="platform-jobs", validation_alias=AliasChoices("JOBS_TABLE", "jobs_table_name")
    )
    sessions_table_name: str = Field(
        default="platform-sessions",
        validation_alias=AliasChoices("SESSIONS_TABLE", "sessions_table_name"),
    )
    event_bus_name: str | None = None

    entra_client_id: str | None = None
    entra_client_secret: str | None = None
    entra_tenant_id: str | None = None
    entra_token_endpoint: str | None = None
    entra_audience: str | None = None
    entra_client_id_secret_arn: str | None = None
    entra_client_secret_secret_arn: str | None = None
    runtime_ping_url: str | None = Field(
        default=None, validation_alias=AliasChoices("RUNTIME_PING_URL", "MOCK_RUNTIME_URL")
    )

    appconfig_application_id: str | None = None
    appconfig_environment_id: str | None = None
    appconfig_profile_id: str | None = None
    job_results_bucket: str | None = None
    ag_ui_scope_name: str = "Agent.AgUi.Connect"
    runtime_region_param: str = "/platform/config/runtime-region"
    mock_runtime_url_param: str = "/platform/config/mock-runtime-url"
    valkey_endpoint_param: str = "/platform/config/valkey-endpoint"
    tenant_execution_role_param_template: str = "/platform/tenants/{tenant_id}/execution-role-arn"
    job_result_url_expiry_seconds: int = 3600
    agentcore_runtime_connect_timeout_seconds: int = 5
    agentcore_runtime_read_timeout_seconds: int = 900
    bedrock_agentcore_dp_endpoint: str | None = None
    valkey_endpoint: str | None = Field(
        default=None, validation_alias=AliasChoices("VALKEY_ENDPOINT", "TPM_VALKEY_ENDPOINT")
    )

    platform_credentials_path: str | None = None
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
    def bff(self) -> BffSettings:
        return BffSettings(
            entra_client_id=self.entra_client_id,
            entra_client_secret=self.entra_client_secret,
            entra_tenant_id=self.entra_tenant_id,
            entra_token_endpoint=self.entra_token_endpoint,
            entra_audience=self.entra_audience,
            entra_client_id_secret_arn=self.entra_client_id_secret_arn,
            entra_client_secret_secret_arn=self.entra_client_secret_secret_arn,
            runtime_ping_url=self.runtime_ping_url,
        )

    @property
    def bridge(self) -> BridgeSettings:
        return BridgeSettings(
            appconfig_application_id=self.appconfig_application_id,
            appconfig_environment_id=self.appconfig_environment_id,
            appconfig_profile_id=self.appconfig_profile_id,
            agents_table_name=self.agents_table_name,
            jobs_table_name=self.jobs_table_name,
            sessions_table_name=self.sessions_table_name,
            job_results_bucket=self.job_results_bucket,
            ag_ui_scope_name=self.ag_ui_scope_name,
            runtime_region_param=self.runtime_region_param,
            mock_runtime_url_param=self.mock_runtime_url_param,
            valkey_endpoint_param=self.valkey_endpoint_param,
            tenant_execution_role_param_template=self.tenant_execution_role_param_template,
            job_result_url_expiry_seconds=self.job_result_url_expiry_seconds,
            runtime_connect_timeout_seconds=self.agentcore_runtime_connect_timeout_seconds,
            runtime_read_timeout_seconds=self.agentcore_runtime_read_timeout_seconds,
            bedrock_agentcore_dp_endpoint=self.bedrock_agentcore_dp_endpoint,
            valkey_endpoint=self.valkey_endpoint,
        )

    @property
    def ops(self) -> OpsSettings:
        return OpsSettings(
            credentials_path=self.platform_credentials_path,
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
