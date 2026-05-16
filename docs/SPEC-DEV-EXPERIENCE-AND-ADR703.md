# Specification: Developer Experience Separation and ADR-703 Implementation

**Date:** 2026-04-23
**Source:** Architectural review of sample-bedrock-proxy-gateway vs tf-acore-aas,
developer inner loop analysis, ADR-703 decisions.
**ADRs:** ADR-703 (gateway efficiency patterns)
**Related existing tasks:** TASK-705 (granular validation targets — do not duplicate)

---

## Overview

Three work streams, independently deliverable:

| Stream | Tasks | Summary |
|--------|-------|---------|
| 8xx — Developer experience | TASK-801 to 805 | Separate platform engineer and agent developer inner loops |
| 9xx — ADR-703 capabilities | TASK-901 to 906 | TTFT metric, TPM rate limiting, rate-limit headers, ID mapping |
| 10xx — Architecture diagrams | TASK-1001 to 1006 | Six missing diagrams identified in architectural review |

---

## Stream 8xx: Developer Experience Separation

### Context

The current inner loop conflates two distinct developer personas that have
different prerequisites, different tools, and different concerns:

- **Platform engineer** — works on `src/`, `infra/cdk/`, `spa/`, `gateway/`.
  Needs Docker, Node, CDK, LocalStack. Validates with `make validate-local`.
- **Agent developer** — works only in `agents/<name>/`. Needs `uv` only.
  Inner loop is pure Python: edit `handler.py`, run `make test-agent`. No Docker,
  no CDK, no Node, no LocalStack required.

The current Makefile, `make bootstrap`, and `PLATFORM-SETUP.md` do not make this
separation explicit. Agent developers are told to run `make dev` (Docker +
LocalStack) as an optional step — this is misleading and unnecessary for
handler logic iteration.

Note: TASK-705 covers granular validation targets for the platform engineer path.
The 8xx tasks do not duplicate that work; they focus on agent developer
separation.

---

### TASK-801: Add `bootstrap-agent` and `help-agent` Makefile targets

**Seq:** 801
**Depends on:** none

**Problem**
`make bootstrap` installs Node, CDK, npm, and Docker — none of which an agent
developer needs. An agent developer's only prerequisite is `uv`. There is no
way to get just the agent developer setup, and `make help` shows CDK, Terraform,
SPA, and ops targets that are irrelevant to agent development.

**Scope**
- Add `make bootstrap-agent` target: checks for `uv` only, runs `uv sync` in
  the repo root, prints the agent developer quick-start message. No Docker, Node,
  or CDK checks.
- Add `make help-agent` target: prints only the agent-relevant target subset:
  `test-agent`, `agentcore-dev`, `agentcore-invoke-dev`, `agentcore-launch`,
  `agentcore-invoke-runtime`, `agentcore-stop-session`, `agentcore-destroy`,
  `agent-push`, `agent-invoke`, `agent-rollback`.
- Rename existing `bootstrap` to `bootstrap-platform` and add a `bootstrap`
  alias that prints a disambiguation message directing the caller to either
  `bootstrap-platform` or `bootstrap-agent`.

**Acceptance Criteria**
- [ ] `make bootstrap-agent` completes without Docker, Node, or CDK installed.
- [ ] `make help-agent` output contains no CDK, Terraform, SPA, ops, or
      worktree targets.
- [ ] `make bootstrap-platform` behaves identically to the current `make bootstrap`.
- [ ] `make bootstrap` (bare) prints a disambiguation message and exits non-zero.

**Test Plan**
```bash
make bootstrap-agent        # must succeed with only uv installed
make help-agent             # review output for no platform targets
make bootstrap-platform     # must succeed identically to old make bootstrap
```

---

### TASK-802: Add per-agent Makefile scaffold

**Seq:** 802
**Depends on:** TASK-801

**Problem**
Agent developers must navigate to the repo root and use `make test-agent AGENT=x`
rather than working inside their agent directory. There is no `cd agents/my-agent
&& make test` experience. The agent directory has no Makefile.

**Scope**
- Add `agents/echo-agent/Makefile` with targets: `test`, `dev`, `invoke`, `push`.
  Each target delegates to the corresponding root Makefile target with `AGENT`
  pre-filled.
- Add a `Makefile.agent.template` at the repo root (or in `agents/`) that new
  agents copy during scaffolding. The template uses `AGENT ?= $(notdir $(CURDIR))`
  to self-identify.
