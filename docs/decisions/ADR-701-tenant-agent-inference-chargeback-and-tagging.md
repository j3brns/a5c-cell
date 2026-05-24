# ADR-701: Tenant, Agent, and Inference Chargeback With Tenant-Instantiation Tags

## Status: Proposed
## Date: 2026-04-03

## Context
The platform already has partial foundations for tenant-aware billing and
attribution:
- tenant provisioning creates per-tenant stacks and currently applies minimal
  stack tags during create
- invocation records already capture `tenantId`, `appId`, `agentName`,
  `agentVersion`, token counts, and runtime region
- the daily billing pipeline already aggregates invocation token counts to
  produce tenant-level billing summaries

However, the current design does not yet declare a single end-to-end chargeback
strategy spanning:
- tenant infrastructure allocation
- per-agent invocation attribution
- LLM inference attribution
- future downstream gateway or proxy-based model invocation

The architecture also has an important near-term default:
- inference is expected to run in the platform account path
  by default
- downstream inference through a dedicated model gateway may be introduced later
  as a phased evolution

That means the platform needs a design that is:
- immediately usable for the current default where `a5c-cell` is the direct
  inference owner
- explicitly prepared for a later phase where a downstream gateway becomes the
  authoritative inference meter

Without an explicit decision, the platform risks:
- tenant tags that are too weak for finance or AWS-native cost allocation
- control-plane records that cannot explain model-level spend
- future migration friction when inference moves behind a downstream gateway
- ambiguous source-of-truth boundaries between infrastructure chargeback and
  runtime inference chargeback

## Current State Assessment
The platform is **partially catered for already**, but not fully:

1. **Already present**
   - Tenant invocation records include tenant, app, agent, token, and runtime
     dimensions in the invocation ledger
   - Tenant billing is computed from invocation records
   - Tenant provisioning already applies a minimal tag set at stack creation

2. **Missing or not yet declared**
   - a mandatory chargeback tag set across tenant-instantiated resources
   - a formal distinction between infrastructure tags and runtime usage ledgers
   - a required persistence model for `modelId` / `inferenceProfileId`
   - a phased design for later downstream inference metering

## Decision
The platform adopts a two-layer chargeback model:

1. **Infrastructure chargeback uses AWS resource tags**
   - Tags are mandatory on tenant-instantiated infrastructure
   - Tags support CUR / Cost Explorer / account-level allocation and operator
     reporting

2. **Inference chargeback uses the invocation ledger**
   - The control plane invocation record is the canonical billing and audit record
   - It must carry tenant, app, agent, and model attribution dimensions

3. **Current default**
   - While inference remains in the platform account path,
     `a5c-cell` is both:
     - the attribution owner
     - the authoritative inference meter

4. **Future phased evolution**
   - If inference later moves to a downstream Bedrock proxy or model gateway,
     the platform keeps `a5c-cell` as the canonical billing-record owner
   - The downstream gateway may become the authoritative inference meter
   - That future mode must use a joined-ledger contract rather than replacing
     the control-plane billing record

5. **Audit and observability invariants remain inherited from ADR-016**
   - chargeback-related logs, metrics, traces, and metering envelopes must
     preserve `tenantid` and `appid`
   - downstream metering integration must not create a second unaudited billing
     path or weaken the control-plane tenant boundary

## Mandatory Chargeback Dimensions
The following dimensions are mandatory for runtime chargeback:
- `tenantId`
- `appId`
- `agentName`
- `modelId` or `inferenceProfileId`

The following are strongly recommended and should be persisted when available:
- `agentVersion`
- `invocationId`
- `sessionId`
- `runtimeRegion`
- `gatewayAccountId` when inference is performed by a downstream gateway
- `gatewayRegion` when inference is performed by a downstream gateway

## Canonical Persisted Field Contract
The invocation ledger and any downstream metering integration must use the
following canonical persisted fields.

Identity and attribution:
- `tenant_id`
- `app_id`
- `agent_name`
- `agent_version`
- `invocation_id`
- `session_id`
- `runtime_region`

Requested inference target:
- `requested_model_id`
- `requested_inference_profile_id`

Resolved authoritative inference target:
- `resolved_model_id`
- `resolved_inference_profile_id`

Usage fields:
- `estimated_input_tokens`
- `estimated_output_tokens`
- `authoritative_input_tokens`
- `authoritative_output_tokens`
- `usage_quality`

Downstream gateway provenance when applicable:
- `metering_source`
- `gateway_account_id`
- `gateway_region`

Field semantics:
- `requested_*` reflects what the control plane or caller intended
- `resolved_*` reflects what actually executed and is authoritative for
  chargeback when present
- `usage_quality` must be one of `authoritative`, `estimated`, or `unknown`
- `metering_source` must identify the authoritative meter, such as
  `a5c-cell` or a named downstream gateway

## Usage Quality Model
The platform distinguishes between estimated and authoritative usage:
- `estimatedUsage` is provisional and may be used for admission control,
  internal quotas, or early UX
- `authoritativeUsage` is final and used for chargeback and billing

Estimated and authoritative usage must not overwrite each other.

If no authoritative usage is available, any fallback billing based on estimated
usage must:
- be explicitly marked degraded
- set `usageQuality = estimated`
- be treated as an exception path, not the steady state

## Tenant Instantiation Tagging Strategy
Mandatory tags must be applied as part of tenant instantiation for tenant-owned
or tenant-dedicated resources.

