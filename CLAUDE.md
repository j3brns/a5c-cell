# CLAUDE.md — Rules for AI Coding Assistants
# Read this at the start of every session. No exceptions.

If this repository also contains a `GEMINI.md`, read it alongside this file.

## What This Platform Is

A production multi-tenant Agent as a Service platform on Amazon Bedrock AgentCore.
B2E users and E2B integrations invoke AI agents via REST API. The platform manages isolation, identity,
memory, tool access, billing, and observability. This is a production system — not
a prototype — with real tenants, real data, and real compliance obligations.

## Priority Order

When trade-offs arise, resolve in this order:
1. Security — a security flaw ships last, regardless of schedule
2. Operability — ops must run this at 3am without a developer on call
3. Correctness — wrong behaviour is worse than slow behaviour
4. Performance — optimise only after correctness is proven
5. Developer experience — the inner loop matters, but it is last

## Absolute Constraints (non-negotiable)

If any implementation path violates these, stop, state the conflict, propose an
alternative. Never silently work around them.

1. No Cognito anywhere. Auth is Entra ID OIDC/JWT for humans, SigV4 for machines.
2. No hardcoded credentials, ARNs, account IDs, secrets, or region strings.
   Exception: CDK stack definitions may declare the home region (eu-west-2) as an
   architectural constant (e.g. const HOME_REGION = 'eu-west-2'). This constraint
   applies to application code — Lambda handlers and scripts that call AWS APIs at
   runtime must always read the region from os.environ['AWS_REGION'].
3. No IAM policies with wildcard Action or wildcard Resource.
4. No public S3 buckets.
5. No long-lived AWS access keys. Bootstrap IAM user deleted after first deploy.
6. No secrets in GitLab CI/CD variables — Secrets Manager only.
7. Every Lambda: X-Ray tracing, DLQ, structured JSON logging with appid+tenantid.
8. Every DynamoDB table: PITR, KMS encryption, deletion protection in staging/prod.
9. AgentCore Runtime is arm64 only. Dependencies cross-compiled aarch64-manylinux2014.
   Sync limit 15 minutes. Async uses app.add_async_task / app.complete_async_task.
10. No impersonation — act-on-behalf only. Original JWT never reaches tool Lambdas.
11. appid and tenantid on every log line, metric dimension, and trace annotation.
12. data-access-lib is the only permitted way to access DynamoDB from Lambda handlers.
13. No superuser IAM roles in normal operation.
14. All data remains in the EU at all times.
15. `platform-tenants` is control-plane metadata only. Do not write high-frequency
    runtime activity, counters, or last-seen markers to tenant `METADATA` records.
    Use `platform-invocations`, `platform-sessions`, CloudWatch metrics, or a
    dedicated aggregate path for hot activity data.

## How To Work

Before writing any code:
1. Read this file
2. Read docs/ARCHITECTURE.md
3. Identify the issue you are working on. GitLab Issues are the canonical task queue; `docs/TASKS.md` is a snapshot.
4. Read the ADR(s) linked to the current task/issue (use `docs/TASKS.md` as a reference snapshot when needed)
5. In local WSL, confirm you are in an issue worktree on a policy branch (not `main` in the primary repo working tree)
6. Start from a known fresh issue worktree based on current `main`/`origin/main`; do not begin implementation in a stale resumed worktree without first refreshing or recreating it
7. If not already in that fresh issue worktree, start via `make worktree` / `make worktree-next-issue` unless the operator explicitly instructs in-place work
8. If you are in local WSL with the repo checked out, run `make validate-local` — confirm it passes
   (use `make validate-local-full` when a full-repo secret scan is required)
9. State which issue/task you are working on explicitly
10. If branch validation exposes unrelated breakage or drift outside the active issue scope, stop bundling it into the same branch: queue/fix it separately or refresh onto a mainline that already contains that fix, then restart from a fresh worktree.

