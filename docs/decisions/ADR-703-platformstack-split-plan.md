# ADR-703: PlatformStack Split Plan — Storage, Compute, and SPA Extraction

## Status: Proposed
## Date: 2026-04-24
## Issue: #25 (TASK-203)

## Context

`PlatformStack` (`platform-core-{env}`) currently owns all control-plane resources in
eu-west-2: eight DynamoDB tables, AppConfig configuration, eleven Lambda functions,
multiple SQS queues, a Step Functions state machine, EventBridge rules, an S3 results
bucket, the SPA CloudFront distribution and backing S3 bucket, the REST API Gateway,
WAF, and AgentCore Gateway. This concentrates blast radius, makes deployments of
unrelated changes interdependent, and produces large CloudFormation change sets.

The CDK code already separates concerns at the module level (`platform-storage.ts`,
`platform-compute.ts`, `platform-spa.ts`, `platform-api.ts`, `platform-waf.ts`,
`platform-gateway.ts`), but all modules still deploy into the same CloudFormation stack.
Converting the logical modules into separate CDK stacks delivers:

- Independent deployment of storage, compute, and SPA resources
- Smaller, reviewable CloudFormation change sets per domain
- Cleaner IAM blast radius per stack executor role
- Explicit cross-domain dependency contracts (visible as CloudFormation exports or SSM refs)

The constraint from the issue is: **no big-bang refactor**. Migration must be safe for
production. This document records the design decision, stable-name inventory, risk
analysis, rollback strategy, and implementation sequencing.

## Current Stack Inventory

### `platform-core-{env}` resources (all in one stack today)

**Storage** (`platform-storage.ts`):

| Resource | Physical Name | Type |
|----------|--------------|------|
| TenantsTable | `platform-tenants` | DynamoDB (provisioned, deletion-protected) |
| AgentsTable | `platform-agents` | DynamoDB (provisioned, deletion-protected) |
| ToolsTable | `platform-tools` | DynamoDB (provisioned, deletion-protected) |
| OpsLocksTable | `platform-ops-locks` | DynamoDB (provisioned+TTL, deletion-protected) |
| GatewayIdempotencyTable | `platform-gateway-idempotency` | DynamoDB (on-demand+TTL, deletion-protected) |
| InvocationsTable | `platform-invocations` | DynamoDB (on-demand+TTL, deletion-protected) |
| JobsTable | `platform-jobs` | DynamoDB (on-demand+TTL+stream, deletion-protected) |
| SessionsTable | `platform-sessions` | DynamoDB (on-demand+TTL, deletion-protected) |
| AppConfigApplication | `platform-config-{env}` | AppConfig app |
| AppConfigEnvironment | `{env}` | AppConfig env |
| CapabilityProfile | `tenant-capabilities` | AppConfig profile |
| CapabilityDeploymentStrategy | `tenant-capabilities-linear-{env}` | AppConfig strategy |

**Compute** (`platform-compute.ts`):

| Resource | Physical Name | Type |
|----------|--------------|------|
| tenantMgmtFn | `platform-core-{env}-tenant-mgmt` | Lambda (arm64) |
| webhookRegistryFn | `platform-core-{env}-webhook-registry` | Lambda |
| agentRegistryFn | `platform-core-{env}-agent-registry` | Lambda |
| adminOpsFn | `platform-core-{env}-admin-ops` | Lambda |
| bridgeFn | `platform-core-{env}-bridge` | Lambda (15-min timeout) |
| webhookDeliveryFn | `platform-core-{env}-webhook-delivery` | Lambda |
| bffFn | `platform-core-{env}-bff` | Lambda |
| authoriserFn | `platform-core-{env}-authoriser` | Lambda |
| requestInterceptorFn | `platform-core-{env}-interceptor-request` | Lambda |
| responseInterceptorFn | `platform-core-{env}-interceptor-response` | Lambda |
| billingFn | `platform-core-{env}-billing` | Lambda |
| tenantProvisionerFn | `platform-core-{env}-tenant-provisioner` | Lambda (internal) |
| webhookDeliveryRetryQueue | CDK-generated name | SQS |
| webhookDeliveryRetryDlq | CDK-generated name | SQS |
| {fn}Dlq (×12) | CDK-generated names | SQS |
| TenantProvisioningStateMachine | `platform-tenant-provisioning-{env}` | Step Functions |
| TenantCreatedRule | `platform-tenant-created-{env}` | EventBridge rule |
| TenantProvisioningCompletedRule | `platform-tenant-provisioning-completed-{env}` | EventBridge rule |
| DailyBillingRule | CDK-generated name | EventBridge rule |
| ResultsBucket | `platform-results-{env}` | S3 |
| ScopedTokenSigningKeySecret | `platform/{env}/gateway/scoped-token-signing-key` | Secrets Manager |
| BridgeLiveAlias | `live` on bridgeFn | Lambda alias |
| AuthoriserLiveAlias | `live` on authoriserFn | Lambda alias (PC=10) |
| BridgeCanaryDeploymentGroup | CDK-generated | CodeDeploy |

