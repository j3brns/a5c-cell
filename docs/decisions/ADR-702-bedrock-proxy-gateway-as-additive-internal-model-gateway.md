# ADR-702: Bedrock Proxy Gateway as an Additive Internal Model Gateway

## Status: Proposed
## Date: 2026-04-03

## Context
The platform already has a defined tenant-aware northbound invocation path:
- CloudFront and REST API Gateway form the public ingress boundary
- Authoriser Lambda establishes tenant identity and policy context
- Bridge Lambda assumes tenant execution roles and invokes AgentCore Runtime
- AgentCore Runtime is the canonical execution surface for published agents

This control-plane contract is intentionally different from direct foundation
model access.

Separately, a Bedrock proxy gateway can provide direct `bedrock-runtime`
capabilities such as:
- direct foundation-model invocation
- model and inference-profile abstraction
- Bedrock guardrail attachment
- Bedrock-specific quota and rate controls
- cross-account Bedrock routing

Those concerns are useful, but they do not belong on the public northbound
tenant API by default.

If the platform integrates a Bedrock proxy gateway without an explicit boundary,
it risks:
- weakening the existing tenant-aware public API contract
- introducing a second public invoke surface that bypasses platform policy and
  audit controls
- coupling AgentCore runtime orchestration to direct Bedrock model-routing
  concerns
- confusing agent execution with direct model invocation

The current platform architecture also has a near-term default:
- direct inference and metering remain in the platform control-plane / platform
  account path by default
- a downstream model gateway is a later additive option, not the starting point

## Decision
The platform will treat any Bedrock proxy gateway as an additive internal model
gateway, not as a replacement for the public AgentCore invoke path.

The integration model is:

1. **Keep the current public invoke path**
   - the platform remains the only tenant-facing and operator-facing public API
     for agent invocation
   - the `Bridge Lambda -> AgentCore Runtime` path remains the canonical path
     for published agent execution

2. **Use the Bedrock proxy gateway only as an internal downstream service**
   - the gateway is private platform infrastructure
   - it may be called only by approved internal services, platform-owned agents,
     or explicitly approved tool and agent execution paths that need direct
     foundation-model access

3. **Do not redefine the public contract around direct model invocation**
   - public clients do not call the model gateway directly
   - direct model invocation remains an internal implementation concern unless a
     future ADR explicitly approves a new public API surface
   - the gateway must not become a generic tenant invoke router or a hidden
     alternate path for published-agent invocation semantics

4. **Preserve the current default**
   - while inference remains in the platform control-plane / platform account
     path, the control plane remains both the attribution owner and the
     authoritative inference meter
   - introducing a downstream gateway later is a phased evolution, not an
     immediate architectural inversion

5. **Prepare for later additive integration**
   - if a downstream Bedrock proxy gateway is introduced later, it may become an
     authoritative inference meter for the calls it executes directly
   - the control plane remains the canonical billing-record owner under ADR-701
   - any downstream gateway integration must preserve the joined-ledger and
     tenant-attribution rules from ADR-701

## Boundary Rules
### Public API Boundary
- The public tenant-facing API remains the REST control plane.
- Agent invocation stays anchored to the existing auth, RBAC, audit, and Bridge
  semantics.
- A Bedrock proxy gateway must not be exposed as a second general-purpose
  tenant-facing invoke endpoint by default.

### Internal Service Boundary
- A Bedrock proxy gateway is an internal platform service for model-access
  concerns.
- It is not a second generic invocation plane for tenant API clients.
- It may be used where direct foundation-model APIs are the correct abstraction,
  for example:
  - internal tools
  - explicitly approved agent sub-steps that require direct model access
  - future inference-profile-backed routing
  - Bedrock guardrail-enforced calls

Allowed-caller rule:
- any non-platform use of the gateway from tenant-invoked agent execution must
  be an explicit approved capability, not an implicit right of all agents
- Bridge-mediated published-agent execution remains the canonical invoke path;
  use of a gateway inside agent execution is an internal implementation detail,
  not a new public contract

### Tenant and Audit Boundary
- Tenant identity, app identity, and agent attribution remain owned by the
  control plane.
- Any call into a downstream model gateway must carry trusted platform-derived
  attribution context rather than arbitrary client-supplied identity.
- This preserves the invariants of ADR-016: `tenantid` and `appid` remain
  visible in logs, metrics, traces, and auditable metering flows.

Minimum downstream contract:
- any downstream gateway integration must return or emit the ADR-701 canonical
  chargeback fields required for authoritative metering when it acts as the
  direct inference meter
- at minimum this includes:
  - `invocation_id`
  - `resolved_model_id`
  - `resolved_inference_profile_id`
  - `authoritative_input_tokens`
  - `authoritative_output_tokens`
  - `usage_quality`
  - `metering_source`

## Configuration Ownership
- the platform owns:
  - public API contracts
  - tenant identity and authorization
  - agent registry and invocation policy
  - execution-role assumption
  - billing-record ownership and chargeback attribution
- the internal Bedrock proxy gateway owns, when present:
  - direct Bedrock Runtime request forwarding
  - model and inference-profile abstraction
  - Bedrock-specific quota controls
  - Bedrock guardrail attachment
  - authoritative metering for the direct model calls it executes

## Consequences
### Positive
- The current public invoke contract remains stable.
- The platform can add direct foundation-model access without collapsing
  AgentCore and Bedrock Runtime into one public surface.
- Bedrock-specific capabilities such as inference profiles and guardrail routing
  get a clear home if they are introduced later.
- Chargeback and attribution boundaries stay consistent with ADR-701.

### Negative
- Additional integration work is required if a downstream gateway is introduced:
  - private networking
  - service authentication
  - trusted attribution propagation
  - metering-envelope reconciliation
- Some model-facing functionality remains intentionally unavailable directly to
  public clients unless a later ADR approves it.

## Implementation Notes
- Default posture: no downstream gateway required for the current platform
  baseline.
- If introduced later, the target posture is private ingress and private DNS or
  equivalent service-to-service connectivity.
- Early adoption must not assume that all runtime and networking prerequisites
  for a fully private path already exist in the current topology; any exception
  must be explicit and documented rather than assumed away.
- Gateway endpoint configuration and secrets must live in approved platform
  config surfaces rather than application code constants.
- Any later gateway integration must persist requested and resolved model/profile
  dimensions on the invocation record in line with ADR-701.
- The gateway must not become an undocumented second northbound boundary.

## Alternatives Rejected
- **Replace the public AgentCore invoke path with a Bedrock proxy gateway**:
  breaks the current platform contract and conflates agent execution with direct
  model access.
- **Expose the Bedrock proxy gateway directly to tenants as a second public API**:
  introduces a second policy boundary and risks bypassing control-plane audit and
  authorization semantics.
- **Fold all Bedrock model-routing concerns directly into Bridge Lambda**:
  increases coupling between public invocation orchestration and direct
  model-access concerns.
- **Ignore direct model access as a future concern**: leaves no explicit place
  for inference profiles, Bedrock guardrails, or direct model-routing concerns if
  they are later needed.
