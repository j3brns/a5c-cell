# Testing Plan: issue-tool Typer Migration & Modularization

**Goal:** Refactor the `issue-tool` from `argparse` to `typer` and continue modularization while ensuring zero regressions across all 80+ existing unit tests and key user workflows.

## 1. Regression Testing Strategy
### 1.1 Existing Unit Tests
- The 852 existing tests (specifically the 81 in `tests/unit/issue_tool/`) must remain green at every step.
- Since current tests call functions directly from `scripts.issue_tool.cli`, we will maintain function parity during the lift-and-shift.
- As functions move to new modules (`worktree.py`, `batch.py`, etc.), the `tests/unit/issue_tool/_support.py` module will be updated to redirect imports, ensuring tests continue to target the correct implementation.

### 1.2 CLI Interface Parity
- Create a new parity test suite `tests/unit/issue_tool/test_cli_parity.py`.
- This suite will use `typer.testing.CliRunner` (for the new Typer commands) and `subprocess.run` (for the old argparse commands) to verify:
    - Help output contains expected subcommands.
    - Argument/Option naming remains consistent (e.g., `--repo`, `--json`, `--limit`).
    - Exit codes for success and known failure modes match.

## 2. Phased Execution & Validation
### Phase 1: Shared Primitive Consolidation
- **Action:** Move `run()`, `eprint()`, `parse_bool_env()` etc. to `scripts/issue_tool/shared.py`.
- **Validation:** Run `make validate-python`. Ensure no circular imports.

### Phase 2: Typer Entrypoint Boilerplate
- **Action:** Create `scripts/issue_tool/main.py` with a Typer `app`.
- **Action:** Register `issue-tool = "scripts.issue_tool.main:app"` in `pyproject.toml`.
- **Validation:** `uv run issue-tool --help` returns a Typer-formatted help message.

### Phase 3: Incremental Command Migration
For each subcommand (e.g., `issue-queue`, `issue-create`, `worktree`):
- **Action:** Define the Typer command in a dedicated module (e.g., `scripts/issue_tool/commands/queue.py`).
- **Action:** Lift-and-shift the logic from `cli.py` to the new command module.
- **Validation:** 
    1. Run existing unit tests for that domain.
    2. Add `CliRunner` test to `test_cli_parity.py`.

### Phase 4: Final Monolith Deletion
- **Action:** Once all commands are moved, delete `scripts/issue_tool/cli.py`.
- **Validation:** Full repository validation: `make validate-local-full`.

## 3. Success Criteria
- [ ] `uv run issue-tool --help` shows all original subcommands.
- [ ] `make issue-queue`, `make issue-create`, etc. (Makefile targets) work without modification.
- [ ] 852/852 tests pass.
- [ ] `scripts/issue_tool/` contains no files larger than 1,000 lines.
- [ ] `pyright` report shows 0 errors.
