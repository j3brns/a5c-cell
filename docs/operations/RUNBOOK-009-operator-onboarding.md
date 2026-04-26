# RUNBOOK-009: Operator Onboarding

## Purpose
Steps for a platform engineer to follow when a new operator joins.

## Prerequisites (platform engineer actions)
1. Add operator to Entra group: platform-operators
2. Confirm the operator has: uv, AWS CLI v2, platform repo access (read)
3. Verify operator does NOT have direct AWS console write access (read-only is acceptable)

## Day 1 Steps

### 1. Get access verified
```bash
# Operator logs in via Entra on the SPA admin view
# Should see: Platform.Operator role, platform health dashboard, and tenant usage dashboard access
# Should NOT see: tenant data, individual invocation content
```

### Tenant usage dashboard migration note
TenantStack no longer creates one CloudWatch dashboard per tenant and no longer exports
`DashboardName`. Use the shared dashboard exported from ObservabilityStack as
`TenantUsageDashboardName` instead. In a standard environment its dashboard name is
`platform-tenant-usage-platform-observability-{env}`.

Set the dashboard `tenantId` variable to the tenant id under investigation and set
`tenantTier` to the tenant's current tier. The tenant id is a text input so idle,
newly provisioned, or recently suspended tenants remain inspectable even when they have
not emitted recent API request metrics.

Repository audit for TASK-205 found no in-repo automation depending on the removed
TenantStack `DashboardName` output. External scripts should migrate to the shared
dashboard output before removing references to per-tenant dashboard names.

CloudWatch dashboard variable behavior was verified against AWS documentation:
[dashboard body variables](https://docs.aws.amazon.com/AmazonCloudWatch/latest/APIReference/CloudWatch-Dashboard-Body-Structure.html#CloudWatch-Dashboard-Properties-Variables-Structure),
[dashboard variables](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/cloudwatch_dashboard_variables.html),
and [search expression limits](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/search-expression-syntax.html).

### 2. Install CLI tools
```bash
git clone {repo-url}
cd platform
make bootstrap
# Only needs: uv and AWS CLI — not Docker or Node
```

### 3. Configure ops CLI
```bash
cp .env.example .env.local
# Set: API_BASE_URL, ENTRA_CLIENT_ID
make ops-login
# Fetches Entra JWT with Platform.Operator scope
# Stores in ~/.platform/credentials (TTL 1 hour)
```

### 4. Verify ops access
```bash
make ops-quota-report ENV=prod        # Should return quota data
make ops-billing-status ENV=prod      # Should return current billing status
make logs-bridge ENV=prod MINUTES=5   # Should return recent bridge telemetry
```

### 5. Read all runbooks (in order)
RUNBOOK-000 through RUNBOOK-009. Understand each trigger and response.
Complete a dry-run of RUNBOOK-001 (failover) in the dev environment only while the
pre-v0.2 runtime-failover path remains implemented.

## Success Criteria
Operator is considered onboarded when:
- They can complete the current runtime incident procedure in dev without assistance
- They can answer: "How do I identify the tenant driving the most quota pressure?"
  Use the parameterized CloudWatch tenant usage dashboard together with AgentCore concurrent-session metrics,
  tenant audit records, and usage records.
- They have NOT needed direct AWS console access for any of the above

## What Operators Cannot Do
Operators cannot:
- Access AWS console for write operations (read-only is acceptable for investigation)
- Directly modify DynamoDB records (use ops.py commands only)
- Delete tenants (Platform.Admin role required)
- Access agent invocation content (privacy boundary)