- Update `make agent-push` scaffolding notes in `AGENT-DEVELOPER-GUIDE.md` to
  reference the per-agent Makefile.

**Per-agent Makefile targets**

| Target | Delegates to |
|--------|-------------|
| `make test` | `make test-agent AGENT=$(AGENT)` |
| `make dev` | `make agentcore-dev AGENT=$(AGENT)` |
| `make invoke` | `make agentcore-invoke-dev AGENT=$(AGENT)` |
| `make push ENV=dev` | `make agent-push AGENT=$(AGENT) ENV=$(ENV)` |
| `make logs ENV=dev` | `make agent-logs AGENT=$(AGENT) ENV=$(ENV)` |

**Acceptance Criteria**
- [ ] `cd agents/echo-agent && make test` runs the echo-agent test suite.
- [ ] `cd agents/echo-agent && make dev` starts the agentcore dev server for echo-agent.
- [ ] `cd agents/echo-agent && make push ENV=dev` triggers agent-push.
- [ ] `Makefile.agent.template` exists and is referenced in AGENT-DEVELOPER-GUIDE.md.
- [ ] New agents created by copying echo-agent have a working per-agent Makefile
      without manual edits (AGENT derived from directory name).

**Test Plan**
```bash
cd agents/echo-agent
make test       # must run tests
make --dry-run push ENV=dev   # must emit the correct agent-push command
```

---

### TASK-803: Rewrite AGENT-DEVELOPER-GUIDE.md to reflect clean separation

**Seq:** 803
**Depends on:** TASK-801, TASK-802

**Problem**
The current guide includes:
- Step 5: "Optional: `make dev` (requires Docker)" — agent developers do not
  need this for logic iteration and should not be directed toward it.
- No explicit statement of prerequisites (only `uv`).
- No mention of the per-agent Makefile.
- References to `make validate-local` which runs CDK synth — irrelevant to
  agent developers.

**Scope**
- Add a prerequisites box at the top: `uv` only. No Docker, no Node, no CDK.
- Replace the multi-step quick start with a two-phase model:
  - Phase 1 (local, no AWS): `uv sync` → `make test` (in agent dir) → iterate.
  - Phase 2 (runtime, no platform): `make dev` / `make invoke` (agentcore dev server).
  - Phase 3 (deploy): `make push ENV=dev`.
- Remove the optional `make dev` (LocalStack) step entirely. If an agent
  developer needs to verify platform routing behavior, add a note directing them
  to the platform engineer on their team — not to run LocalStack themselves.
- Add a "What the platform handles for you" section listing: auth, routing,
  memory, tool access, observability, billing — things the agent developer
  explicitly does not need to build or configure.
- Reference TASK-802 per-agent Makefile targets throughout.

**Acceptance Criteria**
- [ ] Guide contains no reference to `make validate-local`, CDK synth, or
      LocalStack as agent developer actions.
- [ ] Prerequisites section lists only `uv`.
- [ ] Quick start works end-to-end from a fresh clone using only the guide.
- [ ] A new team member with no platform context can run `make test` in
      `agents/echo-agent` within 5 minutes of reading the guide.

**Test Plan**
Walkthrough review with someone unfamiliar with the platform.

---

### TASK-804: Rename PLATFORM-SETUP.md to PLATFORM-SETUP.md and fix references

**Seq:** 804
**Depends on:** TASK-803

**Problem**
`PLATFORM-SETUP.md` reads as the general developer setup guide. It lists Docker,
Node, CDK, and npm as prerequisites — all of which are platform engineer
concerns. An agent developer following it would install unnecessary tools.
After TASK-803 rewrites the agent guide, the link between the two documents
must be explicit.

**Scope**
- Rename `docs/development/PLATFORM-SETUP.md` to `docs/development/PLATFORM-SETUP.md`.
- Add a header callout: "This guide is for platform engineers working on `src/`,
  `infra/`, `spa/`, and `gateway/`. If you are building an agent, see
  `AGENT-DEVELOPER-GUIDE.md` instead."
- Update all inbound links in `README.md`, `ARCHITECTURE.md`, and any other
  docs that reference `PLATFORM-SETUP.md`.
- Update `make help` and `make help-platform` to reference `PLATFORM-SETUP.md`.

