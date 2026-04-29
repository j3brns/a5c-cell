from __future__ import annotations

import importlib.util
import re
import sys
import time
from pathlib import Path


def _load_validate_local_module():
    repo_root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "validate_local", repo_root / "scripts" / "validate_local.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


validate_local = _load_validate_local_module()
MAKEFILE = Path(__file__).resolve().parents[2] / "Makefile"


class _Completed:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_build_task_set_fast_and_full() -> None:
    fast = validate_local.build_task_set("fast")
    pre_push = validate_local.build_task_set("pre-push")
    full = validate_local.build_task_set("full")
    legacy_fast = validate_local.build_legacy_benchmark_task_set("fast")

    assert [task.target for task in fast] == [
        "rules-sync-audit",
        "generated-state-audit",
        "docs-sync-audit",
        "validate-lint",
        "validate-typecheck",
        "validate-contract",
        "validate-cdk-ts-local",
        "validate-secrets-diff",
    ]
    assert [task.target for task in pre_push] == [
        "generated-state-audit",
        "docs-sync-audit",
        "validate-python",
        "validate-cdk-ts-push",
        "validate-secrets-push",
    ]
    assert [task.target for task in full] == [
        "rules-sync-audit",
        "generated-state-audit",
        "docs-sync-audit",
        "validate-lint",
        "validate-typecheck",
        "validate-contract",
        "validate-cdk",
        "validate-secrets-full",
    ]
    assert [task.target for task in legacy_fast] == [
        "rules-sync-audit",
        "generated-state-audit",
        "docs-sync-audit",
        "validate-python",
        "validate-cdk-ts-local",
        "validate-secrets-diff",
    ]


def test_pre_push_adds_python_unit_tests_for_python_runtime_changes(tmp_path: Path) -> None:
    tasks = validate_local.materialize_task_set(
        "pre-push",
        tmp_path,
        changed_files=("src/bridge/handler.py",),
    )

    assert [task.target for task in tasks][-1:] == ["test-unit"]
    assert tasks[-1].command == ("make", "--no-print-directory", "test-unit")


def test_pre_push_uses_mapped_unit_test_subset_for_known_validation_changes(
    tmp_path: Path,
) -> None:
    tasks = validate_local.materialize_task_set(
        "pre-push",
        tmp_path,
        changed_files=(
            "Makefile",
            "scripts/validate_local.py",
            "tests/unit/test_validate_local.py",
        ),
    )

    assert [task.target for task in tasks][-1:] == ["python-unit-changed"]
    assert tasks[-1].command == (
        "uv",
        "run",
        "pytest",
        "tests/unit/test_validate_local.py",
        "-v",
        "--tb=short",
    )


def test_pre_push_falls_back_to_test_unit_for_unmapped_python_source(tmp_path: Path) -> None:
    tasks = validate_local.materialize_task_set(
        "pre-push",
        tmp_path,
        changed_files=("scripts/deploy_frontend.py", "tests/unit/test_validate_local.py"),
    )

    assert [task.target for task in tasks][-1:] == ["test-unit"]
    assert tasks[-1].command == ("make", "--no-print-directory", "test-unit")


def test_pre_push_adds_spa_quick_tests_for_spa_changes(tmp_path: Path) -> None:
    tasks = validate_local.materialize_task_set(
        "pre-push",
        tmp_path,
        changed_files=("spa/src/App.tsx",),
    )

    assert [task.target for task in tasks][-1:] == ["spa-test-quick"]
    assert tasks[-1].command == ("npm", "run", "test:quick")
    assert tasks[-1].cwd == Path("spa")


def test_pre_push_adds_both_runtime_suites_for_python_and_spa_changes(tmp_path: Path) -> None:
    tasks = validate_local.materialize_task_set(
        "pre-push",
        tmp_path,
        changed_files=("scripts/validate_local.py", "spa/package.json"),
    )

    assert [task.target for task in tasks][-2:] == ["python-unit-changed", "spa-test-quick"]


def test_pre_push_does_not_add_runtime_tests_for_docs_only_changes(tmp_path: Path) -> None:
    tasks = validate_local.materialize_task_set(
        "pre-push",
        tmp_path,
        changed_files=("docs/ARCHITECTURE.md",),
    )

    assert "test-unit" not in [task.target for task in tasks]
    assert "spa-test-quick" not in [task.target for task in tasks]


