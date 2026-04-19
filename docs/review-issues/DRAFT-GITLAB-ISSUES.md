# Draft GitLab Issue Backlog

Source inputs:
- `docs/review-issues/FITNESS-FOR-PURPOSE-REPORT-V2.md`
- Senior-engineer remedy review in this session
- GitLab issue-process hardening completed in this session

Use this file as a staging list before creating live GitLab issues. Create issues with
`make issue-create` so every issue has the required `Seq:` and `Depends on:` parser fields
plus `type:task` and `status:not-started` labels.

## Creation Rules

- Use `Seq:` for ordering. Keep gaps of 10 so follow-up issues can be inserted.
- Prefer `Depends on: #<issue>` after live issue numbers exist. `TASK-###` remains accepted.
- Add `READY=1` only when the issue can be picked up immediately.
- Keep each issue atomic enough for one worktree and one MR.
- Do not use dependencies as a priority mechanism. Add `Depends on:` only for real technical
  ordering, not just because one issue is more important.

## Critical Review Notes

- Tier 1 should be created first, but not every Tier 2 item must depend on all Tier 1
  issues. Over-blocking the queue is one reason issue systems drift.
- Security-sensitive ambiguity should be its own issue before implementation. In
  particular, tenant execution role account scope needs an explicit decision before the
  policy is changed.
- Retire/deletion work should be tracked explicitly. Otherwise deprecated templates,
  shims, and local workflow files survive indefinitely because they are "not hurting
  anything" until they confuse the next agent.
- The backlog should prefer deletion before extraction for tooling. Only extract modules
  after deciding which legacy surfaces remain.

## Tier 1 - Pilot / Production Blockers

### TASK-101: Add authoriser failure alarms

Seq: 101
Depends on: none
Labels: `type:task`, `status:not-started`, `ready`
Source: ARCH-04, senior-engineer review

Problem:
The authoriser is on every API request path. Existing observability covers latency, but
not all authoriser failure modes. Lambda `Errors` alone is insufficient because some
internal failures are caught and returned as Deny.

Acceptance:
- Add an authoriser Lambda error alarm.
- Add a metric/log-filter alarm for internal authoriser failures or Deny-by-internal-error paths.
- Include CDK tests for the new alarms.
- Keep tenant/app dimensions and logging constraints intact.

Create:

```bash
make issue-create TITLE='TASK-101: Add authoriser failure alarms' SEQ=101 READY=1
```

### TASK-102: Grant Bridge scoped CloudWatch metric publishing

Seq: 102
Depends on: none
Labels: `type:task`, `status:not-started`, `ready`
Source: ENG-01, senior-engineer review

Problem:
Bridge emits `Platform/Bridge` metrics but does not have its own
`cloudwatch:PutMetricData` permission. The policy must not become a broad wildcard
exception beyond what CloudWatch requires.

Acceptance:
- Add `cloudwatch:PutMetricData` to the Bridge Lambda role.
- Keep `Resource: "*"` only because CloudWatch requires it for this action.
- Add a `cloudwatch:namespace = Platform/Bridge` condition.
- Add or update CDK and guard tests for the conditioned permission.

Create:

```bash
make issue-create TITLE='TASK-102: Grant Bridge scoped CloudWatch metric publishing' SEQ=102 READY=1
```

### TASK-103: Persist tenant invite records before notification

Seq: 103
Depends on: none
Labels: `type:task`, `status:not-started`, `ready`
Source: ENG-02

Problem:
`handle_invite_user()` emits an EventBridge event but does not write an `INVITE#...`
record. `handle_list_invites()` queries persisted invite records, so the invite list can
remain empty after successful invite creation.

Acceptance:
- Persist invite records under the tenant metadata table before emitting notification.
- Use a deterministic key shape such as `PK=TENANT#{tenantId}`, `SK=INVITE#{inviteId}`.
- Include status, expiry, actor, app metadata, and normalized email/role.
- Add tests for invite-create then invite-list.
- Define and test EventBridge failure behavior.

Create:

```bash
make issue-create TITLE='TASK-103: Persist tenant invite records before notification' SEQ=103 READY=1
```

### TASK-104: Decide tenant execution role account boundary

Seq: 104
Depends on: none
Labels: `type:task`, `status:not-started`, `ready`
Source: ENG-03

Problem:
Bridge can assume `arn:aws:iam::*:role/platform-tenant-*-execution-role`. That account
wildcard is too broad unless cross-account tenant execution is explicitly designed and
threat-modeled.

Acceptance:
- Decide and document whether tenant execution roles are same-account only.
- If cross-account is required, document the threat model and explicit account allow-list
  approach.