Before marking any task complete:
1. All tests pass
2. `make validate-local` passes
3. Senior engineer review completed (code review mindset: bugs, regressions, risks, missing tests)
4. Review recommendations are actioned
5. Senior engineer review re-run and clear (or remaining risks explicitly accepted by operator)
6. New infrastructure passes cfn-guard
7. Before any push: run `make preflight-session` and `make pre-validate-session` (fast path, no cdk synth)
8. State completion with the issue/task identifier (for legacy tasks, `TASK-NNN complete. Tests passing.`)

When uncertain about a security decision — stop and ask. Do not guess.

When changing AWS infrastructure or service configuration, verify service-specific
assumptions against current AWS documentation before shipping. Use the
`aws-knowledge-mcp-server` tools first for AWS service details, parameters,
permissions, and regional support. Use web search only as a last resort when the
AWS MCP tools do not provide the required detail. Do not infer required
properties, encryption behavior, IAM actions, or regional support from old code
or memory. Record the specific AWS doc URL(s) used in the issue, review notes,
or commit/MR narrative whenever the assumption affects resource shape,
permissions, encryption, or region policy.

### Execution Loop (Drive To Completion)

The agent should drive the task to completion without stopping at the first error.
Use failure output and operational signals to diagnose and fix the next issue until
the closure criteria are met.

Preferred signals (use what is available in the current environment):
- Test failures and stack traces (`pytest`, Jest, `make test-*`)
- Validation output (`make validate-local`, `make validate-local-full`)
- Fast pre-push validation (`make validate-pre-push`, `make pre-validate-session`)
- Lint/typecheck output (Ruff, Pyright, TypeScript)
- CDK synth/deploy error output
- Local runtime logs (`make dev-logs`, `docker compose logs`)
- Platform logs (`make logs-*`, `aws logs tail ...`)
- Git state (`git status`, diff, merge conflicts)

Do not stop just because one command failed. Investigate the error, form a hypothesis,
apply a fix, and re-run the smallest relevant check. Only stop for the explicit
"stop and ask" conditions, gate tasks, or when the operator redirects you.
Do not stop at intermediate delivery milestones such as local commit, branch push,
or MR creation. Continue through merge and required closeout steps until the issue
meets the repository Definition of Done, unless one of the explicit stop/escalate
conditions applies.

## When To Stop And Ask

- Any change to DynamoDB partition key or GSI design
- Any change to IAM policies or trust relationships
- Any change to authoriser Lambda validation logic
- Any new dependency adding >10MB to the deployment package
- Any change affecting tenant isolation in data-access-lib
- Any change to KMS key policy
- Any operation touching production data

## Naming Conventions

- AWS resources: platform-{resource}-{environment}
- Python: snake_case everywhere — this includes source directory names.
  Lambda source dirs must be snake_case (src/async_runner/, not src/async-runner/)
  because hyphenated names cannot be Python package names and break static type checking.
- Every Python source directory must contain an __init__.py so Pyright resolves
  identically-named modules (e.g. handler.py) as distinct packages.
- TypeScript: camelCase properties, PascalCase classes
- Environment variables: SCREAMING_SNAKE_CASE
- SSM: /platform/{category}/{name}
- DynamoDB keys: {ENTITY}#{id}

## Forbidden Patterns

```python
# FORBIDDEN: raw boto3 DynamoDB in handlers
dynamodb.Table('platform-tenants').get_item(...)

# REQUIRED: data-access-lib only
from data_access import TenantScopedDynamoDB
db = TenantScopedDynamoDB(tenant_context)

# FORBIDDEN: hardcoded region
boto3.client('ssm', region_name='eu-west-2')

# REQUIRED: from environment
boto3.client('ssm', region_name=os.environ['AWS_REGION'])

# FORBIDDEN: bare exception silencing
try:
    do_something()
except Exception:
    pass

# REQUIRED: log and handle
try:
    do_something()
except TenantAccessViolation as e:
    logger.error("Tenant access violation", extra={"tenant_id": tenant_id})
    return error_response(403, "UNAUTHORISED")
```

## Issue Workflow (Canonical)

GitLab Issues are the canonical task queue (effective 2026-04-18 local).
Use issue `Seq:` for ordering and `Depends on:` for dependency gating.
`docs/TASKS.md` is a snapshot/report and may lag.
Use `glab` for issue and merge-request operations. The issue worktree tooling
defaults to the `gitlab` remote.

