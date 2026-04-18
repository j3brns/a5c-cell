# a5c-cell — Fitness-for-Purpose Report v2

**Date:** 2026-04-10
**Supersedes:** FITNESS-FOR-PURPOSE-REPORT.md (v1, 2026-04-09)
**Review method:** Multi-agent parallel review — code quality specialist, senior engineer, solutions architect — with manual CR issue validation, verified volumetrics, AWS documentation cross-check, and GitNexus utilisation assessment.
**Verdict:** Fit for purpose as an exploratory platform with production-grade discipline. Narrower gap to production than v1 reported — 9 of 13 original CR issues are resolved. New findings from specialist reviews are more significant than the remaining CRs.

---

## 1. Verified Code Volumetrics

All line counts verified by `wc -l` on 2026-04-10. v1 report contained inflated numbers from byte-count misreads.

### Application Code

| Module | Lines | Notes |
|--------|-------|-------|
| `src/authoriser/` | 631 | JWT + SigV4 auth, well-factored into handler + jwt_service + sigv4_service |
| `src/bridge/` | 2,720 | 10 submodules. handler.py is 602 lines (facade, see §5.1) |
| `src/tenant_api/` | 3,546 | 25 files. handler.py reduced to dispatcher (CR012 resolved) |
| `src/bff/` | 540 | Thin BFF — token refresh + session keepalive only |
| `src/billing/` | 379 | Daily aggregation pipeline |
| `src/webhook_delivery/` | 650 | Async webhook with retry + DLQ |
| `src/tenant_provisioner/` | 290 | EventBridge → Step Functions → CloudFormation |
| `src/platform_tools/` | 266 | Platform diagnostics handler |
| `src/ top-level` | 122 | platform_utils.py, platform_aws.py |
| `data-access-lib (src)` | 1,507 | Tenant isolation enforcement — security-critical |
| `gateway/` | 1,284 | REQUEST + RESPONSE interceptors |
| **Total application** | **11,935** | |

### Infrastructure

| Module | Lines | Notes |
|--------|-------|-------|
| `infra/cdk/lib+bin` | 4,088 | 14 stack files + entry point |
| `infra/cdk/test` | 2,207 | 8 test suites |
| `infra/terraform` | 247 | Account vending only |
| `infra/guard` | 212 | cfn-guard security policy |
| **Total infra** | **6,754** | |

### Frontend

| Module | Lines | Notes |
|--------|-------|-------|
| `spa/src (app)` | 5,702 | React 18, MSAL, Vite, Tailwind, shadcn/ui |
| `spa/src (test)` | 2,320 | Vitest + Testing Library |
| **Total SPA** | **8,022** | |

### Tests

| Module | Lines | Notes |
|--------|-------|-------|
| `tests/unit` | 17,992 | 59 test files |
| `tests/integration` | 654 | 5 test files |
| `tests/mocks` | 189 | Mock runtime + JWKS |
| `data-access-lib/tests` | 1,135 | Security-critical path |
| **Total test** | **19,970** | |

### Tooling & Harness

| Module | Lines | Notes |
|--------|-------|-------|
| `scripts/` (excl issue_tool, codex_flow) | 7,712 | ops, bootstrap, agent lifecycle, deploy |
| `scripts/issue_tool/` | 5,004 | **Issue queue + worktree automation** |
| `scripts/codex_flow/` | 493 | AI agent workflow orchestration |
| `Makefile` | 1,033 | 122 targets |
| **Total tooling** | **14,242** | |

### Documentation

| Asset | Lines |
|-------|-------|
| `CLAUDE.md` | 482 |
| `docs/` (all .md) | 6,189 |
| `docs/openapi.yaml` | 2,208 |
| ADRs | 20 files |
| Runbooks | 10 files |
| Review issues | 13 files |

### Ratios

| Ratio | Value | Assessment |
|-------|-------|-----------|
| Test : Application | 1.67:1 | Appropriate for security-critical multi-tenant platform |
| Tooling : Application | 1.19:1 | **Disproportionate.** The harness is larger than the platform. See §7. |
| CDK test : CDK source | 0.54:1 | Reasonable |
| SPA test : SPA source | 0.41:1 | Acceptable for current maturity |

