from __future__ import annotations

import argparse
import re
import subprocess
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from traceback import format_exception_only


@dataclass(frozen=True)
class ValidationTask:
    label: str
    target: str
    command: tuple[str, ...]
    cwd: Path = Path(".")


@dataclass(frozen=True)
class ValidationResult:
    label: str
    target: str
    returncode: int
    duration_seconds: float
    stdout: str
    stderr: str
    log_path: Path

    @property
    def ok(self) -> bool:
        return self.returncode == 0


FAST_TASKS = (
    ValidationTask(
        "Rules sync", "rules-sync-audit", ("make", "--no-print-directory", "rules-sync-audit")
    ),
    ValidationTask("Lint", "validate-lint", ("make", "--no-print-directory", "validate-lint")),
    ValidationTask(
        "Typecheck", "validate-typecheck", ("make", "--no-print-directory", "validate-typecheck")
    ),
    ValidationTask(
        "Contract", "validate-contract", ("make", "--no-print-directory", "validate-contract")
    ),
    ValidationTask(
        "CDK TypeScript",
        "validate-cdk-ts-local",
        ("make", "--no-print-directory", "validate-cdk-ts-local"),
    ),
    ValidationTask(
        "Secrets diff",
        "validate-secrets-diff",
        ("make", "--no-print-directory", "validate-secrets-diff"),
    ),
)

FULL_TASKS = (
    ValidationTask(
        "Rules sync", "rules-sync-audit", ("make", "--no-print-directory", "rules-sync-audit")
    ),
    ValidationTask("Lint", "validate-lint", ("make", "--no-print-directory", "validate-lint")),
    ValidationTask(
        "Typecheck", "validate-typecheck", ("make", "--no-print-directory", "validate-typecheck")
    ),
    ValidationTask(
        "Contract", "validate-contract", ("make", "--no-print-directory", "validate-contract")
    ),
    ValidationTask("CDK", "validate-cdk", ("make", "--no-print-directory", "validate-cdk")),
    ValidationTask(
        "Secrets full",
        "validate-secrets-full",
        ("make", "--no-print-directory", "validate-secrets-full"),
    ),
)

LEGACY_FAST_BENCHMARK_TASKS = (
    ValidationTask(
        "Rules sync", "rules-sync-audit", ("make", "--no-print-directory", "rules-sync-audit")
    ),
    ValidationTask(
        "Python", "validate-python", ("make", "--no-print-directory", "validate-python")
    ),
    ValidationTask(
        "CDK TypeScript",
        "validate-cdk-ts-local",
        ("make", "--no-print-directory", "validate-cdk-ts-local"),
    ),
    ValidationTask(
        "Secrets diff",
        "validate-secrets-diff",
        ("make", "--no-print-directory", "validate-secrets-diff"),
    ),
)

LEGACY_FULL_BENCHMARK_TASKS = (
    ValidationTask(
        "Rules sync", "rules-sync-audit", ("make", "--no-print-directory", "rules-sync-audit")
    ),
    ValidationTask(
        "Python", "validate-python", ("make", "--no-print-directory", "validate-python")
    ),
    ValidationTask("CDK", "validate-cdk", ("make", "--no-print-directory", "validate-cdk")),
    ValidationTask(
        "Secrets full",
        "validate-secrets-full",
        ("make", "--no-print-directory", "validate-secrets-full"),
    ),
)


def build_task_set(mode: str) -> tuple[ValidationTask, ...]:
    if mode == "fast":
        return FAST_TASKS
    if mode == "full":
        return FULL_TASKS
    raise ValueError(f"Unsupported validation mode: {mode}")


def materialize_task_set(mode: str, repo_root: Path) -> tuple[ValidationTask, ...]:
    _ = repo_root
    return build_task_set(mode)


def build_legacy_benchmark_task_set(mode: str) -> tuple[ValidationTask, ...]:
    if mode == "fast":
        return LEGACY_FAST_BENCHMARK_TASKS
    if mode == "full":
        return LEGACY_FULL_BENCHMARK_TASKS
    raise ValueError(f"Unsupported validation mode: {mode}")


def create_log_dir(repo_root: Path, mode: str) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    log_dir = repo_root / ".build" / "validate-local" / f"{mode}-{timestamp}"
    suffix = 1
    while log_dir.exists():
        log_dir = repo_root / ".build" / "validate-local" / f"{mode}-{timestamp}-{suffix}"
        suffix += 1
    log_dir.mkdir(parents=True, exist_ok=False)
    return log_dir


def task_log_path(log_dir: Path, task: ValidationTask) -> Path:
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", task.target).strip("-")
    return log_dir / f"{safe_name}.log"


def format_command(task: ValidationTask) -> str:
    return " ".join(task.command)


def format_log_path(path: Path, *, repo_root: Path) -> str:
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)