Parent `CR-*` issues are roadmap/design containers, not runnable tasks.
They must not carry `type:task`, and they do not enter the issue queue.
Only atomic child task issues are queueable and should carry `Seq:` / `Depends on:`.
Parent `CR-*` issues do not count toward WIP limits; WIP is tracked on child task issues only.
Merge request merge is delivery truth. Local `.build` artifacts are execution evidence for this clone.
Use `make issue-evidence ISSUE=<n>` to inspect linked worktree and `.build` state for an issue.
Missing local `.build` evidence must never auto-close or auto-reopen a GitLab issue by itself.

### Queue and worktree commands (preferred)

```bash
make issue-create TITLE='TASK-123: Summary' SEQ=123
make issue-queue                    # dependency-aware queue ordered by Seq
make worktree-next-issue            # create worktree for next runnable issue
make worktree                       # interactive queue/worktree/finish menu
make worktree-create-issue ISSUE=23 # explicit issue
make worktree-resume-issue          # resume existing linked issue worktree
```

### Push policy (mandatory)

All pushes must be pre-validated. Use the enforced path:

```bash
make worktree-push-issue            # runs preflight + validate-pre-push, then pushes
```

Or, at minimum, run before a manual push:

```bash
make preflight-session
make pre-validate-session           # fast path, no cdk synth
```

Install local hook once per clone:

```bash
make install-git-hooks              # installs .githooks/pre-push
```

The pre-push hook runs `make validate-pre-push` (fast path; no CDK synth).
Opening an MR is not completion by itself; after push, continue through MR merge and
`make finish-worktree-close` unless a listed blocker requires escalation.

### Issue lifecycle label policy (mandatory)

- Every task issue must have exactly one `status:*` label at all times.
- Open task issues must never be `status:done`.
- Closed task issues must always be `status:done` and must never retain `status:in-progress`, `status:not-started`, or `ready`.
- Parent `CR-*` issues must not carry `type:task`; if they do, the issue audit must fail.
- Parent `CR-*` issues must not carry `status:in-progress`; active implementation is tracked on child task issues.
- `make finish-worktree-close` is the required close path even if the issue was already closed manually; it is the normalization and hand-back step for lifecycle labels and `.build` evidence.
- If issue state or labels drift, run `make issues-reconcile` immediately, then re-run `make issues-audit` until it passes.

### Issue Definition of Done (mandatory)

An issue is done only when all items below are true:
1. Implementation and tests are complete for the scoped issue; no unresolved TODOs for that scope.
2. `make validate-local` passes in the issue worktree (or equivalent required checks for remote/mobile mode).
3. Senior engineer review is complete; findings are fixed or explicitly accepted in writing.
4. `make preflight-session` and `make pre-validate-session` pass on the final branch state.
5. Branch is pushed and MR is open with validation evidence and issue linkage.
6. MR is merged (not just opened).
7. `.build` hand-back evidence is finalized; do not leave the issue in partial local state such as `agent-launching` or an incomplete closeout.
8. Issue is closed and normalized only after merge verification (`make finish-worktree-close`).
9. `make issues-audit` passes after close; if not, run `make issues-reconcile` and re-audit before declaring the issue complete.
10. Cleanup residue is reported explicitly if present, but worktree or branch deletion is not part of semantic completion and must not block done status by itself.

### Merge Conflict Rule (mandatory)

Never leave a task in a merge-conflicted state.
1. If branch update or MR merge reports conflicts, resolve them in the issue worktree immediately.
2. Re-run targeted tests plus `make preflight-session` and `make pre-validate-session`.
3. Push the conflict-resolution commit(s) and confirm the MR is mergeable before closing the issue.
4. If a required permission/policy blocks merge, stop and report the blocker with the exact next command.

## Technology Stack

