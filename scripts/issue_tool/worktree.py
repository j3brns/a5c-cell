from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from platform_config import settings
from scripts.issue_tool import (
    evidence,
    git_utils,
    gitnexus,
    multiplexer,
    pre_provisioning,
    shared,
    tracker_client,
)
from scripts.issue_tool.closeout import read_closeout_report
from scripts.issue_tool.constants import (
    STATUS_LABELS,
    VALIDATION_RECEIPTS_DIR,
    WORKTREE_AGENT_RUN_DIR,
    WORKTREE_BRANCH_ISSUE_RE,
    WORKTREE_BRANCH_REGEX,
    WORKTREE_RUNS_DIR,
    WORKTREE_STATE_DIR,
)
from scripts.issue_tool.models import Issue, QueueItem, QueueSelection, WorktreeInfo


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
        text = git_utils.run(["git", "worktree", "list", "--porcelain"], cwd=root).stdout
    except subprocess.CalledProcessError as exc:
        raise shared.CliError("Failed to list worktrees") from exc
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
        git_utils.run(["git", "show-ref", "--verify", "--quiet", remote_ref], cwd=root, check=True)
        return f"origin/{required_main_branch}"
    except subprocess.CalledProcessError:
        return required_main_branch


def local_branch_exists(root: Path, branch: str) -> bool:
    try:
        git_utils.run(
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
    raise shared.CliError(f"Issue #{number} not found in fetched dataset")


def claim_issue(root: Path, repo: str, issue: Issue) -> bool:
    # Re-fetch labels to reduce stale-queue races.
    data = tracker_client.get_issue(root, repo, issue.number)
    if not isinstance(data, dict):
        raise shared.CliError(f"Unexpected response while checking issue #{issue.number}")
    labels = [x["name"] for x in data.get("labels", []) if isinstance(x, dict) and "name" in x]
    had_ready = "ready" in labels
    states = [label for label in labels if label in STATUS_LABELS]
    if len(set(states)) != 1:
        raise shared.CliError(
            f"Issue #{issue.number} has invalid status labels {sorted(set(states)) or 'none'}; "
            "run `make issues-reconcile`"
        )
    if states[0] != "status:not-started":
        raise shared.CliError(
            f"Issue #{issue.number} must be status:not-started to claim (found {states[0]})"
        )
    add_labels = ["status:in-progress"] if "status:in-progress" not in labels else []
    remove_labels = ["status:not-started"]
    if had_ready:
        remove_labels.append("ready")
    tracker_client.update_issue_labels(
        root, repo, issue.number, add=add_labels, remove=remove_labels
    )

    verified = tracker_client.get_issue(root, repo, issue.number)
    verified_labels = [
        x["name"] for x in verified.get("labels", []) if isinstance(x, dict) and "name" in x
    ]
    verified_statuses = [label for label in verified_labels if label in STATUS_LABELS]
    if set(verified_statuses) != {"status:in-progress"} or "ready" in verified_labels:
        raise shared.CliError(
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
    tracker_client.update_issue_labels(
        root, repo, issue.number, add=add_labels, remove=remove_labels
    )


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
        raise shared.CliError(f"Invalid scope '{scope_val}'")
    if not re.fullmatch(r"[a-z0-9._-]+", slug_val):
        raise shared.CliError(f"Invalid slug '{slug_val}'")
    branch = f"wt/{scope_val}/{issue.number}-{slug_val}"
    if not WORKTREE_BRANCH_REGEX.fullmatch(branch):
        raise shared.CliError(
            f"Branch name '{branch}' does not match policy {WORKTREE_BRANCH_REGEX.pattern}"
        )

    base_dir.mkdir(parents=True, exist_ok=True)
    name_val = folder_name or suggest_worktree_dir_name(issue.number, base_dir)
    wt_path = (base_dir / name_val).resolve()
    if wt_path.exists():
        raise shared.CliError(f"Worktree path already exists: {wt_path}")

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
            git_utils.run(["git", "worktree", "add", str(wt_path), branch], cwd=root)
        else:
            git_utils.run(
                ["git", "worktree", "add", str(wt_path), "-b", branch, start_ref], cwd=root
            )
        print(f"Created worktree at {wt_path}")
        ensure_uv_venv(wt_path)
        gitnexus.prepare_gitnexus_for_worktree(wt_path)
        if pre_provision:
            pre_provisioning.start_worktree_pre_provision(wt_path)
    except Exception:
        if claimed:
            try:
                unclaim_issue(root, repo, issue, add_ready=claim_had_ready)
                git_utils.eprint(f"Rolled back claim for issue #{issue.number}")
            except Exception as rollback_exc:  # pragma: no cover - best effort
                git_utils.eprint(
                    f"WARNING: failed to roll back claim for #{issue.number}: {rollback_exc}"
                )
        raise

    if preflight:
        try:
            run_preflight(path=wt_path, root=root, repo=repo)
        except shared.CliError as exc:
            git_utils.eprint(f"WARNING: post-create preflight failed: {exc}")
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


def current_branch(path: Path) -> str:
    out = git_utils.run(["git", "branch", "--show-current"], cwd=path).stdout.strip()
    if not out:
        raise shared.CliError("Detached HEAD is not allowed for session work")
    return out


def resolve_current_worktree(path: Path, worktrees: list[WorktreeInfo]) -> WorktreeInfo:
    path_resolved = path.resolve()
    matches = [
        wt for wt in worktrees if path_resolved == wt.path or path_resolved.is_relative_to(wt.path)
    ]
    if not matches:
        raise shared.CliError("Current path is not inside a registered git worktree")
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
        raise shared.CliError("No git worktrees found")
    primary = worktrees[0]
    current = resolve_current_worktree(path, worktrees)
    branch = current_branch(current.path)
    errors: list[str] = []
    warnings: list[str] = []

    enforce_lookup = shared.parse_bool_env("ENFORCE_TRACKER_ISSUE_LOOKUP", True)
    require_clean = shared.parse_bool_env("REQUIRE_CLEAN_WORKTREE", False)

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
                    repo = git_utils.origin_repo_slug(root)
                except shared.CliError as exc:
                    errors.append(str(exc))
                    repo = None
            if repo is not None:
                if not tracker_client.tracker_available():
                    warnings.append(f"issue CLI not found; skipped issue lookup for #{issue_id}")
                else:
                    try:
                        tracker_client.get_issue(root, repo, issue_id)
                    except shared.CliError as exc:
                        errors.append(f"issue lookup failed for #{issue_id}: {exc}")

    if require_clean:
        status = git_utils.run(["git", "status", "--porcelain"], cwd=current.path).stdout.strip()
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
        raise shared.CliError("Preflight failed")
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
        raise shared.CliError(
            "All runnable queue issues already have linked worktrees. "
            f"Use worktree-resume to continue them ({skipped_text})."
        )
    raise shared.CliError(f"No runnable issues found in queue (source={selection.source_mode}).")


def select_worktree_interactive(worktrees: list[WorktreeInfo]) -> WorktreeInfo:
    if not worktrees:
        raise shared.CliError("No linked worktrees available")
    print("Select a worktree:")
    for idx, wt in enumerate(worktrees, start=1):
        print(f"  {idx}) {wt.path} | {wt.branch}")
    print("  0) Back")
    while True:
        choice = input("Choice [1]: ").strip() or "1"
        if choice in {"0", "back"}:
            raise shared.CliError("Back")
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
    if tracker_client.shutil_which("uv") is None:
        git_utils.eprint("WARNING: uv not found; skipping virtual environment creation")
        return
    try:
        git_utils.run(["uv", "venv"], cwd=path)
        print("Created .venv with `uv venv`")
    except subprocess.CalledProcessError as exc:
        git_utils.eprint(f"WARNING: failed to create .venv with uv: {exc}")


def process_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def open_shell(path: Path) -> None:
    shell = settings.ops.shell or "bash"
    ensure_uv_venv(path)
    print(f"Opening shell in {path} (with .venv activation when available)")
    path_q = shlex.quote(str(path))
    shell_q = shlex.quote(shell)
    cmd = (
        f"cd {path_q} && "
        "if [ -f .venv/bin/activate ]; then source .venv/bin/activate; fi; "
        f"exec {shell_q} -l"
    )
    os.execvp("bash", ["bash", "-lc", cmd])


def worktree_runs_root(root: Path) -> Path:
    return root / WORKTREE_RUNS_DIR


def worktree_state_root(root: Path) -> Path:
    return root / WORKTREE_STATE_DIR


def issue_state_path(root: Path, issue_number: int) -> Path:
    return worktree_state_root(root) / f"issue-{issue_number}.json"


def worktree_agent_run_dir(path: Path) -> Path:
    return path / WORKTREE_AGENT_RUN_DIR


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
    existing = shared.read_json_file(path) or {}
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
    return shared.write_json_file(path, payload)


def issue_has_handback_comment(
    *,
    root: Path,
    repo: str,
    issue_id: int,
    evidence_hash: str,
) -> bool:
    try:
        data = tracker_client.get_issue(root, repo, issue_id, comments=True)
    except shared.CliError:
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
    tracker_client.comment_issue(
        root, repo, issue_id, evidence.build_issue_handback_comment(summary)
    )


def pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def worktree_agent_status(path: Path) -> dict[str, object] | None:
    return shared.read_json_file(worktree_agent_run_dir(path) / "status.json")


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
            and multiplexer.tmux_session_exists(session_name)
        )
    pid = status.get("pid")
    if not isinstance(pid, int):
        return False
    state = status.get("state")
    return state in {"starting", "running"} and pid_is_running(pid)