---

## 2. CR Issue Validation (Verified Against Code)

| ID | Title | v1 Status | v2 Verified Status | Evidence |
|----|-------|-----------|-------------------|----------|
| CR001 | Billing filter Key→Attr | Fixed | **FIXED** ✓ | `billing/handler.py:143` uses `Attr("SK")`, `Attr("status")`. Comment references CR001. |
| CR002 | Billing raw boto3 | Fixed | **FIXED** ✓ | `billing/handler.py:283` uses `db.update_item()` via data-access-lib. Comment references CR002. |
| CR003 | Billing race condition | Fixed | **FIXED** ✓ | `billing/handler.py:212` uses atomic `ADD` expression. No read-modify-write. Comment references CR003. |
| CR004 | S3 list truncation | Fixed | **FIXED** ✓ | `client.py:615` has `while True` loop with `ContinuationToken` / `IsTruncated` check. |
| CR005 | SigV4 full table scan | Resolved | **FIXED** ✓ | Issue marked RESOLVED. GSI `gsi-execution-role-arn` in CDK. In-memory 60s TTL cache in authoriser. |
| CR006 | Billing f-string logging | Fixed | **FIXED** ✓ | Zero `logger.*(f"` matches in `billing/handler.py`. All structured kwargs. |
| CR007 | Bootstrap dual-key attrs | Open | **OPEN** ✗ | `bootstrap.py:607-654` still writes both `"tenant_id"` and `"tenantId"`, `"created_at"` and `"createdAt"`, etc. 14 dual-key instances remain. |
| CR008 | Reserved tenant lockout | Partial | **PARTIALLY FIXED** | `validation.py:26` has `allow_reserved` parameter, used in `bootstrap.py:84` for admin callers. But only exempts `"platform"` — other reserved IDs (`admin`, `root`, `system`, `stub`) are still blocked for all operations including reads. |
| CR009 | Scan isolation bypass | Partial | **PARTIALLY FIXED** | `TenantScopedDynamoDB.scan()` raises `RuntimeError`. But `ControlPlaneDynamoDB` inherits from it and re-enables scan. No explicit `AdminDynamoDB` factory. Class hierarchy concern remains. |
| CR010 | Dev bootstrap tenant mismatch | Fixed | **FIXED** ✓ | `.env.test` writes `t-test-001` / `t-test-002` matching DynamoDB fixtures. Signing key is deterministic (`_LOCAL_SCOPED_TOKEN_SIGNING_KEY`). |
| CR011 | Authoriser PII logging | Fixed | **FIXED** ✓ | `jwt_service.py:47` logs `present_claims: sorted(payload.keys())`. No f-strings. No payload values in any logger call. |
| CR012 | Handler god module | Resolved | **FIXED** ✓ | Issue marked RESOLVED. handler.py reduced to dispatcher. 11 extracted modules. |
| CR013 | Bridge handler god module | Open | **OPEN** (facade variant) | handler.py is 602 lines — submodules exist and do the real work, but handler.py retains ~20 thin wrapper functions. See §5.1. |

**Summary:** 9 fixed, 2 partially fixed, 2 open. The two open issues (CR007, CR013) are low-to-medium severity.

---

## 3. New Findings — Architecture (Solutions Architect Review)

### ARCH-01: PlatformStack monolith blast radius — HIGH

PlatformStack owns all 11 Lambdas, all 8 DynamoDB tables, REST API, WAF, CloudFront, Secrets Manager, S3, SQS DLQs, CodeDeploy, Step Functions, EventBridge, and AppConfig. A single Lambda env var change puts the entire API surface into `UPDATE_IN_PROGRESS`.

The `createPlatformStorage` and `createPlatformCompute` functions already exist as logical boundaries — promoting them to separate stacks is the natural next step.

**Recommendation:** Extract storage, compute, and SPA into separate stacks. Use SSM parameters for cross-stack references.

### ARCH-02: VPC attachment contradicts ADR-014 — HIGH (~$120/month waste)