def test_changed_files_for_push_uses_origin_main_when_upstream_is_missing(
    tmp_path: Path, monkeypatch
) -> None:
    def _runner(cmd, *, cwd, text, capture_output, check):
        _ = cwd, text, capture_output, check
        args = tuple(cmd[1:])
        if args == ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"):
            return _Completed(1)
        if args == ("show-ref", "--verify", "--quiet", "refs/remotes/origin/main"):
            return _Completed(0)
        if args == ("diff", "--name-only", "--diff-filter=ACMR", "origin/main...HEAD"):
            return _Completed(0, stdout="src/bridge/handler.py\n")
        return _Completed(0)

    monkeypatch.setattr(validate_local.subprocess, "run", _runner)

    assert validate_local.changed_files_for_push(tmp_path) == ("src/bridge/handler.py",)


def test_changed_files_for_push_ignores_uncommitted_wip(tmp_path: Path, monkeypatch) -> None:
    def _runner(cmd, *, cwd, text, capture_output, check):
        _ = cwd, text, capture_output, check
        args = tuple(cmd[1:])
        if args == ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"):
            return _Completed(0, stdout="origin/main\n")
        if args == ("diff", "--name-only", "--diff-filter=ACMR", "origin/main...HEAD"):
            return _Completed(0, stdout="scripts/validate_local.py\n")
        if args in (
            ("diff", "--name-only", "--diff-filter=ACMR"),
            ("diff", "--cached", "--name-only", "--diff-filter=ACMR"),
            ("ls-files", "--others", "--exclude-standard"),
        ):
            raise AssertionError(f"unexpected working-tree query: {args}")
        return _Completed(0)

    monkeypatch.setattr(validate_local.subprocess, "run", _runner)

    assert validate_local.changed_files_for_push(tmp_path) == ("scripts/validate_local.py",)


def test_make_validate_pre_push_delegates_to_validator_composition() -> None:
    content = MAKEFILE.read_text(encoding="utf-8")
    match = re.search(r"(?ms)^validate-pre-push:.*?(?=^## validate-local-prereqs:)", content)
    assert match is not None
    block = match.group(0)
    recipe_lines = [
        line.removeprefix("\t").removeprefix("@")
        for line in block.splitlines()
        if line.startswith("\t")
    ]

    assert recipe_lines == [
        'echo "==> Running pre-push validation (no cdk synth)"',
        "uv run platform-cli validate local pre-push",
        "uv run python -m scripts.issue_tool write-validation-receipt --check "
        "validate-pre-push >/dev/null",
        'echo "==> Pre-push validation passed"',
    ]


def test_run_validation_mode_fails_when_one_subtask_fails(tmp_path: Path, capsys) -> None:
    seen: list[str] = []

    def _runner(cmd, *, cwd, text, capture_output, check):
        _ = cwd, text, capture_output, check
        target = cmd[-1]
        seen.append(target)
        if target == "validate-cdk-ts-local":
            return _Completed(2, stdout="cdk failed")
        return _Completed(0, stdout=f"{target} ok")

    exit_code = validate_local.run_validation_mode(mode="fast", repo_root=tmp_path, runner=_runner)

    assert exit_code == 1
    assert len(seen) == len(validate_local.FAST_TASKS)
    output = capsys.readouterr().out
    assert "[FAIL] CDK TypeScript (validate-cdk-ts-local)" in output
    assert "validate-cdk-ts-local.log" in output
    assert "cdk failed" in output

    log_file = tmp_path / ".build" / "validate-local"
    cdk_logs = list(log_file.glob("fast-*/validate-cdk-ts-local.log"))
    assert len(cdk_logs) == 1
    assert "cdk failed" in cdk_logs[0].read_text(encoding="utf-8")


