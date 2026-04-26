# v0.2 Secure Deployment Contract

Status: target-state contract

Linked decision: [ADR-023](../decisions/ADR-023-v0-2-secure-deployment-contract.md)

This contract defines the secure v0.2 deployment target before the implementation
issues change CDK, CI, bootstrap, or runbooks. It is deliberately narrower than a
general migration plan: v0.2 is treated as a fresh secure baseline, not as a live
production migration from the old Dublin runtime topology.

## Scope

Applies to staging and production v0.2 deployments of the platform runtime path:

- platform VPC and runtime network posture
- AgentCore Runtime serving region
- CI/CD deployment gates
- operator expectations for unsupported or deferred runtime features

It does not change local development behavior by itself. Current code may still
carry pre-v0.2 defaults until the implementing issues land.

## Target Topology

| Area | v0.2 contract |
|---|---|
| Home region | `eu-west-2` |
| Platform VPC | one platform VPC in `eu-west-2` |
| AgentCore Runtime | primary runtime in `eu-west-2` |
| Runtime network mode | `VPC` in staging and production |
| Runtime fallback | no `eu-west-1` runtime fallback |
| Serving-path regional failover | deferred for v0.2 |
| AgentCore Gateway, Memory, Identity, Observability | `eu-west-2` |
| AgentCore Evaluations | non-serving quality gate; may remain outside `eu-west-2` if AWS regional support requires it |

## Non-Negotiable Deployment Rules

1. Staging and production must not deploy AgentCore Runtime in `PUBLIC` network mode.
2. Staging and production must not synthesize or deploy a runtime serving path that
   targets `eu-west-1`.
3. Staging and production must not retain runtime-region rollback logic that sends
   traffic to `eu-west-1`.
4. The v0.2 platform has no current production deployment to preserve. Do not carry
   dual-running, 14-day deprovision holds, or Dublin rollback requirements into the
   implementation unless a later issue records a real deployed-state constraint.
5. CI must fail closed for staging and production if any secure deployment invariant
   cannot be proven. Warning-only checks are insufficient for this contract.

## CI/CD Gate Contract

Before staging or production deployment, CI must prove:

- synthesized AgentCore Runtime resources use `NetworkMode: VPC`
- runtime subnet and security group references are present for the `eu-west-2`
  platform VPC
- no serving-path runtime ARN, SSM default, or tenant execution-role region set
  includes `eu-west-1`
- protected production deployment approval checks still pass before `deploy-prod`
- cfn-guard and CDK tests reject `PUBLIC` runtime mode for staging and production

The implementation may allow dev-only exceptions for local/test synthesis, but those
exceptions must be explicit, environment-scoped, and unable to reach staging or prod.

## Deferred Or Unsupported In v0.2

The following are not part of the v0.2 serving contract:

- runtime regional failover
- `eu-west-1` runtime rollback
- cross-region runtime VPC peering
- eu-west-1 Runtime metric stream into London
- Browser or Code Interpreter internet egress through a broad NAT path
- production/staging `PUBLIC` AgentCore Runtime mode

If one of these becomes necessary, it needs a separate issue and decision record.

## AWS Documentation Snapshot

Checked on 2026-04-26:

- AgentCore supported regions:
  https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-regions.html
- AgentCore Runtime and built-in tools VPC connectivity:
  https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-vpc.html

The AWS region table shows AgentCore Runtime, Memory, Gateway, Identity, built-in
tools, Observability, and Policy support in Europe (London). The VPC documentation
shows Runtime, Browser, and Code Interpreter VPC connectivity support and lists the
supported `eu-west-2` AZ IDs: `euw2-az1`, `euw2-az2`, and `euw2-az3`.
