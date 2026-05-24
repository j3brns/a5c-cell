# Logical to Physical ID Mapping Contract

Status: accepted convention

This contract formalises the ADR-704 P3 pattern for resource identifiers whose
AWS names vary by account, environment, or region.

## Terms

A logical ID is a stable, environment-agnostic name owned by the platform. It is
human-readable and safe to use in manifests, tenant policy, runbooks, and API
payloads. Examples include `baseline-security`, `standard-chat-model`, or
`get_platform_health`.

A physical ID is the provider identifier for one deployed resource in one
account and region. Examples include a Lambda ARN, AgentCore Gateway target ID,
Bedrock guardrail ID, inference profile ARN, or model alias ARN.

Application code may accept and log logical IDs. It must not embed physical IDs
for resources that are expected to vary across environments.

## Registry

Logical to physical mappings live in a platform-owned registry, not in Lambda
handler constants or environment variables.

Approved registry surfaces are:
- a DynamoDB table with tenant, account, and region-aware keys
- an SSM Parameter Store namespace when the mapping is operational config rather
  than a queryable tenant registry

Environment variables may name the registry table or SSM namespace. They must
not carry the mapped physical resource IDs themselves.

Registry records must make the lookup scope explicit. At minimum that scope is
the logical ID, environment, AWS account, and region. Tenant-scoped mappings
must include tenant ID or an explicit `GLOBAL` fallback, and callers must not
silently cross from one tenant scope to another.

## Resolution Contract

Lambda handlers resolve physical IDs through the registry at the service
boundary before calling AWS APIs or downstream platform services.

The handler contract is:
- accept or derive a logical ID
- resolve it with the current tenant, account, environment, and region context
- fail closed when no enabled mapping exists
- log the logical ID and mapping scope, not raw provider details unless needed
  for operator diagnostics

Direct use of physical IDs in handler code is a forbidden pattern. New resource
types such as guardrails, inference profiles, model aliases, runtime targets, or
gateway targets must adopt this resolver pattern before the first production
integration.

## Caching

Resolved mappings may be cached by Lambda execution environments for up to five
minutes. Cache files may live under `/tmp`; in-memory caches are also allowed.

Stale reads during that window are acceptable after a registry update. Operators
must plan rollouts with that propagation delay in mind. Handlers must still
revalidate expiry and must not treat cache presence as evidence that a disabled
or deleted mapping remains valid forever.

## Reference Implementation

The `platform-tools` table is the current reference implementation.

It uses the logical tool name as the stable key:
- `PK`: `TOOL#{toolName}`
- `SK`: `TENANT#{tenantId}` or `GLOBAL`

The current persisted record uses snake_case attributes and stores the
environment-specific physical targets alongside policy flags:
- `lambda_arn`
- `gateway_target_id`
- `enabled`
- `tier_minimum`

The Gateway request interceptor receives a tool call with a logical tool name,
looks up `TOOL#{toolName}` first for the tenant and then for `GLOBAL`, and only
allows the call when an enabled registry record satisfies tenant capability and
tier policy. The physical target attributes remain in the same record so
Gateway provisioning and future resolver paths use the same logical key instead
of duplicating provider IDs in code.

Future resolver implementations should copy that shape unless a resource type
needs a stronger key, for example `ACCOUNT#{accountId}#REGION#{region}` as part
of the sort key for account-local model or guardrail mappings.