**SPA** (`platform-spa.ts` via `PlatformSpa` construct):

| Resource | Physical Name | Type |
|----------|--------------|------|
| SpaBucket | CDK-generated name | S3 |
| SpaLogBucket | `platform-spa-logs-{env}` | S3 |
| SpaDistribution | CDK-generated ID | CloudFront |
| SpaOriginAccessControl | `{stackName}-spa-oac` | CloudFront OAC |
| SpaRouteRewriteFunction | CDK-generated | CloudFront Function |
| SpaCspResponseHeadersPolicy | `{stackName}-spa-security-headers` | CloudFront Policy |

**API, WAF, Gateway** (`platform-api.ts`, `platform-waf.ts`, `platform-gateway.ts`):
- REST API Gateway (regional, with usage plans, access logging)
- WAF WebACL (`REGIONAL`, associated with API)
- AgentCore Gateway (PolicyEngine + Gateway CfnResources + interceptor wiring)

### SSM parameters written by `platform-core-{env}`

| Path | Owner module | Consumers |
|------|-------------|-----------|
| `/platform/core/{env}/results-bucket-arn` | PlatformStack | tenantMgmtFn env |
| `/platform/core/{env}/bridge-lambda-role-arn` | PlatformStack | operational reference |
| `/platform/{env}/config/appconfig-app-id` | platform-storage | bridgeFn, authoriserFn, requestInterceptorFn, adminOpsFn |
| `/platform/{env}/config/appconfig-env-id` | platform-storage | same |
| `/platform/{env}/config/appconfig-capability-profile-id` | platform-storage | same |
| `/platform/spa/{env}/bucket-name` | PlatformSpa | deployment pipeline |
| `/platform/spa/{env}/distribution-id` | PlatformSpa | deployment pipeline |
| `/platform/spa/{env}/domain-name` | PlatformSpa | operational reference |

### CloudFormation outputs (overridden logical IDs in `platform-core-{env}`)

| Output key | Logical ID override | Consumer |
|------------|---------------------|---------|
| SpaBucketName | `SpaBucketName` | deployment scripts, ObservabilityStack cross-ref |
| SpaDistributionId | `SpaDistributionId` | deployment scripts |
| SpaDomainName | `SpaDomainName` | operational reference |
| BridgeCanaryPolicy | (none) | informational only |
| AgentCoreGatewayPolicyMode | (none) | informational only |

### Cross-stack references from `ObservabilityStack` → `platform-core-{env}`

ObservabilityStack (`app.ts:107–127`) receives these props from `platformStack`:
`api`, `apiWebAcl`, `spaDistribution`, `bridgeFn`, `bffFn`, `authoriserFn`,
`requestInterceptorFn`, `responseInterceptorFn`, `tenantsTable`, `agentsTable`,
`invocationsTable`, `jobsTable`, `sessionsTable`, `toolsTable`, `opsLocksTable`,
`billingFn`, `dlqs`.

