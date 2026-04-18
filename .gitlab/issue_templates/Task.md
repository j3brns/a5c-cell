<!--
Use this template for queueable implementation work.
Apply labels: type:task, status:not-started.
Add ready when the task may be picked by make issue-queue in ready mode.
-->

Seq:

Depends on: none

## Problem

Describe the problem to solve and why it matters.

## Scope

Keep the change narrowly scoped to this issue.

## Acceptance Criteria

- [ ] The requested behaviour is implemented.
- [ ] Existing behaviour outside this scope is unchanged.
- [ ] Security, tenant isolation, and operability constraints in CLAUDE.md are preserved.

## Test Plan

List the smallest command(s) that prove the change works.

## Definition of Done

- [ ] Implementation and tests are complete for this issue.
- [ ] `make validate-local` passes, or the accepted equivalent is recorded.
- [ ] Senior engineer review is complete and findings are resolved or explicitly accepted.
- [ ] `make preflight-session` and `make pre-validate-session` pass before push.
- [ ] Merge request is merged.
- [ ] `make finish-worktree-close` closes and normalizes the issue.
