# ADR-001: AgentCore Runtime over Custom Orchestration

## Status: Accepted
## Date: 2026-02-24

## Context
The platform needs to run AI agents with session isolation, extended execution windows,
and enterprise security. Alternatives were: ECS Fargate per session, Kubernetes with
custom orchestrator, Lambda (15-minute limit), or AgentCore Runtime.

## Decision
Use Amazon Bedrock AgentCore Runtime as the agent execution environment.
Current pre-v0.2 implementation still contains Dublin-era defaults. ADR-023 defines
the v0.2 secure target: AgentCore Runtime in eu-west-2 with VPC mode for staging and
production, and no eu-west-1 runtime fallback.

## Consequences
- arm64 Firecracker microVM isolation per session — no cross-tenant leakage
- 8-hour execution windows for async agents
- Session state persists within session; use AgentCore Memory for cross-session durability
- Cold start 300–800ms — acceptable, not zero
- Auto-scaling managed by AWS — no infrastructure to operate
- All Python dependencies must be cross-compiled for aarch64-manylinux2014

## Alternatives Rejected
- ECS Fargate per session: expensive ($0.04/vCPU/hour idle), slow provisioning
- Kubernetes: significant operational overhead for a small team
- Lambda: 15-minute hard limit, no session continuity
- Self-hosted: violates the principle of not operating undifferentiated infrastructure