CDK resolves these as CloudFormation Export/Import pairs with auto-generated names
(hash-based). When resources move to new stacks, ObservabilityStack must be updated
simultaneously; the old exports must be removed from `platform-core-{env}`.

## Decision

### 1. Target stack topology

| Stack | Existing name? | Resources | Deploy order |
|-------|---------------|-----------|-------------|
| `platform-network-{env}` | yes | VPC, subnets, endpoints (unchanged) | 1 |
| `platform-identity-{env}` | yes | OIDC, Entra config (unchanged) | 2 |
| **`platform-storage-{env}`** | new | DynamoDB tables, AppConfig, bootstrap SSM | 3 |
| **`platform-spa-{env}`** | new | S3 SPA bucket + logs, CloudFront, CSP headers | 3 |
| **`platform-core-{env}`** | keep name | Compute (Lambdas, SQS, SFN, EB), results S3, API, WAF, Gateway, secrets | 4 |
| `platform-edge-security-{env}` | yes | us-east-1 CloudFront WAF (unchanged) | 3 |
| `platform-tenant-{tenantId}-{env}` | yes | per-tenant (unchanged) | 5 |
| `platform-observability-{env}` | yes | dashboards, alarms (updated cross-refs) | 6 |
| `platform-agentcore-{env}` | yes | Runtime config eu-west-1 (unchanged) | 6 |

`platform-core-{env}` **retains its name** so that all Lambda function names remain
unchanged (they embed `${this.stackName}` today). No `functionName` override patches
are required for the compute resources that stay in this stack.

The SPA resources leave `platform-core-{env}` and move to `platform-spa-{env}`.
The storage resources leave `platform-core-{env}` and move to `platform-storage-{env}`.

### 2. Dependency contracts between new stacks

`platform-compute-props` (passed from storage to core at CDK synth) is currently
function-internal. After the split, `platform-storage-{env}` must publish its
resource identifiers so `platform-core-{env}` can consume them.

**Chosen mechanism: SSM Parameter Store** (already the pattern in this codebase).

`platform-storage-{env}` writes parameters to SSM (all already exist — see inventory
above). `platform-core-{env}` reads them at CDK synth time using
`ssm.StringParameter.valueFromLookup()` OR at Lambda runtime from the existing
`APPCONFIG_APPLICATION_ID` / `TENANTS_TABLE_NAME` environment variable chain.

Since all Lambda environment variables are already set to literal table names (e.g.
`platform-tenants`) and AppConfig IDs are read from SSM, no new runtime discovery is
needed. The only CDK-synth-time cross-stack data needed is the DynamoDB table ARNs for
IAM grant statements — these can be reconstructed from the known table names using
`dynamodb.Table.fromTableName()`.

`platform-spa-{env}` must publish its CloudFront distribution domain name (for CORS
configuration in PlatformApi) and SPA bucket name (for deployment pipeline). Both are
already published to SSM (`/platform/spa/{env}/distribution-id`,
`/platform/spa/{env}/bucket-name`). PlatformApi reads `spaAllowedOrigin` at CDK synth
time; after the split this becomes `ssm.StringParameter.valueFromLookup('/platform/spa/{env}/domain-name')`.

### 3. Stable-name inventory — must not change

The following physical names and paths must remain unchanged through migration:

**DynamoDB table names** (read by Lambda env vars at runtime):
`platform-tenants`, `platform-agents`, `platform-tools`, `platform-ops-locks`,
`platform-gateway-idempotency`, `platform-invocations`, `platform-jobs`, `platform-sessions`

**Lambda function names** (stay in `platform-core-{env}` — no change):
`platform-core-{env}-bridge`, `platform-core-{env}-authoriser`, and all other
`platform-core-{env}-*` names.

**Step Functions state machine name**:
`platform-tenant-provisioning-{env}`

**EventBridge rule names**:
`platform-tenant-created-{env}`, `platform-tenant-provisioning-completed-{env}`

**S3 bucket name** (SPA log bucket):
`platform-spa-logs-{env}`

**AppConfig names**:
`platform-config-{env}` (app), `{env}` (env), `tenant-capabilities` (profile),
`tenant-capabilities-linear-{env}` (strategy)