- If same-account is required, state that follow-up implementation must constrain STS to
  `${stack.account}`.
- No IAM policy mutation in this issue unless the decision is already unambiguous when
  the issue starts.

Create:

```bash
make issue-create TITLE='TASK-104: Decide tenant execution role account boundary' SEQ=104 READY=1
```

### TASK-106: Constrain Bridge tenant execution role assumption

Seq: 106
Depends on: TASK-104
Labels: `type:task`, `status:not-started`
Source: ENG-03

Problem:
After the account-boundary decision is recorded, the Bridge IAM policy must enforce it.

Acceptance:
- Remove the unconstrained `arn:aws:iam::*:role/platform-tenant-*-execution-role` resource.
- Implement the selected same-account or explicit allow-list model.
- Add CDK tests proving the wildcard account pattern is gone.
- Update threat-model notes if cross-account role assumption remains supported.

Create:

```bash
make issue-create TITLE='TASK-106: Constrain Bridge tenant execution role assumption' SEQ=106 DEPENDS='TASK-104'
```

### TASK-105: Fail webhook delivery fast when DLQ URL is missing

Seq: 105
Depends on: none
Labels: `type:task`, `status:not-started`, `ready`
Source: ENG-05, senior-engineer review

Problem:
`send_to_dlq()` silently returns when `WEBHOOK_DLQ_URL` is absent, causing exhausted
delivery failures to lose their DLQ message.

Acceptance:
- Make delivery configuration require a DLQ URL before processing records.
- Avoid import-time crashes for static imports/tests; validate when building or using
  delivery config.
- Add unit tests for missing DLQ config and successful DLQ send.

Create:

```bash
make issue-create TITLE='TASK-105: Fail webhook delivery fast when DLQ URL is missing' SEQ=105 READY=1
```

## Tier 2 - Scaling And Architecture

### TASK-201: Make VPC attachment opt-in for platform Lambdas

Seq: 201
Depends on: TASK-106
Labels: `type:task`, `status:not-started`
Source: ARCH-02, ADR-014

Problem:
The Lambda factory attaches all platform Lambdas to isolated VPC subnets despite ADR-014
preferring non-VPC control-plane Lambdas. This adds endpoint cost and cold-start overhead.

Acceptance:
- Change Lambda factory defaults to non-VPC.
- Add explicit opt-in for any Lambda with a real private-network dependency.
- Update CDK tests to assert expected VPC/non-VPC placement.
- Verify security group and endpoint assumptions after synth.

Create:

```bash
make issue-create TITLE='TASK-201: Make VPC attachment opt-in for platform Lambdas' SEQ=201 DEPENDS='TASK-106'
```

### TASK-202: Write ADR for eu-west-2 AgentCore runtime collapse

Seq: 202
Depends on: TASK-104
Labels: `type:task`, `status:not-started`
Source: ARCH-03

Problem:
AgentCore Runtime and Memory support in `eu-west-2` makes the current cross-region zigzag
a candidate for removal, but the migration needs an explicit decision record and rollback
plan.

Acceptance:
- Add successor ADR to ADR-009.
- Cover Runtime, Memory, execution-role region, observability, rollback, and failover.
- Record current AWS documentation references for supported regions.
- Do not implement migration in this issue.

Create:

```bash
make issue-create TITLE='TASK-202: Write ADR for eu-west-2 AgentCore runtime collapse' SEQ=202 DEPENDS='TASK-104'
```

### TASK-203: Split PlatformStack storage, compute, and SPA blast radius

Seq: 203
Depends on: TASK-201
Labels: `type:task`, `status:not-started`
Source: ARCH-01

Problem:
PlatformStack owns most control-plane resources. Small Lambda changes can put the whole
API surface into stack update. Existing `createPlatformStorage` and `createPlatformCompute`
boundaries are candidates for stack extraction.

Acceptance:
- Propose and implement stack boundaries for storage, compute, and SPA where safe.
- Use SSM parameters or explicit stack outputs for cross-stack references.
- Preserve deploy ordering and existing resource names where required.
- Add CDK tests for exported/imported references.

Create:

```bash
make issue-create TITLE='TASK-203: Split PlatformStack storage, compute, and SPA blast radius' SEQ=203 DEPENDS='TASK-201'
```

### TASK-204: Standardize platform DynamoDB tables on on-demand capacity

Seq: 204
Depends on: TASK-101
Labels: `type:task`, `status:not-started`
Source: ARCH-06

Problem:
Several tables use low provisioned capacity without auto-scaling. At current maturity,
on-demand capacity is simpler and avoids avoidable throttling.