def test_run_validation_mode_reports_multiple_failures_with_log_paths(
    tmp_path: Path, capsys
) -> None:
    def _runner(cmd, *, cwd, text, capture_output, check):
        _ = cwd, text, capture_output, check
        target = cmd[-1]
        if target == "validate-lint":
            return _Completed(1, stdout="lint failed", stderr="lint stderr")
        if target == "validate-secrets-diff":
            return _Completed(1, stdout=f"{target} failed", stderr=f"{target} stderr")
        return _Completed(0, stdout=f"{target} ok")

    exit_code = validate_local.run_validation_mode(mode="fast", repo_root=tmp_path, runner=_runner)

    assert exit_code == 1
    output = capsys.readouterr().out
    assert "[FAIL] Lint (validate-lint)" in output
    assert "[FAIL] Secrets diff (validate-secrets-diff)" in output
    assert "--- Lint (validate-lint) failed with exit 1; log:" in output
    assert "--- Secrets diff (validate-secrets-diff) failed with exit 1; log:" in output
    assert "validate-lint.log" in output
    assert "validate-secrets-diff.log" in output

    log_root = tmp_path / ".build" / "validate-local"
    assert len(list(log_root.glob("fast-*/validate-lint.log"))) == 1
    assert len(list(log_root.glob("fast-*/validate-secrets-diff.log"))) == 1


def test_run_validation_mode_runs_tasks_in_parallel(tmp_path: Path, capsys) -> None:
    def _runner(cmd, *, cwd, text, capture_output, check):
        _ = cmd, cwd, text, capture_output, check
        time.sleep(0.05)
        return _Completed(0, stdout="ok")

    started = time.perf_counter()
    exit_code = validate_local.run_validation_mode(mode="fast", repo_root=tmp_path, runner=_runner)
    duration = time.perf_counter() - started

    assert exit_code == 0
    assert duration < 0.16
    output = capsys.readouterr().out
    assert "==> Parallel wall time:" in output
    assert "==> Aggregate task runtime:" in output
    assert "Benchmark improvement" not in output


def test_run_validation_mode_benchmark_measures_sequential_baseline(tmp_path: Path, capsys) -> None:
    def _runner(cmd, *, cwd, text, capture_output, check):
        _ = cmd, cwd, text, capture_output, check
        time.sleep(0.02)
        return _Completed(0, stdout="ok")

    exit_code = validate_local.run_validation_mode(
        mode="fast", repo_root=tmp_path, runner=_runner, benchmark=True
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "==> Benchmark: measuring legacy sequential baseline" in output
    assert "==> Benchmark improvement:" in output
    match = re.search(r"Benchmark improvement: (\d+)%", output)
    assert match is not None
    assert int(match.group(1)) >= 30
    assert list((tmp_path / ".build" / "validate-local").glob("fast-benchmark-*/sequential/*.log"))
    assert list((tmp_path / ".build" / "validate-local").glob("fast-benchmark-*/parallel/*.log"))


def test_run_validation_mode_normalizes_task_startup_exceptions(tmp_path: Path, capsys) -> None:
    def _runner(cmd, *, cwd, text, capture_output, check):
        _ = cmd, cwd, text, capture_output, check
        raise FileNotFoundError("uv missing")

    exit_code = validate_local.run_validation_mode(mode="fast", repo_root=tmp_path, runner=_runner)

    assert exit_code == 1
    output = capsys.readouterr().out
    assert "==> Failure summary" in output
    assert "FileNotFoundError: uv missing" in output
    logs = list((tmp_path / ".build" / "validate-local").glob("fast-*/*.log"))
    assert len(logs) == len(validate_local.FAST_TASKS)
    assert "FileNotFoundError: uv missing" in logs[0].read_text(encoding="utf-8")


def test_build_task_set_fast_and_full_order_is_stable_for_public_targets() -> None:
    assert [task.target for task in validate_local.FAST_TASKS] == [
        "rules-sync-audit",
        "generated-state-audit",
        "docs-sync-audit",
        "validate-lint",
        "validate-typecheck",
        "validate-contract",
        "validate-cdk-ts-local",
        "validate-secrets-diff",
    ]


def test_run_validation_mode_prints_summary_for_success(tmp_path: Path, capsys) -> None:
    def _runner(cmd, *, cwd, text, capture_output, check):
        _ = cwd, text, capture_output, check
        return _Completed(0, stdout=f"{cmd[-1]} ok")

    exit_code = validate_local.run_validation_mode(mode="full", repo_root=tmp_path, runner=_runner)

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "==> Validation summary" in output
    assert "[PASS] Rules sync (rules-sync-audit)" in output
    assert "[PASS] Generated state (generated-state-audit)" in output
    assert "[PASS] Docs sync (docs-sync-audit)" in output
    assert "[PASS] Secrets full (validate-secrets-full)" in output
    assert "==> Validation passed" in output
    assert list((tmp_path / ".build" / "validate-local").glob("full-*/validate-cdk.log"))