**SSM paths** (all paths listed in the inventory above must remain unchanged):
Parameters are purely by path and value; the owning stack changes but the path does not.

**Secrets Manager secret name**:
`platform/{env}/gateway/scoped-token-signing-key`

**S3 bucket name** (results):
`platform-results-{env}` (explicit name in `platform-stack.ts:228`)

## Resource Replacement Risk Analysis

### SPA extraction (`platform-spa-{env}`)

| Resource | Risk | Reason | Mitigation |
|----------|------|--------|-----------|
| SpaBucket | **MEDIUM** | No explicit name; CDK assigns one. Extraction creates a NEW bucket with a new CDK-generated name. | After extraction, re-deploy SPA assets to new bucket; update CloudFront origin. The old bucket in `platform-core-{env}` is removed with `RemovalPolicy.RETAIN`. |
| SpaLogBucket | **LOW** | Has explicit name `platform-spa-logs-{env}`. CloudFormation will try to create a bucket with that name while the old one still exists → conflict. | Remove from old stack with `RETAIN` first; then import into new stack OR use `Bucket.fromBucketName()` in new stack (read-only reference). |
| SpaDistribution | **LOW** | No explicit name. New stack creates a new distribution. Old one retained or deleted after DNS cutover. Custom domain stays the same (re-wired to new distribution). | Two-step: new distribution live + DNS cutover + old distribution deletion. Traffic is zero-downtime if custom domain or SPA domain in SSM is updated before deleting old distribution. |
| CloudFront Function / OAC / ResponseHeadersPolicy | **LOW** | Recreating these is trivial (no data, no external deps). | Standard replacement. |

### Storage extraction (`platform-storage-{env}`)

| Resource | Risk | Reason | Mitigation |
|----------|------|--------|-----------|
| DynamoDB tables | **HIGH — data-bearing, deletion-protected** | Moving logical ownership from `platform-core-{env}` to `platform-storage-{env}` requires CloudFormation resource import. If done incorrectly, CloudFormation creates new tables (fail: names conflict) or deletes existing tables (fail: deletion protection). | All tables already have `removalPolicy: RETAIN`. Correct sequence: remove from `platform-core-{env}` → deploy (tables retained) → `cdk import` into `platform-storage-{env}`. Use `overrideLogicalId()` to preserve construct logical IDs. |
| DynamoDB jobs table stream ARN | **MEDIUM** | `webhookDeliveryFn` EventSourceMapping uses `jobsTable.tableStreamArn` — a CloudFormation `!GetAtt` that cannot be computed from the table name alone (stream labels are time-based). | Export `jobsTable.tableStreamArn` as a `CfnOutput` from `platform-storage-{env}`; import via `Fn.importValue()` in `platform-core-{env}`. This is a hard CFN export dependency and must be treated as stable (renaming the output key without coordination breaks the import reference). |
| AppConfig app/env/profile + deployment resources | **MEDIUM** | Physical IDs are CloudFormation-generated. Moving stacks changes the physical IDs, which changes the `APPCONFIG_APPLICATION_ID` / `APPCONFIG_ENVIRONMENT_ID` / `APPCONFIG_PROFILE_ID` environment variables injected into bridgeFn, authoriserFn, and requestInterceptorFn at CDK synth time, and the IAM policy ARNs for those same functions. | Write AppConfig IDs to SSM (already done) and resolve them via `ssm.StringParameter.valueFromLookup()` in `platform-core-{env}`. **Ordering**: `platform-storage-{env}` must deploy before `platform-core-{env}` synths. First-deploy on a blank environment requires explicit CI pipeline ordering. |
| SSM parameters | **NONE** | Parameters are by path. New stack writes same paths with same values. | No action needed; paths are stable. |

### Compute stays in `platform-core-{env}` — no extraction risk

All Lambda functions, SQS queues, Step Functions, and EventBridge resources that are
part of "compute" remain in `platform-core-{env}`. Their logical IDs and physical names
do not change. IAM policies, CodeDeploy deployment groups, and Lambda alias ARNs are
all stable.

