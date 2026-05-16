  ## Ground Rules

  You are an independent worker agent operating in this repo.
  
  Project Status & Policy:
  - This system is in active development and is NOT YET LIVE. 
  - Substantial refactors and structural updates are acceptable and encouraged to reach target state.
  - We are in a "Zero Technical Debt" phase. Do the work the right way the first time.
  - Prioritize durability, scalability, operability, and security over speed.

  Instruction precedence:
  1. `CLAUDE.md` (project source of truth)
  2. This task prompt
  3. Your default preferences

  Engineering standards (Rationale: eliminate legacy drift before it starts):
  - Do the work the right way. Do not introduce avoidable technical debt.
  - If a clean solution is materially larger/slower, state the tradeoff and ask before taking shortcuts.
  - No compatibility shims: Since we are not yet live, we do not need to support legacy callers.
  - No wrappers for deprecated APIs: Fix the root cause instead of hiding it.
  - Fix the code directly at the call sites / implementation layer to keep the architecture flat and traceable.

  Code modification rules (Rationale: maintain surgical precision and auditability):
  - NEVER run scripts/tools that bulk-process or rewrite source files in this repo. Bulk changes obscure the intent of
  the individual task and increase the risk of undetected regressions.
  - Make code changes manually and methodically. This ensures that every line of code is intentional and validated
  within the context of the specific issue.
  - Allowed: build/test/lint/typecheck/validation commands and normal project tooling (`make`, `pytest`, `ruff`,
  `pyright`, etc.).

  No file proliferation (Rationale: keep the codebase lean and discoverable):
  - Prefer revising existing files in place. A single source of truth is easier to maintain than multiple variants.
  - Do not create renamed variants like `*_v2`, `*_improved`, `*_enhanced`. These create confusion about which version
  is current.
  - New files are only for genuinely new functionality that does not belong in an existing file.

  ## Begin Task

  Start by assigning yourself the next runnable issue from the canonical queue.

  Follow `CLAUDE.md` exactly.

  Issue selection (canonical):
  1. GitLab Issues are the source of truth (ordered by `Seq:` and gated by `Depends on:`).
  2. Run `make issue-queue` and pick the next runnable issue (or use the operator-provided issue).
  3. State: `Starting issue #NNN: <title>` (include `TASK-XXX` when present in title).
  4. Read the ADR(s) linked to that issue/task before coding. ADRs provide the "why" behind the existing design.
  5. Use the issue worktree protocol (`make worktree`). This keeps the primary repository checkout clean and allows
  for parallel task execution.
  6. Never begin implementation directly on `main` in the primary repo working tree.
  7. If no runnable issue exists, report that clearly and stop.

  Then:
  1. Read `docs/ARCHITECTURE.md` to understand the system-wide impact of your changes.
  2. Read the relevant ADR(s) to align with established architectural decisions.
  3. Give a short plan with expected file changes to allow for early operator course-correction.

  Execution rules (Rationale: drive to high-signal completion):
  - Drive the task to completion. Do not stop at the first failure.
  - Use failures and signals (tests, validation output, lint/typecheck/synth errors, logs, git state) as feedback to
  guide the next fix.
  - When a check fails: diagnose -> hypothesize -> fix -> re-run the smallest relevant check -> continue.
  - If GitNexus context becomes stale or a PostToolUse hook notifies of staleness, run `make gitnexus-refresh`. A
  stale index leads to incorrect impact analysis and missed dependencies.
  - Only stop for explicit `CLAUDE.md` stop/ask conditions, gate tasks, or operator-required decisions.

  Completion rules (Rationale: verify before delivery):
  - Run final validation (`make validate-local`). A task is not complete until its correctness is proven.
  - Before any push, run `make preflight-session` and `make pre-validate-session`. This prevents broken builds from
  reaching the remote.
  - Run a senior engineer review on your changes. Identify bugs, regressions, and risks before the operator sees them.
  - Action findings, re-run checks, and review again until clear.
  - Do not close/push until errors are cleared.
