# Codex Flow

Decision: retain `codex_flow` as a narrow local planning scratchpad, not as an
implementation or issue-lifecycle workflow.

`codex_flow` is a separate local kanban for agent work. It does not read GitLab
issue labels, does not use the repository issue queue, and does not try to be
the canonical workflow. It is just a lightweight local board for planning and
tracking work while you keep using Codex or Gemini.

Local state lives entirely under `.codex-flow/`.

## When to use it

Use GitLab Issues and `scripts.issue_tool` for all repo work that needs queue
ordering, dependency gates, status labels, worktree creation, validation, merge
requests, closeout evidence, or issue closure.

Use `codex_flow` only when the work is intentionally local and non-canonical:

- sketching a personal scratch backlog before deciding whether issues are needed
- coordinating temporary Codex/Gemini/manual lanes outside the GitLab lifecycle
- keeping private notes for short-lived agent handoffs
- importing GitLab issue titles as local cards without changing their labels,
  queue order, or lifecycle state

Do not choose `codex_flow` instead of GitLab Issues for tracked platform tasks.
If the work must be delivered, reviewed, merged, or closed, create or use a
GitLab issue and the issue-driven worktree flow.

## Command comparison

| Need | Use | Why |
|------|-----|-----|
| Create or order task work | `scripts.issue_tool issue-create` / `issue-queue` | GitLab issue metadata is the canonical queue. |
| Start, resume, or close a worktree | `scripts.issue_tool worktree-*` / `finish-*` | Worktree state, labels, validation receipts, and hand-back evidence stay connected. |
| Push a branch | `scripts.issue_tool push-branch` | Preflight and pre-push validation are enforced before `git push`. |
| Finish delivery | `scripts.issue_tool finish-close` | Merged-MR verification, issue closeout, and label normalization are enforced. |
| Launch agents for issue work | `scripts.issue_tool agent-handoff` / `wt-batch` | Agent launch remains attached to the issue worktree and evidence path. |
| Track a temporary local card | `scripts.codex_flow add` / `move` / `assign` | The card is local-only and does not affect GitLab lifecycle state. |
| Keep a local note for a card | `scripts.codex_flow note` | Notes live under `.codex-flow/` and are not delivery evidence. |
| Mirror issues into a scratch board | `scripts.codex_flow import-gitlab` | Imports issue numbers and titles as local metadata; it does not claim, label, order, or close issues. |

## What it is for

- keeping a small local backlog
- moving cards between `backlog`, `next`, `doing`, `blocked`, and `done`
- assigning a card to `codex`, `gemini`, or `manual`
- attaching optional metadata like `issue`, `worktree_path`, and `branch`
- keeping short per-card notes

## What it is not

- not the GitLab issue queue
- not label-driven
- not tied to `ready` or `status:*`
- not a replacement for the canonical repo lifecycle

## Commands

```bash
uv run python -m scripts.codex_flow init
uv run python -m scripts.codex_flow add "stabilize bridge auth split" --lane next
uv run python -m scripts.codex_flow assign 1 --owner codex --role implement
uv run python -m scripts.codex_flow move 1 --lane doing
uv run python -m scripts.codex_flow attach 1 --issue 388 --path ../worktrees/wt388
uv run python -m scripts.codex_flow note 1
uv run python -m scripts.codex_flow import-gitlab --state open --lane backlog
uv run python -m scripts.codex_flow board
```

## Tracker migration

If you already have tracker issues and want them in the local board:

```bash
uv run python -m scripts.codex_flow init
uv run python -m scripts.codex_flow import-gitlab --state open --lane backlog
```

That imports issue numbers and titles as cards. It does not make the board
dependent on labels or the issue queue after import.

## Suggested usage

Use it as a separate planning surface:

- add cards for what you actually want to do
- move one or two cards into `doing`
- assign one owner per card
- attach issue/worktree metadata only when useful

Recommended split:

- `codex` + `implement`
- `gemini` + `review`
- `gemini` + `plan`
- `manual` for work you do yourself

The point is to give you a local kanban without forcing the canonical issue system
to act like one.
