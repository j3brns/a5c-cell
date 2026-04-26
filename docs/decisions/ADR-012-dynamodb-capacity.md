# ADR-012: All DynamoDB Tables on On-Demand Capacity

## Status: Accepted (supersedes original split-capacity decision, 2026-04-25)

## Context
The original decision split tables by access pattern: on-demand for unpredictable
high-frequency tables (invocations, jobs, sessions) and provisioned with auto-scaling
for lower-volume config tables (tenants, agents, tools, ops-locks).

At current platform maturity, the provisioned config tables carry no auto-scaling
wiring (only static 5 RCU/WCU for most tables, 1 RCU/WCU for ops-locks). The
operational complexity of provisioned capacity without auto-scaling creates avoidable
throttling risk without meaningful cost savings. On-demand capacity eliminates that
risk, simplifies operations, and removes a class of capacity-planning errors for a
platform still in early growth.

CloudFormation update behavior: switching `BillingMode` from `PROVISIONED` to
`PAY_PER_REQUEST` is an in-place update. No table replacement occurs. Keys, GSIs,
table names, PITR, KMS encryption, and deletion protection are preserved.
Reference: https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/aws-resource-dynamodb-table.html

Rollback plan: CloudFormation update can be reversed by restoring `BillingMode:
PROVISIONED` with explicit capacity values. DynamoDB allows at most two billing-mode
switches per table per 24-hour period.

## Decision
All platform DynamoDB tables use on-demand (`PAY_PER_REQUEST`) capacity:
- platform-tenants
- platform-agents
- platform-tools
- platform-ops-locks
- platform-gateway-idempotency
- platform-invocations
- platform-jobs
- platform-sessions

No provisioned capacity is set on any table or GSI.

`platform-tenants` stores control-plane metadata only. High-frequency runtime
activity such as invocation counters, last-activity markers, session heartbeats,
or per-request status updates must not be written back to the tenant `METADATA`
record. Those signals belong in `platform-invocations`, `platform-sessions`,
CloudWatch metrics, or a dedicated aggregate path.

Hot partition mitigation on invocations: composite SK includes a random 2-character
jitter suffix for tenants exceeding 1000 requests/minute.

## Consequences
- No table can throttle due to capacity-planning errors
- On-demand cost scales with actual request volume; at low volume this is cheaper
  than the previous static 5 RCU/WCU minimum; at sustained high volume provisioned
  would be cheaper, but that threshold is not expected at current maturity
- Operations simplified: no capacity alarms, no auto-scaling policies to maintain
- Billing mode switch is safe to deploy (in-place CloudFormation update, no replacement)

## Alternatives Rejected
- Provisioned with auto-scaling on config tables: adds operational complexity
  (scaling policies, alarms, warm-up periods) for tables with unpredictable but
  generally low traffic; auto-scaling was never wired up in practice
- Keeping mixed capacity: inconsistency between tables with no observable benefit
  at current scale