Acceptance:
- Convert applicable platform tables to on-demand billing.
- Preserve PITR, KMS encryption, and deletion protection expectations.
- Update CDK tests and cost/operability notes as needed.

Create:

```bash
make issue-create TITLE='TASK-204: Standardize platform DynamoDB tables on on-demand capacity' SEQ=204 DEPENDS='TASK-101'
```

### TASK-205: Replace per-tenant dashboards with parameterized dashboard

Seq: 205
Depends on: TASK-101
Labels: `type:task`, `status:not-started`
Source: ARCH-05

Problem:
TenantStack creates a dedicated dashboard per tenant. This scales linearly in cost and
dashboard count.

Acceptance:
- Replace per-tenant dashboards with one parameterized CloudWatch dashboard.
- Preserve key tenant metrics and budget visibility.
- Update CDK tests to assert dashboard count and variables.
- Document any CloudWatch sharing limitations.

Create:

```bash
make issue-create TITLE='TASK-205: Replace per-tenant dashboards with parameterized dashboard' SEQ=205 DEPENDS='TASK-101'
```

## Tier 3 - Correctness / Maintainability

### TASK-301: Remove bootstrap dual-key tenant attributes

Seq: 301
Depends on: none
Labels: `type:task`, `status:not-started`
Source: CR007

Problem:
Bootstrap still writes both snake_case and camelCase variants for tenant metadata fields.
This keeps schema ambiguity alive.

Acceptance:
- Remove redundant dual-key writes from bootstrap paths.
- Preserve backward-compatible reads where still required.
- Add/update tests proving canonical keys are written.

Create:

```bash
make issue-create TITLE='TASK-301: Remove bootstrap dual-key tenant attributes' SEQ=301
```

### TASK-302: Clarify reserved tenant ID admin access

Seq: 302
Depends on: none
Labels: `type:task`, `status:not-started`
Source: CR008

Problem:
Reserved tenant ID handling was partially fixed for `platform`, but other reserved IDs
remain blocked for operations where admin read/access semantics may be required.

Acceptance:
- Define intended semantics for `admin`, `root`, `system`, and `stub`.
- Keep unsafe tenant creation blocked.
- Add explicit tests for allowed admin reads and forbidden writes.

Create:

```bash
make issue-create TITLE='TASK-302: Clarify reserved tenant ID admin access' SEQ=302
```

### TASK-303: Introduce explicit admin DynamoDB access factory

Seq: 303
Depends on: none
Labels: `type:task`, `status:not-started`
Source: CR009

Problem:
`ControlPlaneDynamoDB` re-enables scan through inheritance from tenant-scoped access.
The privilege boundary should be explicit.

Acceptance:
- Introduce or document an explicit admin/control-plane data access factory.
- Ensure tenant-scoped callers cannot obtain scan-capable clients.
- Add tests for forbidden tenant scan and allowed explicit admin scan.

Create:

```bash
make issue-create TITLE='TASK-303: Introduce explicit admin DynamoDB access factory' SEQ=303
```

### TASK-304: Add paginated tenant list path

Seq: 304
Depends on: TASK-303
Labels: `type:task`, `status:not-started`
Source: ENG-04

Problem:
Tenant listing scans all tenant records and filters in Python. This will not scale.

Acceptance:
- Implement server-side pagination and/or a queryable index for tenant list filters.
- Preserve tenant isolation and operator/admin access controls.
- Add tests for pagination and filter behavior.

Create:

```bash
make issue-create TITLE='TASK-304: Add paginated tenant list path' SEQ=304 DEPENDS='TASK-303'
```

### TASK-305: Classify tenant provisioner transient failures as retryable

Seq: 305
Depends on: none
Labels: `type:task`, `status:not-started`
Source: ENG-06

Problem:
Tenant provisioner broad exception handling can convert transient AWS failures into
permanent failed tenant state.

Acceptance:
- Classify known transient AWS errors separately from permanent validation failures.
- Return or emit retryable state for transient failures.
- Add tests for throttling/network-like errors and permanent errors.

Create:

```bash
make issue-create TITLE='TASK-305: Classify tenant provisioner transient failures as retryable' SEQ=305
```

### TASK-306: Replace bridge telemetry f-string warnings

Seq: 306
Depends on: none
Labels: `type:task`, `status:not-started`
Source: CQ-03

Problem:
Bridge telemetry has two residual f-string logging calls. Structured logging should be
used consistently.

Acceptance:
- Replace f-string warning logs with structured logger arguments.
- Add/update focused tests if logging behavior is already covered.

Create:

```bash
make issue-create TITLE='TASK-306: Replace bridge telemetry f-string warnings' SEQ=306
```

### TASK-307: Reduce bridge handler facade bloat