def run_task(
    task: ValidationTask,
    *,
    repo_root: Path,
    log_dir: Path,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> ValidationResult:
    started = time.perf_counter()
    command = list(task.command)
    cwd = repo_root / task.cwd
    stdout = ""
    stderr = ""
    returncode = 1
    try:
        completed = runner(
            command,
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
        )
        stdout = completed.stdout
        stderr = completed.stderr
        returncode = completed.returncode
    except Exception as exc:
        stderr = "".join(format_exception_only(type(exc), exc)).strip()
    duration = time.perf_counter() - started
    log_path = task_log_path(log_dir, task)
    log_path.write_text(
        "\n".join(
            (
                f"label: {task.label}",
                f"target: {task.target}",
                f"command: {' '.join(command)}",
                f"cwd: {cwd}",
                f"returncode: {returncode}",
                f"duration_seconds: {duration:.3f}",
                "",
                "----- stdout -----",
                stdout.rstrip(),
                "",
                "----- stderr -----",
                stderr.rstrip(),
                "",
            )
        ),
        encoding="utf-8",
    )
    return ValidationResult(
        label=task.label,
        target=task.target,
        returncode=returncode,
        duration_seconds=duration,
        stdout=stdout,
        stderr=stderr,
        log_path=log_path,
    )


def print_summary(results: list[ValidationResult], *, repo_root: Path) -> None:
    print("==> Validation summary")
    for result in results:
        status = "PASS" if result.ok else "FAIL"
        log_path = format_log_path(result.log_path, repo_root=repo_root)
        print(
            f"[{status}] {result.label} ({result.target}) "
            f"{result.duration_seconds:.1f}s log: {log_path}"
        )


def output_excerpt(result: ValidationResult, *, line_limit: int = 20) -> list[str]:
    combined = "\n".join(part for part in (result.stdout, result.stderr) if part)
    lines = [line for line in combined.splitlines() if line.strip()]
    if len(lines) <= line_limit:
        return lines
    omitted = len(lines) - line_limit
    return [f"... {omitted} earlier output lines omitted ...", *lines[-line_limit:]]


def print_failures(results: list[ValidationResult], *, repo_root: Path) -> None:
    failures = [result for result in results if not result.ok]
    if not failures:
        return
    print()
    print("==> Failure summary")
    for result in failures:
        log_path = format_log_path(result.log_path, repo_root=repo_root)
        print(
            f"--- {result.label} ({result.target}) failed with exit "
            f"{result.returncode}; log: {log_path}"
        )
        for line in output_excerpt(result):
            print(line)


def run_tasks_parallel(
    tasks: tuple[ValidationTask, ...],
    *,
    repo_root: Path,
    log_dir: Path,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> tuple[list[ValidationResult], float]:
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=len(tasks)) as executor:
        futures = [
            executor.submit(run_task, task, repo_root=repo_root, log_dir=log_dir, runner=runner)
            for task in tasks
        ]
        results = [future.result() for future in futures]
    return results, time.perf_counter() - started


def run_tasks_sequential(
    tasks: tuple[ValidationTask, ...],
    *,
    repo_root: Path,
    log_dir: Path,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> tuple[list[ValidationResult], float]:
    started = time.perf_counter()
    results = [
        run_task(task, repo_root=repo_root, log_dir=log_dir, runner=runner) for task in tasks
    ]
    return results, time.perf_counter() - started


def order_results(
    results: list[ValidationResult], tasks: tuple[ValidationTask, ...]
) -> list[ValidationResult]:
    task_order = [task.target for task in tasks]
    return sorted(results, key=lambda result: task_order.index(result.target))


def run_validation_mode(
    *,
    mode: str,
    repo_root: Path,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    benchmark: bool = False,
) -> int:
    tasks = materialize_task_set(mode, repo_root)
    log_dir = create_log_dir(repo_root, f"{mode}-benchmark" if benchmark else mode)
    parallel_log_dir = log_dir / "parallel" if benchmark else log_dir
    parallel_log_dir.mkdir(parents=True, exist_ok=True)
    print(f"==> Running local validation ({mode})")
    print(f"==> Logs: {format_log_path(log_dir, repo_root=repo_root)}")
    print("==> Launching parallel tasks")
    for task in tasks:
        print(f" - {task.label}: {format_command(task)}")

    sequential_wall_seconds: float | None = None
    if benchmark:
        baseline_tasks = build_legacy_benchmark_task_set(mode)
        sequential_log_dir = log_dir / "sequential"
        sequential_log_dir.mkdir(parents=True, exist_ok=True)
        print("==> Benchmark: measuring legacy sequential baseline")
        sequential_results, sequential_wall_seconds = run_tasks_sequential(
            baseline_tasks,
            repo_root=repo_root,
            log_dir=sequential_log_dir,
            runner=runner,
        )
        if any(not result.ok for result in sequential_results):
            ordered = order_results(sequential_results, baseline_tasks)
            print()
            print_summary(ordered, repo_root=repo_root)
            print_failures(ordered, repo_root=repo_root)
            return 1

    results, wall_seconds = run_tasks_parallel(
        tasks,
        repo_root=repo_root,
        log_dir=parallel_log_dir,
        runner=runner,
    )

    ordered = order_results(results, tasks)
    serial_seconds = sum(result.duration_seconds for result in ordered)
    print()
    print_summary(ordered, repo_root=repo_root)
    print(f"==> Parallel wall time: {wall_seconds:.1f}s")
    print(f"==> Aggregate task runtime: {serial_seconds:.1f}s")
    if sequential_wall_seconds is not None:
        improvement = (
            (sequential_wall_seconds - wall_seconds) / sequential_wall_seconds * 100
            if sequential_wall_seconds
            else 0.0
        )
        print(
            f"==> Benchmark improvement: {improvement:.0f}% "
            f"({sequential_wall_seconds:.1f}s sequential vs {wall_seconds:.1f}s parallel)"
        )
    print_failures(ordered, repo_root=repo_root)

    if any(not result.ok for result in ordered):
        return 1

    print()
    print("==> Validation passed")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run fast or full local validation")
    parser.add_argument("mode", choices=("fast", "full"))
    parser.add_argument(
        "--benchmark",
        action="store_true",
        help="Measure a real sequential baseline before the parallel run",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    return run_validation_mode(mode=args.mode, repo_root=repo_root, benchmark=args.benchmark)


def validate_local(mode: str, benchmark: bool = False) -> int:
    repo_root = Path(__file__).resolve().parents[1]
    return run_validation_mode(mode=mode, repo_root=repo_root, benchmark=benchmark)


if __name__ == "__main__":
    raise SystemExit(main())
