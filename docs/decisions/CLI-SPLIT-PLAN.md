# Dry YAGNI Plan: CLI Monolith Split & Tech Debt Removal

**Goal:** Execute ADR-022 and SPEC-DEV-EXPERIENCE-AND-ADR703 by decoupling `agent-cli` from `platform-cli` and refactoring the 4,600-line `scripts/issue_tool/cli.py` monolith.

## Phase 1: Issue Tool Modularization (TASK-701)
Extract independent domain boundaries from `scripts/issue_tool/cli.py` into `scripts/issue_tool/`:
- **GitNexus Integration:** Extract `gitnexus_*` functions.
- **Pre-provisioning:** Extract `worktree_preprovision_*` logic.
- **Worktree Lifecycle:** Extract `create_worktree`, `close_issue`, `issue_closed` logic.
- **Tmux Integration:** Extract terminal multiplexer session management.
*YAGNI Principle:* Pure lift-and-shift. No re-engineering of the extracted functions. Update imports only.

## Phase 2: DevEx Separation (Stream 8xx)
Implement developer-focused CLI entry points and Makefile targets.
- Rename `platform_cli.py` sub-apps (`agent`, `dev`) into a new `scripts/agent_cli.py`.
- Add `bootstrap-agent` and `help-agent` to the Makefile.
- Create `agents/Makefile.agent.template` for per-agent loop (`make test`, `make dev`).
- Update `pyproject.toml` to register `agent-cli`.
*YAGNI Principle:* Only what is strictly necessary to separate the personas; do not rewrite the platform commands.

## Phase 3: Typer Migration (TASK-056)
Migrate the slimmed down `platform_cli.py` and `agent_cli.py` from `argparse` to `typer`.
*YAGNI Principle:* 1:1 translation of existing arguments to Typer annotations.

## Phase 4: Clean up
Verify tests, run full validation suite (`make validate-local-full`), and ensure zero regressions.

---

### Simulated Review / Questions
1. Should `gitnexus` and `pre_provisioning` logic go into `scripts/issue_tool/integrations.py` or separate files? (Assume separate files for now).
2. For the Makefile per-agent template, should it be auto-copied during scaffolding? (Assume yes, will update the `agent_cli` scaffold command).
3. Do we need to retain alias commands in `platform-cli` for `agent-cli` functions temporarily? (Assume no, ADR-022 mandates independent CLIs with clear boundaries).

Proceeding immediately with Phase 1 execution.