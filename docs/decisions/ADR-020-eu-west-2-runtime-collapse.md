# ADR-020: eu-west-2 Runtime Collapse — Removing the Dublin Zigzag

## Status: Superseded for v0.2 by ADR-023
## Date: 2026-04-24
## Supersedes: ADR-009

## Status Note (2026-04-26)

ADR-023 replaces this ADR's migration framing for v0.2. The approved v0.2 target is a
fresh secure deployment baseline: one `eu-west-2` platform VPC, AgentCore Runtime in
`eu-west-2`, `NetworkMode: VPC` for staging and production, no `eu-west-1` runtime
fallback, and fail-closed CI gates. This ADR remains useful as historical context for
why the Dublin zigzag is being removed, but its `eu-west-1` rollback and 14-day
deprovisioning plan are not part of the v0.2 target state.

## Context

ADR-009 placed AgentCore Runtime in eu-west-1 (Dublin) because eu-west-2 (London) did
not support AgentCore Runtime at the time. To preserve UK/EU data residency, all data
remained in eu-west-2, producing a "zigzag" on every agent invocation: Bridge Lambda
(London) → AgentCore Runtime (Dublin) → AgentCore Gateway (London). This added ~12ms
round-trip latency and required ongoing dual-region operational management.

ADR-009 contained an explicit successor clause (status note 2026-03-10):

> "AWS now offers AgentCore Runtime and Policy in eu-west-2 (London). This ADR
> remains the active platform deployment policy until a successor ADR explicitly
> approves a migration away from the current London-home / Dublin-runtime zigzag
> topology."

This ADR is that successor. It approves the topology collapse from Dublin-primary to
London-primary, defines the go/no-go gates that must pass before implementation begins,
and records the rollback plan and rejected alternatives.

## Current Topology (ADR-009)

```
eu-west-2 London   — home: all data, control plane, application services
eu-west-1 Dublin   — AgentCore Runtime (arm64 Firecracker), AgentCore Browser,
                     Code Interpreter, AgentCore metric stream → London
eu-central-1 Frankfurt — AgentCore Evaluations, AgentCore Policy (Cedar),
                         runtime failover target, shadow NetworkStack
```

Hot path on every invocation:
`Bridge Lambda eu-west-2 → sts:AssumeRole → Runtime eu-west-1 → Gateway eu-west-2`

NetworkMode is `PUBLIC` by explicit exception: VPC infrastructure exists only in
eu-west-2, not in eu-west-1. Moving Runtime to VPC mode would require designing and
deploying a dedicated eu-west-1 VPC (subnets, security groups, service endpoints).

## Decision

Collapse the primary runtime region from eu-west-1 (Dublin) to eu-west-2 (London).

Target topology:

```
eu-west-2 London   — home: all data, control plane, application services,
                     AND AgentCore Runtime (primary)
eu-central-1 Frankfurt — AgentCore Evaluations, AgentCore Policy, runtime failover
```

AgentCore Browser and Code Interpreter availability in eu-west-2 must be confirmed
as gate G-01 before migration. If either service is unavailable in eu-west-2, it
remains in eu-west-1 as a documented service exception with its own SSM-controlled
region parameter, reviewed as a separate follow-on decision.

## Alternatives Considered

### Option A — Do Nothing: Maintain ADR-009 Zigzag

Keep eu-west-1 as primary runtime with eu-central-1 as failover. No changes.

**Rejected because**: AWS has removed the original blocking reason (no eu-west-2 Runtime
support). Maintaining dual-region runtime complexity, ~12ms RTT overhead, cross-region
data transfer charges, and a permanent `NetworkMode: PUBLIC` exception in eu-west-1
is not justified when a simpler, lower-latency, single-region topology is available.

### Option B — Keep eu-west-1 Primary, Add eu-west-2 Failover

Retain Dublin as primary, add London as secondary failover rather than Frankfurt.

**Rejected because**: Running primary compute outside the home region while the home
region supports the service inverts the intended posture. The zigzag and its costs
remain. Frankfurt failover continuity is lost.

### Option C — Collapse to eu-west-2 London (this ADR's decision)

Move primary runtime to eu-west-2. Retain eu-central-1 as failover. See consequences below.

### Option D — Migrate Primary to eu-central-1 Frankfurt

Make Frankfurt the primary and London a failover.

**Rejected because**: Frankfurt is already the evaluation and failover region. Promoting
it to primary while London now supports Runtime inverts both the data-residency preference
and the existing operational posture for no benefit.

## Dimension Comparison