The only change to `platform-core-{env}` compute resources after the split:
- DynamoDB table objects injected into compute are now resolved via
  `dynamodb.Table.fromTableName()` (from `platform-storage-{env}`) instead of direct
  construct references from `createPlatformStorage()`.
- AppConfig IDs are resolved from SSM lookup (already written by storage stack).

This is a CDK/synth-time change only. No Lambda runtime environment variables change.

## Cross-Stack Reference Strategy

| Dependency | Today | After split | Note |
|-----------|-------|------------|------|
| Compute → DynamoDB table names | Direct construct ref (`.tableName`) | `dynamodb.Table.fromTableName()` using hardcoded table name constant | Safe: names are stable constants |
| Compute → DynamoDB table ARNs (for IAM grants) | Direct construct ref (`.tableArn`) | `dynamodb.Table.fromTableArn()` constructed from known table name, `stack.region`, `stack.account` | Safe: ARN is deterministic from the name |
| Compute → AppConfig IDs (env vars + IAM ARNs) | Direct construct ref (`.ref`) injected at CDK synth | `ssm.StringParameter.valueFromLookup()` from existing SSM paths at CDK synth | **Ordering constraint**: `platform-storage-{env}` must be deployed before synthing `platform-core-{env}` so SSM holds the live IDs. CDK synth will fail on a blank environment if SSM is absent. |
| Compute → jobsTable stream ARN (EventSourceMapping) | Direct construct ref (`.tableStreamArn`) — CloudFormation `!GetAtt` | CloudFormation export from `platform-storage-{env}` (`CfnOutput` for `jobsTable.tableStreamArn`); `platform-core-{env}` imports via `Fn.importValue()` | **Hard CFN export dependency**: stream ARN is not computable from table name alone (stream label is time-based). Only reliable path is CFN export + import. |
| Compute → AppConfig extension ARN | Hardcoded in `platform-compute.ts` | Unchanged | — |
| API → SPA allowed origin | Direct construct ref (`platformSpa.spaAllowedOrigin`) | `ssm.StringParameter.valueFromLookup('/platform/spa/{env}/domain-name')` | Ordering constraint: `platform-spa-{env}` must deploy before `platform-core-{env}` synth |
| Observability → all resources | CDK cross-stack CFN exports (auto-named) | Updated direct construct refs from new stacks in `app.ts` | CDK manages export lifecycle automatically |

ObservabilityStack props in `app.ts` will reference the new stack objects
(`platformStorageStack`, `platformSpaStack`, `platformCoreStack`) instead of a single
`platformStack`. Because these are direct CDK object references (resolved at synth),
CDK generates new CloudFormation Export names pointing to the new stacks. The old
`platform-core-{env}` exports are automatically removed.

### Deployment ordering after split (mandatory)

```
1. platform-storage-{env}   deploy  ← writes AppConfig IDs to SSM
2. platform-spa-{env}       deploy  ← writes SPA domain name to SSM
3. platform-core-{env}      synth   ← reads SSM for AppConfig IDs + SPA domain
                            deploy
4. platform-observability-{env} deploy
```

For first-deploy on a blank environment, SSM parameters will be absent before
step 1. CDK's `ssm.StringParameter.valueFromLookup()` returns a dummy token when
the parameter is absent, causing `cdk synth` to succeed but producing incorrect
Lambda env vars. Mitigation: enforce in CI that `platform-storage-{env}` and
`platform-spa-{env}` deploy jobs gate `platform-core-{env}` deploy.

## Rollback Strategy Per Boundary

### SPA rollback

Rollback trigger: new CloudFront distribution misbehaves, SPA assets not deployed
to new bucket.

Strategy: DNS alias still points to old distribution (custom domain not yet cut over),
or CloudFront distribution domain name in SSM still points to old distribution. No
data risk. Roll back by:
1. Remove `platform-spa-{env}` stack (all resources are new; old ones retained).
2. Update PlatformApi CORS config to point back to old SPA origin (SSM path unchanged,
   old distribution is still live).

