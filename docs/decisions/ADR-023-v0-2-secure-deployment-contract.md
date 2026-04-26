# ADR-023: v0.2 Secure Deployment Contract

## Status: Accepted
## Date: 2026-04-26
## Related: ADR-020, Issue #56 / TASK-907

## Context

ADR-020 approved collapsing the AgentCore Runtime topology from Dublin to London, but
it still framed the work as a migration from an existing deployment. That framing
kept rollback to `eu-west-1`, a deprovisioning hold, and runtime regional failover in
the target implementation plan.

For v0.2, there is no current staging or production deployment that must be preserved.
The platform can define a secure target state first, then implement directly toward
that state. Carrying old Dublin rollback behavior into a fresh baseline would preserve
complexity and public-runtime exceptions that the next release is meant to remove.

AWS documentation checked on 2026-04-26 shows AgentCore Runtime and core AgentCore
services available in Europe (London), and documents VPC connectivity for Runtime and
built-in tools with supported London AZ IDs.

## Decision

v0.2 adopts a fresh secure deployment baseline:

1. The platform has one serving VPC in `eu-west-2`.
2. AgentCore Runtime is deployed in `eu-west-2` for the serving path.
3. Staging and production AgentCore Runtime use `NetworkMode: VPC`.
4. `eu-west-1` is not a runtime fallback, rollback target, metric-stream source, or
   authorized tenant execution-role runtime region.
5. Runtime regional failover is deferred for v0.2. A future failover design must be
   separately approved and must not reintroduce Dublin by default.
6. CI/CD gates fail closed when the secure deployment posture cannot be proven for
   staging or production.
7. Unsupported or deferred features are recorded in the deployment contract rather
   than left implicit in runbooks or comments.

The reviewable contract is published in
[v0.2 Secure Deployment Contract](../contracts/v0-2-secure-deployment-contract.md).

## Consequences

### Positive

- Removes the old `NetworkMode: PUBLIC` staging/prod exception from the target state.
- Avoids implementing rollback machinery for a deployment that does not exist.
- Makes CI responsible for blocking insecure staging/prod deployment drift.
- Keeps v0.2 small enough to ship: one serving region, one platform VPC, no hidden
  regional fallback path.

### Negative

- v0.2 has no serving-path regional runtime failover. A London Runtime outage is a
  platform degradation event, not an automatic failover operation.
- Browser or Code Interpreter internet egress may need a later narrow design if agents
  require public-web browsing from VPC mode.
- Existing pre-v0.2 CDK, bootstrap, and runbooks still need follow-up implementation
  issues to converge on this contract.

### Neutral

- AgentCore Evaluations may remain a non-serving quality gate outside `eu-west-2` if
  AWS regional support requires it. That is not runtime fallback.
- The platform remains EU-only. No v0.2 serving data path is approved outside the EU.

## Alternatives Rejected

### Keep ADR-020 Migration Rollback

Rejected because v0.2 has no live staging or production deployment to preserve. The
rollback plan would add delivery work and keep Dublin in the mental model without
reducing production risk.

### Keep Dublin As A Dormant Runtime Fallback

Rejected because it preserves tenant execution-role permissions, SSM defaults, tests,
and runbooks for a region that the secure baseline is explicitly removing.

### Ship VPC Mode As Advisory Only

Rejected because staging and production network posture is a security invariant. CI
must block drift; documentation-only warnings are not enough once implementation lands.

## Required Follow-Up Issues

1. Update CDK to synthesize the v0.2 runtime in `eu-west-2` with `NetworkMode: VPC`
   for staging and production.
2. Remove `eu-west-1` from runtime defaults, tenant execution role region sets, metric
   streams, tests, and operator runbooks.
3. Replace runtime regional failover runbook paths with a v0.2 degradation procedure.
4. Add cfn-guard/CDK tests that fail staging/prod when runtime VPC posture or
   `eu-west-1` exclusion is violated.
5. Re-check AWS docs during implementation and record any service exception for
   Browser, Code Interpreter, or Evaluations explicitly.

## AWS Documentation References

- AgentCore supported regions:
  https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-regions.html
- AgentCore Runtime and built-in tools VPC connectivity:
  https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-vpc.html