ADR-014 (accepted 2026-03-14) explicitly recommends control-plane Lambdas should NOT be VPC-attached. But `createPythonLambda` unconditionally attaches every Lambda to VPC with isolated subnets. This requires 4 interface endpoints across 2 AZs in 2 regions ≈ $120/month, plus 1-2s ENI cold start overhead per Lambda.

**Recommendation:** Remove VPC from the Lambda factory. Make VPC opt-in per Lambda. This is the single highest-impact change for cost and cold start performance.

### ARCH-03: Zigzag collapse is viable — HIGH (latency + complexity reduction)

AgentCore Runtime has been GA in eu-west-2 since 2026-01-26. The zigzag adds ~12ms RTT per invocation, cross-region metric streaming, and dual-region operational burden. Migration risk is low-to-medium (Runtime is stateless; Memory stores need migration plan; execution role regions need transition period).

**Recommendation:** Open successor ADR to ADR-009. Phase: add eu-west-2 to authorized regions → deploy AgentCoreStack in eu-west-2 → migrate Memory → flip SSM parameter → decommission eu-west-1 resources. Keep eu-central-1 as failover.

### ARCH-04: No authoriser error alarm — HIGH

The authoriser is on the critical path for every API request. If it fails (JWKS unreachable, DynamoDB throttle), the entire platform is down. There is a P99 latency alarm (FM-2) but no error count alarm.

**Recommendation:** Add alarm for authoriser error count > 0 over 1 minute. 5-minute implementation.

### ARCH-05: Per-tenant CloudWatch dashboards won't scale — MEDIUM

Each TenantStack creates a dedicated dashboard (10 widgets, $3/month). At 100 tenants = $300/month. At 1000 = $3,000/month.

**Recommendation:** Replace with a single parameterized dashboard using CloudWatch dashboard variables. $3/month regardless of tenant count.

### ARCH-06: DynamoDB provisioned without auto-scaling — MEDIUM

4 tables use provisioned capacity (5/5 RCU/WCU) with no auto-scaling. At these capacities, a burst of 6 concurrent reads throttles. Cost difference vs on-demand is ~$3/month per table — negligible.

**Recommendation:** Standardize on on-demand for all tables at this stage. Add auto-scaling later when traffic patterns are known.

---

## 4. New Findings — Senior Engineer Review

### ENG-01: Bridge Lambda missing `cloudwatch:PutMetricData` IAM — HIGH

Bridge calls `emit_invocation_metrics()` and `emit_bedrock_throttle_metric()` but the CDK stack only grants this permission to `billingFn`. Metrics will silently fail in production.

**Recommendation:** Add `cloudwatch:PutMetricData` to bridge Lambda IAM policy.

### ENG-02: Invite records never persisted — HIGH

`handle_invite_user()` emits an EventBridge event but never writes the invite to DynamoDB. `handle_list_invites()` queries for `SK begins_with("INVITE#")` which will always return empty. The invite flow is broken end-to-end.

**Recommendation:** Persist invite record before emitting event, or implement an EventBridge consumer that materialises invite records.

### ENG-03: STS AssumeRole cross-account wildcard — HIGH

Bridge Lambda's `sts:AssumeRole` resource pattern uses `*` for account ID: `arn:aws:iam::*:role/platform-tenant-*-execution-role`. This allows assuming any matching role in any AWS account.

**Recommendation:** Constrain to `${stack.account}` or maintain an explicit allow-list. If cross-account is intentional, document in threat model and add condition keys.

### ENG-04: Tenant list full scan won't scale — MEDIUM

`handle_list_tenants` calls `db.scan_all()` and filters in Python. O(n) on entire tenants table.

**Recommendation:** Add a GSI for filtered queries, or implement server-side pagination.

### ENG-05: Webhook DLQ URL nullable — silent message loss — MEDIUM

`send_to_dlq()` silently returns if `queue_url is None`. No validation at init time.

**Recommendation:** Validate `WEBHOOK_DLQ_URL` at module load time. Raise if missing.

### ENG-06: Tenant provisioner swallows transient errors — MEDIUM

