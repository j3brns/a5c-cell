# ADR-703: Gateway Efficiency Patterns

## Status: Proposed
## Date: 2026-04-23

## Context
The proxy architecture review identified four gateway-side patterns that improve
operability without changing the public AgentCore invoke contract.

The platform already owns tenant identity, invocation metering, and runtime
routing. Gateway efficiency work must preserve that boundary while adding
targeted controls for Bedrock-facing cost, latency, and resource selection.

## Decision
The platform adopts four patterns from the review:

1. **P1: token-per-minute rate limiting**
   Use a shared counter store for tenant and agent token usage. Start in log-only
   mode, then enforce after calibration.

2. **P2: time-to-first-token metric**
   Record TTFT for streaming invocations after the first non-empty runtime chunk
   arrives.

3. **P3: logical to physical ID mapping**
   Treat account-local AWS resource identifiers as physical IDs behind stable
   platform-owned logical IDs. The binding contract is published in
   [Logical to Physical ID Mapping Contract](../contracts/LOGICAL-PHYSICAL-ID-MAPPING.md).
   New guardrail, inference profile, model alias, runtime target, and gateway
   target integrations must resolve through that registry pattern rather than
   hardcoding provider IDs in Lambda handlers.

4. **P4: rate-limit consumption headers**
   Return rate-limit consumption headers once authoritative token counters are
   available.

## Consequences
The patterns can land independently. TTFT has no infrastructure dependency. TPM
rate limiting and consumption headers depend on a shared counter store and an
operator calibration gate. Logical to physical ID mapping is documentation and
convention first, with automated guardrails deferred until resource-specific
registries are added.

## Alternatives Rejected
- **Hardcode environment-specific resource IDs in handlers**: works for a single
  account but breaks as soon as guardrails, aliases, profiles, or gateway targets
  vary by environment or account.
- **Expose physical IDs directly in tenant-facing API contracts**: leaks provider
  topology and makes future account moves tenant-visible.