**Acceptance Criteria**
- [ ] `PLATFORM-SETUP.md` no longer exists; `PLATFORM-SETUP.md` exists in its place.
- [ ] `grep -r LOCAL-SETUP docs/ README.md` returns no results.
- [ ] `PLATFORM-SETUP.md` has the agent developer redirect callout at the top.

**Test Plan**
```bash
grep -r "LOCAL-SETUP" docs/ README.md   # must return empty
```

---

### TASK-805: Add `make lint` fast-path target for platform engineers

**Seq:** 805
**Depends on:** TASK-705 (granular validation targets)

**Problem**
`make validate-local` includes CDK synth, making it ~60-90 seconds. For a
mid-session edit-verify loop on Python-only changes (Lambda handler edits,
data-access-lib changes, new tests), the CDK synth is unnecessary friction.
TASK-705 defines granular targets; this task wires the fast subset into a
named `make lint` shorthand.

**Scope**
- Add `make lint` as an alias for the ruff + mypy subset from TASK-705, without
  CDK synth or secret scan.
- Add `make lint-watch` using `watchmedo` (from `watchdog`, already a dev dep
  via uv) to re-run `make lint` on changes to `src/**/*.py`, `gateway/**/*.py`,
  `tests/**/*.py`. Exit on first failure.
- Document both targets in `make help-platform`.

**Acceptance Criteria**
- [ ] `make lint` completes in under 15 seconds on a warmed Python environment.
- [ ] `make lint-watch` re-runs on `.py` file save and exits clearly on failure.
- [ ] `make validate-local` is unchanged and still includes CDK synth.

**Test Plan**
```bash
time make lint          # must be < 15s
make lint-watch &       # touch src/bridge/handler.py; verify re-run fires
```

---

## Stream 9xx: ADR-703 Capabilities

### Context

ADR-703 records four patterns adopted from the proxy architecture review:
- P1: Token-Per-Minute (TPM) rate limiting — phased, requires Redis
- P2: Time-to-First-Token (TTFT) metric — immediate, no new infrastructure
- P3: Logical-to-physical ID mapping — adopt as pattern, defer guardrails
- P4: Rate-limit consumption headers — contingent on P1 counters

Implementation order from ADR-703:
1. TTFT (P2) — no dependencies
2. Redis/Valkey infrastructure
3. TPM log-only counters (P1 phase 1)
4. Calibration gate (operator review, 2-week bake)
5. TPM enforcement + rate-limit headers (P1 phase 2 + P4)
6. Logical→physical mapping formalisation (P3)

---

### TASK-901: Record TTFT on streaming invocations

**Seq:** 901
**Depends on:** none

**Problem**
The platform records `latencyMs` end-to-end for all invocations in
`platform-invocations`. For streaming agents — and especially the AG-UI path —
the user-perceived latency is Time-to-First-Token (TTFT), not total duration.
TTFT is currently not captured. Without it, streaming latency SLOs cannot be
defined or monitored.

**Scope**
- In the Bridge Lambda streaming path, record the wall-clock time from the
  `invoke` call to the first chunk received from AgentCore Runtime.
- Add `ttftMs` as a nullable integer attribute on `platform-invocations` records.
  Null for non-streaming invocations. Never zero — if the first chunk is
  immediate, record 1ms minimum to distinguish from null.
- Emit `gen_ai.ttft_ms` as a CloudWatch metric with dimensions:
  `AgentName`, `InvocationMode=streaming`, `RuntimeRegion`.
- Add `FM-2` alarm threshold note: if P99 TTFT exceeds the AG-UI SLO threshold
  (TBD by operator), alert. Placeholder alarm definition in ObservabilityStack
  with a disabled state pending SLO definition.

**Out of scope**
- TTFT measured from the client's perspective (network RTT to client).
- TTFT for non-streaming invocation modes.
- Defining the SLO threshold value — that requires production data.

**Acceptance Criteria**
- [ ] `platform-invocations` records for streaming invocations include `ttftMs`.
- [ ] `platform-invocations` records for sync invocations have `ttftMs=null`.
- [ ] `gen_ai.ttft_ms` CloudWatch metric emitted and visible in dev environment.
- [ ] No change to non-streaming Bridge code paths.
- [ ] Unit tests cover: streaming with TTFT captured, non-streaming with null.

**Test Plan**
```bash
make dev-invoke MODE=streaming      # verify ttftMs in invocation record
make test-unit                      # must pass
```