Broad `except Exception` converts all errors to permanent `FAILED` state. Transient errors (throttling, network) should be retryable.

**Recommendation:** Distinguish transient from permanent errors. Return `RETRY` for transient.

---

## 5. New Findings — Code Quality Review

### CQ-01: `issue_tool/cli.py` is a 3,926-line god module — HIGH

135 functions spanning 8+ responsibility domains. The submodule extraction (11 files, 1,078 lines) is incomplete — cli.py is 4x larger than all its submodules combined.

**Recommendation:** See §7 for harness reduction proposal.

### CQ-02: Bridge handler.py facade bloat — MEDIUM

handler.py is 602 lines but only ~90 lines are the actual handler. The remaining 512 lines are thin wrapper functions that inject dependencies and forward to submodules.

**Recommendation:** Introduce a dependency container or context object. Target handler.py at ~150 lines.

### CQ-03: 2 residual f-string logging calls — LOW

`src/bridge/telemetry.py` lines 118 and 145 use `logger.warning(f"...")` instead of structured kwargs.

**Recommendation:** Replace with `logger.warning("message", error=str(e))`.

### CQ-04: Zero TODO/FIXME/HACK/XXX markers — POSITIVE

The entire codebase has zero deferred-work markers. Unusually disciplined.

---

## 6. GitNexus Utilisation Assessment

### Status: Installed but never run

- 6 skill files present in `.claude/skills/gitnexus/` (exploring, impact-analysis, debugging, refactoring, guide, cli)
- `AGENTS.md` contains GitNexus configuration block with instructions
- **No `.gitnexus/` directory exists** — the index has never been generated
- No `meta.json`, no knowledge graph, no embeddings
- The PostToolUse hook for auto-refresh after `git commit` has never fired (no index to refresh)

### What GitNexus would provide if activated

- Knowledge graph of all symbols, call chains, and module dependencies
- `query` tool for execution flow analysis
- `impact` tool for blast radius assessment (useful for the PlatformStack refactoring)
- `detect_changes` for git-diff impact analysis
- `rename` for coordinated multi-file refactoring (useful for cli.py extraction)

### Recommendation

Run `npx gitnexus analyze` to generate the initial index. The codebase is large enough (~12K lines of application code, 14 CDK stacks, 10 bridge submodules) that a knowledge graph would materially improve refactoring confidence — particularly for ARCH-01 (stack splitting) and CQ-01 (cli.py extraction). Cost: one-time ~30s analysis. Ongoing: auto-refresh via PostToolUse hook.

---

## 7. Harness Reduction Proposals

The tooling layer (14,242 lines) is 1.19x the size of the application code (11,935 lines). The primary contributor is `scripts/issue_tool/` at 5,004 lines — 42% of the entire platform source.

### Proposal A: Extract cli.py into domain modules (recommended)

Split cli.py (3,926 lines, 135 functions) into:

| New module | Responsibility | Est. lines |
|-----------|---------------|-----------|
| `worktree_ops.py` | Worktree create/list/resolve/clean | ~400 |
| `mux_sessions.py` | tmux + zellij integration | ~400 |
| `agent_handoff.py` | Expand existing — agent launch, prompt building, batch | ~500 |
| `finish_flow.py` | Finish summary, close issue, push enforcement | ~350 |
| `batch_ops.py` | Batch manifest, entry management, batch launch | ~300 |
| `audit_ops.py` | Issue audit, reconciliation, evidence drift | ~200 |
| `cli.py` (reduced) | Argparse + cmd_* dispatch only | ~900 |

**Net effect:** cli.py drops from 3,926 to ~900 lines. Total issue_tool stays ~5,004 but becomes maintainable.

### Proposal B: Retire deprecated Makefile targets

Remove 6 deprecated `task-*` targets and consolidate the 14 `validate-*` targets into a clearer hierarchy. Saves ~50 lines and reduces cognitive load.

### Proposal C: Assess codex_flow necessity

`scripts/codex_flow/` (493 lines) is an AI agent workflow orchestrator. If this is superseded by the issue_tool agent launch capabilities, it can be retired.

