#!/usr/bin/env python3
"""
Validate generated CDK state policy.

CDK may leave local generated output after synth. That output is acceptable only
as ignored, untracked local state; retired generated caches are not acceptable.
"""

from __future__ import annotations

import argparse
import subprocess
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

RETIRED_GENERATED_DIRS = ("infra/cdk/generated",)
IGNORED_GENERATED_SENTINELS = (
    "infra/cdk/cdk.out/.gitignore-check",
    "infra/cdk/dist/.gitignore-check",
)
TRACKED_GENERATED_PATHS = (
    "infra/cdk/cdk.out",
    "infra/cdk/dist",
    "infra/cdk/generated",
)


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _run_git(
    args: list[str],
    *,
    runner: Runner = subprocess.run,
) -> subprocess.CompletedProcess[str]:
    return runner(
        ["git", *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _tracked_generated_paths(*, runner: Runner) -> list[str]:
    completed = _run_git(["ls-files", *TRACKED_GENERATED_PATHS], runner=runner)
    if completed.returncode != 0:
        raise subprocess.SubprocessError(completed.stderr.strip() or "git ls-files failed")
    return [line for line in completed.stdout.splitlines() if line.strip()]


def _is_ignored(path: str, *, runner: Runner) -> bool:
    completed = _run_git(["check-ignore", path], runner=runner)
    return completed.returncode == 0


def cmd_check(*, runner: Runner = subprocess.run) -> int:
    errors: list[str] = []

    for rel in RETIRED_GENERATED_DIRS:
        if (ROOT / rel).exists():
            errors.append(f"retired generated cache directory still exists: {rel}")

    try:
        for path in _tracked_generated_paths(runner=runner):
            errors.append(f"generated CDK artifact is tracked: {path}")

        for sentinel in IGNORED_GENERATED_SENTINELS:
            if not _is_ignored(sentinel, runner=runner):
                errors.append(f"generated CDK path is not ignored: {sentinel.rsplit('/', 1)[0]}")
    except subprocess.SubprocessError as exc:
        errors.append(f"generated state audit could not inspect git state: {exc}")

    print("Generated state audit:", "PASS" if not errors else "FAILED")
    for item in errors:
        print(f"  ERROR: {item}")

    return 0 if not errors else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check",), help="Audit generated state policy")
    args = parser.parse_args()

    if args.command == "check":
        return cmd_check()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