---

### TASK-902: Provision Redis/Valkey for rate limiting

**Seq:** 902
**Depends on:** none

**Problem**
ADR-703 P1 (TPM limiting) requires a shared in-memory counter store accessible
from all Bridge Lambda instances. The platform has no Redis/Valkey today.

**Scope**
- Add `ElastiCache Serverless` (Valkey engine) to `PlatformStack` in eu-west-2.
- Configure within the existing VPC with a security group permitting inbound
  6379/TCP from the Bridge Lambda security group only.
- Store the cluster endpoint in SSM at
  `/platform/{env}/config/valkey-endpoint` (not hardcoded).
- Add VPC endpoint for ElastiCache if required for private connectivity.
- Add CDK cfn-guard rules: no public access, encryption at rest enabled,
  encryption in transit enabled.
- Add `FM-12: Valkey unavailable` to ARCHITECTURE.md failure modes table:
  detection = Bridge `valkey_unavailable` fail-open metric/log; response =
  Bridge continues with fail-open (TPM check skipped, metric emitted). Cache
  command volume metrics are not sufficient because they conflate idle traffic
  with outage.
- Bridge Lambda must not hard-depend on Valkey availability. Valkey connection
  failure must be caught, logged with `event.name=valkey_unavailable`, and the
  request allowed to proceed (fail-open).
- Direct Bridge VPC attachment is intentionally not part of this task: ADR-014
  rejects broad NAT egress, and ADR-020 gates runtime-region VPC changes. TASK-903
  must use an approved narrow adapter or runtime-network design before Bridge
  executes Valkey commands.

**Out of scope**
- TPM counter logic (TASK-903).
- Runtime client integration and the Bridge-to-Valkey network adapter (TASK-903).
- Multi-region Valkey enforcement.

**Acceptance Criteria**
- [ ] `cdk synth` includes the ElastiCache Serverless cluster in PlatformStack.
- [ ] `cfn-guard` passes: encryption at rest, encryption in transit, no public access.
- [ ] Cluster endpoint published to SSM `/platform/{env}/config/valkey-endpoint`.
- [ ] Valkey client network path is documented for TASK-903 without regressing
      the current Bridge-to-AgentCore Runtime path.
- [ ] `make validate-local` passes.

**Test Plan**
```bash
make infra-synth                         # no synth errors
make infra-diff ENV=dev                  # review diff before deploy
# After deploy:
aws elasticache describe-serverless-caches  # verify cluster state
```

---

### TASK-903: TPM log-only counters in Bridge (P1 phase 1)

**Seq:** 903
**Depends on:** TASK-902

**Problem**
API Gateway usage plans enforce RPM only. Token consumption varies by orders of
magnitude between requests. A tenant making 10 RPM but with 50K-token prompts
can exhaust Bedrock quota while staying within RPM limits. ADR-703 requires a
two-phase approach: log-only first to calibrate estimation accuracy, enforce later.

**Scope**
- In the Bridge Lambda, after AgentCore Runtime returns the invocation result,
  read `inputTokens` and `outputTokens` from the response (already in the
  invocation record per ADR-701).
- Increment a Valkey counter keyed
  `LIMITER/{tenantId}:{modelId}:tpm/{windowExpiry}` using the same fixed-window
  (60-second bucket) pattern as specified in ADR-703. EXPIRE set to 90 seconds.
- Also maintain an estimated pre-request counter using character-count
  heuristics (≈4 chars per token) for the pre-request check path that will be
  used in phase 2.
- **Do not enforce.** Log only: emit `rate_limit.tpm_used` and
  `rate_limit.tpm_estimated` as structured log fields on every invocation.
  Emit `gen_ai.tpm_window_usage` as a CloudWatch metric per tenant, app, and
  model.
- If Valkey is unavailable, skip the counter update, emit
  `event.name=tpm_counter_skipped`, and continue.

**Calibration output**
Log lines must include enough data to answer, after 2 weeks of production data:
- What is the estimation error rate (estimated vs actual tokens)?
- Which tenants are approaching their Bedrock quota?
- What TPM limits would have triggered in the past 2 weeks at various
  thresholds?