Seq: 307
Depends on: TASK-306
Labels: `type:task`, `status:not-started`
Source: CQ-02, CR013

Problem:
`src/bridge/handler.py` remains a wrapper-heavy facade even after core logic extraction.

Acceptance:
- Reduce wrapper/dependency-injection boilerplate without changing behavior.
- Keep handler entrypoint clear and testable.
- Preserve existing bridge tests.

Create:

```bash
make issue-create TITLE='TASK-307: Reduce bridge handler facade bloat' SEQ=307 DEPENDS='TASK-306'
```

## Tier 4 - Retire Before Refactor

### TASK-401: Retire GitHub issue-template residue

Seq: 401
Depends on: none
Labels: `type:task`, `status:not-started`, `ready`
Source: session GitLab migration

Problem:
GitHub issue templates and docs are obsolete now that GitLab Issues are canonical.
Keeping mirror templates makes it easy to create work in the wrong tracker.

Acceptance:
- Delete `.github/ISSUE_TEMPLATE/` and any empty `.github/` directory if no other GitHub
  configuration remains.
- Update docs/tests to reference `.gitlab/issue_templates/Task.md`.
- Verify stale-reference search finds no GitHub issue-process references outside
  historical review notes or third-party download URLs.

Create:

```bash
make issue-create TITLE='TASK-401: Retire GitHub issue-template residue' SEQ=401 READY=1
```

### TASK-402: Retire deprecated snapshot task workflow

Seq: 402
Depends on: none
Labels: `type:task`, `status:not-started`
Source: Proposal B

Problem:
`make task-*` is deprecated but remains documented and implemented. It competes with the
GitLab issue worktree flow.

Acceptance:
- Remove or archive deprecated `task-*` Make targets and `scripts/task.py` if no longer used.
- Preserve any still-useful prompt-generation behavior in issue_tool if required.
- Update docs/tests.

Create:

```bash
make issue-create TITLE='TASK-402: Retire deprecated snapshot task workflow' SEQ=402
```

### TASK-403: Decide whether codex_flow remains necessary

Seq: 403
Depends on: none
Labels: `type:task`, `status:not-started`
Source: Proposal C

Problem:
`scripts/codex_flow/` overlaps with issue_tool agent/worktree orchestration. Keeping both
may confuse operators.

Acceptance:
- Decide whether codex_flow is retained, reduced, or removed.
- If retained, document its distinct purpose.
- If removed, migrate any useful behavior into issue_tool or docs.

Create:

```bash
make issue-create TITLE='TASK-403: Decide whether codex_flow remains necessary' SEQ=403
```

### TASK-404: Retire compatibility shims and generated caches

Seq: 404
Depends on: TASK-401, TASK-402, TASK-403
Labels: `type:task`, `status:not-started`
Source: session cleanup review

Problem:
Legacy shims and generated caches increase the apparent surface area and cause stale
references in search results.

Acceptance:
- Delete `scripts/worktree_issues.py` after confirming all docs/tests invoke
  `python -m scripts.issue_tool`.
- Remove committed/generated `__pycache__` files if any are tracked.
- Confirm `.gitignore` excludes Python bytecode and local agent state directories.
- Keep only one canonical issue/worktree entrypoint.

Create:

```bash
make issue-create TITLE='TASK-404: Retire compatibility shims and generated caches' SEQ=404 DEPENDS='TASK-401,TASK-402,TASK-403'
```

## Tier 5 - Refactor Only After Retirement

### TASK-501: Finish issue_tool module extraction

Seq: 501
Depends on: TASK-404
Labels: `type:task`, `status:not-started`
Source: CQ-01, session issue-process hardening

Problem:
`scripts/issue_tool/cli.py` remains too large and owns multiple domains despite the new
GitLab-first tracker client.

Acceptance:
- Extract worktree operations, finish/closeout flow, audit/reconcile, and batch/mux logic
  into focused modules.
- Keep command behavior and tests stable.
- Do not add dependencies.

Create:

```bash
make issue-create TITLE='TASK-501: Finish issue_tool module extraction' SEQ=501 DEPENDS='TASK-404'
```

### TASK-502: Consolidate shared CLI AWS helpers

Seq: 502
Depends on: none
Labels: `type:task`, `status:not-started`
Source: Proposal D

Problem:
`ops.py` and `bootstrap.py` share AWS client, SSM, and DynamoDB helper patterns.

Acceptance:
- Extract shared helper code only where duplication is real and behavior is covered.
- Keep bootstrap and ops command behavior stable.
- Add focused tests for shared helper behavior.

Create:

```bash
make issue-create TITLE='TASK-502: Consolidate shared CLI AWS helpers' SEQ=502
```