| Dimension | ADR-009 Dublin primary | This ADR: London primary |
|-----------|------------------------|--------------------------|
| Data residency | Data in eu-west-2; runtime compute in eu-west-1 | Data and runtime compute both in eu-west-2 |
| Invocation latency | ~12ms cross-region RTT added per call | RTT eliminated |
| Cross-region transfer cost | Charged on every Bridge ↔ Runtime ↔ Gateway round trip | Eliminated for runtime path |
| VPC posture | `PUBLIC` exception in eu-west-1; VPC requires dual-region design | VPC mode feasible with existing eu-west-2 VPC |
| Operational scope | Two runtime regions (eu-west-1 primary, eu-central-1 failover) | One primary (eu-west-2); eu-central-1 failover retained |
| Failover topology | Dublin → Frankfurt | London → Frankfurt (same mechanism) |
| CDK stack regions | AgentCoreStack in eu-west-1; all others in eu-west-2 | All stacks in eu-west-2 |
| Runtime quota pool | eu-west-1 quota applies | eu-west-2 quota applies (gate G-03) |
| Tenant execution role | Authorises eu-west-1 and eu-central-1 runtimes | Authorises eu-west-2 and eu-central-1 runtimes |
| Metric stream | eu-west-1 Runtime telemetry → eu-west-2 dashboards | Not required (same region); dashboards read local metrics |
| UK/EU compliance | Maintained (data always in eu-west-2) | Strengthened (compute now co-located with data) |

## Components Affected by Migration

| Component | Current region | After collapse | Notes |
|-----------|----------------|----------------|-------|
| AgentCore Runtime | eu-west-1 | eu-west-2 | Primary move |
| AgentCore Browser | eu-west-1 | eu-west-2 (or exception; gate G-01) | Availability must be confirmed |
| AgentCore Code Interpreter | eu-west-1 | eu-west-2 (or exception; gate G-01) | Availability must be confirmed |
| AgentCoreStack (CDK) | eu-west-1 | eu-west-2 | Stack region change |
| Runtime metric stream | eu-west-1 → eu-west-2 | Decommissioned (same-region metrics) | Simplification |
| Tenant execution role | Allows eu-west-1, eu-central-1 | Allows eu-west-2, eu-central-1 | IAM resource update |
| Bridge SSM default runtime-region | eu-west-1 | eu-west-2 | SSM parameter value update |
| Bridge SSM failover runtime-region | eu-central-1 | eu-central-1 (unchanged) | No change |
| NetworkMode | `PUBLIC` (exception) | `VPC` (enabled by gate G-05) | Removes exception |
| AgentCore Memory | eu-west-2 | eu-west-2 (unchanged) | Already home region |
| AgentCore Gateway | eu-west-2 | eu-west-2 (unchanged) | Already home region |
| AgentCore Identity | eu-west-2 | eu-west-2 (unchanged) | Already home region |
| AgentCore Policy (Cedar) | eu-central-1 | eu-central-1 (unchanged; gate G-04) | Confirm eu-west-2 availability before follow-on |

## Go/No-Go Gates

No implementation work (CDK, IAM, Bridge, SSM changes, or Runtime deprovisioning) may
begin until all gates below are evaluated and pass evidence is recorded in the
implementation issue.

| Gate | Description | Pass condition |
|------|-------------|----------------|
| G-01 | Browser and Code Interpreter availability | GA availability in eu-west-2 confirmed per AWS documentation; or documented service exception accepted in writing by operator if unavailable |
| G-02 | AgentCore Runtime GA in eu-west-2 | GA (not preview) status confirmed from AWS documentation; URL and date recorded |
| G-03 | Quota parity | eu-west-2 Runtime session concurrency quota ≥ current eu-west-1 in-use headroom; quota increase requested if needed before cutover |
| G-04 | AgentCore Policy (Cedar) eu-west-2 availability | Confirm GA status in eu-west-2 for Gateway authorization decisions; or confirm eu-central-1 remains sufficient for current Gateway Cedar evaluation path |
| G-05 | VPC design review | eu-west-2 VPC subnets, security groups, and required service endpoints reviewed for Runtime VPC mode; cfn-guard rule update scoped |
| G-06 | Tenant execution role update reviewed | IAM resource change (eu-west-1 → eu-west-2 in allowed runtime region set) peer-reviewed before apply; tests confirm eu-west-1-only role is denied after change |
| G-07 | Failover path tested | SSM `/platform/config/runtime-region` switched to `eu-central-1` in dev with eu-west-2 as primary; DynamoDB distributed lock and Bridge failover routing confirmed correct |
| G-08 | Rollback path validated | SSM revert to eu-west-1 restores prior routing end-to-end in dev before any prod cutover; EU data residency preserved throughout |
| G-09 | Observability continuity | CloudWatch dashboards and alarms confirmed operational for eu-west-2 Runtime telemetry; metric stream from eu-west-1 confirmed decommissioned without alarm or dashboard gaps |
| G-10 | AgentCoreStack deploy tested in dev | AgentCoreStack synthesizes and deploys cleanly in eu-west-2 dev account; no drift or orphan resources in eu-west-1 before staging promotion |