**Acceptance Criteria**
- [ ] Every invocation emits `rate_limit.tpm_used` in the structured log.
- [ ] `gen_ai.tpm_window_usage` metric visible in CloudWatch.
- [ ] No request is rejected due to TPM (log-only, no enforcement).
- [ ] Valkey unavailability does not fail the invocation.
- [ ] Unit tests cover: counter increment, Valkey failure (fail-open), log fields.

**Test Plan**
```bash
make test-unit                           # bridge tests pass
make dev-invoke                          # inspect logs for tpm_used field
# After 2-week bake in staging/prod → operator review before TASK-904
```

**Gate:** Operator must review TPM estimation accuracy data after 2 weeks before
TASK-904 is started. Do not begin TASK-904 without written sign-off.

---

### TASK-904: TPM enforcement and request rejection (P1 phase 2)

**Seq:** 904
**Depends on:** TASK-903 (+ operator gate sign-off on calibration data)

**Problem**
Log-only TPM counters (TASK-903) provide visibility but no protection. After
calibration confirms estimation accuracy is acceptable, the platform should
reject pre-request invocations that would exceed a tenant's TPM limit.

**Scope**
- At the start of the Bridge invocation path (before calling AgentCore Runtime),
  read the tenant's TPM limit from AppConfig capability policy.
- Perform a pre-request TPM check against the Valkey counter using the estimated
  token count. Use an atomic Lua script: check current window value + estimate
  vs limit; increment and set EXPIRE only if within limit.
- On limit exceeded: return HTTP 429 with `X-RateLimit-Limit-TPM`,
  `X-RateLimit-Used-TPM`, `X-RateLimit-Reset` headers. Emit
  `event.name=tpm_limit_exceeded` metric.
- Post-response: correct the counter with actual tokens (replace estimate with
  `inputTokens + outputTokens`). Correction uses a second atomic Lua script:
  subtract the estimate, add actual.
- Streaming invocations: use the pre-request estimate only (no post-response
  correction, consistent with ADR-703 accepted limitation). Document this
  explicitly in the code.
- Fail-open on Valkey unavailability (same as TASK-903).

**TPM limits in AppConfig**
TPM limits are per-tier, per-model in the capability policy document. Default
if not configured: unlimited (no enforcement). Enforcement only when a limit
is explicitly set.

**Acceptance Criteria**
- [ ] Requests exceeding TPM limit receive 429 with rate-limit headers.
- [ ] Requests within limit proceed normally.
- [ ] Post-response correction updates counter with actual tokens (non-streaming).
- [ ] Streaming requests use pre-request estimate and are not corrected.
- [ ] Valkey failure never rejects a request.
- [ ] Unit tests cover: under limit, at limit, over limit, Valkey down, streaming.
- [ ] `make validate-local` passes.

**Test Plan**
```bash
make test-unit
# Integration: configure a low TPM limit in dev AppConfig
# Fire requests until 429 is returned; verify headers
```

---

### TASK-905: Rate-limit consumption headers on northbound responses

**Seq:** 905
**Depends on:** TASK-904

**Problem**
E2B machine clients cannot observe their own TPM consumption without polling a
quota endpoint. This causes polling overhead and makes client-side back-off
reactive rather than proactive.

**Scope**
- After each invocation completes (TASK-904 counter already updated), add the
  following headers to the Bridge response:
  - `x-ratelimit-limit-tpm`: tenant's configured TPM limit for this model
    (or `unlimited` if unconfigured).
  - `x-ratelimit-used-tpm`: current window consumption after this request.
  - `x-ratelimit-limit-rpm`: tenant's API Gateway usage plan RPM limit.
  - `x-ratelimit-used-rpm`: current window RPM usage (read from AppConfig
    or the existing invocation record; do not add a new Redis counter for RPM).
  - `x-ratelimit-reset`: Unix timestamp of the next 60-second window boundary.
- Headers are added in Bridge Lambda response, not at API Gateway.
- Headers are always present on model invocation responses. On non-model routes
  (health, ops) headers are omitted.
- Do not add headers during TASK-903 log-only phase. Enable in TASK-904 only.

**Acceptance Criteria**
- [ ] Model invocation responses include all five headers.
- [ ] `x-ratelimit-limit-tpm` is `unlimited` when no limit is configured.
- [ ] Headers are present on 200, 429, and error responses from model routes.
- [ ] No headers on `/health` or `/v1/ops/*` routes.
- [ ] Unit tests cover: limited quota, unlimited quota, header values after 429.

