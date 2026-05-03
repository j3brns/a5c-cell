from __future__ import annotations

import os
import shlex
import subprocess
import time
from pathlib import Path

from platform_config import env_optional
from scripts.issue_tool.constants import (
    WORKTREE_PREPROVISION_DIR,
    WORKTREE_PREPROVISION_FAILED,
    WORKTREE_PREPROVISION_LOG,
    WORKTREE_PREPROVISION_PID,
    WORKTREE_READY_SENTINEL,
)
from scripts.issue_tool.git_utils import eprint
from scripts.issue_tool.shared import CliError
from scripts.issue_tool.tracker_client import shutil_which


def worktree_ready_sentinel(path: Path) -> Path:
    return path / WORKTREE_READY_SENTINEL


def worktree_preprovision_dir(path: Path) -> Path:
    return path / WORKTREE_PREPROVISION_DIR


def worktree_preprovision_log(path: Path) -> Path:
    return worktree_preprovision_dir(path) / WORKTREE_PREPROVISION_LOG


def worktree_preprovision_failed(path: Path) -> Path:
    return worktree_preprovision_dir(path) / WORKTREE_PREPROVISION_FAILED


def worktree_preprovision_pid(path: Path) -> Path:
    return worktree_preprovision_dir(path) / WORKTREE_PREPROVISION_PID


def process_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def worktree_preprovision_pid_running(path: Path) -> bool:
    pid_path = worktree_preprovision_pid(path)
    if not pid_path.exists():
        return False
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except ValueError:
        return False
    return process_running(pid)


def start_worktree_pre_provision(path: Path) -> None:
    provision_dir = worktree_preprovision_dir(path)
    provision_dir.mkdir(parents=True, exist_ok=True)
    ready_path = worktree_ready_sentinel(path)
    failed_path = worktree_preprovision_failed(path)
    log_path = worktree_preprovision_log(path)
    pid_path = worktree_preprovision_pid(path)
    for marker in (ready_path, failed_path, pid_path):
        marker.unlink(missing_ok=True)

    script = "\n".join(
        [
            "set -e",
            f'trap "touch {shlex.quote(str(failed_path))}" ERR',
            "echo '[worktree-pre-provision] start '$(date -Is)",
            "uv sync",
            "npm install --prefix infra/cdk",
            "npm install --prefix spa",
            f"touch {shlex.quote(str(ready_path))}",
            f"rm -f {shlex.quote(str(failed_path))}",
            "echo '[worktree-pre-provision] ready '$(date -Is)",
        ]
    )
    with log_path.open("w", encoding="utf-8") as log_file:
        try:
            proc = subprocess.Popen(
                ["bash", "-lc", script],
                cwd=path,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as exc:
            failed_path.write_text(str(exc), encoding="utf-8")
            raise CliError(f"Failed to start worktree pre-provisioning: {exc}") from exc
    pid_path.write_text(str(proc.pid), encoding="utf-8")
    print(f"Started worktree pre-provisioning in background (pid={proc.pid})")
    print(f"  ready: {ready_path}")
    print(f"  log:   {log_path}")


def await_worktree_ready_if_provisioning(path: Path) -> None:
    ready_path = worktree_ready_sentinel(path)
    failed_path = worktree_preprovision_failed(path)
    pid_path = worktree_preprovision_pid(path)
    log_path = worktree_preprovision_log(path)
    if ready_path.exists():
        print(f"Worktree environment ready: {ready_path}")
        return
    if failed_path.exists():
        raise CliError(f"Worktree pre-provisioning failed; see {log_path}")
    if not pid_path.exists():
        print("Worktree readiness sentinel missing; continuing with cold environment")
        return

    wait_seconds = int(env_optional("WORKTREE_READY_WAIT_SECONDS", "900") or "900")
    deadline = time.monotonic() + max(0, wait_seconds)
    print(f"Waiting for worktree pre-provisioning to finish (timeout={wait_seconds}s)")
    while time.monotonic() <= deadline:
        if ready_path.exists():
            print(f"Worktree environment ready: {ready_path}")
            return
        if failed_path.exists():
            raise CliError(f"Worktree pre-provisioning failed; see {log_path}")
        if not worktree_preprovision_pid_running(path):
            break
        time.sleep(2)
    raise CliError(f"Worktree is not ready; see {log_path}")
