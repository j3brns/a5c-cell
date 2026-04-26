#!/usr/bin/env python3
"""
Issue-driven worktree workflow (GitLab Issues as source of truth).

Key behavior:
- Queue order uses `Seq:` in issue bodies as the canonical ordering.
- `Depends on:` task IDs (TASK-###) gate runnable items.
- Uses `glab` CLI for GitLab reads/writes and local `git worktree` for worktree ops.

This intentionally keeps Makefile targets thin; the policy/selection logic lives here.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import random
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TypeAlias, cast

from platform_config import env_optional
from scripts.issue_tool.agent_launch import (
    AGENT_CAPABILITIES,
    DEFAULT_INTERACTIVE_AGENT_POOL,
    build_agent_launch_command,
    launch_interactive_session,
    resolve_launch_request,
)
from scripts.issue_tool.audit import audit_issues
from scripts.issue_tool.closeout import (
    cleanup_finished_worktree as _cleanup_finished_worktree,
)
from scripts.issue_tool.closeout import (
    closeout_event as _closeout_event,
)
from scripts.issue_tool.closeout import (
    closeout_report_path as _closeout_report_path,
)
from scripts.issue_tool.closeout import (
    read_closeout_report,
)
from scripts.issue_tool.closeout import (
    verify_cleanup_finished as _verify_cleanup_finished,
)
from scripts.issue_tool.closeout import (
    write_closeout_report as _write_closeout_report,
)
from scripts.issue_tool.constants import (
    ANSI_ESCAPE_RE,
    CR_TITLE_RE,
    DEPENDS_RE,
    DETACHED_STARTUP_PROBE_INTERVAL_SECONDS,
    DETACHED_STARTUP_PROBE_SECONDS,
    MANAGED_TASK_ID_RE,
    SEQ_RE,
    STATUS_LABELS,
    TASK_ID_TOKEN_RE,
    TITLE_TASK_RE,
    VALIDATION_RECEIPTS_DIR,
    WORKTREE_AGENT_RUN_DIR,
    WORKTREE_BRANCH_ISSUE_RE,
    WORKTREE_BRANCH_REGEX,
    WORKTREE_CLOSEOUT_DIR,
    WORKTREE_RUNS_DIR,
    WORKTREE_STATE_DIR,
)
from scripts.issue_tool.evidence import (
    audit_issue_handoff_evidence as _audit_issue_handoff_evidence,
)
from scripts.issue_tool.evidence import (
    build_issue_handback_comment as _build_issue_handback_comment,
)
from scripts.issue_tool.evidence import (
    find_latest_validation_receipt as _find_latest_validation_receipt,
)
from scripts.issue_tool.evidence import (
    historical_issue_evidence as _historical_issue_evidence,
)
from scripts.issue_tool.evidence import (
    issue_evidence_summary as _issue_evidence_summary,
)
from scripts.issue_tool.evidence import (
    validation_receipt_path as _validation_receipt_path,
)
from scripts.issue_tool.evidence import (
    validation_receipts_root as _validation_receipts_root,
)
from scripts.issue_tool.evidence import (
    write_validation_receipt as _write_validation_receipt,
)
from scripts.issue_tool.git_utils import (
    current_path,
    eprint,
    origin_repo_slug,
    repo_root,
    run,
)
from scripts.issue_tool.issue_queue import (
    build_queue,
    build_task_issue_body,
    choose_next_runnable,
    fetch_repo_issues,
    parse_issue_meta,
)
from scripts.issue_tool.logic import (
    assert_issue_startable,
    choose_reconciled_status,
    edit_issue_labels,
    lifecycle_status,
    normalize_closed_issue_labels,
    parse_depends,
    parse_task_id_from_issue,
    queue_task_issues,
    reconcile_issue_label_changes,
    status_labels,
)
from scripts.issue_tool.models import (
    AuditFinding,
    BatchLaunchResult,
    Issue,
    QueueItem,
    QueueSelection,
    SessionPair,
    WorktreeInfo,
)
from scripts.issue_tool.shared import CliError
from scripts.issue_tool.tracker_client import (
    WORKFLOW_LABEL_DEFAULTS,
    close_issue,
    comment_issue,
    create_issue,
    get_issue,
    merge_request_for_branch,
    shutil_which,
    tracker_available,
    update_issue_labels,
)

WORKTREE_READY_SENTINEL = ".ready"
WORKTREE_PREPROVISION_DIR = Path(".build") / "worktree-provision"
WORKTREE_PREPROVISION_FAILED = "failed"
WORKTREE_PREPROVISION_LOG = "provision.log"
WORKTREE_PREPROVISION_PID = "pid"


def print_queue(
    selection: QueueSelection, *, limit: int | None = None, show_blocked: bool = True
) -> None:
    items = selection.items if show_blocked else selection.runnable
    if limit is not None:
        items = items[: max(0, limit)]
    if not items:
        print(f"No issues in queue (source={selection.source_mode}).")
        return
    print(f"Issue queue (source={selection.source_mode}; order=Seq -> priority -> createdAt)")
    if selection.source_note:
        print(f"  note: {selection.source_note}")
    for idx, item in enumerate(items, start=1):
        issue = item.issue
        seq_text = str(issue.seq) if issue.seq is not None else "unset"
        labels = "|".join(issue.labels) if issue.labels else "-"
        status = "RUNNABLE" if item.runnable else "BLOCKED"
        print(f"{idx:>2}. #{issue.number} [{status}] Seq:{seq_text} {issue.title}")
        print(f"    labels: {labels}")
        if item.blocked_reasons:
            print(f"    why:    {'; '.join(item.blocked_reasons)}")


def slugify_text(text: str) -> str:
    text = text.lower()
    text = re.sub(r"^[a-z0-9._-]+:\s*", "", text)  # trim issue prefix like TASK-015:
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text[:60] or "task"


def infer_scope(issue: Issue) -> str:
    labels = {label.lower() for label in issue.labels}
    title = issue.title.lower()
    if "docs" in labels or any(
        t in title for t in ("readme", "roadmap", "runbook", "adr", "docs/")
    ):
        return "docs"
    if "ci" in labels or any(t in title for t in ("pipeline", "gitlab", "ci/cd")):
        return "ci"
    if any(t in title for t in ("spa", "frontend", "react", "bff")):
        return "frontend"
    if any(t in title for t in ("stack", "cdk", "terraform", "infra")):
        return "infra"
    return "task"


def list_worktrees(root: Path) -> list[WorktreeInfo]:
    try:
        text = run(["git", "worktree", "list", "--porcelain"], cwd=root).stdout
    except subprocess.CalledProcessError as exc:
        raise CliError("Failed to list worktrees") from exc
    entries: list[WorktreeInfo] = []
    cur_path: Path | None = None
    cur_head = ""
    cur_branch = "(detached)"
    for line in text.splitlines():
        if line.startswith("worktree "):
            if cur_path is not None:
                entries.append(WorktreeInfo(cur_path, cur_head, cur_branch))
            cur_path = Path(line[len("worktree ") :]).resolve()
            cur_head = ""
            cur_branch = "(detached)"
        elif line.startswith("HEAD "):
            cur_head = line[len("HEAD ") :]
        elif line.startswith("branch refs/heads/"):
            cur_branch = line[len("branch refs/heads/") :]
        elif line.strip() == "":
            if cur_path is not None:
                entries.append(WorktreeInfo(cur_path, cur_head, cur_branch))
                cur_path = None
                cur_head = ""
                cur_branch = "(detached)"
    if cur_path is not None:
        entries.append(WorktreeInfo(cur_path, cur_head, cur_branch))
    if entries:
        primary = entries[0].path
        for entry in entries:
            entry.is_primary = entry.path == primary
    return entries


def default_worktrees_dir(root: Path) -> Path:
    return root.parent / "worktrees"


def suggest_worktree_dir_name(issue_number: int, base_dir: Path) -> str:
    preferred = f"wt{issue_number}"
    if not (base_dir / preferred).exists():
        return preferred
    i = 2
    while True:
        candidate = f"wt{issue_number}-{i}"
        if not (base_dir / candidate).exists():
            return candidate
        i += 1


def choose_base_ref(root: Path, required_main_branch: str = "main") -> str:
    remote_ref = f"refs/remotes/origin/{required_main_branch}"
    try:
        run(["git", "show-ref", "--verify", "--quiet", remote_ref], cwd=root, check=True)
        return f"origin/{required_main_branch}"
    except subprocess.CalledProcessError:
        return required_main_branch


def local_branch_exists(root: Path, branch: str) -> bool:
    try:
        run(
            ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
            cwd=root,
            check=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def issue_by_number(issues: list[Issue], number: int) -> Issue:
    for issue in issues:
        if issue.number == number:
            return issue
    raise CliError(f"Issue #{number} not found in fetched dataset")


def claim_issue(root: Path, repo: str, issue: Issue) -> bool:
    # Re-fetch labels to reduce stale-queue races.
    data = get_issue(root, repo, issue.number)
    if not isinstance(data, dict):
        raise CliError(f"Unexpected response while checking issue #{issue.number}")
    labels = [x["name"] for x in data.get("labels", []) if isinstance(x, dict) and "name" in x]
    had_ready = "ready" in labels
    states = [label for label in labels if label in STATUS_LABELS]
    if len(set(states)) != 1:
        raise CliError(
            f"Issue #{issue.number} has invalid status labels {sorted(set(states)) or 'none'}; "
            "run `make issues-reconcile`"
        )
    if states[0] != "status:not-started":
        raise CliError(
            f"Issue #{issue.number} must be status:not-started to claim (found {states[0]})"
        )
    add_labels = ["status:in-progress"] if "status:in-progress" not in labels else []
    remove_labels = ["status:not-started"]
    if had_ready:
        remove_labels.append("ready")
    update_issue_labels(root, repo, issue.number, add=add_labels, remove=remove_labels)

    verified = get_issue(root, repo, issue.number)
    verified_labels = [
        x["name"] for x in verified.get("labels", []) if isinstance(x, dict) and "name" in x
    ]
    verified_statuses = [label for label in verified_labels if label in STATUS_LABELS]
    if set(verified_statuses) != {"status:in-progress"} or "ready" in verified_labels:
        raise CliError(
            f"Issue #{issue.number} claim post-condition failed; "
            f"labels are {sorted(verified_labels)}"
        )
    return had_ready


def unclaim_issue(root: Path, repo: str, issue: Issue, *, add_ready: bool = True) -> None:
    # Best-effort rollback for failed worktree creation.
    add_labels = ["status:not-started"]
    remove_labels = ["status:in-progress"]
    if add_ready:
        add_labels.append("ready")
    update_issue_labels(root, repo, issue.number, add=add_labels, remove=remove_labels)


def create_worktree_for_issue(
    *,
    root: Path,
    repo: str,
    issue: Issue,
    base_dir: Path,
    base_ref: str | None,
    scope: str | None,
    slug: str | None,
    folder_name: str | None,
    auto_claim: bool,
    preflight: bool,
    dry_run: bool,
    pre_provision: bool = False,
) -> Path:
    scope_val = scope or infer_scope(issue)
    slug_val = slug or slugify_text(issue.title)
    if not re.fullmatch(r"[a-z0-9._-]+", scope_val):
        raise CliError(f"Invalid scope '{scope_val}'")
    if not re.fullmatch(r"[a-z0-9._-]+", slug_val):
        raise CliError(f"Invalid slug '{slug_val}'")
    branch = f"wt/{scope_val}/{issue.number}-{slug_val}"
    if not WORKTREE_BRANCH_REGEX.fullmatch(branch):
        raise CliError(
            f"Branch name '{branch}' does not match policy {WORKTREE_BRANCH_REGEX.pattern}"
        )

    base_dir.mkdir(parents=True, exist_ok=True)
    name_val = folder_name or suggest_worktree_dir_name(issue.number, base_dir)
    wt_path = (base_dir / name_val).resolve()
    if wt_path.exists():
        raise CliError(f"Worktree path already exists: {wt_path}")

    start_ref = base_ref or choose_base_ref(root)
    branch_exists = local_branch_exists(root, branch)

    print("Create worktree")
    print(f"  issue:   #{issue.number} {issue.title}")
    print(f"  path:    {wt_path}")
    print(f"  branch:  {branch}")
    if branch_exists:
        print("  mode:    attach existing local branch")
    else:
        print(f"  baseRef: {start_ref}")
    if dry_run:
        return wt_path

    claimed = False
    claim_had_ready = False
    try:
        if auto_claim:
            claim_had_ready = claim_issue(root, repo, issue)
            claimed = True
            if claim_had_ready:
                print(f"Claimed issue #{issue.number} (ready -> in-progress)")
            else:
                print(f"Claimed issue #{issue.number} (set in-progress; no ready label to remove)")

        if branch_exists:
            run(["git", "worktree", "add", str(wt_path), branch], cwd=root)
        else:
            run(["git", "worktree", "add", str(wt_path), "-b", branch, start_ref], cwd=root)
        print(f"Created worktree at {wt_path}")
        ensure_uv_venv(wt_path)
        prepare_gitnexus_for_worktree(wt_path)
        if pre_provision:
            start_worktree_pre_provision(wt_path)
    except Exception:
        if claimed:
            try:
                unclaim_issue(root, repo, issue, add_ready=claim_had_ready)
                eprint(f"Rolled back claim for issue #{issue.number}")
            except Exception as rollback_exc:  # pragma: no cover - best effort
                eprint(f"WARNING: failed to roll back claim for #{issue.number}: {rollback_exc}")
        raise

    if preflight:
        try:
            run_preflight(path=wt_path, root=root, repo=repo)
        except CliError as exc:
            eprint(f"WARNING: post-create preflight failed: {exc}")
    record_issue_handoff_event(
        root=root,
        repo=repo,
        issue=issue,
        branch=branch,
        worktree_path=wt_path,
        event_type="worktree-created",
        state="worktree-ready",
        details={
            "base_ref": start_ref,
            "scope": scope_val,
            "slug": slug_val,
            "auto_claim": auto_claim,
            "preflight": preflight,
            "pre_provision": pre_provision,
        },
        idempotency_key=f"create:{issue.number}:{branch}:{wt_path}",
    )
    return wt_path


def parse_bool_env(name: str, default: bool) -> bool:
    raw = env_optional(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def current_branch(path: Path) -> str:
    out = run(["git", "branch", "--show-current"], cwd=path).stdout.strip()
    if not out:
        raise CliError("Detached HEAD is not allowed for session work")
    return out


def resolve_current_worktree(path: Path, worktrees: list[WorktreeInfo]) -> WorktreeInfo:
    path_resolved = path.resolve()
    matches = [
        wt for wt in worktrees if path_resolved == wt.path or path_resolved.is_relative_to(wt.path)
    ]
    if not matches:
        raise CliError("Current path is not inside a registered git worktree")
    # Prefer longest path for nested matching correctness.
    matches.sort(key=lambda wt: len(str(wt.path)), reverse=True)
    return matches[0]


def run_preflight(
    *,
    path: Path,
    root: Path,
    repo: str | None = None,
    required_main_branch: str = "main",
) -> None:
    worktrees = list_worktrees(root)
    if not worktrees:
        raise CliError("No git worktrees found")
    primary = worktrees[0]
    current = resolve_current_worktree(path, worktrees)
    branch = current_branch(current.path)
    errors: list[str] = []
    warnings: list[str] = []

    enforce_lookup = parse_bool_env("ENFORCE_TRACKER_ISSUE_LOOKUP", True)
    require_clean = parse_bool_env("REQUIRE_CLEAN_WORKTREE", False)

    if current.path == primary.path:
        if branch != required_main_branch:
            errors.append(
                f"primary worktree must stay on '{required_main_branch}', found '{branch}'"
            )
    else:
        if not WORKTREE_BRANCH_REGEX.fullmatch(branch):
            errors.append(
                f"linked worktree branch '{branch}' does not match {WORKTREE_BRANCH_REGEX.pattern}"
            )
        issue_id = extract_issue_id_from_branch(branch)
        if issue_id is None:
            errors.append(f"cannot extract issue id from branch '{branch}'")
        elif enforce_lookup:
            if repo is None:
                try:
                    repo = origin_repo_slug(root)
                except CliError as exc:
                    errors.append(str(exc))
                    repo = None
            if repo is not None:
                if not tracker_available():
                    warnings.append(f"issue CLI not found; skipped issue lookup for #{issue_id}")
                else:
                    try:
                        get_issue(root, repo, issue_id)
                    except CliError as exc:
                        errors.append(f"issue lookup failed for #{issue_id}: {exc}")

    if require_clean:
        status = run(["git", "status", "--porcelain"], cwd=current.path).stdout.strip()
        if status:
            errors.append("working tree is not clean")

    print("Preflight context:")
    print(f"  repo:     {root}")
    print(f"  path:     {path.resolve()}")
    print(f"  worktree: {current.path}")
    print(f"  primary:  {primary.path}")
    print(f"  branch:   {branch}")
    if warnings:
        print("Warnings:")
        for warning in warnings:
            print(f"  - {warning}")
    if errors:
        print("Preflight result: FAILED")
        for error in errors:
            print(f"  - {error}")
        raise CliError("Preflight failed")
    print("Preflight result: PASS")


def extract_issue_id_from_branch(branch: str) -> int | None:
    if m := WORKTREE_BRANCH_ISSUE_RE.match(branch):
        return int(m.group(1))
    return None


def list_resume_candidates(root: Path) -> list[WorktreeInfo]:
    worktrees = list_worktrees(root)
    return [wt for wt in worktrees if not wt.is_primary]


def find_linked_worktree_for_issue(root: Path, issue_number: int) -> WorktreeInfo | None:
    for wt in list_resume_candidates(root):
        if extract_issue_id_from_branch(wt.branch) == issue_number:
            return wt
    return None


def choose_next_runnable_without_existing_worktree(
    root: Path, selection: QueueSelection
) -> tuple[QueueItem, list[tuple[int, Path]]]:
    skipped: list[tuple[int, Path]] = []
    for item in selection.items:
        if not item.runnable:
            continue
        existing = find_linked_worktree_for_issue(root, item.issue.number)
        if existing is None:
            return item, skipped
        skipped.append((item.issue.number, existing.path))
    if skipped:
        skipped_text = ", ".join(f"#{num}:{path}" for num, path in skipped)
        raise CliError(
            "All runnable queue issues already have linked worktrees. "
            f"Use worktree-resume to continue them ({skipped_text})."
        )
    raise CliError(f"No runnable issues found in queue (source={selection.source_mode}).")


def select_worktree_interactive(worktrees: list[WorktreeInfo]) -> WorktreeInfo:
    if not worktrees:
        raise CliError("No linked worktrees available")
    print("Select a worktree:")
    for idx, wt in enumerate(worktrees, start=1):
        print(f"  {idx}) {wt.path} | {wt.branch}")
    print("  0) Back")
    while True:
        choice = input("Choice [1]: ").strip() or "1"
        if choice in {"0", "back"}:
            raise CliError("Back")
        if choice.isdigit():
            n = int(choice)
            if 1 <= n <= len(worktrees):
                return worktrees[n - 1]
        print("Invalid choice.")


def ensure_uv_venv(path: Path) -> None:
    venv_activate = path / ".venv" / "bin" / "activate"
    if venv_activate.exists():
        print(f"Python venv ready: {venv_activate.parent.parent}")
        return
    if shutil_which("uv") is None:
        eprint("WARNING: uv not found; skipping virtual environment creation")
        return
    try:
        run(["uv", "venv"], cwd=path)
        print("Created .venv with `uv venv`")
    except subprocess.CalledProcessError as exc:
        eprint(f"WARNING: failed to create .venv with uv: {exc}")


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


def gitnexus_refresh_enabled() -> bool:
    return parse_bool_env("WORKTREE_GITNEXUS_REFRESH", True)


def gitnexus_npx_cache_dir() -> Path | None:
    if shutil_which("npm") is None:
        return None
    try:
        cache_dir = run(["npm", "config", "get", "cache"]).stdout.strip()
    except subprocess.CalledProcessError:
        return None
    if not cache_dir or cache_dir == "undefined":
        return None
    return Path(cache_dir) / "_npx"


def gitnexus_npx_cache_corrupted(output: str) -> bool:
    lowered = output.lower()
    return "enotempty" in lowered and "/_npx/" in lowered


def gitnexus_embeddings_present(path: Path) -> bool:
    meta_path = path / ".gitnexus" / "meta.json"
    if not meta_path.exists():
        return False
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    embeddings = meta.get("stats", {}).get("embeddings", 0)
    try:
        return int(embeddings) > 0
    except (TypeError, ValueError):
        return False


def gitnexus_analyze_supports(option: str) -> bool:
    try:
        proc = run_gitnexus_command(
            Path.cwd(),
            ["analyze", "--help"],
            check=False,
            timeout_seconds=30,
        )
    except subprocess.CalledProcessError:
        return False
    output = "\n".join(
        part.strip() for part in (proc.stdout or "", proc.stderr or "") if part.strip()
    )
    return option in output


def gitnexus_cli_path() -> Path | None:
    override = env_optional("WORKTREE_GITNEXUS_CLI")
    if override:
        candidate = Path(override).expanduser()
        if candidate.exists():
            return candidate
    which = shutil_which("gitnexus")
    if which:
        candidate = Path(which).expanduser()
        if candidate.exists():
            return candidate
    return None


def gitnexus_timeout_seconds() -> float:
    raw = env_optional("WORKTREE_GITNEXUS_TIMEOUT_SECONDS", "300") or "300"
    try:
        value = float(raw)
    except ValueError:
        return 300.0
    return max(value, 1.0)


def run_gitnexus_command(
    path: Path,
    args: list[str],
    *,
    check: bool,
    timeout_seconds: float | None = None,
) -> subprocess.CompletedProcess[str]:
    cli_path = gitnexus_cli_path()
    node = shutil_which("node")
    if cli_path is not None and node is not None:
        if cli_path.suffix == ".js":
            cmd = [node, str(cli_path), *args]
        else:
            cmd = [str(cli_path), *args]
    else:
        cmd = ["npx", "--yes", "gitnexus", *args]
    attempts = 0
    while True:
        attempts += 1
        try:
            proc = subprocess.run(
                cmd,
                cwd=path,
                capture_output=True,
                text=True,
                check=False,
                timeout=(
                    timeout_seconds if timeout_seconds is not None else gitnexus_timeout_seconds()
                ),
            )
        except subprocess.TimeoutExpired as exc:
            raise subprocess.CalledProcessError(
                124,
                cmd,
                output=exc.stdout,
                stderr=exc.stderr,
            ) from exc
        combined_output = "\n".join(
            part.strip() for part in (proc.stdout or "", proc.stderr or "") if part.strip()
        )
        if attempts == 1 and gitnexus_npx_cache_corrupted(combined_output):
            npx_cache_dir = gitnexus_npx_cache_dir()
            if npx_cache_dir is None:
                eprint("WARNING: npm cache path unavailable; cannot repair GitNexus npx cache")
            else:
                print(f"GitNexus: clearing corrupt npx cache at {npx_cache_dir}")
                shutil.rmtree(npx_cache_dir, ignore_errors=True)
                continue
        if check and proc.returncode != 0:
            raise subprocess.CalledProcessError(
                proc.returncode,
                cmd,
                output=proc.stdout,
                stderr=proc.stderr,
            )
        return proc


def prepare_gitnexus_for_worktree(path: Path) -> None:
    if not gitnexus_refresh_enabled():
        print("GitNexus: refresh disabled by WORKTREE_GITNEXUS_REFRESH=0")
        return
    if gitnexus_cli_path() is None and shutil_which("npx") is None:
        eprint("WARNING: gitnexus CLI and npx not found; skipping GitNexus refresh")
        return

    print(f"GitNexus: checking local index in {path}")
    status_proc = run_gitnexus_command(path, ["status"], check=False)
    status_output = "\n".join(
        part.strip()
        for part in (status_proc.stdout or "", status_proc.stderr or "")
        if part.strip()
    )
    if status_output:
        print(status_output)

    needs_refresh = status_proc.returncode != 0
    lowered = status_output.lower()
    refresh_markers = (
        "stale",
        "not indexed",
        "not analyzed",
        "not analysed",
        "missing",
        "out of date",
    )
    if any(marker in lowered for marker in refresh_markers):
        needs_refresh = True

    if not needs_refresh:
        print("GitNexus: local index already fresh")
        return

    analyze_args = ["analyze"]
    if gitnexus_embeddings_present(path):
        analyze_args.append("--embeddings")
    if gitnexus_analyze_supports("--skip-agents-md"):
        analyze_args.append("--skip-agents-md")
    if gitnexus_analyze_supports("--no-stats"):
        analyze_args.append("--no-stats")

    print(f"GitNexus: rebuilding local index for this worktree ({' '.join(analyze_args)})")
    try:
        run_gitnexus_command(path, analyze_args, check=True)
    except subprocess.CalledProcessError as exc:
        eprint(f"WARNING: GitNexus analyze failed in {path}: {exc}")


def open_shell(path: Path) -> None:
    shell = env_optional("SHELL", "bash") or "bash"
    ensure_uv_venv(path)
    print(f"Opening shell in {path} (with .venv activation when available)")
    path_q = shell_quote(str(path))
    shell_q = shell_quote(shell)
    cmd = (
        f"cd {path_q} && "
        "if [ -f .venv/bin/activate ]; then source .venv/bin/activate; fi; "
        f"exec {shell_q} -l"
    )
    os.execvp("bash", ["bash", "-lc", cmd])


def shell_quote(value: str) -> str:
    return shlex.quote(value)


def worktree_runs_root(root: Path) -> Path:
    return root / WORKTREE_RUNS_DIR


def worktree_state_root(root: Path) -> Path:
    return root / WORKTREE_STATE_DIR


def issue_state_path(root: Path, issue_number: int) -> Path:
    return worktree_state_root(root) / f"issue-{issue_number}.json"


def validation_receipts_root(root: Path) -> Path:
    return _validation_receipts_root(root, VALIDATION_RECEIPTS_DIR)


def validation_receipt_path(root: Path, issue_number: int, head_sha: str) -> Path:
    return _validation_receipt_path(root, issue_number, head_sha, VALIDATION_RECEIPTS_DIR)


def worktree_agent_run_dir(path: Path) -> Path:
    return path / WORKTREE_AGENT_RUN_DIR


def write_json_file(path: Path, payload: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def read_json_file(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def find_latest_validation_receipt(root: Path, issue_id: int) -> Path | None:
    return _find_latest_validation_receipt(root, issue_id, VALIDATION_RECEIPTS_DIR)


def git_issue_branches(root: Path, issue_id: int) -> dict[str, list[str]]:
    def _list_branches(pattern: str, *, remote: bool) -> list[str]:
        cmd = ["git", "branch"]
        if remote:
            cmd.append("-r")
        cmd.extend(["--format=%(refname:short)", "--list", pattern])
        output = run(cmd, cwd=root, check=False).stdout
        return [line.strip() for line in output.splitlines() if line.strip()]

    return {
        "local": _list_branches(f"wt/*/{issue_id}-*", remote=False),
        "remote": _list_branches(f"origin/wt/*/{issue_id}-*", remote=True),
    }


def git_log_issue_matches(root: Path, issue_id: int, *, limit: int = 5) -> list[dict[str, str]]:
    output = run(
        [
            "git",
            "log",
            "--all",
            "--extended-regexp",
            f"-n{limit}",
            "--pretty=format:%H%x09%cI%x09%s",
            "--grep",
            rf"#{issue_id}\b",
            "--grep",
            rf"issue[- ]{issue_id}\b",
        ],
        cwd=root,
        check=False,
    ).stdout.strip()
    matches: list[dict[str, str]] = []
    if not output:
        return matches
    for line in output.splitlines():
        sha, ts, subject = (line.split("\t", 2) + ["", ""])[:3]
        matches.append({"sha": sha, "timestamp": ts, "subject": subject})
    return matches


def historical_issue_evidence(root: Path, issue_id: int) -> dict[str, object] | None:
    return _historical_issue_evidence(root, issue_id, run_fn=run)


def write_validation_receipt(
    root: Path,
    *,
    issue_id: int,
    worktree_path: Path,
    branch: str | None,
    check_name: str,
) -> Path:
    return _write_validation_receipt(
        root,
        issue_id=issue_id,
        worktree_path=worktree_path,
        branch=branch,
        check_name=check_name,
        run_fn=run,
        write_json_file_fn=write_json_file,
        receipts_dir=VALIDATION_RECEIPTS_DIR,
    )


def record_issue_handoff_event(
    *,
    root: Path,
    repo: str | None,
    issue: Issue | None = None,
    issue_number: int | None = None,
    issue_title: str | None = None,
    branch: str | None = None,
    worktree_path: Path | None = None,
    event_type: str,
    state: str,
    details: dict[str, object] | None = None,
    idempotency_key: str | None = None,
) -> Path | None:
    resolved_issue_number = issue.number if issue is not None else issue_number
    if resolved_issue_number is None:
        return None

    path = issue_state_path(root, resolved_issue_number)
    existing = read_json_file(path) or {}
    events = existing.get("events")
    if not isinstance(events, list):
        events = []
    start_events = {"worktree-created", "worktree-reused", "worktree-resumed"}
    terminal_states = {"done", "closed", "cleanup-failed", "handback-failed"}
    existing_branch = existing.get("branch")
    existing_worktree = existing.get("worktree_path")
    incoming_worktree = str(worktree_path) if worktree_path is not None else None
    if event_type in start_events and (
        existing.get("state") in terminal_states
        or (branch and existing_branch and branch != existing_branch)
        or (incoming_worktree and existing_worktree and incoming_worktree != existing_worktree)
    ):
        events = []
        existing = {}

    event = {
        "ts": datetime.now(UTC).isoformat(),
        "event_type": event_type,
        "state": state,
        "repo": repo,
        "issue_number": resolved_issue_number,
        "issue_title": (
            issue.title if issue is not None else (issue_title or existing.get("issue_title"))
        ),
        "branch": branch or existing.get("branch"),
        "worktree_path": (
            str(worktree_path) if worktree_path is not None else existing.get("worktree_path")
        ),
        "details": details or {},
    }
    if idempotency_key:
        event["idempotency_key"] = idempotency_key
        last = events[-1] if events else None
        if isinstance(last, dict) and last.get("idempotency_key") == idempotency_key:
            return path

    events.append(event)
    if len(events) > 50:
        events = events[-50:]

    payload: dict[str, object] = {
        "issue_number": resolved_issue_number,
        "issue_title": (
            issue.title if issue is not None else (issue_title or existing.get("issue_title"))
        ),
        "repo": repo or existing.get("repo"),
        "branch": branch or existing.get("branch"),
        "worktree_path": (
            str(worktree_path) if worktree_path is not None else existing.get("worktree_path")
        ),
        "state": state,
        "last_event_type": event_type,
        "last_updated_at": event["ts"],
        "events": events,
    }
    if details:
        payload["details"] = details
    return write_json_file(path, payload)


def audit_issue_handoff_evidence(
    *,
    root: Path,
    repo: str,
    issue_id: int,
    target: WorktreeInfo,
    report_path: Path,
) -> dict[str, object]:
    return _audit_issue_handoff_evidence(
        root=root,
        repo=repo,
        issue_id=issue_id,
        target=target,
        report_path=report_path,
        read_json_file_fn=read_json_file,
        read_closeout_report_fn=read_closeout_report,
        issue_state_path_fn=issue_state_path,
    )


def build_issue_handback_comment(summary: dict[str, object]) -> str:
    return _build_issue_handback_comment(summary)


def issue_has_handback_comment(
    *,
    root: Path,
    repo: str,
    issue_id: int,
    evidence_hash: str,
) -> bool:
    try:
        data = get_issue(root, repo, issue_id, comments=True)
    except CliError:
        return False
    if not isinstance(data, dict):
        return False
    comments = data.get("comments")
    if not isinstance(comments, list):
        return False
    needle = f"Evidence hash: {evidence_hash}"
    for comment in comments:
        if isinstance(comment, dict) and needle in str(comment.get("body") or ""):
            return True
    return False


def append_issue_handback_comment(
    *,
    root: Path,
    repo: str,
    issue_id: int,
    summary: dict[str, object],
) -> None:
    if issue_has_handback_comment(
        root=root,
        repo=repo,
        issue_id=issue_id,
        evidence_hash=str(summary["evidence_hash"]),
    ):
        return
    comment_issue(root, repo, issue_id, build_issue_handback_comment(summary))


def pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def worktree_agent_status(path: Path) -> dict[str, object] | None:
    return read_json_file(worktree_agent_run_dir(path) / "status.json")


def worktree_agent_running(path: Path) -> bool:
    status = worktree_agent_status(path)
    if not status:
        return False
    backend = status.get("backend")
    if backend == "tmux":
        session_name = status.get("session_name")
        state = status.get("state")
        return (
            isinstance(session_name, str)
            and state == "interactive"
            and tmux_session_exists(session_name)
        )
    pid = status.get("pid")
    if not isinstance(pid, int):
        return False
    state = status.get("state")
    return state in {"starting", "running"} and pid_is_running(pid)


def batch_run_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    suffix = f"{os.getpid():x}"
    return f"run-{stamp}-{suffix}"


def batch_run_dir(root: Path, run_id: str) -> Path:
    return worktree_runs_root(root) / run_id


def batch_manifest_path(root: Path, run_id: str) -> Path:
    return batch_run_dir(root, run_id) / "manifest.json"


def batch_entry_path(root: Path, run_id: str, issue_number: int, agent: str) -> Path:
    return batch_run_dir(root, run_id) / f"issue-{issue_number}-{agent}.json"


def write_batch_entry(root: Path, run_id: str, entry: BatchLaunchResult) -> Path:
    payload = {
        "issue_number": entry.issue_number,
        "agent": entry.agent,
        "worktree_path": str(entry.worktree_path),
        "branch": entry.branch,
        "command": entry.command,
        "state": entry.state,
        "pid": entry.pid,
        "backend": entry.backend,
        "session_name": entry.session_name,
        "window_name": entry.window_name,
        "local_status_path": str(entry.local_status_path) if entry.local_status_path else None,
        "stdout_log_path": str(entry.stdout_log_path) if entry.stdout_log_path else None,
        "stderr_log_path": str(entry.stderr_log_path) if entry.stderr_log_path else None,
        "detail": entry.detail,
        "generated_at": datetime.now(UTC).isoformat(),
    }
    return write_json_file(batch_entry_path(root, run_id, entry.issue_number, entry.agent), payload)


def agent_requires_tty(agent: str) -> bool:
    return AGENT_CAPABILITIES.get(agent, {}).get("requires_tty", False)


def agent_supports_detached(agent: str) -> bool:
    return AGENT_CAPABILITIES.get(agent, {}).get("supports_detached", False)


def read_log_tail(path: Path, *, line_count: int = 5) -> str:
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-line_count:]).strip()


def write_worktree_runtime_status(path: Path, payload: dict[str, object]) -> Path:
    runtime_dir = worktree_agent_run_dir(path)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    return write_json_file(runtime_dir / "status.json", payload)


def launch_agent_detached(
    *,
    root: Path,
    run_id: str,
    issue_number: int,
    path: Path,
    branch: str,
    agent: str,
    command: str,
) -> BatchLaunchResult:
    if not agent_supports_detached(agent):
        raise CliError(f"Agent '{agent}' does not support detached startup")
    ensure_uv_venv(path)
    runtime_dir = worktree_agent_run_dir(path)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    stdout_log = runtime_dir / "stdout.log"
    stderr_log = runtime_dir / "stderr.log"
    status_path = runtime_dir / "status.json"
    pid_path = runtime_dir / "pid"
    shell_cmd = f"if [ -f .venv/bin/activate ]; then source .venv/bin/activate; fi; exec {command}"
    started_at = datetime.now(UTC).isoformat()
    with stdout_log.open("ab") as stdout_fp, stderr_log.open("ab") as stderr_fp:
        proc = subprocess.Popen(
            ["bash", "-lc", shell_cmd],
            cwd=path,
            stdout=stdout_fp,
            stderr=stderr_fp,
            start_new_session=True,
        )
    try:
        proc.wait(timeout=DETACHED_STARTUP_PROBE_SECONDS)
        exited_early = True
    except subprocess.TimeoutExpired:
        exited_early = False

    if not exited_early:
        state = "running"
        detail = "started detached agent process"
    else:
        state = "failed"
        stderr_tail = read_log_tail(stderr_log)
        detail = stderr_tail or "agent exited during detached startup probe"
    status_payload: dict[str, object] = {
        "run_id": run_id,
        "issue_number": issue_number,
        "agent": agent,
        "branch": branch,
        "command": command,
        "backend": "detached",
        "state": state,
        "pid": proc.pid,
        "started_at": started_at,
        "stdout_log_path": str(stdout_log),
        "stderr_log_path": str(stderr_log),
        "orchestrator_manifest": str(batch_manifest_path(root, run_id)),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    write_json_file(status_path, status_payload)
    pid_path.write_text(f"{proc.pid}\n", encoding="utf-8")
    return BatchLaunchResult(
        issue_number=issue_number,
        agent=agent,
        worktree_path=path,
        branch=branch,
        command=command,
        state=state,
        pid=proc.pid,
        local_status_path=status_path,
        stdout_log_path=stdout_log,
        stderr_log_path=stderr_log,
        backend="detached",
        detail=detail,
    )


def record_tmux_agent_launch(
    *,
    root: Path,
    run_id: str,
    issue_number: int,
    path: Path,
    branch: str,
    agent: str,
    command: str,
    session_name: str,
    window_name: str,
) -> BatchLaunchResult:
    runtime_dir = worktree_agent_run_dir(path)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    stdout_log = runtime_dir / "stdout.log"
    stderr_log = runtime_dir / "stderr.log"
    status_payload: dict[str, object] = {
        "run_id": run_id,
        "issue_number": issue_number,
        "agent": agent,
        "branch": branch,
        "command": command,
        "backend": "tmux",
        "state": "interactive",
        "pid": None,
        "session_name": session_name,
        "window_name": window_name,
        "stdout_log_path": str(stdout_log),
        "stderr_log_path": str(stderr_log),
        "orchestrator_manifest": str(batch_manifest_path(root, run_id)),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    status_path = write_worktree_runtime_status(path, status_payload)
    return BatchLaunchResult(
        issue_number=issue_number,
        agent=agent,
        worktree_path=path,
        branch=branch,
        command=command,
        state="interactive",
        pid=None,
        local_status_path=status_path,
        stdout_log_path=stdout_log,
        stderr_log_path=stderr_log,
        backend="tmux",
        session_name=session_name,
        window_name=window_name,
        detail="started tmux interactive agent session",
    )


def choose_agent_interactive(default: str = "codex") -> str:
    mapping = {
        "1": "gemini",
        "gemini": "gemini",
        "2": "claude",
        "claude": "claude",
        "3": "codex",
        "codex": "codex",
    }
    while True:
        print("Choose agent:")
        print("  1) gemini")
        print("  2) claude")
        print("  3) codex")
        print("  0) Back")
        default_choice = {"gemini": "1", "claude": "2", "codex": "3"}.get(default, "3")
        raw = input(f"Choice [{default_choice}]: ").strip()
        if not raw:
            return default
        if raw in {"0", "back"}:
            raise CliError("Back")
        if raw.lower() in mapping:
            return mapping[raw.lower()]
        print("Invalid choice.")


def choose_agent_mode_interactive(default: str = "yolo") -> str:
    mapping = {
        "1": "normal",
        "normal": "normal",
        "2": "yolo",
        "yolo": "yolo",
    }
    while True:
        print(f"Choose launch mode ({default} default):")
        print("  1) normal")
        print("  2) yolo / equivalent")
        print("  0) Back")
        raw = input(f"Choice [{'2' if default == 'yolo' else '1'}]: ").strip()
        if not raw:
            return default
        if raw in {"0", "back"}:
            raise CliError("Back")
        if raw.lower() in mapping:
            return mapping[raw.lower()]
        print("Invalid choice.")


def choose_handoff_action_interactive(default: str = "execute-now") -> str:
    mapping = {
        "1": "execute-now",
        "execute-now": "execute-now",
        "execute": "execute-now",
        "2": "print-only",
        "print-only": "print-only",
        "print": "print-only",
    }
    while True:
        print("Choose handoff behavior:")
        print("  1) execute-now")
        print("  2) print-only (open shell, do not launch agent)")
        print("  0) Back")
        raw = input(f"Choice [{'1' if default == 'execute-now' else '2'}]: ").strip()
        if not raw:
            return default
        if raw in {"0", "back"}:
            raise CliError("Back")
        if raw.lower() in mapping:
            return mapping[raw.lower()]
        print("Invalid choice.")


def choose_post_create_action_interactive() -> str:
    while True:
        print("Next action after worktree creation:")
        print("  1) Open shell with agent handoff (default)")
        print("  2) Return to menu")
        print("  0) Back")
        raw = input("Choice [1]: ").strip() or "1"
        if raw in {"1", "shell"}:
            return "shell"
        if raw in {"2", "return"}:
            return "return"
        if raw in {"0", "back"}:
            raise CliError("Back")
        print("Invalid choice.")


def worktree_issue_id(path: Path) -> int | None:
    try:
        branch = current_branch(path)
    except CliError:
        return None
    return extract_issue_id_from_branch(branch)


def fetch_issue_labels_for_prompt(root: Path, repo: str | None, issue_id: int | None) -> str:
    if repo is None or issue_id is None or not tracker_available():
        return ""
    try:
        data = get_issue(root, repo, issue_id)
    except CliError:
        return ""
    if not isinstance(data, dict):
        return ""
    labels = [x["name"] for x in data.get("labels", []) if isinstance(x, dict) and "name" in x]
    return "|".join(labels)


def choose_default_launch_agent(pool: tuple[str, ...] = DEFAULT_INTERACTIVE_AGENT_POOL) -> str:
    return random.choice(pool)


def resolve_cli_launch_request(
    args: argparse.Namespace, *, default_agent: str = "codex"
) -> tuple[str, str, str, str]:
    agent, agent_mode, handoff, mux = resolve_launch_request(args)
    requested_agent = getattr(args, "agent", None)
    if requested_agent == "random":
        agent = choose_default_launch_agent()
    elif requested_agent is None:
        agent = default_agent
    return agent, agent_mode, handoff, mux


def build_agent_prompt_for_worktree(path: Path, root: Path, repo: str | None) -> str:
    branch = run(["git", "branch", "--show-current"], cwd=path).stdout.strip() or "(detached)"
    issue_id = worktree_issue_id(path)
    issue_ref = f"GitLab issue #{issue_id}" if issue_id is not None else "no linked GitLab issue"
    issue_labels = fetch_issue_labels_for_prompt(root, repo, issue_id)
    labels_clause = issue_labels or "-"
    prompt_lines = [
        (
            f"Context: {issue_ref}; project {repo or '(gitlab remote unavailable)'}; "
            f"branch {branch}; worktree {path}; labels {labels_clause}."
        ),
        (
            "Read: CLAUDE.md; docs/ARCHITECTURE.md; "
            "issue-linked ADRs if easy to identify from repo or issue context."
        ),
    ]
    if issue_id is None:
        prompt_lines.append(
            "Worktree policy: this path is not an issue worktree branch. Do not start new "
            "implementation from main or another non-issue branch; create or resume the "
            "correct issue worktree first unless the operator explicitly directs otherwise."
        )
    prompt_lines.extend(
        [
            (
                "Operating mode: you are the implementation owner for this issue worktree. "
                "Proceed without asking for permission on clear, reversible next steps; ask "
                "only for destructive actions, production access, or policy/security decisions "
                "that the repo rules require escalating."
            ),
            (
                "Scope: only this issue. Do not broaden scope, bundle opportunistic cleanup, "
                "or repair unrelated failures in the same branch. If unrelated drift blocks "
                "validation, document it as a separate follow-up and keep this branch focused."
            ),
            (
                "First step: inspect the current branch diff, linked GitLab issue, issue "
                "labels, dependencies, relevant ADRs/docs, and the smallest set of files that "
                "control the behavior before editing."
            ),
            (
                "Context lookup: prefer GitNexus for unfamiliar flows and blast-radius checks "
                "when it is available. Use context/impact before editing shared symbols, then "
                "run detect_changes before commit. If GitNexus is unavailable or stale, fall "
                "back to rg, git diff/log, and direct file reads; do not block on GitNexus."
            ),
            (
                "Execution loop: inspect; form the smallest defensible plan; add or update "
                "tests before behavior changes when practical; implement; run the narrowest "
                "useful checks; then run make preflight-session and make pre-validate-session "
                "before push. Fix failures and repeat until the issue is actually complete."
            ),
            (
                "Change shape: keep diffs small and reversible; prefer deletion over addition; "
                "reuse existing patterns before introducing new abstractions; do not add "
                "dependencies without explicit need."
            ),
            (
                "Documentation: reconcile implementation with relevant ADRs, runbooks, and "
                "architecture docs. Adhere to the project's pithy, narrative technical style "
                "without unduly dehydrating critical content; a feature is incomplete while "
                "its documentation remains stale."
            ),
            (
                "Do not stop at: MR creation, one passing test, a local commit, a pushed "
                "branch, or a partial implementation. Those are intermediate states."
            ),
            (
                "Review gate: before claiming completion, run a senior-engineer review pass "
                "focused on bugs, regressions, security/operability risks, and missing tests. "
                "If a second agent is available, use it for that review; otherwise perform the "
                "review yourself with the same standard."
            ),
            (
                "Completion sequence: push through make worktree-push-issue or an equivalent "
                "prevalidated push; create/update the MR; address review feedback; merge to "
                "the target branch; close and normalize the issue; record validation evidence; "
                "finalize .build hand-back evidence; then run make finish-worktree-close. "
                "Report cleanup residue explicitly, but do not treat worktree or branch "
                "deletion as semantic completion."
            ),
            (
                "Pause only if: repo rules mandate escalation, a security/policy decision is "
                "unsafe to infer, required credentials or permissions are missing, or a "
                "destructive operation is unavoidable. Otherwise make a reasonable local "
                "decision, keep moving, and report blockers with the exact failed command, "
                "evidence, and next command needed."
            ),
        ]
    )
    return "\n".join(prompt_lines)


def build_review_prompt_for_worktree(
    path: Path,
    root: Path,
    repo: str | None,
    *,
    implementation_agent: str,
) -> str:
    branch = run(["git", "branch", "--show-current"], cwd=path).stdout.strip() or "(detached)"
    issue_id = worktree_issue_id(path)
    issue_ref = f"GitLab issue #{issue_id}" if issue_id is not None else "no linked GitLab issue"
    issue_labels = fetch_issue_labels_for_prompt(root, repo, issue_id)
    labels_clause = issue_labels or "-"
    prompt_lines = [
        (
            f"Context: reviewer lane for {issue_ref}; "
            f"project {repo or '(gitlab remote unavailable)'}; "
            f"branch {branch}; worktree {path}; labels {labels_clause}; implementation agent "
            f"{implementation_agent}."
        ),
        (
            "Read: CLAUDE.md; docs/ARCHITECTURE.md; "
            "issue-linked ADRs if easy to identify from repo or issue context."
        ),
        (
            "Role: reviewer only. Do not take ownership of implementation unless the primary "
            "agent is blocked or the operator explicitly redirects you."
        ),
        (
            "Review focus: bugs, behavioral regressions, security/operability risks, contract "
            "drift, cleanup gaps, documentation staleness, and missing tests."
        ),
        (
            "Method: inspect the current branch diff first, then read only the smallest set of "
            "surrounding files needed to validate correctness."
        ),
        (
            "Output: report concrete findings first with file references and the specific risk. "
            "If no findings remain, say that explicitly and note any residual test gaps."
        ),
        (
            "Do not approve based only on a passing test subset, an open merge request, or the "
            "primary agent's summary."
        ),
    ]
    return "\n".join(prompt_lines)


def build_agent_command(agent: str, mode: str, prompt: str) -> str:
    quoted = shell_quote(prompt)
    if agent == "gemini":
        approval_flag = "--approval-mode=yolo " if mode == "yolo" else ""
        return f"gemini {approval_flag}-i {quoted}".strip()
    if agent == "claude":
        flag = "--dangerously-skip-permissions " if mode == "yolo" else ""
        return f"claude {flag}{quoted}".strip()
    if agent == "codex":
        flag = "--yolo " if mode == "yolo" else ""
        return f"codex {flag}{quoted}".strip()
    raise CliError(f"Unsupported agent '{agent}'")


def worktree_env_preamble() -> str:
    return (
        "if [ -f .venv/bin/activate ]; then source .venv/bin/activate; fi; "
        'export CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"; '
        'case "$CODEX_HOME" in /*) ;; *) export CODEX_HOME="$PWD/$CODEX_HOME" ;; esac; '
        'mkdir -p "$CODEX_HOME"'
    )


def handoff_to_agent_or_shell(
    *,
    path: Path,
    root: Path,
    repo: str | None,
    agent: str | None = None,
    agent_mode: str | None = None,
    review_agent: str | None = None,
    review_agent_mode: str | None = None,
    handoff: str | None = None,
    print_only_override: bool = False,
    mux: str | None = None,
) -> None:
    await_worktree_ready_if_provisioning(path)
    ensure_uv_venv(path)
    agent_val = (agent or choose_agent_interactive()).lower()
    mode_val = (agent_mode or choose_agent_mode_interactive()).lower()
    review_agent_val = review_agent.lower() if review_agent else None
    review_mode_val = (review_agent_mode or "normal").lower() if review_agent_val else None
    handoff_val = (handoff or choose_handoff_action_interactive()).lower()
    if print_only_override:
        handoff_val = "print-only"

    prompt = build_agent_prompt_for_worktree(path, root, repo)
    command = build_agent_command(agent_val, mode_val, prompt)
    review_prompt = (
        build_review_prompt_for_worktree(
            path,
            root,
            repo,
            implementation_agent=agent_val,
        )
        if review_agent_val
        else None
    )
    review_command = (
        build_agent_command(review_agent_val, review_mode_val or "normal", review_prompt)
        if review_agent_val and review_prompt
        else None
    )

    if mux is None:
        mux = auto_detect_mux() if handoff_val == "execute-now" else "none"

    print()
    print(f"Target: {path}")
    print(f"Agent:  {agent_val} ({mode_val})")
    if review_agent_val:
        print(f"Review: {review_agent_val} ({review_mode_val})")
    print(f"Mux:    {mux}")
    print(f"Prompt: {prompt}")
    if review_prompt is not None:
        print(f"Review prompt: {review_prompt}")
    sys.stdout.flush()

    if review_agent_val and handoff_val == "execute-now" and mux == "none":
        raise CliError(
            "Review lane requires tmux/zellij or print-only handoff; rerun without --no-mux"
        )

    if review_agent_val and handoff_val == "execute-now" and mux == "zellij":
        session = worktree_session_pair(path.name)
        try:
            launch_zellij_batch_session(
                session_name=session.session_name,
                launches=[
                    ("implement", path, command),
                    ("review", path, review_command or ""),
                ],
            )
            return
        except (subprocess.CalledProcessError, OSError) as exc:
            raise CliError(
                f"Review lane launch failed via zellij: {exc}. "
                "Rerun with a working mux or use HANDOFF=print-only."
            ) from exc

    if review_agent_val and handoff_val == "execute-now" and mux == "tmux":
        session = worktree_session_pair(path.name)
        try:
            launch_tmux_batch_session(
                session_name=session.session_name,
                launches=[
                    ("implement", path, command),
                    ("review", path, review_command or ""),
                ],
            )
            return
        except (subprocess.CalledProcessError, OSError) as exc:
            raise CliError(
                f"Review lane launch failed via tmux: {exc}. "
                "Rerun with a working mux or use HANDOFF=print-only."
            ) from exc

    if mux == "zellij" and handoff_val == "execute-now":
        try:
            launch_zellij_session(path=path, agent_command=command)
            return
        except (subprocess.CalledProcessError, OSError) as exc:
            eprint(f"WARNING: zellij launch failed ({exc}); falling back to direct shell execution")
            mux = "none"

    if mux == "tmux" and handoff_val == "execute-now":
        try:
            launch_tmux_session(path=path, agent_command=command)
            return
        except (subprocess.CalledProcessError, OSError) as exc:
            eprint(f"WARNING: tmux launch failed ({exc}); falling back to direct shell execution")
            mux = "none"

    if handoff_val == "execute-now":
        path_q = shell_quote(str(path))
        cmd = f"cd {path_q} && {worktree_env_preamble()}; {command}"
        os.execvp("bash", ["bash", "-lc", cmd])

    if not sys.stdin.isatty():
        return
    open_shell(path)


def wants_agent_launch(args: argparse.Namespace) -> bool:
    return bool(
        getattr(args, "agent", None)
        or getattr(args, "agent_mode", None)
        or getattr(args, "review_agent", None)
        or getattr(args, "review_agent_mode", None)
        or getattr(args, "handoff", None)
        or getattr(args, "print_only", False)
        or getattr(args, "tmux", None)
        or getattr(args, "zellij", None)
        or getattr(args, "no_mux", False)
    )


def run_command_in_worktree(path: Path, command: str) -> None:
    print(f"Running in {path}: {command}")
    subprocess.run(["bash", "-lc", command], cwd=path, check=True)


def run_pre_validate(path: Path) -> None:
    print(f"Running pre-push validation in {path} (make validate-pre-push)")
    subprocess.run(["bash", "-lc", "make validate-pre-push"], cwd=path, check=True)


def tmux_available() -> bool:
    return shutil.which("tmux") is not None


def tmux_session_exists(name: str) -> bool:
    result = subprocess.run(["tmux", "has-session", "-t", name], capture_output=True)
    return result.returncode == 0


def tmux_session_name_for_worktree(path: Path) -> str:
    return path.name


def worktree_session_pair(label: str) -> SessionPair:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    session_name = f"{label}-{stamp}-{os.getpid()}"
    return SessionPair(label=label, session_name=session_name)


def launch_tmux_session(
    *,
    path: Path,
    agent_command: str,
    session_name: str | None = None,
    attach: bool = True,
) -> None:
    name = session_name or tmux_session_name_for_worktree(path)
    path_str = str(path)
    venv_preamble = worktree_env_preamble()

    if tmux_session_exists(name):
        print(f"tmux session '{name}' already exists — attaching.")
        if attach:
            os.execvp("tmux", ["tmux", "attach-session", "-t", name])
        return

    subprocess.run(
        [
            "tmux",
            "new-session",
            "-d",
            "-s",
            name,
            "-c",
            path_str,
            "-x",
            "220",
            "-y",
            "55",
        ],
        check=True,
    )
    pane_listing = subprocess.run(
        ["tmux", "list-panes", "-t", name, "-F", "#{session_name}:#{window_index}.#{pane_index}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    first_pane = pane_listing.splitlines()[0] if pane_listing else f"{name}:0.0"
    initial_window = first_pane.rsplit(".", 1)[0]
    subprocess.run(["tmux", "rename-window", "-t", initial_window, name], check=True)
    subprocess.run(
        ["tmux", "split-window", "-h", "-t", initial_window, "-c", path_str],
        check=True,
    )
    subprocess.run(
        ["tmux", "send-keys", "-t", f"{initial_window}.1", venv_preamble, "Enter"],
        check=True,
    )
    subprocess.run(
        [
            "tmux",
            "send-keys",
            "-t",
            f"{initial_window}.0",
            f"{venv_preamble} && {agent_command}",
            "Enter",
        ],
        check=True,
    )
    subprocess.run(["tmux", "select-pane", "-t", f"{initial_window}.0"], check=True)

    print(f"tmux session '{name}' launching in {path}")
    print(f"  Session label: {name}")
    print(f"  Session name:  {name}")
    print("  Left pane:  agent running")
    print("  Right pane: shell ready")
    print(f"  Attach:    tmux a -t {name}")
    print("  List:      tmux ls")

    if attach:
        os.execvp("tmux", ["tmux", "attach-session", "-t", name])


def _launch_tmux_worktree_window(
    *,
    session_name: str,
    window_name: str,
    path: Path,
    agent_command: str,
    create_session: bool,
) -> None:
    path_str = str(path)
    venv_preamble = worktree_env_preamble()
    target = f"{session_name}:{window_name}"

    if create_session:
        subprocess.run(
            [
                "tmux",
                "new-session",
                "-d",
                "-s",
                session_name,
                "-n",
                window_name,
                "-c",
                path_str,
                "-x",
                "220",
                "-y",
                "55",
            ],
            check=True,
        )
    else:
        subprocess.run(
            [
                "tmux",
                "new-window",
                "-t",
                session_name,
                "-n",
                window_name,
                "-c",
                path_str,
            ],
            check=True,
        )

    subprocess.run(["tmux", "split-window", "-h", "-t", target, "-c", path_str], check=True)
    subprocess.run(["tmux", "send-keys", "-t", f"{target}.1", venv_preamble, "Enter"], check=True)
    subprocess.run(
        ["tmux", "send-keys", "-t", f"{target}.0", f"{venv_preamble} && {agent_command}", "Enter"],
        check=True,
    )
    subprocess.run(["tmux", "select-pane", "-t", f"{target}.0"], check=True)


def launch_tmux_batch_session(
    *,
    session_name: str,
    launches: list[tuple[str, Path, str]],
    attach: bool = True,
    announce_windows: bool = True,
) -> None:
    if tmux_session_exists(session_name):
        print(f"tmux session '{session_name}' already exists — replacing.")
        subprocess.run(["tmux", "kill-session", "-t", session_name], check=False)

    if not launches:
        raise CliError("No launches provided for tmux batch session.")

    print(f"tmux session '{session_name}' launching with {len(launches)} worktree window(s)")

    for idx, (window_name, path, agent_command) in enumerate(launches):
        _launch_tmux_worktree_window(
            session_name=session_name,
            window_name=window_name,
            path=path,
            agent_command=agent_command,
            create_session=(idx == 0),
        )

    subprocess.run(["tmux", "select-window", "-t", f"{session_name}:0"], check=True)

    if announce_windows:
        for window_name, path, _ in launches:
            print(f"  {window_name}: {path}")
    print(f"  Reattach:   tmux a -t {session_name}")
    print("  List all:   tmux ls")

    if attach:
        os.execvp("tmux", ["tmux", "attach-session", "-t", session_name])


def _launch_tmux_viewer_window(
    *,
    session_name: str,
    window_name: str,
    path: Path,
    stdout_log_path: Path,
    create_session: bool,
) -> None:
    path_str = str(path)
    target = f"{session_name}:{window_name}"
    venv_preamble = worktree_env_preamble()
    log_cmd = (
        f"touch {shell_quote(str(stdout_log_path))} && "
        f"tail -n 50 -f {shell_quote(str(stdout_log_path))}"
    )

    if create_session:
        subprocess.run(
            [
                "tmux",
                "new-session",
                "-d",
                "-s",
                session_name,
                "-n",
                window_name,
                "-c",
                path_str,
                "-x",
                "220",
                "-y",
                "55",
            ],
            check=True,
        )
    else:
        subprocess.run(
            [
                "tmux",
                "new-window",
                "-t",
                session_name,
                "-n",
                window_name,
                "-c",
                path_str,
            ],
            check=True,
        )

    subprocess.run(["tmux", "split-window", "-h", "-t", target, "-c", path_str], check=True)
    subprocess.run(["tmux", "send-keys", "-t", f"{target}.0", log_cmd, "Enter"], check=True)
    subprocess.run(["tmux", "send-keys", "-t", f"{target}.1", venv_preamble, "Enter"], check=True)
    subprocess.run(["tmux", "select-pane", "-t", f"{target}.1"], check=True)


def launch_tmux_batch_viewer(
    *,
    session_name: str,
    views: list[tuple[str, Path, Path]],
    attach: bool = True,
) -> None:
    if tmux_session_exists(session_name):
        print(f"tmux session '{session_name}' already exists — replacing.")
        subprocess.run(["tmux", "kill-session", "-t", session_name], check=False)

    if not views:
        raise CliError("No worktree views provided for tmux batch viewer.")

    print(f"tmux session '{session_name}' launching with {len(views)} worktree viewer(s)")

    for idx, (window_name, path, stdout_log_path) in enumerate(views):
        _launch_tmux_viewer_window(
            session_name=session_name,
            window_name=window_name,
            path=path,
            stdout_log_path=stdout_log_path,
            create_session=(idx == 0),
        )

    subprocess.run(["tmux", "select-window", "-t", f"{session_name}:0"], check=True)
    print(f"  Reattach:   tmux a -t {session_name}")
    print("  Left pane:  agent stdout log tail")
    print("  Right pane: interactive shell")

    if attach:
        os.execvp("tmux", ["tmux", "attach-session", "-t", session_name])


def zellij_bin() -> str:
    return shutil.which("zellij") or os.path.expanduser("~/bin/zellij")


def zellij_available() -> bool:
    path = zellij_bin()
    return os.path.isfile(path) and os.access(path, os.X_OK)


def zellij_session_exists(name: str) -> bool:
    zj = zellij_bin()
    result = subprocess.run([zj, "list-sessions"], capture_output=True, text=True)
    for line in result.stdout.splitlines():
        cleaned = ANSI_ESCAPE_RE.sub("", line).strip()
        if cleaned.startswith(name):
            return True
    return False


def disable_terminal_flow_control() -> None:
    # Ctrl+S is used by our zellij config for scroll mode, so disable XON/XOFF
    # before handing the terminal over to zellij.
    subprocess.run(
        ["stty", "-ixon"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def launch_zellij_session(
    *,
    path: Path,
    agent_command: str,
    session_name: str | None = None,
    attach: bool = True,
) -> None:
    import tempfile

    zj = zellij_bin()
    pair = worktree_session_pair(path.name)
    label = session_name or pair.label
    name = session_name or pair.session_name
    path_str = str(path)
    disable_terminal_flow_control()

    print(f"zellij session '{label}' launching in {path}")
    print(f"  Session label: {label}")
    print(f"  Session name:  {name}")

    if zellij_session_exists(name):
        print(f"zellij session '{name}' already exists — attaching.")
        if attach:
            os.execvp(zj, [zj, "attach", name])
        return

    temp_dir = Path(tempfile.mkdtemp(prefix=f"wt-layout-{name}-"))
    layout_file = temp_dir / "layout.kdl"
    agent_script = _write_zellij_worktree_wrapper_script(
        temp_dir / "agent.sh", path_str=path_str, command=agent_command
    )
    shell_script = _write_zellij_worktree_wrapper_script(
        temp_dir / "shell.sh", path_str=path_str, shell=True
    )
    layout_file.write_text(
        f"""\