**Test Plan**
```bash
curl -I https://api.dev.platform/v1/invoke/... -H "Authorization: Bearer $JWT"
# Verify x-ratelimit-* headers in response
```

---

### TASK-906: Formalise logical-to-physical ID mapping pattern

**Seq:** 906
**Depends on:** none

**Problem**
As guardrails, inference profiles, and model aliases are introduced in a
multi-account topology (Option B/C), their AWS physical IDs will vary per
account and per environment. Without a declared pattern, implementations will
hardcode physical IDs or invent ad-hoc resolution. ADR-703 P3 requires this
pattern to be established before any new resource type is added.

**Scope**
This task is documentation and convention only — no new Lambda code.

- Add `docs/contracts/LOGICAL-PHYSICAL-ID-MAPPING.md` defining:
  - The pattern: a logical ID is a stable, environment-agnostic, human-readable
    name owned by the platform. A physical ID is the AWS resource identifier
    for a specific account and region.
  - The registry: logical→physical mappings live in a DynamoDB table or SSM
    namespace, never in application code constants or environment variables.
  - The resolution contract: all Lambda handlers must resolve via the registry.
    Direct use of physical IDs in handler code is a forbidden pattern (same
    category as raw boto3 DynamoDB in CLAUDE.md).
  - The caching contract: resolved mappings may be cached in Lambda `/tmp` for
    up to 5 minutes. Stale cache on SSM update is acceptable.
  - Example: `platform-tools` table already implements this pattern for tools.
    Document it as the reference implementation.
- Add the forbidden pattern to `CLAUDE.md`:
  ```python
  # FORBIDDEN: physical resource ID in application code
  guardrail_id = "abc123def456"
  # REQUIRED: resolve via registry
  guardrail_id = registry.resolve("baseline-security", account_id)
  ```
- Add a one-paragraph note to ADR-703 P3 section linking to the new contract
  document.

**Acceptance Criteria**
- [ ] `docs/contracts/LOGICAL-PHYSICAL-ID-MAPPING.md` exists and covers the
      four points above.
- [ ] `CLAUDE.md` forbidden patterns section includes the physical-ID pattern.
- [ ] No new Lambda handler code is changed.

**Test Plan**
Documentation review only. No automated tests for this task.

---

## Stream 10xx: Architecture Diagrams

### Context

The repository has nine draw.io diagrams (with PNG/SVG exports) and one embedded
Mermaid flowchart. The architectural review identified six missing diagrams that
are referenced in docs or ADRs but not illustrated. GitLab renders Mermaid
natively; sequence diagrams and flows are better expressed as Mermaid embedded
in the relevant document than as external draw.io files requiring a separate tool.

---

### TASK-1001: Tenant isolation 4-layer swimlane diagram

**Seq:** 1001
**Depends on:** none

**Problem**
The 4-layer isolation model (JWT validation → STS role assumption → Cedar policy
→ TenantScopedDynamoDB) is described in a text table in ARCHITECTURE.md. It is
the most important security property of the platform and is referenced in
compliance reviews, threat model discussions, and engineer onboarding. A vertical
swimlane showing what each layer enforces and what a single-layer breach does not
expose is required for audit readiness.

**Scope**
- Create `docs/images/tf_acore_aas_tenant_isolation.drawio` matching the
  existing diagram style (same canvas size, same colour palette, same font).
- Export as `.drawio.png` and `.drawio.svg`.
- Diagram structure: four vertical swimlanes (Layer 1–4), each showing:
  - The component enforcing isolation (Authoriser, Bridge, Gateway, data-access-lib)
  - What it validates
  - What a breach of only this layer exposes (i.e. what the next layer still
    protects)
- Add a reference to the diagram in ARCHITECTURE.md under "Tenant Isolation Model".

**Acceptance Criteria**
- [ ] `.drawio`, `.drawio.png`, `.drawio.svg` all present in `docs/images/`.
- [ ] ARCHITECTURE.md references the diagram with an `![...]` image link.
- [ ] Diagram accurately represents the 4-layer model as described in
      ARCHITECTURE.md.

---

### TASK-1002: Identity and token transformation chain (Mermaid, ADR-004)

**Seq:** 1002
**Depends on:** none

**Problem**
ADR-004 (act-on-behalf identity) describes the JWT transformation chain in prose.
The key security property — that the original client JWT never reaches tool
Lambdas — is not visually evident. Auditors and new engineers reading ADR-004
cannot follow the token transformation without working through multiple documents.