### Proposal D: Consolidate ops.py and bootstrap.py

`ops.py` (658 lines) and `bootstrap.py` (1,012 lines) share patterns for AWS client management, SSM parameter handling, and DynamoDB operations. A shared `platform_cli_base.py` module could reduce duplication by ~200 lines.

---

## 8. Priority-Ordered Action Plan

### Tier 1 — Before production (blocking)

| # | Finding | Source | Effort |
|---|---------|--------|--------|
| 1 | Add authoriser error alarm | ARCH-04 | 5 min |
| 2 | Add `cloudwatch:PutMetricData` to bridge IAM | ENG-01 | 5 min |
| 3 | Fix invite record persistence | ENG-02 | 1 hour |
| 4 | Constrain STS AssumeRole account scope | ENG-03 | 15 min |
| 5 | Validate webhook DLQ URL at init | ENG-05 | 10 min |

### Tier 2 — Before scaling (high-value)

| # | Finding | Source | Effort |
|---|---------|--------|--------|
| 6 | Remove VPC from control-plane Lambdas (ADR-014) | ARCH-02 | 1 day |
| 7 | Open successor ADR for zigzag collapse | ARCH-03 | 2 hours (ADR), 1 week (migration) |
| 8 | Split PlatformStack | ARCH-01 | 1 day |
| 9 | Standardize DynamoDB on-demand | ARCH-06 | 30 min |
| 10 | Replace per-tenant dashboards with parameterized | ARCH-05 | 2 hours |

### Tier 3 — Code quality (maintenance)

| # | Finding | Source | Effort |
|---|---------|--------|--------|
| 11 | Extract cli.py into domain modules | CQ-01 / §7 | 1 day |
| 12 | Reduce bridge handler.py facade bloat | CQ-02 / CR013 | 2 hours |
| 13 | Fix bootstrap dual-key attributes | CR007 | 1 hour |
| 14 | Fix 2 f-string logging in telemetry.py | CQ-03 | 5 min |
| 15 | Run `npx gitnexus analyze` | §6 | 1 min |

### Tier 4 — Deferred (track)

| # | Finding | Source |
|---|---------|--------|
| 16 | Tenant list scan scalability | ENG-04 |
| 17 | Tenant provisioner transient error handling | ENG-06 |
| 18 | CR008 reserved ID exemption for non-platform IDs | CR008 |
| 19 | CR009 explicit AdminDynamoDB factory | CR009 |
| 20 | Retire deprecated Makefile targets | §7 Proposal B |

---

## 9. Revised Verdict

The platform is architecturally sound and operationally mature. The v1 report overstated the gap to production by treating already-fixed CR issues as open. The actual state:

- **9 of 13 CR issues are resolved** in code
- **Zero TODO/FIXME markers** in the codebase
- **4-layer tenant isolation** is correctly implemented and tested
- **CI/CD pipeline** with OIDC, canary deployments, staging rollout windows, and prod approval gates is production-grade

The new findings from specialist reviews are more actionable than the remaining CRs:

- **5 blocking items** (Tier 1) are all quick fixes — total effort ~2 hours
- **5 high-value items** (Tier 2) are architectural improvements that reduce cost (~$120/month VPC savings), latency (~12ms zigzag elimination), and blast radius
- **The harness is disproportionate** (1.19x application code) but the primary issue is a single 3,926-line file that can be mechanically extracted

The platform is ready for controlled pilot use after Tier 1 fixes. Tier 2 should be completed before scaling beyond a handful of tenants.

---

## Appendix: Review Agents

| Agent | Focus | Key Findings |
|-------|-------|-------------|
| Code Quality Specialist | Code smells, module structure, logging, Makefile | cli.py god module, bridge facade bloat, 2 f-string residuals, zero TODO markers |
| Senior Engineer | Production readiness, error handling, test quality, IAM | Missing IAM permission, broken invites, cross-account wildcard, scan scalability |
| Solutions Architect | Stack isolation, region topology, data model, cost, observability | Monolith stack, VPC/ADR-014 gap, zigzag collapse, missing alarms, dashboard scaling |