No operator action required on DynamoDB or tenant data.

### Storage rollback

Rollback trigger: `cdk import` fails mid-flight, AppConfig ID mismatch causes Lambda
startup failures, or deployment validation fails.

Strategy:
1. `platform-storage-{env}` deploy is aborted or rolled back in CloudFormation; DynamoDB
   tables remain in `platform-core-{env}` (import is atomic in CloudFormation — either
   all resources import or none do).
2. Lambda env vars still point to SSM paths that were written by the original storage
   stack. SSM values are unchanged.
3. `platform-core-{env}` retains the original `createPlatformStorage()` call until
   storage split is confirmed live.

Two-phase rollback boundary: during the split, `platform-core-{env}` should retain
read-only references (`Table.fromTableName()`) alongside the original storage module
for a minimum of one full deployment cycle before the original storage code is removed.

### Compute rollback

Compute stays in `platform-core-{env}`; no rollback required for compute extraction
since no compute resources are being moved. The compute change is limited to how
DynamoDB table objects are resolved in CDK. This change is fully reversible by
reverting to direct construct refs if the storage split is rolled back.

## Sequencing vs VPC Opt-In (ADR-020)

**Recommendation: PlatformStack split precedes VPC opt-in.**

ADR-020 targets eu-west-1 runtime region collapse (AgentCore Runtime moved to
eu-west-2). The changes in ADR-020 affect `AgentCoreStack` and `NetworkStack`
(adding VPC infrastructure in eu-west-2 for the Runtime). None of these overlap
with the `platform-storage`, `platform-spa`, or `platform-core` compute changes
in this ADR.

The stack split is organizational work in eu-west-2 control-plane stacks; VPC opt-in
is network posture work for the runtime path. They are orthogonal and can proceed
concurrently without conflict, but the split should not block on ADR-020 completion.

If ADR-020 implementation begins before this split completes, the two branches can
merge cleanly because they modify different CDK files and different stacks.

## Implementation Phases

### Phase 1: SPA extraction (lowest risk, stand-alone)

1. Create `platform-spa-{env}` CDK stack in `infra/cdk/lib/platform-spa-stack.ts`.
   Move the `PlatformSpa` construct invocation from `PlatformStack` to the new stack.
2. Rename the construct-generated names to include the new stack name
   (OAC, response headers policy already use `${cdk.Stack.of(this).stackName}`).
3. Update `app.ts`: instantiate `PlatformSpaStack` before `PlatformStack`.
   Pass `spaStack.spaAllowedOrigin` to `PlatformStack` via SSM lookup or direct prop.
4. Deploy `platform-spa-{env}`. New S3 bucket created. Deploy SPA assets to new bucket.
   Verify new CloudFront distribution serves assets.
5. Update DNS/custom domain (or SSM domain-name param) to point to new distribution.
6. Update `platform-core-{env}` to remove SPA resources (`cdk deploy`).
   Old SpaBucket and SpaDistribution removed (SpaBucket: `RETAIN`; SpaLogBucket: retain
   and import into new stack or leave as orphan).
7. Run `make validate-local` + `make pre-validate-session` + deploy validation.

Follow-on issue: **`platform-spa-stack` extraction** (Phase 1).

### Phase 2: Storage extraction (highest care required)

All DynamoDB tables already carry `removalPolicy: cdk.RemovalPolicy.RETAIN` in the
current code. No pre-flight removal-policy update is needed.

`cdk import` requires that a resource is NOT owned by any CloudFormation stack at the
time of import. The correct sequence is: remove from old stack (retain) → deploy → then
import into new stack. Steps 1–5 follow this ordering.

1. Capture current DynamoDB logical IDs: run `cdk synth platform-core-{env}` and extract
   the logical IDs for all eight `AWS::DynamoDB::Table` resources from the synthesized
   template (`cdk.out/platform-core-{env}.template.json`).