**Scope**
- Add a `sequenceDiagram` Mermaid block to ADR-004 showing:
  - Client → Authoriser: client JWT
  - Authoriser → Bridge: validated tenant context (not the JWT)
  - Bridge → STS: assume tenant execution role
  - Bridge → AgentCore Runtime: invoke with execution credentials
  - AgentCore Runtime → Gateway REQUEST interceptor: tool call
  - Gateway → Tool Lambda: scoped act-on-behalf token only
  - Tool Lambda: never receives client JWT (annotate explicitly)
- Embed the diagram directly in ADR-004 between the Decision and Consequences
  sections.

**Acceptance Criteria**
- [ ] Mermaid diagram renders correctly in GitLab MR preview.
- [ ] Diagram accurately represents the token flow described in ADR-004 prose.
- [ ] Client JWT does not appear after the Authoriser step.

---

### TASK-1003: Async invocation sequence diagram (deferred by ADR-024)

**Seq:** 1003
**Depends on:** none

**Problem**
ADR-024 removes async invocation from the v0.2 supported contract. This diagram
should not be added until the platform owns native async completion/status/results
semantics end to end.

**Scope**
- Deferred future scope: add a `sequenceDiagram` Mermaid block to the "Invocation
  Modes" section of ARCHITECTURE.md covering:
  - Client → API Gateway → Bridge: invoke request
  - Bridge → AgentCore Runtime: invoke returns only after a real async backend exists
  - Bridge → DynamoDB: write JOB record
  - Bridge → Client: accepted job response
  - AgentCore Runtime → agent code: `add_async_task` → HealthyBusy
  - agent code: background work
  - agent code: `complete_async_task` → Healthy
  - Client → API Gateway → Bridge: `GET /v1/jobs/{jobId}` polling
  - (Alternative path) AgentCore Runtime → Webhook delivery Lambda → Tenant endpoint
- Show both polling and webhook delivery paths.

**Acceptance Criteria**
- [ ] Mermaid diagram renders in GitLab.
- [ ] Both polling and webhook paths are shown.
- [ ] `add_async_task` / `complete_async_task` / `HealthyBusy` states are labelled.

---

### TASK-1004: Runtime degradation sequence diagram (Mermaid, RUNBOOK-001)

**Seq:** 1004
**Depends on:** none

**Problem**
RUNBOOK-001 describes the v0.2 London Runtime degradation procedure in prose. The
sequence of events (who detects the failure, what operators pause, and when
traffic is considered recovered) should be skimmable at 3am.

**Scope**
- Add a `sequenceDiagram` Mermaid block to RUNBOOK-001 showing:
  - Bridge Lambda: receives `ServiceUnavailableException` from eu-west-2
  - Bridge Lambda: records the invocation failure and returns the runtime error
  - Operator: verifies AWS Health and Bridge logs
  - Operator: declares degraded runtime mode and pauses risky releases/promotions
  - Operator: monitors `eu-west-2` recovery signals
  - Operator: records the incident and opens a follow-up decision issue only if
    a future release needs serving-path failover
- Explicitly show that no SSM runtime-region update or fallback lock is used in v0.2.

**Acceptance Criteria**
- [ ] Mermaid diagram renders in GitLab.
- [ ] No runtime-region switch, fallback lock, or `eu-west-1` path is shown.
- [ ] Degradation and recovery signals match ADR-023.

---

### TASK-1005: Hot-path configuration resolution diagram (Mermaid, ARCHITECTURE.md)

**Seq:** 1005
**Depends on:** none

**Problem**
The AppConfig Lambda extension → SSM fallback → code default resolution chain
is described in a prose table in ARCHITECTURE.md. The deny-by-default fallback
semantics (missing config = most restrictive, not most permissive) are critical
for security correctness but are not obvious from the table alone.

**Scope**
- Add a `flowchart TD` Mermaid block to the "Configuration Ownership Model"
  section of ARCHITECTURE.md showing:
  - Lambda invocation → AppConfig Lambda extension (sub-ms, on-box)
  - Extension hit → use value → proceed
  - Extension miss / error → direct SSM fetch
  - SSM hit → use value (log extension miss)
  - SSM miss / error → use approved operational default from code
  - At each fallback: annotate the deny-by-default rule: "missing = restrictive,
    never permissive"
  - Kill switch path: kill switch in AppConfig overrides all other values