The mandatory base tag set is:
- `tenantid`
- `appid`
- `platform:environment`
- `platform:chargeback-scope`
- `platform:managed-by`

Required semantics:
- `tenantid`: canonical tenant identifier
- `appid`: canonical application identifier for the tenant-facing workload
- `platform:environment`: `dev`, `staging`, or `prod`
- `platform:chargeback-scope`: one of `tenant-dedicated`, `tenant-shared`,
  `platform-shared`
- `platform:managed-by`: control-plane provisioning system identifier

Recommended extension tags where supported:
- `platform:tier`
- `platform:owner-team`
- `platform:service`
- `platform:cost-center`

Mandatory-tag availability rules:
- tenant instantiation must not silently create tenant-dedicated infrastructure
  without the mandatory base tag set
- if `tenantid` is unavailable, provisioning fails
- if `appid` is unavailable for a tenant-dedicated create path, provisioning
  fails rather than inventing a placeholder
- shared platform resources that cannot truthfully carry a tenant/app tag must
  not be provisioned through a tenant-dedicated path; they must use an approved
  shared-resource path and `platform:chargeback-scope` value

## Tagging Rules
1. Tenant-instantiated stacks must apply the mandatory base tag set at create
   time.
2. Updates must preserve the mandatory tag set; missing mandatory chargeback
   tags are configuration drift.
3. Shared platform resources that cannot truthfully carry a single tenant tag
   must use `platform:chargeback-scope = platform-shared` or
   `tenant-shared` and rely on runtime ledgers for allocation.
4. Agent names must not be used as AWS infrastructure tags for every resource by
   default because many shared resources serve multiple agents; agent-level
   chargeback belongs primarily in the invocation ledger.

`platform:chargeback-scope` truth table:
- `tenant-dedicated`: resource is instantiated for exactly one tenant and may
  carry `tenantid` and `appid`
- `tenant-shared`: resource serves multiple tenants within a bounded service or
  account boundary and must not be allocated by tag alone
- `platform-shared`: resource is global/shared control-plane infrastructure and
  must not pretend to be tenant-owned for finance purposes

## Inference Metering Policy
### Phase 1: Current default in `a5c-cell`
- `a5c-cell` meters inference directly when it receives the authoritative model
  response
- invocation records must persist:
  - requested and resolved `modelId` or `inferenceProfileId` when applicable
  - authoritative input/output token counts
  - optional estimated usage kept separately if used

### Phase 2: Optional downstream gateway later
- the downstream gateway may become the authoritative inference meter
- `a5c-cell` remains the canonical billing-record owner
- the gateway must return or emit a metering envelope linked by `invocationId`
- the control plane persists the authoritative downstream usage on the
  invocation record

## Joined-Ledger Preparation
The design must be ready now for later downstream inference without requiring a
billing-model rewrite.

If a downstream gateway is introduced, the metering envelope should include:
- `invocationId`
- `tenantId`
- `appId`
- `agentName`
- `agentVersion` when available
- `modelId` or `inferenceProfileId`
- `authoritativeInputTokens`
- `authoritativeOutputTokens`
- `gatewayAccountId`
- `gatewayRegion`

This later gateway mode is additive and phased. It does not change the default
current position that `a5c-cell` performs direct inference and direct metering.

## Billing Semantics
The current tenant billing pipeline remains tenant-tier priced unless and until a
successor ADR explicitly approves model-sensitive or profile-sensitive billing.

That means:
- tenant invoices and budget enforcement may continue to use the current tier
  pricing path
- persisted `resolved_model_id` and `resolved_inference_profile_id` are still
  mandatory for attribution, optimization, audit, and future pricing evolution
- model/profile dimensions must not be omitted merely because current billing is
  still tier-priced

## Consequences
- The current platform can continue using the existing invocation-ledger billing
  path without waiting for a downstream gateway.
- Tenant instantiation gets a declared, finance-usable chargeback tag baseline.
- Agent-level chargeback is represented in runtime ledgers rather than forced
  onto inappropriate shared-resource tags.
- The platform is prepared for future downstream inference without changing the
  canonical billing record owner.
- Billing, audit, and AWS-native cost allocation each have a clear source of
  truth for the concerns they are actually good at answering.

## Implementation Notes
- Update tenant instantiation so the mandatory base tag set is provided on all
  create paths and preserved on update paths.
- Extend invocation records and billing transforms to persist the canonical
  chargeback field contract defined above.
- Keep estimated usage fields separate from authoritative usage fields.
- Where direct inference remains in `a5c-cell`, copy the authoritative metering
  pattern from the Bedrock-facing component that receives the model response
  rather than relying only on static estimates.
- Treat missing mandatory chargeback tags on tenant-dedicated resources as
  configuration drift that must be corrected rather than normalized away.

## Alternatives Rejected
- **Tags only for all chargeback**: insufficient for shared runtime paths and
  cannot represent per-agent or per-invocation inference allocation.
- **Invocation ledger only with no mandatory resource tags**: weak for AWS-native
  cost allocation, CUR analysis, and infrastructure showback.
- **Make downstream gateways the sole billing system of record**: breaks the
  existing control-plane audit and tenant-attribution boundary.
- **Delay the design until downstream inference exists**: creates avoidable
  migration friction and leaves current tenant instantiation under-specified.
