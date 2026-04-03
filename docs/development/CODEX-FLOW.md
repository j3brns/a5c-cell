# Codex Flow

`codex_flow` is a separate local kanban for agent work. It does not read GitHub
issue labels, does not use the repository issue queue, and does not try to be
the canonical workflow. It is just a lightweight local board for planning and
tracking work while you keep using Codex or Gemini.

Local state lives entirely under `.codex-flow/`.

## What it is for

- keeping a small local backlog
- moving cards between `backlog`, `next`, `doing`, `blocked`, and `done`
- assigning a card to `codex`, `gemini`, or `manual`
- attaching optional metadata like `issue`, `worktree_path`, and `branch`
- keeping short per-card notes

## What it is not

- not the GitHub issue queue
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
uv run python -m scripts.codex_flow import-github --state open --lane backlog
uv run python -m scripts.codex_flow board
```

## GitHub migration

If you already have GitHub issues and want them in the local board:

```bash
uv run python -m scripts.codex_flow init
uv run python -m scripts.codex_flow import-github --state open --lane backlog
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

The point is to give you a local kanban without forcing the GitHub issue system
to act like one.