**Acceptance Criteria**
- [ ] Mermaid diagram renders in GitLab.
- [ ] All three resolution layers shown (AppConfig extension → SSM → code default).
- [ ] Deny-by-default annotation present at fallback branches.
- [ ] Kill switch override path shown.

---

### TASK-1006: Billing and chargeback data flow diagram (Mermaid, ADR-701)

**Seq:** 1006
**Depends on:** none

**Problem**
ADR-701 defines the authoritative metering fields. There is no diagram showing
how token counts flow from the invocation through to tenant billing summaries.
This is a gap for compliance reviews and for implementing the ADR-703 TPM work
(TASK-903 needs to understand where actual token counts come from).

**Scope**
- Add a `flowchart LR` Mermaid block to ADR-701 showing:
  - AgentCore Runtime → Bridge: invocation response with `inputTokens`, `outputTokens`
  - Bridge → `platform-invocations`: writes invocation record with ADR-701 fields
  - Billing Lambda (daily): reads `platform-invocations`, aggregates per tenant
  - Billing Lambda → `platform-tenants`: writes billing summary
  - Billing Lambda → CloudWatch: emits billing metrics
  - `platform-invocations` → (TASK-903 path) Valkey TPM counter update
- Annotate the `metering_source` field (Bridge = authoritative; future downstream
  gateway = also authoritative when present per ADR-702).

**Acceptance Criteria**
- [ ] Mermaid diagram renders in GitLab.
- [ ] ADR-701 canonical fields annotated at the Bridge → DynamoDB step.
- [ ] Billing Lambda aggregation path shown.
- [ ] TASK-903 Valkey counter update path shown (as a branch, not blocking).

---

## Dependency Graph

```
801 (bootstrap-agent + help-agent)
 └─ 802 (per-agent Makefile)
     └─ 803 (rewrite agent guide)
         └─ 804 (rename PLATFORM-SETUP.md)

705 (existing: granular validation)
 └─ 805 (make lint fast-path)

901 (TTFT metric)          ← no dependencies

902 (Valkey infrastructure)
 └─ 903 (TPM log-only)    ← GATE: 2-week bake + operator sign-off
     └─ 904 (TPM enforce)
         └─ 905 (rate-limit headers)

906 (logical→physical ID mapping)   ← no dependencies

1001–1006 (diagrams)                ← all independent
```

---

## Issue Creation Commands

```bash
# Stream 8xx
make issue-create TITLE='TASK-801: Add bootstrap-agent and help-agent Makefile targets' SEQ=801
make issue-create TITLE='TASK-802: Add per-agent Makefile scaffold' SEQ=802
make issue-create TITLE='TASK-803: Rewrite AGENT-DEVELOPER-GUIDE for clean persona separation' SEQ=803
make issue-create TITLE='TASK-804: Rename PLATFORM-SETUP.md to PLATFORM-SETUP.md' SEQ=804
make issue-create TITLE='TASK-805: Add make lint fast-path for platform engineers' SEQ=805

# Stream 9xx
make issue-create TITLE='TASK-901: Record TTFT metric on streaming invocations' SEQ=901
make issue-create TITLE='TASK-902: Provision Redis/Valkey for TPM rate limiting' SEQ=902
make issue-create TITLE='TASK-903: TPM log-only counters in Bridge (P1 phase 1)' SEQ=903
make issue-create TITLE='TASK-904: TPM enforcement and request rejection (P1 phase 2)' SEQ=904
make issue-create TITLE='TASK-905: Rate-limit consumption headers on northbound responses' SEQ=905
make issue-create TITLE='TASK-906: Formalise logical-to-physical ID mapping pattern' SEQ=906

# Stream 10xx
make issue-create TITLE='TASK-1001: Tenant isolation 4-layer swimlane diagram' SEQ=1001
make issue-create TITLE='TASK-1002: Identity and token transformation chain diagram' SEQ=1002
make issue-create TITLE='TASK-1003: Async invocation sequence diagram' SEQ=1003
make issue-create TITLE='TASK-1004: Runtime failover sequence diagram' SEQ=1004
make issue-create TITLE='TASK-1005: Hot-path configuration resolution diagram' SEQ=1005
make issue-create TITLE='TASK-1006: Billing and chargeback data flow diagram' SEQ=1006
```