2. Create `platform-storage-{env}` CDK stack in `infra/cdk/lib/platform-storage-stack.ts`.
   Move `createPlatformStorage()` to the new stack. Add `cfnTable.overrideLogicalId('...')`
   to each table construct using the logical IDs captured in step 1. Add a `CfnOutput`
   exporting `jobsTable.tableStreamArn` for the webhookDelivery EventSourceMapping.
3. Remove the `createPlatformStorage()` call from `PlatformStack`. Update
   `platform-core-{env}` compute module to resolve tables via
   `dynamodb.Table.fromTableName()` + `dynamodb.Table.fromTableArn()`, AppConfig IDs via
   `ssm.StringParameter.valueFromLookup()`, and the jobs table stream ARN via
   `cdk.Fn.importValue('...')` from the `platform-storage-{env}` export.
4. Run `cdk diff platform-core-{env}` — tables must show as **removed (retain)**, not
   replaced or deleted. Abort and fix if any replacement is shown.
5. Deploy `platform-core-{env}`. Tables are removed from CloudFormation ownership but
   the physical DynamoDB resources are retained.
6. Run `cdk import platform-storage-{env}` with the resource mapping derived from
   step 1. CloudFormation imports the existing tables as new logical resources in the
   new stack without deletion or recreation.
7. Run `cdk deploy platform-storage-{env}` to finalize SSM parameter writes
   (AppConfig IDs now published by new stack).
8. Re-synth and re-deploy `platform-core-{env}` so Lambda env vars and IAM ARNs pick
   up the live AppConfig IDs from SSM.
9. Validate: `make test-*`, `make validate-local`. Confirm Lambda env vars unchanged
   (table names stable; AppConfig IDs match new storage stack values).
10. Monitor DynamoDB table metrics for 15 minutes post-deployment to confirm no
    throttling or access disruption.

Follow-on issue: **`platform-storage-stack` extraction** (Phase 2).

### Phase 3: Compute logical split (documentation only — no stack move)

Compute resources remain in `platform-core-{env}`. The `platform-compute.ts` module
already provides logical separation. No new stack is created in Phase 3.

If a future decision requires a dedicated `platform-compute-{env}` stack (e.g., for
separate IAM executor role), Phase 3 can proceed with:
- Explicit `functionName` overrides on all Lambda functions using the current
  `platform-core-{env}-{suffix}` naming pattern.
- Explicit `queueName` overrides on all SQS queues (DLQs included) where cross-stack
  event source mappings need stable ARNs.
- Same `cdk import` approach as storage.

Phase 3 is not required to deliver the value of the split. It is recorded here as a
known-safe future path.

Follow-on issue (if Phase 3 accepted): **`platform-compute-stack` extraction** (Phase 3,
optional).

## Follow-On Issues

Upon acceptance of this plan, create the following GitLab issues:

| Issue | Title | Seq | Depends on |
|-------|-------|-----|-----------|
| TBD | TASK-203a: Extract SPA to platform-spa-stack | after #25 | #25 |
| TBD | TASK-203b: Extract Storage to platform-storage-stack | after 203a | 203a |
| TBD | TASK-203c: (Optional) Extract Compute to platform-compute-stack | after 203b | 203b |

## Consequences

Accepted:
- Independent deployability of SPA and storage layers
- Smaller CloudFormation change sets, better blast radius isolation
- Clear ownership boundary for each domain visible in CDK code and CloudFormation

Accepted trade-offs:
- Cross-stack SSM lookups add ~1 CDK synth step (SSM lookup at synth time, not runtime)
- ObservabilityStack `app.ts` wiring becomes more verbose (three source stacks instead of one)
- DynamoDB `cdk import` requires a careful pre-flight to map logical IDs

Rejected:
- **Renaming `platform-core-{env}`** to `platform-compute-{env}`: changes all Lambda
  function names; breaks CodeDeploy, IAM policies, runbook references. Risk exceeds value.
- **Blue-green stack creation**: standing up a parallel full stack in production is
  operationally complex and doubles cost during migration window.
- **Single big-bang migration**: violates the non-negotiable "no big-bang" constraint
  and concentrates risk into one deployment event.
