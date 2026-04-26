#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

ProbeMode = str

REQUIRED_PATHS = (
    ".venv",
    "infra/cdk/node_modules",
    "spa/node_modules",
)

TEST_BINARIES = (
    "uv",
    "node",
    "npm",
    "npx",
)

AGENT_BINARIES = (
    *TEST_BINARIES,
    "git",
    "glab",
)


@dataclass(frozen=True)
class ProbeResult:
    missing_paths: list[str]
    missing_binaries: list[str]

    @property
    def ok(self) -> bool:
        return not self.missing_paths and not self.missing_binaries


def required_binaries(mode: ProbeMode) -> tuple[str, ...]:
    if mode == "test":
        return TEST_BINARIES
    return AGENT_BINARIES


def run_probe(root: Path, *, mode: ProbeMode = "agent") -> ProbeResult:
    root = root.resolve()
    missing_paths = [relative for relative in REQUIRED_PATHS if not (root / relative).exists()]
    missing_binaries = [
        binary for binary in required_binaries(mode) if shutil.which(binary) is None
    ]
    return ProbeResult(missing_paths=missing_paths, missing_binaries=missing_binaries)


def print_result(result: ProbeResult) -> None:
    if result.ok:
        print("Worktree probe: PASS")
        return

    print("Worktree probe: FAILED")
    if result.missing_paths:
        print("Missing dependency paths:")
        for path in result.missing_paths:
            print(f"  - {path}")
    if result.missing_binaries:
        print("Missing core binaries:")
        for binary in result.missing_binaries:
            print(f"  - {binary}")
    print("Run: make ensure-tools")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify issue worktree developer dependencies")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Repository root to probe")
    parser.add_argument(
        "--mode",
        choices=("agent", "test"),
        default="agent",
        help="Probe scope: agent handoff environment or test prerequisites",
    )
    args = parser.parse_args(argv)

    result = run_probe(args.root, mode=args.mode)
    print_result(result)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