layout {{
    cwd "{path_str}"
    pane split_direction="vertical" {{
        pane command={json.dumps(str(agent_script))} {{
            name "agent"
            focus true
        }}
        pane command={json.dumps(str(shell_script))} {{
            name "shell"
        }}
    }}
}}
""",
        encoding="utf-8",
    )

    print(f"  Attach:    zellij attach {name}")
    print("  List:      zellij ls")

    if attach:
        _exec_zellij_with_layout_cleanup(
            zj,
            ["--new-session-with-layout", str(layout_file), "--session", name],
            str(temp_dir),
        )
    else:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _zellij_worktree_pane_layout(
    temp_dir: Path, tab_name: str, path: Path, agent_command: str, *, focus: bool
) -> str:
    agent_script = _write_zellij_worktree_wrapper_script(
        temp_dir / f"{tab_name}-agent.sh", path_str=str(path), command=agent_command
    )
    shell_script = _write_zellij_worktree_wrapper_script(
        temp_dir / f"{tab_name}-shell.sh", path_str=str(path), shell=True
    )
    focus_str = "true" if focus else "false"
    return (
        '      pane split_direction="vertical" {\n'
        f"        pane command={json.dumps(str(agent_script))} {{\n"
        f'          name "agent"\n'
        f"          focus {focus_str}\n"
        "        }\n"
        f"        pane command={json.dumps(str(shell_script))} {{\n"
        f'          name "shell"\n'
        "        }\n"
        "      }"
    )


def launch_zellij_batch_session(
    *,
    session_name: str,
    launches: list[tuple[str, Path, str]],
    attach: bool = True,
    announce_tabs: bool = True,
) -> None:
    import tempfile

    zj = zellij_bin()
    disable_terminal_flow_control()
    if zellij_session_exists(session_name):
        print(f"zellij session '{session_name}' already exists — replacing.")
        run([zj, "delete-session", session_name], check=False)

    print(f"zellij session '{session_name}' launching with {len(launches)} worktree tab(s)")

    temp_dir = Path(tempfile.mkdtemp(prefix=f"wt-batch-{session_name}-"))
    tabs: list[str] = []
    for idx, (tab_name, path, agent_command) in enumerate(launches):
        pane = _zellij_worktree_pane_layout(
            temp_dir, tab_name, path, agent_command, focus=(idx == 0)
        )
        tabs.append(
            f"    tab name={json.dumps(tab_name)} focus={'true' if idx == 0 else 'false'} {{\n"
            f"{pane}\n"
            "    }"
        )

    layout_file = temp_dir / "layout.kdl"
    layout_file.write_text("layout {\n" + "\n".join(tabs) + "\n}\n", encoding="utf-8")

    if announce_tabs:
        for tab_name, path, _ in launches:
            print(f"  {tab_name}: {path}")
    print(f"  Reattach:   zellij attach {session_name}")
    print("  List all:   zellij ls")

    if attach:
        _exec_zellij_with_layout_cleanup(
            zj,
            ["--new-session-with-layout", str(layout_file), "--session", session_name],
            str(temp_dir),
        )
    else:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _write_zellij_worktree_wrapper_script(
    path: Path, *, path_str: str, command: str | None = None, shell: bool = False
) -> Path:
    if command is None and not shell:
        raise ValueError("wrapper script requires command or shell")
    body: list[str] = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        f"cd {shlex.quote(path_str)}",
        worktree_env_preamble().replace("; ", "\n"),
    ]
    if shell:
        body.append("exec bash -l")
    else:
        body.append(f"exec bash -lc {shlex.quote(command or '')}")
    path.write_text("\n".join(body) + "\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _exec_zellij_with_layout_cleanup(zj: str, args: list[str], temp_dir: str) -> None:
    temp_dir_q = shlex.quote(temp_dir)
    args_q = " ".join(shlex.quote(arg) for arg in [zj, *args])
    cleanup_cmd = f"trap 'rm -rf {temp_dir_q}' EXIT; exec {args_q}"
    os.execvp("bash", ["bash", "-lc", cleanup_cmd])


def resolve_mux_flag(args: argparse.Namespace) -> str | None:
    if getattr(args, "no_tmux", False) or getattr(args, "no_mux", False):
        return "none"
    if getattr(args, "zellij", None):
        return "zellij"
    if getattr(args, "tmux", None):
        return "tmux"
    return None


def auto_detect_mux() -> str:
    if tmux_available():
        return "tmux"
    if zellij_available():
        return "zellij"
    return "none"


def tracker_repo_ready(root: Path) -> tuple[bool, str | None]:
    if not tracker_available():
        return False, None
    try:
        return True, origin_repo_slug(root)
    except CliError:
        return False, None


def merge_request_for_source_branch(root: Path, repo: str, branch: str, state: str) -> dict | None:
    return merge_request_for_branch(root, repo, branch, state)


def format_merge_request_status(mr: dict[str, object] | None) -> str:
    if not mr:
        return "-"
    number = mr.get("number")
    state = str(mr.get("state") or "").lower()
    if not state and mr.get("mergedAt"):
        state = "merged"
    if not state:
        state = "open"
    if mr.get("isDraft") and state == "opened":
        state = "draft"
    prefix = f"!{number}" if number else "mr"
    return f"{prefix}:{state}"


def issue_state_info(root: Path, repo: str, issue_id: int) -> dict | None:
    return get_issue(root, repo, issue_id)


def find_latest_closeout_report(root: Path, issue_id: int) -> Path | None:
    closeout_root = root / WORKTREE_CLOSEOUT_DIR
    if not closeout_root.exists():
        return None
    matches = sorted(
        closeout_root.glob(f"issue-{issue_id}-*.json"),
        key=lambda candidate: candidate.stat().st_mtime,
    )
    return matches[-1] if matches else None


def issue_evidence_summary(root: Path, issue_id: int) -> dict[str, object]:
    def _read_json(path: Path) -> dict[str, object] | None:
        if "worktree-closeouts" in str(path):
            return read_closeout_report(path)
        return read_json_file(path)

    return _issue_evidence_summary(
        root,
        issue_id,
        issue_state_path_fn=issue_state_path,
        latest_closeout_report_path_fn=find_latest_closeout_report,
        read_json_file_fn=_read_json,
        find_latest_validation_receipt_fn=find_latest_validation_receipt,
        historical_issue_evidence_fn=historical_issue_evidence,
        linked_worktree_for_issue_fn=find_linked_worktree_for_issue,
    )


def local_issue_numbers(root: Path, *, active_only: bool = False) -> set[int]:
    numbers: set[int] = set()
    state_root = worktree_state_root(root)
    terminal_states = {"done", "closed", "cleanup-failed", "handback-failed"}
    if state_root.exists():
        for path in state_root.glob("issue-*.json"):
            match = re.match(r"issue-(\d+)\.json$", path.name)
            if not match:
                continue
            payload = read_json_file(path) or {}
            if active_only and payload.get("state") in terminal_states:
                continue
            numbers.add(int(match.group(1)))
    for wt in list_resume_candidates(root):
        issue_id = extract_issue_id_from_branch(wt.branch)
        if issue_id is not None:
            numbers.add(issue_id)
    return numbers


def issue_status_rows(
    root: Path,
    repo: str | None,
    issues: list[Issue],
    *,
    issue_filter: int | None = None,
    include_all: bool = False,
) -> list[dict[str, object]]:
    issue_map = {issue.number: issue for issue in queue_task_issues(issues)}
    if issue_filter is not None:
        numbers = {issue_filter}
    elif include_all:
        numbers = set(issue_map) | local_issue_numbers(root)
    else:
        active = {
            issue.number
            for issue in issue_map.values()
            if issue.state == "open"
            and (lifecycle_status(issue) == "in-progress" or "ready" in issue.labels)
        }
        numbers = active | local_issue_numbers(root, active_only=True)

    rows: list[dict[str, object]] = []
    mr_status_cache: dict[str, str] = {}
    for issue_number in sorted(
        numbers,
        key=lambda number: (
            issue_map[number].seq
            if number in issue_map and issue_map[number].seq is not None
            else 10**9,
            number,
        ),
    ):
        issue = issue_map.get(issue_number)
        evidence = issue_evidence_summary(root, issue_number)
        state = cast(
            dict[str, Any],
            evidence.get("state") if isinstance(evidence.get("state"), dict) else {},
        )
        closeout = cast(
            dict[str, Any],
            evidence.get("closeout") if isinstance(evidence.get("closeout"), dict) else {},
        )
        validation = cast(
            dict[str, Any],
            evidence.get("validation_receipt")
            if isinstance(evidence.get("validation_receipt"), dict)
            else {},
        )
        linked_worktree = evidence.get("linked_worktree") or state.get("worktree_path")
        wt_path = Path(str(linked_worktree)) if linked_worktree else None
        agent_status = worktree_agent_status(wt_path) if wt_path and wt_path.exists() else None
        details = cast(
            dict[str, Any],
            state.get("details") if isinstance(state.get("details"), dict) else {},
        )
        agent = (
            agent_status.get("agent")
            if isinstance(agent_status, dict)
            else details.get("agent")
            if isinstance(details, dict)
            else None
        )
        backend = agent_status.get("backend") if isinstance(agent_status, dict) else None
        runtime_state = agent_status.get("state") if isinstance(agent_status, dict) else None
        session_name = agent_status.get("session_name") if isinstance(agent_status, dict) else None
        live = "-"
        if wt_path and isinstance(agent_status, dict):
            live = "yes" if worktree_agent_running(wt_path) else "no"
        validation_text = "-"
        if validation:
            validation_text = f"{validation.get('check', 'check')}:pass"
        closeout_text = "-"
        if closeout:
            stage = closeout.get("stage") or "present"
            cleanup = closeout.get("cleanup_verified")
            closeout_text = f"{stage}:{cleanup}" if cleanup is not None else str(stage)
        branch = (
            evidence.get("linked_branch")
            or state.get("branch")
            or (agent_status.get("branch") if isinstance(agent_status, dict) else None)
            or "-"
        )
        branch_text = str(branch)
        mr_status = "-"
        if repo and branch_text != "-":
            if branch_text not in mr_status_cache:
                try:
                    mr_status_cache[branch_text] = format_merge_request_status(
                        merge_request_for_source_branch(root, repo, branch_text, "all")
                    )
                except CliError:
                    mr_status_cache[branch_text] = "unknown"
            mr_status = mr_status_cache[branch_text]
        rows.append(
            {
                "issue": issue_number,
                "seq": issue.seq if issue is not None else None,
                "title": issue.title if issue is not None else str(state.get("issue_title") or "-"),
                "issue_status": lifecycle_status(issue) if issue is not None else "-",
                "issue_state": issue.state if issue is not None else "-",
                "worktree": str(linked_worktree or "-"),
                "branch": branch_text,
                "mr": mr_status,
                "agent": str(agent or "-"),
                "runtime": ":".join(
                    part
                    for part in (
                        str(backend or ""),
                        str(runtime_state or ""),
                        str(session_name or ""),
                    )
                    if part
                )
                or "-",
                "live": live,
                "validation": validation_text,
                "closeout": closeout_text,
                "last_event": str(state.get("last_event_type") or "-"),
            }
        )
    return rows


def _clip(value: object, width: int) -> str:
    text = "-" if value is None else str(value)
    if len(text) <= width:
        return text
    return text[: max(0, width - 1)] + "…"


def print_issue_status_rows(rows: list[dict[str, object]]) -> None:
    columns = [
        ("issue", "Issue", 7),
        ("seq", "Seq", 5),
        ("issue_status", "Status", 12),
        ("worktree", "Worktree", 34),
        ("mr", "MR", 12),
        ("agent", "Agent", 8),
        ("runtime", "Runtime", 24),
        ("live", "Live", 5),
        ("validation", "Validation", 24),
        ("closeout", "Closeout", 16),
        ("last_event", "Last event", 24),
    ]
    if not rows:
        print("No issue/worktree/agent status rows found.")
        return
    header = "  ".join(label.ljust(width) for _, label, width in columns)
    print(header)
    print("  ".join("-" * width for _, _, width in columns))
    for row in rows:
        print("  ".join(_clip(row.get(key), width).ljust(width) for key, _, width in columns))


def evidence_drift_findings(root: Path, issues: list[Issue]) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    for issue in queue_task_issues(issues):
        if issue.state != "open" or lifecycle_status(issue) != "in-progress":
            continue
        evidence = issue_evidence_summary(root, issue.number)
        if evidence["linked_worktree"] is None and evidence["state_path"] is None:
            findings.append(
                AuditFinding(
                    severity="warning",
                    issue_number=issue.number,
                    message=(
                        "status:in-progress but no local linked worktree "
                        "or .build evidence in this clone"
                    ),
                )
            )
    return findings


def stale_lock_findings(root: Path, repo: str, issues: list[Issue]) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    for issue in queue_task_issues(issues):
        if issue.state != "open" or lifecycle_status(issue) != "in-progress":
            continue
        if find_linked_worktree_for_issue(root, issue.number) is not None:
            continue
        expected_branch = f"wt/{infer_scope(issue)}/{issue.number}-{slugify_text(issue.title)}"
        try:
            if merge_request_for_source_branch(root, repo, expected_branch, "open"):
                continue
        except CliError:
            pass
        findings.append(
            AuditFinding(
                severity="error",
                issue_number=issue.number,
                message=(
                    "status:in-progress has no linked local worktree and no detected open MR; "
                    f"repair the stale lock or recreate branch {expected_branch}"
                ),
            )
        )
    return findings


def finish_stage(root: Path, wt: WorktreeInfo, repo: str | None) -> str:
    dirty = run(["git", "status", "--porcelain"], cwd=wt.path).stdout.strip()
    if dirty:
        return "implementing"
    branch = wt.branch
    if branch and branch != "(detached)" and repo:
        open_mr = merge_request_for_source_branch(root, repo, branch, "open")
        if open_mr:
            return "review"
        merged_mr = merge_request_for_source_branch(root, repo, branch, "merged")
        if merged_mr:
            return "merged"
    try:
        upstream = run(
            ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
            cwd=wt.path,
        ).stdout.strip()
    except subprocess.CalledProcessError:
        return "ready-to-push"
    if upstream:
        ab = run(
            ["git", "rev-list", "--left-right", "--count", f"{upstream}...HEAD"],
            cwd=wt.path,
        ).stdout.strip()
        if ab:
            behind, ahead = [int(x) for x in ab.split()]
            if ahead > 0:
                return "ready-to-push"
            if ahead == 0 and behind == 0:
                return "mr-open"
    return "mr-open"


def finish_summary(root: Path, *, path: Path | None = None) -> None:
    worktrees = list_worktrees(root)
    target = resolve_current_worktree(path or current_path(), worktrees)
    ready, repo = tracker_repo_ready(root)
    branch = target.branch
    issue_id = extract_issue_id_from_branch(branch) if branch else None
    stage = finish_stage(root, target, repo if ready else None)
    print("Finish Worktree Summary")
    print(f"  worktree: {target.path}")
    print(f"  primary:  {worktrees[0].path}")
    print(f"  branch:   {branch}")
    print(f"  issue:    #{issue_id}" if issue_id else "  issue:    (unparsed)")
    print(f"  stage:    {stage}")
    print(f"  git:      {run(['git', 'status', '-sb'], cwd=target.path).stdout.strip()}")

    if ready and repo and issue_id:
        info = issue_state_info(root, repo, issue_id)
        if info:
            labels = "|".join(x["name"] for x in info.get("labels", []) if isinstance(x, dict))
            print(f"  issue:    {info.get('state')} - {info.get('title')}")
            print(f"  labels:   {labels}")
            print(f"  issueurl: {info.get('url')}")
        open_mr = merge_request_for_source_branch(root, repo, branch, "open")
        merged_mr = merge_request_for_source_branch(root, repo, branch, "merged")
        if open_mr:
            print(f"  mr:       #{open_mr.get('number')} OPEN - {open_mr.get('title')}")
            print(f"  mrurl:    {open_mr.get('url')}")
        elif merged_mr:
            print(f"  mr:       #{merged_mr.get('number')} MERGED")
            print(f"  mrurl:    {merged_mr.get('url')}")
            print(f"  mergedAt: {merged_mr.get('mergedAt')}")
        else:
            print("  mr:       none")
    else:
        if not (ready and repo):
            print("  mr:       (glab unavailable)")
        elif issue_id is None:
            print("  mr:       (not an issue worktree branch)")
        else:
            print("  mr:       (unavailable)")

    print("  policy:   pushes must run preflight + validate-pre-push")
    print("  dod:      merged MR + closed issue + cleaned worktree/branch")
    if stage == "implementing":
        print("  next:     complete implementation/tests; keep git status clean before push")
    elif stage == "ready-to-push":
        print("  next:     make worktree-push-issue")
        if branch and branch != "(detached)":
            print(f"  then:     glab mr create --fill --source-branch {branch}")
    elif stage in {"review", "mr-open"}:
        print(
            "  next:     merge MR; do not stop at MR open. "
            "If conflicts appear, resolve in this worktree and re-validate"
        )
    elif stage == "merged":
        print("  next:     make finish-worktree-close")
    print("  conflict: if merge/rebase conflicts appear:")
    print("            resolve files -> git add <files> -> complete merge/rebase")
    print("            rerun: make preflight-session && make pre-validate-session")
    print("            push conflict-resolution commits before merge")
    print("  cleanup:  git worktree remove <this-worktree-path>")
    if branch and WORKTREE_BRANCH_REGEX.fullmatch(branch):
        print(f"            git branch -d {branch}")
    print("            git worktree prune")


def cleanup_finished_worktree(root: Path, target: WorktreeInfo) -> dict[str, bool]:
    return _cleanup_finished_worktree(
        root,
        target,
        local_branch_exists_fn=local_branch_exists,
        os_module=os,
        run_fn=run,
    )


def closeout_report_path(root: Path, target: WorktreeInfo) -> Path:
    return _closeout_report_path(
        root,
        target,
        extract_issue_id_from_branch_fn=extract_issue_id_from_branch,
    )


def write_closeout_report(root: Path, target: WorktreeInfo, payload: dict[str, object]) -> Path:
    return _write_closeout_report(
        root,
        target,
        payload,
        extract_issue_id_from_branch_fn=extract_issue_id_from_branch,
    )


def closeout_event(
    *,
    stage: str,
    message: str,
    target: WorktreeInfo,
    repo: str | None,
    issue_id: int | None,
) -> dict[str, object]:
    return _closeout_event(
        stage=stage,
        message=message,
        target=target,
        repo=repo,
        issue_id=issue_id,
    )


def verify_cleanup_finished(root: Path, target: WorktreeInfo) -> list[str]:
    return _verify_cleanup_finished(
        root,
        target,
        list_worktrees_fn=list_worktrees,
        local_branch_exists_fn=local_branch_exists,
    )


def close_issue_done(root: Path, *, path: Path | None = None, force: bool = False) -> None:
    worktrees = list_worktrees(root)
    target = resolve_current_worktree(path or current_path(), worktrees)
    ready, repo = tracker_repo_ready(root)
    if not ready or not repo:
        raise CliError("glab/GitLab repo not available")
    issue_id = extract_issue_id_from_branch(target.branch)
    if issue_id is None:
        raise CliError(f"Could not parse issue id from branch {target.branch}")
    report_path = closeout_report_path(root, target)
    report_base: dict[str, object] = {
        "issue_id": issue_id,
        "repo": repo,
        "merged_mr_required": not force,
        "stage": "starting",
        "events": [],
    }
    events = report_base["events"]
    if isinstance(events, list):
        events.append(
            closeout_event(
                stage="starting",
                message="closeout started",
                target=target,
                repo=repo,
                issue_id=issue_id,
            )
        )
    record_issue_handoff_event(
        root=root,
        repo=repo,
        issue_number=issue_id,
        issue_title=target.branch,
        branch=target.branch,
        worktree_path=target.path,
        event_type="closeout-started",
        state="closeout-started",
        details={"force": force, "report_path": str(report_path)},
        idempotency_key=f"closeout-started:{issue_id}:{target.branch}:{target.path}",
    )
    write_closeout_report(root, target, report_base)
    try:
        merged_mr = merge_request_for_source_branch(root, repo, target.branch, "merged")
        if isinstance(events, list):
            events.append(
                closeout_event(
                    stage="merge-check",
                    message=f"merged MR lookup {'found' if merged_mr else 'missed'}",
                    target=target,
                    repo=repo,
                    issue_id=issue_id,
                )
            )
        if not merged_mr and not force:
            raise CliError("No merged MR found for branch; refusing to close issue without --force")
        info = issue_state_info(root, repo, issue_id)
        issue_closed = False
        if info and str(info.get("state", "")).upper() == "CLOSED":
            normalized = normalize_closed_issue_labels(root, repo, issue_id, info)
            print(f"Issue #{issue_id} already closed.")
            if normalized:
                print("Normalized closed-issue lifecycle labels.")
            issue_closed = True
            if isinstance(events, list):
                events.append(
                    closeout_event(
                        stage="issue-close",
                        message="issue already closed; labels normalized",
                        target=target,
                        repo=repo,
                        issue_id=issue_id,
                    )
                )
        else:
            add_labels: list[str] = []
            remove_labels: list[str] = []
            if info:
                label_names = [x["name"] for x in info.get("labels", []) if isinstance(x, dict)]
                if "review" in label_names:
                    remove_labels.append("review")
                if "in-progress" in label_names:
                    remove_labels.append("in-progress")
                if "done" not in label_names:
                    add_labels.append("done")
                if "status:in-progress" in label_names:
                    remove_labels.append("status:in-progress")
                if "status:not-started" in label_names:
                    remove_labels.append("status:not-started")
                if "status:done" not in label_names:
                    add_labels.append("status:done")
            update_issue_labels(root, repo, issue_id, add=add_labels, remove=remove_labels)
            close_issue(root, repo, issue_id)
            print(f"Closed issue #{issue_id}.")
            issue_closed = True
            if isinstance(events, list):
                events.append(
                    closeout_event(
                        stage="issue-close",
                        message="issue closed via glab",
                        target=target,
                        repo=repo,
                        issue_id=issue_id,
                    )
                )
        write_closeout_report(
            root,
            target,
            {
                **report_base,
                "stage": "issue-closed",
                "issue_closed": issue_closed,
            },
        )
        if isinstance(events, list):
            events.append(
                closeout_event(
                    stage="cleanup",
                    message="cleanup started",
                    target=target,
                    repo=repo,
                    issue_id=issue_id,
                )
            )
        cleanup_result: dict[str, object] = {}
        cleanup_verified = False
        cleanup_error: str | None = None
        try:
            cleanup_result = dict(cleanup_finished_worktree(root, target))
            cleanup_problems = verify_cleanup_finished(root, target)
            if cleanup_problems:
                raise CliError("Cleanup verification failed: " + "; ".join(cleanup_problems))
            cleanup_verified = True
            if isinstance(events, list):
                events.append(
                    closeout_event(
                        stage="cleanup-verified",
                        message="cleanup verified",
                        target=target,
                        repo=repo,
                        issue_id=issue_id,
                    )
                )
        except Exception as exc:
            cleanup_error = str(exc)
            print(f"Cleanup deferred: {cleanup_error}")
            print(f"Manual cleanup: git worktree remove {target.path}")
            if target.branch and target.branch != "(detached)":
                print(f"Manual cleanup: git branch -d {target.branch}")
            print("Manual cleanup: git worktree prune")
            if isinstance(events, list):
                events.append(
                    closeout_event(
                        stage="cleanup-deferred",
                        message=cleanup_error,
                        target=target,
                        repo=repo,
                        issue_id=issue_id,
                    )
                )
        write_closeout_report(
            root,
            target,
            {
                **report_base,
                "stage": "complete",
                "issue_closed": issue_closed,
                "cleanup": cleanup_result,
                "cleanup_verified": cleanup_verified,
                **({"cleanup_error": cleanup_error} if cleanup_error else {}),
            },
        )
        record_issue_handoff_event(
            root=root,
            repo=repo,
            issue_number=issue_id,
            issue_title=target.branch,
            branch=target.branch,
            worktree_path=target.path,
            event_type="closeout-complete",
            state="closed",
            details={
                "report_path": str(report_path),
                "issue_closed": issue_closed,
                "cleanup_verified": cleanup_verified,
                **({"cleanup_error": cleanup_error} if cleanup_error else {}),
            },
            idempotency_key=f"closeout-complete:{issue_id}:{target.branch}:{target.path}",
        )
        handback_summary = audit_issue_handoff_evidence(
            root=root,
            repo=repo,
            issue_id=issue_id,
            target=target,
            report_path=report_path,
        )
        record_issue_handoff_event(
            root=root,
            repo=repo,
            issue_number=issue_id,
            issue_title=target.branch,
            branch=target.branch,
            worktree_path=target.path,
            event_type="handback-audited",
            state="evidence-audited",
            details={
                "report_path": str(report_path),
                "evidence_hash": handback_summary["evidence_hash"],
            },
            idempotency_key=f"handback-audited:{issue_id}:{handback_summary['evidence_hash']}",
        )
        append_issue_handback_comment(
            root=root,
            repo=repo,
            issue_id=issue_id,
            summary=handback_summary,
        )
        record_issue_handoff_event(
            root=root,
            repo=repo,
            issue_number=issue_id,
            issue_title=target.branch,
            branch=target.branch,
            worktree_path=target.path,
            event_type="handback-complete",
            state="done",
            details={
                "report_path": str(report_path),
                "evidence_hash": handback_summary["evidence_hash"],
            },
            idempotency_key=f"handback-complete:{issue_id}:{handback_summary['evidence_hash']}",
        )
        print(f"Closeout report: {report_path}")
    except Exception as exc:
        if isinstance(events, list):
            events.append(
                closeout_event(
                    stage="failed",
                    message=str(exc),
                    target=target,
                    repo=repo,
                    issue_id=issue_id,
                )
            )
        write_closeout_report(
            root,
            target,
            {
                **report_base,
                "stage": "failed",
                "error": str(exc),
            },
        )
        record_issue_handoff_event(
            root=root,
            repo=repo,
            issue_number=issue_id,
            issue_title=target.branch,
            branch=target.branch,
            worktree_path=target.path,
            event_type="closeout-failed",
            state="cleanup-failed",
            details={"report_path": str(report_path), "error": str(exc)},
            idempotency_key=f"closeout-failed:{issue_id}:{target.branch}:{target.path}",
        )
        record_issue_handoff_event(
            root=root,
            repo=repo,
            issue_number=issue_id,
            issue_title=target.branch,
            branch=target.branch,
            worktree_path=target.path,
            event_type="handback-failed",
            state="handback-failed",
            details={"report_path": str(report_path), "error": str(exc)},
            idempotency_key=f"handback-failed:{issue_id}:{target.branch}:{target.path}:{str(exc)}",
        )
        print(f"Closeout report: {report_path}")
        raise


def push_branch_enforced(
    root: Path,
    *,
    path: Path | None = None,
    dry_run: bool = False,
) -> None:
    worktrees = list_worktrees(root)
    target = resolve_current_worktree(path or current_path(), worktrees)
    branch = current_branch(target.path)
    if target.is_primary:
        raise CliError("Refusing to push from primary worktree via issue-worktree push command")
    if not WORKTREE_BRANCH_REGEX.fullmatch(branch):
        raise CliError(f"Branch '{branch}' is not a policy-compliant worktree branch")

    try:
        repo = origin_repo_slug(root)
    except CliError:
        repo = None
    run_preflight(path=target.path, root=root, repo=repo)
    run_pre_validate(target.path)

    push_cmd = ["git", "push", "-u", "origin", branch]
    print(f"Push command: {' '.join(push_cmd)}")
    if dry_run:
        print("Dry run: push not executed.")
        return
    subprocess.run(push_cmd, cwd=target.path, check=True)
    print("Push complete.")


def choose_issue_interactive(selection: QueueSelection) -> Issue:
    if not selection.items:
        raise CliError("Queue is empty")
    print_queue(selection)
    while True:
        raw = input("Pick queue index [1] (0=back): ").strip() or "1"
        if raw in {"0", "back"}:
            raise CliError("Back")
        if raw.isdigit():
            idx = int(raw)
            if 1 <= idx <= len(selection.items):
                return selection.items[idx - 1].issue
        print("Invalid choice.")


def cmd_issue_queue(args: argparse.Namespace) -> int:
    root = repo_root()
    repo = args.repo or origin_repo_slug(root)
    issues = fetch_repo_issues(root, repo, state="all")
    selection = build_queue(
        issues,
        stream_label=args.stream_label,
        from_issue=getattr(args, "from_issue", None),
        mode=args.mode,
    )
    print_queue(selection, limit=args.limit, show_blocked=not args.runnable_only)
    if args.json:
        payload = []
        items = selection.runnable if args.runnable_only else selection.items
        if args.limit is not None:
            items = items[: args.limit]
        for item in items:
            payload.append(
                {
                    "number": item.issue.number,
                    "title": item.issue.title,
                    "seq": item.issue.seq,
                    "runnable": item.runnable,
                    "blocked_reasons": item.blocked_reasons,
                    "labels": item.issue.labels,
                    "task_id": item.issue.task_id,
                }
            )
        print(
            json.dumps(
                {
                    "source_mode": selection.source_mode,
                    "source_note": selection.source_note,
                    "items": payload,
                },
                indent=2,
            )
        )
    return 0


def cmd_issue_create(args: argparse.Namespace) -> int:
    root = repo_root()
    repo = args.repo or origin_repo_slug(root)
    title = args.title.strip()
    if not title:
        raise CliError("TITLE is required")
    if not TITLE_TASK_RE.match(title):
        raise CliError("Task issue title must start with TASK-###: ")
    depends = args.depends.strip() if args.depends else "none"
    labels = ["type:task", "status:not-started"]
    if args.ready:
        labels.append("ready")
    body = build_task_issue_body(seq=args.seq, depends=depends, problem=args.problem or "")
    output = create_issue(root, repo, title=title, description=body, labels=labels)
    print(output.strip())
    return 0


def cmd_issue_evidence(args: argparse.Namespace) -> int:
    root = repo_root()
    issue_id = args.issue
    if issue_id is None:
        issue_id = worktree_issue_id(Path(args.path).resolve() if args.path else current_path())
    if issue_id is None:
        raise CliError("Could not determine issue id; pass --issue or run inside an issue worktree")
    summary = issue_evidence_summary(root, issue_id)
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0
    print(f"Issue evidence: #{issue_id}")
    print(f"  evidence_source: {summary['evidence_source']}")
    print(f"  linked_worktree: {summary['linked_worktree'] or '-'}")
    print(f"  linked_branch:   {summary['linked_branch'] or '-'}")
    print(f"  state_path:      {summary['state_path'] or '-'}")
    state = summary.get("state")
    if isinstance(state, dict):
        print(f"  state:           {state.get('state', '-')}")
        print(f"  last_event:      {state.get('last_event_type', '-')}")
        print(f"  last_updated:    {state.get('last_updated_at', '-')}")
    else:
        print("  state:           -")
    print(f"  closeout_path:   {summary['closeout_path'] or '-'}")
    closeout = summary.get("closeout")
    if isinstance(closeout, dict):
        print(f"  closeout_stage:  {closeout.get('stage', '-')}")
        print(f"  cleanup_verified:{closeout.get('cleanup_verified', '-')}")
    else:
        print("  closeout_stage:  -")
    print(f"  validation_path: {summary['validation_receipt_path'] or '-'}")
    validation_receipt = summary.get("validation_receipt")
    if isinstance(validation_receipt, dict):
        print(f"  validation:      {validation_receipt.get('check', '-')}:pass")
        print(f"  validated_head:  {validation_receipt.get('head_sha', '-')}")
    historical = summary.get("historical")
    if isinstance(historical, dict):
        print(f"  historical_ref:  {historical.get('preferred_branch') or '-'}")
        branch_tip = historical.get("branch_tip")
        if isinstance(branch_tip, dict):
            print(
                f"  historical_tip:  {branch_tip.get('timestamp', '-')} "
                f"{branch_tip.get('subject', '-')}"
            )
        log_matches = historical.get("log_matches")
        if isinstance(log_matches, list):
            print(f"  log_matches:     {len(log_matches)}")
    return 0


def cmd_issue_status(args: argparse.Namespace) -> int:
    root = repo_root()
    try:
        repo = args.repo or origin_repo_slug(root)
        issues = fetch_repo_issues(root, repo, state="all")
    except CliError:
        repo = None
        issues = []
    rows = issue_status_rows(
        root,
        repo,
        issues,
        issue_filter=args.issue,
        include_all=args.all,
    )
    if args.json:
        print(json.dumps(rows, indent=2, sort_keys=True))
        return 0
    print_issue_status_rows(rows)
    return 0


def cmd_write_validation_receipt(args: argparse.Namespace) -> int:
    root = repo_root()
    target_path = Path(args.path).resolve() if args.path else current_path()
    issue_id = args.issue if args.issue is not None else worktree_issue_id(target_path)
    if issue_id is None:
        print("Validation receipt: skipped (not in issue worktree)")
        return 0
    branch = run(["git", "branch", "--show-current"], cwd=target_path).stdout.strip() or None
    receipt_path = write_validation_receipt(
        root,
        issue_id=issue_id,
        worktree_path=target_path,
        branch=branch,
        check_name=args.check,
    )
    print(f"Validation receipt: {receipt_path}")
    return 0


def cmd_issues_audit(args: argparse.Namespace) -> int:
    root = repo_root()
    repo = args.repo or origin_repo_slug(root)
    issues = fetch_repo_issues(root, repo, state="all")
    findings = audit_issues(issues)
    findings.extend(evidence_drift_findings(root, issues))
    findings.extend(stale_lock_findings(root, repo, issues))
    errors = [f for f in findings if f.severity == "error"]
    warnings = [f for f in findings if f.severity == "warning"]

    if args.json:
        print(
            json.dumps(
                {
                    "errors": [{"issue": f.issue_number, "message": f.message} for f in errors],
                    "warnings": [{"issue": f.issue_number, "message": f.message} for f in warnings],
                    "ok": len(errors) == 0,
                },
                indent=2,
            )
        )
    else:
        if errors:
            print("Issue audit: FAILED")
            for finding in errors:
                print(f"  ERROR  #{finding.issue_number}: {finding.message}")
        else:
            print("Issue audit: PASS")
        if warnings:
            for finding in warnings:
                print(f"  WARN   #{finding.issue_number}: {finding.message}")
        print(f"Summary: errors={len(errors)} warnings={len(warnings)}")

    return 1 if errors else 0


def cmd_issues_reconcile(args: argparse.Namespace) -> int:
    root = repo_root()
    repo = args.repo or origin_repo_slug(root)
    issues = fetch_repo_issues(root, repo, state="all")
    task_issues = queue_task_issues(issues)

    changed = 0
    for issue in task_issues:
        add_labels, remove_labels = reconcile_issue_label_changes(issue)
        if not add_labels and not remove_labels:
            continue
        changed += 1
        print(f"#{issue.number}: +{','.join(add_labels) or '-'} -{','.join(remove_labels) or '-'}")
        if args.dry_run:
            continue
        update_issue_labels(root, repo, issue.number, add=add_labels, remove=remove_labels)

    print(f"Issues reconciled: {changed} issue(s) {'(dry-run)' if args.dry_run else ''}".strip())
    return 0


def cmd_issue_repair_stale_locks(args: argparse.Namespace) -> int:
    root = repo_root()
    repo = args.repo or origin_repo_slug(root)
    issues = fetch_repo_issues(root, repo, state="all")
    repairs = [
        issue
        for issue in queue_task_issues(issues)
        if issue.state == "open"
        and lifecycle_status(issue) == "in-progress"
        and find_linked_worktree_for_issue(root, issue.number) is None
        and not merge_request_for_source_branch(
            root,
            repo,
            f"wt/{infer_scope(issue)}/{issue.number}-{slugify_text(issue.title)}",
            "open",
        )
    ]
    for issue in repairs:
        print(f"#{issue.number}: status:in-progress -> status:not-started")
        if args.apply:
            update_issue_labels(
                root,
                repo,
                issue.number,
                add=["status:not-started", "ready"] if args.ready else ["status:not-started"],
                remove=["status:in-progress"],
            )
            comment_issue(
                root,
                repo,
                issue.number,
                "Repaired stale issue lock: no linked local worktree or open MR was detected.",
            )
    suffix = "(applied)" if args.apply else "(dry-run)"
    print(f"Stale locks repaired: {len(repairs)} issue(s) {suffix}")
    return 0


def cmd_preflight(args: argparse.Namespace) -> int:
    root = repo_root()
    repo = None
    try:
        repo = args.repo or origin_repo_slug(root)
    except CliError:
        if parse_bool_env("ENFORCE_TRACKER_ISSUE_LOOKUP", True):
            raise
    run_preflight(
        path=Path(args.path).resolve() if args.path else current_path(), root=root, repo=repo
    )
    return 0


def cmd_pre_validate(args: argparse.Namespace) -> int:
    target = Path(args.path).resolve() if args.path else current_path()
    if args.dry_run:
        print(f"Would run in {target}: make validate-pre-push")
        return 0
    run_pre_validate(target)
    return 0


def cmd_worktree_next(args: argparse.Namespace) -> int:
    root = repo_root()
    repo = args.repo or origin_repo_slug(root)
    issues = fetch_repo_issues(root, repo, state="all")
    selection = build_queue(
        issues,
        stream_label=args.stream_label,
        from_issue=getattr(args, "from_issue", None),
        mode=args.mode,
    )
    if args.choose:
        issue = choose_issue_interactive(selection)
        queue_item = next(
            (item for item in selection.items if item.issue.number == issue.number), None
        )
        if queue_item and (not queue_item.runnable) and not args.allow_blocked:
            blocked_msg = "; ".join(queue_item.blocked_reasons)
            raise CliError(f"Selected issue #{issue.number} is blocked: {blocked_msg}")
        existing_wt = find_linked_worktree_for_issue(root, issue.number)
        if existing_wt is not None:
            print(f"Issue #{issue.number} already has linked worktree: {existing_wt.path}")
            prepare_gitnexus_for_worktree(existing_wt.path)
            record_issue_handoff_event(
                root=root,
                repo=repo,
                issue=issue,
                branch=existing_wt.branch,
                worktree_path=existing_wt.path,
                event_type="worktree-reused",
                state="worktree-ready",
                details={"source": "worktree-next", "choose": bool(args.choose)},
                idempotency_key=f"reuse:{issue.number}:{existing_wt.branch}:{existing_wt.path}",
            )
            if args.open_shell and not args.dry_run:
                if not args.no_preflight:
                    run_preflight(path=existing_wt.path, root=root, repo=repo)
                record_issue_handoff_event(
                    root=root,
                    repo=repo,
                    issue=issue,
                    branch=existing_wt.branch,
                    worktree_path=existing_wt.path,
                    event_type="shell-opened",
                    state="shell-active",
                    details={"source": "worktree-next"},
                    idempotency_key=f"shell:{issue.number}:{existing_wt.path}",
                )
                open_shell(existing_wt.path)
                return 0
            if wants_agent_launch(args) and not args.dry_run:
                agent, agent_mode, handoff, _ = resolve_cli_launch_request(args)
                mux = resolve_mux_flag(args)
                review_agent = getattr(args, "review_agent", None)
                review_agent_mode = getattr(args, "review_agent_mode", None)
                record_issue_handoff_event(
                    root=root,
                    repo=repo,
                    issue=issue,
                    branch=existing_wt.branch,
                    worktree_path=existing_wt.path,
                    event_type="agent-launch-requested",
                    state="agent-launching",
                    details={
                        "source": "worktree-next",
                        "agent": agent,
                        "agent_mode": agent_mode,
                        "review_agent": review_agent,
                        "review_agent_mode": review_agent_mode,
                        "handoff": handoff,
                        "mux": mux,
                    },
                    idempotency_key=(
                        f"agent:{issue.number}:{existing_wt.path}:"
                        f"{agent}:{agent_mode}:{handoff}:{mux}"
                    ),
                )
                handoff_to_agent_or_shell(
                    path=existing_wt.path,
                    root=root,
                    repo=repo,
                    agent=agent,
                    agent_mode=agent_mode,
                    review_agent=review_agent,
                    review_agent_mode=review_agent_mode,
                    handoff=handoff,
                    print_only_override=args.print_only,
                    mux=mux,
                )
            return 0
    else:
        queue_item, skipped = choose_next_runnable_without_existing_worktree(root, selection)
        for issue_number, wt_path in skipped:
            print(f"Skipping issue #{issue_number}: existing linked worktree at {wt_path}")
        issue = queue_item.issue

    if (not args.allow_blocked) and queue_item and not queue_item.runnable:
        raise CliError(f"Issue #{issue.number} is blocked: {'; '.join(queue_item.blocked_reasons)}")

    base_dir = (
        Path(args.base_dir).expanduser().resolve() if args.base_dir else default_worktrees_dir(root)
    )
    auto_claim = not args.no_claim

    wt_path = create_worktree_for_issue(
        root=root,
        repo=repo,
        issue=issue,
        base_dir=base_dir,
        base_ref=args.base_ref,
        scope=args.scope,
        slug=args.slug,
        folder_name=args.name,
        auto_claim=auto_claim,
        preflight=(not args.no_preflight),
        dry_run=args.dry_run,
        pre_provision=bool(getattr(args, "pre_provision", False)),
    )
    if args.open_shell and not args.dry_run:
        record_issue_handoff_event(
            root=root,
            repo=repo,
            issue=issue,
            branch=(
                f"wt/{args.scope or infer_scope(issue)}/"
                f"{issue.number}-{args.slug or slugify_text(issue.title)}"
            ),
            worktree_path=wt_path,
            event_type="shell-opened",
            state="shell-active",
            details={"source": "worktree-next"},
            idempotency_key=f"shell:{issue.number}:{wt_path}",
        )
        open_shell(wt_path)
        return 0
    if wants_agent_launch(args) and not args.dry_run:
        agent, agent_mode, handoff, _ = resolve_cli_launch_request(args)
        mux = resolve_mux_flag(args)
        review_agent = getattr(args, "review_agent", None)
        review_agent_mode = getattr(args, "review_agent_mode", None)
        record_issue_handoff_event(
            root=root,
            repo=repo,
            issue=issue,
            branch=(
                f"wt/{args.scope or infer_scope(issue)}/"
                f"{issue.number}-{args.slug or slugify_text(issue.title)}"
            ),
            worktree_path=wt_path,
            event_type="agent-launch-requested",
            state="agent-launching",
            details={
                "source": "worktree-next",
                "agent": agent,
                "agent_mode": agent_mode,
                "review_agent": review_agent,
                "review_agent_mode": review_agent_mode,
                "handoff": handoff,
                "mux": mux,
            },
            idempotency_key=(
                f"agent:{issue.number}:{wt_path}:{agent}:{agent_mode}:{handoff}:{mux}"
            ),
        )
        handoff_to_agent_or_shell(
            path=wt_path,
            root=root,
            repo=repo,
            agent=agent,
            agent_mode=agent_mode,
            review_agent=review_agent,
            review_agent_mode=review_agent_mode,
            handoff=handoff,
            print_only_override=args.print_only,
            mux=mux,
        )
    return 0


def cmd_worktree_create(args: argparse.Namespace) -> int:
    root = repo_root()
    repo = args.repo or origin_repo_slug(root)
    issues = fetch_repo_issues(root, repo, state="all")
    issue = issue_by_number(issues, args.issue)
    existing_wt = find_linked_worktree_for_issue(root, issue.number)
    if existing_wt is not None:
        print(f"Issue #{issue.number} already has linked worktree: {existing_wt.path}")
        prepare_gitnexus_for_worktree(existing_wt.path)
        record_issue_handoff_event(
            root=root,
            repo=repo,
            issue=issue,
            branch=existing_wt.branch,
            worktree_path=existing_wt.path,
            event_type="worktree-reused",
            state="worktree-ready",
            details={"source": "worktree-create"},
            idempotency_key=f"reuse:{issue.number}:{existing_wt.branch}:{existing_wt.path}",
        )
        if args.open_shell and not args.dry_run:
            if not args.no_preflight:
                run_preflight(path=existing_wt.path, root=root, repo=repo)
            record_issue_handoff_event(
                root=root,
                repo=repo,
                issue=issue,
                branch=existing_wt.branch,
                worktree_path=existing_wt.path,
                event_type="shell-opened",
                state="shell-active",
                details={"source": "worktree-create"},
                idempotency_key=f"shell:{issue.number}:{existing_wt.path}",
            )
            open_shell(existing_wt.path)
            return 0
        if wants_agent_launch(args) and not args.dry_run:
            agent, agent_mode, handoff, _ = resolve_cli_launch_request(args)
            mux = resolve_mux_flag(args)
            review_agent = getattr(args, "review_agent", None)
            review_agent_mode = getattr(args, "review_agent_mode", None)
            record_issue_handoff_event(
                root=root,
                repo=repo,
                issue=issue,
                branch=existing_wt.branch,
                worktree_path=existing_wt.path,
                event_type="agent-launch-requested",
                state="agent-launching",
                details={
                    "source": "worktree-create",
                    "agent": agent,
                    "agent_mode": agent_mode,
                    "review_agent": review_agent,
                    "review_agent_mode": review_agent_mode,
                    "handoff": handoff,
                    "mux": mux,
                },
                idempotency_key=(
                    f"agent:{issue.number}:{existing_wt.path}:{agent}:{agent_mode}:{handoff}:{mux}"
                ),
            )
            handoff_to_agent_or_shell(
                path=existing_wt.path,
                root=root,
                repo=repo,
                agent=agent,
                agent_mode=agent_mode,
                review_agent=review_agent,
                review_agent_mode=review_agent_mode,
                handoff=handoff,
                print_only_override=args.print_only,
                mux=mux,
            )
        return 0
    assert_issue_startable(issue, allow_blocked=args.allow_blocked)
    selection = build_queue(
        issues,
        stream_label=args.stream_label,
        from_issue=getattr(args, "from_issue", None),
        mode=args.mode,
    )
    item = next((x for x in selection.items if x.issue.number == issue.number), None)
    if item and (not item.runnable) and not args.allow_blocked:
        raise CliError(f"Issue #{issue.number} is blocked: {'; '.join(item.blocked_reasons)}")
    base_dir = (
        Path(args.base_dir).expanduser().resolve() if args.base_dir else default_worktrees_dir(root)
    )
    auto_claim = not args.no_claim

    wt_path = create_worktree_for_issue(
        root=root,
        repo=repo,
        issue=issue,
        base_dir=base_dir,
        base_ref=args.base_ref,
        scope=args.scope,
        slug=args.slug,
        folder_name=args.name,
        auto_claim=auto_claim,
        preflight=(not args.no_preflight),
        dry_run=args.dry_run,
        pre_provision=bool(getattr(args, "pre_provision", False)),
    )
    if args.open_shell and not args.dry_run:
        branch = (
            f"wt/{args.scope or infer_scope(issue)}/"
            f"{issue.number}-{args.slug or slugify_text(issue.title)}"
        )
        record_issue_handoff_event(
            root=root,
            repo=repo,
            issue=issue,
            branch=branch,
            worktree_path=wt_path,
            event_type="shell-opened",
            state="shell-active",
            details={"source": "worktree-create"},
            idempotency_key=f"shell:{issue.number}:{wt_path}",
        )
        open_shell(wt_path)
        return 0
    if wants_agent_launch(args) and not args.dry_run:
        branch = (
            f"wt/{args.scope or infer_scope(issue)}/"
            f"{issue.number}-{args.slug or slugify_text(issue.title)}"
        )
        agent, agent_mode, handoff, _ = resolve_cli_launch_request(args)
        mux = resolve_mux_flag(args)
        review_agent = getattr(args, "review_agent", None)
        review_agent_mode = getattr(args, "review_agent_mode", None)
        record_issue_handoff_event(
            root=root,
            repo=repo,
            issue=issue,
            branch=branch,
            worktree_path=wt_path,
            event_type="agent-launch-requested",
            state="agent-launching",
            details={
                "source": "worktree-create",
                "agent": agent,
                "agent_mode": agent_mode,
                "review_agent": review_agent,
                "review_agent_mode": review_agent_mode,
                "handoff": handoff,
                "mux": mux,
            },
            idempotency_key=(
                f"agent:{issue.number}:{wt_path}:{agent}:{agent_mode}:{handoff}:{mux}"
            ),
        )
        handoff_to_agent_or_shell(
            path=wt_path,
            root=root,
            repo=repo,
            agent=agent,
            agent_mode=agent_mode,
            review_agent=review_agent,
            review_agent_mode=review_agent_mode,
            handoff=handoff,
            print_only_override=args.print_only,
            mux=mux,
        )
    return 0


def cmd_worktree_resume(args: argparse.Namespace) -> int:
    root = repo_root()
    worktrees = list_resume_candidates(root)
    if not worktrees:
        print("No linked worktrees found.")
        return 0
    if args.path:
        target = next(
            (wt for wt in worktrees if str(wt.path) == str(Path(args.path).resolve())), None
        )
        if target is None:
            raise CliError(f"Worktree not found: {args.path}")
    else:
        target = select_worktree_interactive(worktrees)
    if not args.no_preflight:
        try:
            repo = origin_repo_slug(root)
        except CliError:
            repo = None
        run_preflight(path=target.path, root=root, repo=repo)
    else:
        try:
            repo = origin_repo_slug(root)
        except CliError:
            repo = None
    prepare_gitnexus_for_worktree(target.path)
    issue_id = extract_issue_id_from_branch(target.branch)
    record_issue_handoff_event(
        root=root,
        repo=repo,
        issue_number=issue_id,
        issue_title=target.branch,
        branch=target.branch,
        worktree_path=target.path,
        event_type="worktree-resumed",
        state="worktree-ready",
        details={"source": "worktree-resume"},
        idempotency_key=f"resume:{issue_id}:{target.branch}:{target.path}",
    )
    if args.command:
        run_command_in_worktree(target.path, args.command)
    elif args.open_shell:
        record_issue_handoff_event(
            root=root,
            repo=repo,
            issue_number=issue_id,
            issue_title=target.branch,
            branch=target.branch,
            worktree_path=target.path,
            event_type="shell-opened",
            state="shell-active",
            details={"source": "worktree-resume"},
            idempotency_key=f"shell:{issue_id}:{target.path}",
        )
        open_shell(target.path)
    elif wants_agent_launch(args):
        agent, agent_mode, handoff, _ = resolve_cli_launch_request(args)
        mux = resolve_mux_flag(args)
        review_agent = getattr(args, "review_agent", None)
        review_agent_mode = getattr(args, "review_agent_mode", None)
        print_only = bool(getattr(args, "print_only", False))
        record_issue_handoff_event(
            root=root,
            repo=repo,
            issue_number=issue_id,
            issue_title=target.branch,
            branch=target.branch,
            worktree_path=target.path,
            event_type="agent-launch-requested",
            state="agent-launching",
            details={
                "source": "worktree-resume",
                "agent": agent,
                "agent_mode": agent_mode,
                "review_agent": review_agent,
                "review_agent_mode": review_agent_mode,
                "handoff": handoff,
                "mux": mux,
            },
            idempotency_key=(
                f"agent:{issue_id}:{target.path}:{agent}:{agent_mode}:{handoff}:{mux}"
            ),
        )
        handoff_to_agent_or_shell(
            path=target.path,
            root=root,
            repo=repo,
            agent=agent,
            agent_mode=agent_mode,
            review_agent=review_agent,
            review_agent_mode=review_agent_mode,
            handoff=handoff,
            print_only_override=print_only,
            mux=mux,
        )
    else:
        print(target.path)
        print(f"branch={target.branch}")
    return 0


def cmd_finish_summary(args: argparse.Namespace) -> int:
    root = repo_root()
    finish_summary(root, path=Path(args.path).resolve() if args.path else None)
    return 0


def cmd_finish_close(args: argparse.Namespace) -> int:
    root = repo_root()
    target_path = Path(args.path).resolve() if args.path else None
    close_issue_done(root, path=target_path, force=args.force)
    if getattr(args, "json", False):
        worktrees = list_worktrees(root)
        target = resolve_current_worktree(target_path or current_path(), worktrees)
        print(json.dumps(read_closeout_report(closeout_report_path(root, target)), sort_keys=True))
    return 0


def cmd_push_branch(args: argparse.Namespace) -> int:
    root = repo_root()
    push_branch_enforced(
        root,
        path=Path(args.path).resolve() if args.path else None,
        dry_run=args.dry_run,
    )
    return 0


def cmd_agent_handoff(args: argparse.Namespace) -> int:
    root = repo_root()
    try:
        repo = args.repo or origin_repo_slug(root)
    except CliError:
        repo = None
    target_path = Path(args.path).resolve() if args.path else current_path()
    branch = current_branch(target_path)
    issue_id = extract_issue_id_from_branch(branch)
    agent, agent_mode, handoff, _ = resolve_cli_launch_request(args, default_agent="codex")
    mux = resolve_mux_flag(args)
    review_agent = getattr(args, "review_agent", None)
    review_agent_mode = getattr(args, "review_agent_mode", None)
    record_issue_handoff_event(
        root=root,
        repo=repo,
        issue_number=issue_id,
        issue_title=branch,
        branch=branch,
        worktree_path=target_path,
        event_type="agent-launch-requested",
        state="agent-launching",
        details={
            "source": "agent-handoff",
            "agent": agent,
            "agent_mode": agent_mode,
            "review_agent": review_agent,
            "review_agent_mode": review_agent_mode,
            "handoff": handoff,
            "mux": mux,
        },
        idempotency_key=(f"agent:{issue_id}:{target_path}:{agent}:{agent_mode}:{handoff}:{mux}"),
    )
    handoff_to_agent_or_shell(
        path=target_path,
        root=root,
        repo=repo,
        agent=agent,
        agent_mode=agent_mode,
        review_agent=review_agent,
        review_agent_mode=review_agent_mode,
        handoff=handoff,
        print_only_override=args.print_only or handoff == "print-only",
        mux=mux,
    )
    return 0


def cmd_wt_batch(args: argparse.Namespace) -> int:
    """Create N worktrees and start detached or interactive agent runs."""
    import random

    count = args.count
    raw_agents = args.agents.split(",") if args.agents else ["gemini"]
    agents = [agent.strip() for agent in raw_agents if agent.strip()]
    mode = args.agent_mode or "yolo"
    if not args.interactive:
        unsupported = sorted(agent for agent in agents if not agent_supports_detached(agent))
        if unsupported:
            names = ", ".join(unsupported)
            raise CliError(
                f"Detached wt-batch does not support agent(s): {names}. "
                "Use INTERACTIVE=1 or choose a detached-capable agent pool."
            )

    root = repo_root()
    repo = args.repo or origin_repo_slug(root)
    issues = fetch_repo_issues(root, repo, state="all")
    selection = build_queue(
        issues,
        stream_label=args.stream_label,
        from_issue=getattr(args, "from_issue", None),
        mode=args.mode,
    )
    base_dir = (
        Path(args.base_dir).expanduser().resolve() if args.base_dir else default_worktrees_dir(root)
    )

    picked: list[tuple[QueueItem, Path | None]] = []
    for item in selection.items:
        if len(picked) >= count:
            break
        if not item.runnable:
            continue
        existing = find_linked_worktree_for_issue(root, item.issue.number)
        if existing is not None and worktree_agent_running(existing.path):
            print(f"Skipping #{item.issue.number}: agent already running in {existing.path}")
            continue
        picked.append((item, existing.path if existing is not None else None))

    if not picked:
        raise CliError("No runnable issues available for batch creation.")
    if len(picked) < count:
        print(f"WARNING: only {len(picked)} runnable issue(s) available (requested {count})")

    run_id = batch_run_id()
    run_dir = batch_run_dir(root, run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_entries: list[dict[str, object]] = []
    launch_results: list[BatchLaunchResult] = []
    interactive_launches: list[tuple[str, Path, str]] = []
    total = len(picked)

    print(f"Batch run: {total} issue(s)")
    print(f"Run id:   {run_id}")
    print(f"Manifest: {batch_manifest_path(root, run_id)}")
    for idx, (item, existing_path) in enumerate(picked, start=1):
        agent = random.choice(agents)
        issue = item.issue

        print(f"[{idx}/{total}] #{issue.number} -> starting ({agent})")
        if existing_path is not None:
            wt_path = existing_path
        else:
            with contextlib.redirect_stdout(io.StringIO()):
                wt_path = create_worktree_for_issue(
                    root=root,
                    repo=repo,
                    issue=issue,
                    base_dir=base_dir,
                    base_ref=None,
                    scope=None,
                    slug=None,
                    folder_name=None,
                    auto_claim=True,
                    preflight=False,
                    dry_run=args.dry_run,
                    pre_provision=False,
                )

        if args.dry_run:
            print(f"[{idx}/{total}] #{issue.number} -> dry-run {wt_path}")
            manifest_entries.append(
                {
                    "issue_number": issue.number,
                    "agent": agent,
                    "worktree_path": str(wt_path),
                    "state": "dry-run",
                    "generated_at": datetime.now(UTC).isoformat(),
                }
            )
            continue

        with contextlib.redirect_stdout(io.StringIO()):
            prepare_gitnexus_for_worktree(wt_path)
        prompt = build_agent_prompt_for_worktree(wt_path, root, repo)
        command = build_agent_command(agent, mode, prompt)
        branch = run(["git", "branch", "--show-current"], cwd=wt_path).stdout.strip()
        if args.interactive:
            session = "pending"
            window_name = f"wt{issue.number}"
            result = record_tmux_agent_launch(
                root=root,
                run_id=run_id,
                issue_number=issue.number,
                path=wt_path,
                branch=branch,
                agent=agent,
                command=command,
                session_name=session,
                window_name=window_name,
            )
            interactive_launches.append((window_name, wt_path, command))
        else:
            result = launch_agent_detached(
                root=root,
                run_id=run_id,
                issue_number=issue.number,
                path=wt_path,
                branch=branch,
                agent=agent,
                command=command,
            )
        launch_results.append(result)
        write_batch_entry(root, run_id, result)
        manifest_entries.append(
            {
                "issue_number": result.issue_number,
                "agent": result.agent,
                "worktree_path": str(result.worktree_path),
                "branch": result.branch,
                "state": result.state,
                "pid": result.pid,
                "backend": result.backend,
                "session_name": result.session_name,
                "window_name": result.window_name,
                "local_status_path": (
                    str(result.local_status_path) if result.local_status_path else None
                ),
                "stdout_log_path": str(result.stdout_log_path) if result.stdout_log_path else None,
                "stderr_log_path": str(result.stderr_log_path) if result.stderr_log_path else None,
                "detail": result.detail,
                "generated_at": datetime.now(UTC).isoformat(),
            }
        )
        pid_note = f" pid={result.pid}" if result.pid is not None else ""
        print(f"[{idx}/{total}] #{issue.number} -> {result.state}{pid_note} {wt_path}")

    manifest_payload = {
        "run_id": run_id,
        "repo": repo,
        "count_requested": count,
        "count_selected": total,
        "agent_pool": agents,
        "agent_mode": mode,
        "state": "dry-run" if args.dry_run else "started",
        "generated_at": datetime.now(UTC).isoformat(),
        "entries": manifest_entries,
    }
    write_json_file(batch_manifest_path(root, run_id), manifest_payload)

    if not args.dry_run:
        started = sum(1 for item in launch_results if item.state in {"running", "interactive"})
        failed = sum(1 for item in launch_results if item.state not in {"running", "interactive"})
        print()
        print("Run summary:")
        print(f"  started:  {started}")
        print(f"  failed:   {failed}")
        print(f"  manifest: {batch_manifest_path(root, run_id)}")
        if args.interactive:
            if not tmux_available():
                raise CliError("wt-batch --interactive requires tmux")
            session = worktree_session_pair("wt-batch")
            print(f"  interactive: tmux session {session.session_name}")
            for result in launch_results:
                result.session_name = session.session_name
                if result.local_status_path:
                    payload = read_json_file(result.local_status_path) or {}
                    payload["session_name"] = session.session_name
                    payload["updated_at"] = datetime.now(UTC).isoformat()
                    write_json_file(result.local_status_path, payload)
                write_batch_entry(root, run_id, result)
            launch_tmux_batch_session(
                session_name=session.session_name,
                launches=interactive_launches,
                attach=True,
                announce_windows=True,
            )

    return 0


def cmd_gitnexus_refresh(args: argparse.Namespace) -> int:
    target = Path(args.path).resolve() if args.path else current_path()
    prepare_gitnexus_for_worktree(target)
    return 0


def cmd_menu(args: argparse.Namespace) -> int:
    # Lightweight interactive wrapper. Keep policies in the underlying commands.
    while True:
        print()
        print("Issue Worktree Menu")
        print("  1) Show queue")
        print("  2) Create next runnable worktree")
        print("  3) Create worktree from queue (pick issue)")
        print("  4) Resume worktree (shell)")
        print("  5) Resume worktree (print path)")
        print("  6) Preflight current worktree")
        print("  7) Pre-validate current worktree (make validate-pre-push)")
        print("  8) Push current worktree branch (preflight + pre-validate enforced)")
        print("  9) Finish summary (current worktree)")
        print("  10) Close issue done (current worktree, requires merged MR)")
        print("  0) Exit")
        choice = input("Choice [1]: ").strip() or "1"
        try:
            if choice == "1":
                ns = argparse.Namespace(
                    repo=args.repo,
                    stream_label=args.stream_label,
                    from_issue=args.from_issue,
                    mode=args.mode,
                    limit=None,
                    runnable_only=False,
                    json=False,
                )
                cmd_issue_queue(ns)
            elif choice == "2":
                post_create = choose_post_create_action_interactive()
                ns = argparse.Namespace(
                    repo=args.repo,
                    stream_label=args.stream_label,
                    from_issue=args.from_issue,
                    mode=args.mode,
                    choose=False,
                    allow_blocked=False,
                    base_dir=args.base_dir,
                    base_ref=None,
                    scope=None,
                    slug=None,
                    name=None,
                    no_claim=False,
                    no_preflight=False,
                    dry_run=False,
                    open_shell=(post_create == "shell"),
                    shell_only=(post_create == "shell"),
                    agent=None,
                    agent_mode=None,
                    handoff=None,
                    print_only=False,
                )
                cmd_worktree_next(ns)
            elif choice == "3":
                post_create = choose_post_create_action_interactive()
                ns = argparse.Namespace(
                    repo=args.repo,
                    stream_label=args.stream_label,
                    from_issue=args.from_issue,
                    mode=args.mode,
                    choose=True,
                    allow_blocked=False,
                    base_dir=args.base_dir,
                    base_ref=None,
                    scope=None,
                    slug=None,
                    name=None,
                    no_claim=False,
                    no_preflight=False,
                    dry_run=False,
                    open_shell=(post_create == "shell"),
                    shell_only=(post_create == "shell"),
                    agent=None,
                    agent_mode=None,
                    handoff=None,
                    print_only=False,
                )
                cmd_worktree_next(ns)
            elif choice == "4":
                ns = argparse.Namespace(
                    path=None,
                    no_preflight=False,
                    open_shell=True,
                    shell_only=True,
                    command=None,
                    agent=None,
                    agent_mode=None,
                    handoff=None,
                    print_only=False,
                )
                cmd_worktree_resume(ns)
            elif choice == "5":
                ns = argparse.Namespace(
                    path=None,
                    no_preflight=False,
                    open_shell=False,
                    command=None,
                    agent=None,
                    agent_mode=None,
                    handoff=None,
                    print_only=False,
                )
                cmd_worktree_resume(ns)
            elif choice == "6":
                ns = argparse.Namespace(repo=args.repo, path=None)
                cmd_preflight(ns)
            elif choice == "7":
                ns = argparse.Namespace(path=None, dry_run=False)
                cmd_pre_validate(ns)
            elif choice == "8":
                ns = argparse.Namespace(path=None, dry_run=False)
                cmd_push_branch(ns)
            elif choice == "9":
                ns = argparse.Namespace(path=None)
                cmd_finish_summary(ns)
            elif choice == "10":
                ns = argparse.Namespace(path=None, force=False)
                cmd_finish_close(ns)
            elif choice in {"0", "exit", "quit"}:
                return 0
            else:
                print("Invalid choice.")
        except CliError as exc:
            print(f"ERROR: {exc}")
        except subprocess.CalledProcessError as exc:
            print(f"ERROR: command failed ({exc.returncode}): {' '.join(exc.cmd)}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    common_repo = argparse.ArgumentParser(add_help=False)
    common_repo.add_argument(
        "--repo", help="GitLab project path (group/project). Defaults to gitlab remote."
    )

    queue_common = argparse.ArgumentParser(add_help=False)
    queue_common.add_argument(
        "--mode",
        choices=["auto", "ready", "open-task"],
        default="auto",
        help=(
            "Queue source: ready-labelled tasks, all open tasks, or auto fallback (default: auto)."
        ),
    )
    queue_common.add_argument(
        "--stream-label", help="Optional label filter (e.g. a, b, provider-matrix)."
    )
    queue_common.add_argument(
        "--from-issue",
        type=int,
        help="Lower bound issue number for queue selection (e.g. start from issue #310).",
    )

    q = sub.add_parser("issue-queue", parents=[common_repo, queue_common], help="Show issue queue")
    q.add_argument("--limit", type=int, help="Limit displayed items")
    q.add_argument("--runnable-only", action="store_true", help="Show only runnable items")
    q.add_argument(
        "--json", action="store_true", help="Also emit JSON payload after human-readable output"
    )
    q.set_defaults(func=cmd_issue_queue)

    create = sub.add_parser(
        "issue-create",
        parents=[common_repo],
        help="Create a canonical GitLab task issue",
    )
    create.add_argument("--title", required=True, help="Issue title, must start with TASK-###:")
    create.add_argument("--seq", required=True, type=int, help="Queue sequence number")
    create.add_argument(
        "--depends",
        default="none",
        help="Dependency list, e.g. none, #123, TASK-123",
    )
    create.add_argument("--problem", default="", help="Optional initial problem statement")
    create.add_argument("--ready", action="store_true", help="Add ready label after creation")
    create.set_defaults(func=cmd_issue_create)

    ev = sub.add_parser(
        "issue-evidence",
        parents=[common_repo],
        help="Show local linked-worktree and .build evidence for an issue",
    )
    ev.add_argument("--issue", type=int, help="Issue number (default: infer from current worktree)")
    ev.add_argument("--path", help="Path to infer issue number from (default: current path)")
    ev.add_argument("--json", action="store_true", help="Emit JSON output")
    ev.set_defaults(func=cmd_issue_evidence)

    status = sub.add_parser(
        "issue-status",
        parents=[common_repo],
        help="Show joined issue/worktree/agent launch status",
    )
    status.add_argument("--issue", type=int, help="Show one issue number")
    status.add_argument("--all", action="store_true", help="Include all known task issues")
    status.add_argument("--json", action="store_true", help="Emit JSON output")
    status.set_defaults(func=cmd_issue_status)

    vr = sub.add_parser(
        "write-validation-receipt",
        parents=[common_repo],
        help="Write a local validation receipt for the current issue worktree",
    )
    vr.add_argument("--issue", type=int, help="Issue number (default: infer from current worktree)")
    vr.add_argument("--path", help="Path to infer issue number from (default: current path)")
    vr.add_argument(
        "--check",
        default="validate-pre-push",
        help="Validation check name to record (default: validate-pre-push)",
    )
    vr.set_defaults(func=cmd_write_validation_receipt)

    aud = sub.add_parser(
        "issues-audit",
        parents=[common_repo],
        help="Audit issue lifecycle/queue invariants (objective gate)",
    )
    aud.add_argument("--json", action="store_true", help="Emit JSON output")
    aud.set_defaults(func=cmd_issues_audit)

    rec = sub.add_parser(
        "issues-reconcile",
        parents=[common_repo],
        help="Reconcile task issue labels to lifecycle rules",
    )
    rec.add_argument("--dry-run", action="store_true", help="Show changes without editing issues")
    rec.set_defaults(func=cmd_issues_reconcile)

    stale = sub.add_parser(
        "issue-repair-stale-locks",
        parents=[common_repo],
        help="Repair in-progress task issues with no linked worktree or open MR",
    )
    stale.add_argument("--apply", action="store_true", help="Apply repairs (default is dry-run)")
    stale.add_argument("--ready", action="store_true", help="Add ready when resetting stale locks")
    stale.set_defaults(func=cmd_issue_repair_stale_locks)

    pf = sub.add_parser("preflight", parents=[common_repo], help="Run session preflight checks")
    pf.add_argument("--path", help="Path to check (default: current path)")
    pf.set_defaults(func=cmd_preflight)

    pv = sub.add_parser(
        "pre-validate",
        help="Run pre-push validation (make validate-pre-push; skips cdk synth)",
    )
    pv.add_argument("--path", help="Worktree path (default: current path)")
    pv.add_argument("--dry-run", action="store_true", help="Print command without running it")
    pv.set_defaults(func=cmd_pre_validate)

    gn = sub.add_parser(
        "gitnexus-refresh",
        help="Refresh local GitNexus index for a worktree if stale or missing",
    )
    gn.add_argument("--path", help="Worktree path (default: current path)")
    gn.set_defaults(func=cmd_gitnexus_refresh)

    wt_common = argparse.ArgumentParser(add_help=False)
    wt_common.add_argument("--base-dir", help="Linked worktree base dir (default: ../worktrees)")
    wt_common.add_argument(
        "--base-ref", help="Base ref (default: origin/main if available else main)"
    )
    wt_common.add_argument("--scope", help="Branch scope namespace (e.g. docs, infra, task)")
    wt_common.add_argument("--slug", help="Branch slug (lowercase hyphenated)")
    wt_common.add_argument("--name", help="Worktree folder name")
    wt_common.add_argument(
        "--no-claim", action="store_true", help="Do not auto-claim issue (ready -> in-progress)"
    )
    wt_common.add_argument("--no-preflight", action="store_true", help="Skip post-create preflight")
    wt_common.add_argument(
        "--dry-run", action="store_true", help="Print create plan without changes"
    )
    wt_common.add_argument(
        "--pre-provision",
        action="store_true",
        help="Start background dependency install and write .ready when complete",
    )
    wt_common.add_argument(
        "--open-shell", action="store_true", help="Open a shell in the created worktree"
    )
    wt_common.add_argument(
        "--allow-blocked", action="store_true", help="Allow creating worktree for blocked issue"
    )
    wt_common.add_argument(
        "--agent",
        choices=["gemini", "claude", "codex", "random"],
        help="Launch agent after worktree creation (explicit agent-launch path)",
    )
    wt_common.add_argument(
        "--agent-mode",
        choices=["normal", "yolo"],
        help="Agent mode for explicit agent-launch path",
    )
    wt_common.add_argument(
        "--review-agent",
        choices=["gemini", "claude", "codex"],
        help="Launch a parallel reviewer agent after worktree creation",
    )
    wt_common.add_argument(
        "--review-agent-mode",
        choices=["normal", "yolo"],
        help="Reviewer agent mode for explicit agent-launch path",
    )
    wt_common.add_argument(
        "--handoff",
        choices=["execute-now", "print-only"],
        help="Handoff behavior for explicit agent-launch path",
    )
    wt_common.add_argument(
        "--print-only",
        action="store_true",
        help="Force print-only handoff for explicit agent-launch path",
    )
    mux_group = wt_common.add_mutually_exclusive_group()
    mux_group.add_argument(
        "--tmux",
        action="store_true",
        default=None,
        help="Launch agent in a named tmux session",
    )
    mux_group.add_argument(
        "--zellij",
        action="store_true",
        default=None,
        help="Launch agent in a named zellij session",
    )
    mux_group.add_argument(
        "--no-mux",
        action="store_true",
        default=False,
        help="Disable multiplexer, use direct exec",
    )

    nxt = sub.add_parser(
        "worktree-next",
        parents=[common_repo, queue_common, wt_common],
        help="Create worktree for next runnable queued issue",
    )
    nxt.add_argument(
        "--choose", action="store_true", help="Interactively choose an issue from queue"
    )
    nxt.set_defaults(func=cmd_worktree_next)

    crt = sub.add_parser(
        "worktree-create",
        parents=[common_repo, queue_common, wt_common],
        help="Create worktree for a specific issue number",
    )
    crt.add_argument("--issue", type=int, required=True, help="Issue number")
    crt.set_defaults(func=cmd_worktree_create)

    res = sub.add_parser("worktree-resume", help="Resume a linked worktree")
    res.add_argument("--path", help="Worktree path (default: choose interactively)")
    res.add_argument("--no-preflight", action="store_true", help="Skip preflight before resume")
    res.add_argument("--open-shell", action="store_true", help="Open shell in selected worktree")
    res.add_argument("--command", help="Run command in selected worktree")
    res.add_argument("--agent", choices=["gemini", "claude", "codex", "random"])
    res.add_argument("--agent-mode", choices=["normal", "yolo"])
    res.add_argument("--review-agent", choices=["gemini", "claude", "codex"])
    res.add_argument("--review-agent-mode", choices=["normal", "yolo"])
    res.add_argument("--handoff", choices=["execute-now", "print-only"])
    res.add_argument(
        "--print-only",
        action="store_true",
        help="Force print-only handoff for explicit agent-launch path",
    )
    res_mux = res.add_mutually_exclusive_group()
    res_mux.add_argument("--tmux", action="store_true", default=None)
    res_mux.add_argument("--zellij", action="store_true", default=None)
    res_mux.add_argument("--no-mux", action="store_true", default=False)
    res.set_defaults(func=cmd_worktree_resume)

    fs = sub.add_parser("finish-summary", help="Show finish/handoff summary for a worktree")
    fs.add_argument("--path", help="Worktree path (default: current path)")
    fs.set_defaults(func=cmd_finish_summary)

    fc = sub.add_parser("finish-close", help="Close issue for worktree after merge")
    fc.add_argument("--path", help="Worktree path (default: current path)")
    fc.add_argument(
        "--force", action="store_true", help="Close issue even without a detected merged MR"
    )
    fc.add_argument(
        "--json",
        action="store_true",
        help="Print the generated closeout report JSON after closing",
    )
    fc.set_defaults(func=cmd_finish_close)

    pb = sub.add_parser(
        "push-branch",
        help="Push current worktree branch (preflight + validate-pre-push enforced)",
    )
    pb.add_argument("--path", help="Worktree path (default: current path)")
    pb.add_argument("--dry-run", action="store_true", help="Run checks but skip git push")
    pb.set_defaults(func=cmd_push_branch)

    ah = sub.add_parser(
        "agent-handoff",
        parents=[common_repo],
        help="Agent selection/yolo handoff for current or specified worktree path",
    )
    ah.add_argument("--path", help="Worktree path (default: current path)")
    ah.add_argument("--agent", choices=["gemini", "claude", "codex"])
    ah.add_argument("--agent-mode", choices=["normal", "yolo"])
    ah.add_argument("--review-agent", choices=["gemini", "claude", "codex"])
    ah.add_argument("--review-agent-mode", choices=["normal", "yolo"])
    ah.add_argument("--handoff", choices=["execute-now", "print-only"], default="print-only")
    ah.add_argument(
        "--print-only",
        action="store_true",
        help="Force print-only handoff (recommended for testing)",
    )
    ah_mux = ah.add_mutually_exclusive_group()
    ah_mux.add_argument("--tmux", action="store_true", default=None)
    ah_mux.add_argument("--zellij", action="store_true", default=None)
    ah_mux.add_argument("--no-mux", action="store_true", default=False)
    ah.set_defaults(func=cmd_agent_handoff)

    batch = sub.add_parser(
        "wt-batch",
        parents=[common_repo, queue_common],
        help="Create N worktrees with randomly assigned detached agent runs and a run manifest",
    )
    batch.add_argument(
        "--count", "-n", type=int, default=3, help="Number of worktrees to create (default: 3)"
    )
    batch.add_argument(
        "--agents",
        default="gemini",
        help="Comma-separated agent pool to randomly pick from (default: gemini)",
    )
    batch.add_argument("--agent-mode", choices=["normal", "yolo"], default="yolo")
    batch.add_argument("--base-dir", help="Worktree base dir (default: ../worktrees)")
    batch.add_argument(
        "--interactive",
        action="store_true",
        help="Open a tmux viewer with log and shell panes for each started worktree",
    )
    batch.add_argument("--dry-run", action="store_true", help="Print plan without creating")
    batch.set_defaults(func=cmd_wt_batch)

    menu = sub.add_parser(
        "menu", parents=[common_repo, queue_common], help="Interactive issue worktree menu"
    )
    menu.add_argument("--base-dir", help="Linked worktree base dir (default: ../worktrees)")
    menu.set_defaults(func=cmd_menu)

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CliError as exc:
        eprint(f"ERROR: {exc}")
        raise SystemExit(1)
