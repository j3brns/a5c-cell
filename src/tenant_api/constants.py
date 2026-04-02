from __future__ import annotations
import re

# Environment Variables
TENANTS_TABLE_ENV = "TENANTS_TABLE_NAME"
AGENTS_TABLE_ENV = "AGENTS_TABLE_NAME"
INVOCATIONS_TABLE_ENV = "INVOCATIONS_TABLE_NAME"
EVENT_BUS_ENV = "EVENT_BUS_NAME"
AUDIT_EXPORT_BUCKET_ENV = "AUDIT_EXPORT_BUCKET"
API_KEY_SECRET_PREFIX_ENV = "TENANT_API_KEY_SECRET_PREFIX"  # pragma: allowlist secret
TENANT_MGMT_ROLE_ARN_ENV = "TENANT_MGMT_ROLE_ARN"
OPS_LOCKS_TABLE_ENV = "OPS_LOCKS_TABLE"
RUNTIME_REGION_PARAM_ENV = "RUNTIME_REGION_PARAM"
FALLBACK_REGION_PARAM_ENV = "FALLBACK_REGION_PARAM"
FAILOVER_LOCK_NAME_ENV = "FAILOVER_LOCK_NAME"

# Business Rules & Lifecycle
DELETE_RETENTION_DAYS = 30
INVITE_EXPIRY_DAYS = 7
TENANT_ID_MIN_LENGTH = 3
TENANT_ID_MAX_LENGTH = 32
AUDIT_EXPORT_PREFIX = "audit-exports"
AUDIT_EXPORT_URL_EXPIRY_SECONDS = 3600
AUDIT_EXPORT_PAGE_SIZE = 200

# Roles
ADMIN_ROLES = frozenset({"Platform.Admin"})
SELF_SERVICE_ADMIN_ROLES = frozenset({"Platform.Admin", "Platform.Operator", "SelfService.Admin"})
ALLOWED_TENANT_INVITE_ROLES = frozenset({"Agent.Invoke"})

# Validation Patterns
TENANT_ID_PATTERN = re.compile(r"^[a-z](?:[a-z0-9-]{1,30}[a-z0-9])$")
AWS_ACCOUNT_ID_PATTERN = re.compile(r"^[0-9]{12}$")
RESERVED_TENANT_IDS = frozenset({"platform", "admin", "root", "system", "stub"})

# Defaults
DEFAULT_OPS_LOCKS_TABLE = "platform-ops-locks"
DEFAULT_RUNTIME_REGION_PARAM = "/platform/config/runtime-region"
DEFAULT_FALLBACK_REGION_PARAM = "/platform/config/fallback-region"
DEFAULT_FAILOVER_LOCK_NAME = "platform-runtime-failover"

# AgentCore Quotas
AGENTCORE_QUOTA_NAME = "Active session workloads per account"
AGENTCORE_CONCURRENT_SESSIONS_NAMESPACE = "AgentCore"
AGENTCORE_CONCURRENT_SESSIONS_METRIC = "ConcurrentSessions"
AGENTCORE_QUOTA_LOOKBACK_MINUTES = 5

# Allowed Status Sets
TENANT_PROVISIONING_STATUSES = frozenset({"pending", "provisioning", "ready", "failed"})
PLATFORM_TENANT_ID = "platform"