## Rollback Plan

The ADR-009 failover mechanism — SSM `/platform/config/runtime-region` with a
DynamoDB distributed lock — serves equally as the rollback control:

1. Set SSM `/platform/config/runtime-region` to `eu-west-1`.
   Bridge reads the cached value (60-second TTL); maximum propagation delay: 60s.
2. Confirm Bridge routes new invocations to eu-west-1.
3. Allow in-flight eu-west-2 sessions to complete or timeout before deprovisioning
   the eu-west-2 AgentCoreStack resources.

**Rollback window constraint**: The eu-west-1 AgentCoreStack must not be deprovisioned
until a minimum of 14 days after eu-west-2 cutover. The implementation issue must record
the deprovisioning decision explicitly and confirm gate G-08 (rollback tested) is still
valid at that point.

## Consequences

### Positive
- Eliminates ~12ms cross-region RTT from every agent invocation
- Reduces Bridge operational footprint to a single primary runtime region
- Enables Runtime `VPC` mode using existing eu-west-2 infrastructure, removing the
  `NetworkMode: PUBLIC` exception without requiring a cross-region VPC design
- Eliminates the cross-region Runtime metric stream; simplifies dashboard sourcing
- Eliminates cross-region data transfer charges on the Bridge → Runtime → Gateway path
- Strengthens data residency posture: Runtime compute co-located with all data stores

### Negative
- eu-central-1 Frankfurt remains the only failover target; simultaneous eu-west-2 and
  eu-central-1 unavailability has no third-region escape
- eu-west-2 Runtime quotas are not yet validated at platform scale; gate G-03 is
  required before cutover
- AgentCoreStack migration is a region-change deployment, not a standard update; must
  be tested thoroughly in dev before staging promotion
- If Browser or Code Interpreter are unavailable in eu-west-2, those services retain
  a eu-west-1 dependency; this partial exception must be tracked and reviewed separately

### Neutral
- Failover topology (primary → Frankfurt) is mechanically unchanged; only the primary
  region name changes in SSM and IAM
- ADR-001 consequences (arm64, aarch64-manylinux2014 cross-compilation, cold start
  300–800ms, AWS-managed auto-scaling) are unchanged — these are Runtime characteristics,
  not region-specific
- ADR-010 async pattern (add_async_task / complete_async_task) is unchanged

## AWS Documentation References

The following documentation must be consulted and the current state recorded in the
implementation issue before gates G-01 through G-04 are marked passed:

- AgentCore supported regions:
  https://docs.aws.amazon.com/bedrock/latest/userguide/bedrock-agentcore-supported-regions.html
- AgentCore Runtime service quotas:
  https://docs.aws.amazon.com/bedrock/latest/userguide/quotas.html
- AgentCore Runtime VPC network mode (for gate G-05):
  https://docs.aws.amazon.com/bedrock/latest/userguide/bedrock-agentcore-runtime-network.html

Current confirmed status (2026-03-10, per ADR-009 status note): AgentCore Runtime and
Policy are available in eu-west-2. Individual service availability for Browser and Code
Interpreter must be re-verified at implementation time from the supported-regions page.

## Required Follow-Up Issues

This ADR is decision-only. Implementation and gate validation are separate issues.

1. **Gate evaluation issue**: Evaluate G-01 through G-10 and record pass evidence.
   No implementation proceeds without this being completed.

2. **AgentCoreStack region migration**: CDK stack region change to eu-west-2; update
   SSM runtime-region default; update tenant execution role IAM resource; decommission
   eu-west-1 metric stream.

3. **Runtime VPC mode enablement** (after G-05 passes): Remove the `NetworkMode: PUBLIC`
   exception from CloudFormation metadata and update cfn-guard tests to enforce VPC mode.

4. **eu-west-1 AgentCoreStack decommission** (14-day hold after cutover): Explicit
   deprovisioning decision recorded in the implementation issue; gate G-08 re-validated.

5. **Browser and Code Interpreter exception review** (if G-01 passes for Runtime but not
   those services): Separate decision for partial eu-west-1 retention.