| Concern            | Technology                      |
|--------------------|---------------------------------|
| Agent runtime      | AgentCore Runtime eu-west-1     |
| Human auth         | Microsoft Entra ID OIDC         |
| Machine auth       | AWS SigV4                       |
| IaC platform       | CDK TypeScript strict           |
| IaC account vend   | Terraform HCL                   |
| Python packaging   | uv + pyproject.toml             |
| Logging            | aws_lambda_powertools Logger    |
| Testing CDK        | Jest + cdk assertions           |
| Testing Python     | pytest + LocalStack             |
| Secrets            | AWS Secrets Manager             |
| Config             | SSM Parameter Store             |
| Async agents       | AgentCore add_async_task SDK    |
| Observability      | AgentCore Observability + CW    |

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

Use GitNexus as a staff-engineer risk tool: it is required for high-blast-radius or
unfamiliar code changes, but optional for docs-only, issue-tracker, simple config,
formatting, and narrow single-file changes.

> If any GitNexus tool warns the index is stale, refresh with `make gitnexus-refresh`.
> If `.gitnexus/meta.json` has `stats.embeddings > 0`, preserve embeddings with
> `npx gitnexus analyze --embeddings`.

## Required Use

Run GitNexus context/impact before:
- renaming, moving, or extracting functions/classes/modules
- editing shared runtime paths or data-access-lib behavior
- changing tenant isolation, authoriser, bridge, gateway, or IAM-sensitive flows
- changing CDK stack boundaries or shared infrastructure contracts
- touching multiple modules where call graph impact is not obvious

Before committing non-trivial code changes, run GitNexus `detect_changes` or document why
GitNexus was unavailable or not useful. Warn the user before continuing if impact analysis
returns HIGH or CRITICAL risk.

## Optional Use

GitNexus is optional for:
- docs-only changes
- GitLab issue/backlog work
- local workflow config
- deleting temporary staging files
- straightforward single-test changes
- simple formatting/lint fixes

Use `rg`, direct file reads, and focused tests for these low-blast-radius changes.

## Recommended Flow

1. `gitnexus_query({query: "<concept or symptom>"})` for unfamiliar flows.
2. `gitnexus_context({name: "<symbol>"})` for callers, callees, and process participation.
3. `gitnexus_impact({target: "<symbol>", direction: "upstream"})` before risky edits.
4. `gitnexus_detect_changes({scope: "all"})` before committing non-trivial code changes.
5. Use `gitnexus_rename(..., dry_run=true)` before any coordinated rename.

## Tools Quick Reference

| Tool | When to use | Command |
|------|-------------|---------|
| `query` | Find code by concept | `gitnexus_query({query: "auth validation"})` |
| `context` | 360-degree view of one symbol | `gitnexus_context({name: "validateUser"})` |
| `impact` | Blast radius before editing | `gitnexus_impact({target: "X", direction: "upstream"})` |
| `detect_changes` | Pre-commit scope check | `gitnexus_detect_changes({scope: "staged"})` |
| `rename` | Safe multi-file rename | `gitnexus_rename({symbol_name: "old", new_name: "new", dry_run: true})` |
| `cypher` | Custom graph queries | `gitnexus_cypher({query: "MATCH ..."})` |

## Impact Risk Levels

| Depth | Meaning | Action |
|-------|---------|--------|
| d=1 | WILL BREAK — direct callers/importers | MUST update these |
| d=2 | LIKELY AFFECTED — indirect deps | Should test |
| d=3 | MAY NEED TESTING — transitive | Test if critical path |

## Resources

Use `mcp__gitnexus__list_repos` or `.gitnexus/meta.json` for current repo/index identity.
Do not hardcode stale generated repo names such as `wt389` or a worktree folder name.

## Keeping the Index Fresh

After committing code changes, the GitNexus index becomes stale. Prefer:

```bash
make gitnexus-refresh
```

If running manually, inspect `.gitnexus/meta.json`. When `stats.embeddings > 0`, run:

```bash
npx gitnexus analyze --embeddings
```

Running analyze without `--embeddings` deletes previously generated embeddings.

## CLI

| Task | Read this skill file / Command |
|------|-------------------------------|
| Issue/worktree health check | `make issue-status` |
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
