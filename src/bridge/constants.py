from __future__ import annotations

import re

from platform_config import settings

# Table Names
TENANTS_TABLE = settings.billing.tenants_table_name
INVOCATIONS_TABLE = settings.billing.invocations_table_name
AGENTS_TABLE = settings.bridge.agents_table_name
JOBS_TABLE = settings.bridge.jobs_table_name
SESSIONS_TABLE = settings.bridge.sessions_table_name

# Environment & Infrastructure
JOB_RESULTS_BUCKET = settings.bridge.job_results_bucket
ENTRA_AUDIENCE = settings.bff.entra_audience
AG_UI_SCOPE_NAME = settings.bridge.ag_ui_scope_name
BFF_TOKEN_REFRESH_PATH = "/v1/bff/token-refresh"
BFF_SESSION_KEEPALIVE_PATH = "/v1/bff/session-keepalive"

# SSM / AppConfig Paths
RUNTIME_REGION_PARAM = settings.bridge.runtime_region_param
MOCK_RUNTIME_URL_PARAM = settings.bridge.mock_runtime_url_param
VALKEY_ENDPOINT_PARAM = settings.bridge.valkey_endpoint_param
TENANT_EXECUTION_ROLE_PARAM_TEMPLATE = settings.bridge.tenant_execution_role_param_template

# Timeouts & TTLs
JOB_RESULT_URL_EXPIRY_SECONDS = settings.bridge.job_result_url_expiry_seconds
AGENTCORE_RUNTIME_CONNECT_TIMEOUT_SECONDS = settings.bridge.runtime_connect_timeout_seconds
AGENTCORE_RUNTIME_READ_TIMEOUT_SECONDS = settings.bridge.runtime_read_timeout_seconds
INVOCATION_TTL_SECONDS = 90 * 24 * 60 * 60
JOB_TTL_SECONDS = 7 * 24 * 60 * 60

# Regex Patterns
IAM_ROLE_ARN_PATTERN = re.compile(
    r"^arn:(?:aws|aws-us-gov|aws-cn):iam::(?P<account_id>\d{12}):role/(?P<role_name>[\w+=,.@\-_/]+)$"
)
RUNTIME_ARN_PATTERN = re.compile(
    r"^arn:(?P<partition>aws|aws-us-gov|aws-cn):bedrock-agentcore:(?P<region>[a-z0-9-]+):"
    r"(?P<account_id>\d{12}):runtime/(?P<runtime_id>[\w+=,.@\-_/]+)$"
)
RUNTIME_ENDPOINT_ARN_PATTERN = re.compile(
    r"^arn:(?P<partition>aws|aws-us-gov|aws-cn):bedrock-agentcore:(?P<region>[a-z0-9-]+):"
    r"(?P<account_id>\d{12}):runtime/(?P<runtime_id>[A-Za-z][A-Za-z0-9_]{0,99}-[A-Za-z0-9]{10})"
    r"/runtime-endpoint/(?P<endpoint_id>[A-Za-z][A-Za-z0-9_]{0,47}-[A-Za-z0-9]{10})$"
)

# Validation
VALID_WEBHOOK_EVENTS = {"job.completed", "job.failed"}
