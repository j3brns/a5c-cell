from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.issue_tool.constants import VALIDATION_RECEIPTS_DIR, WORKTREE_STATE_DIR
from scripts.issue_tool.shared import CliError, read_json_file, write_json_file


def validation_receipts_root(root: Path) -> Path:
    return root / VALIDATION_RECEIPTS_DIR


def validation_receipt_path(root: Path, issue_number: int, head_sha: str) -> Path:
    filename = f"issue-{issue_number}-{head_sha[:12]}.json"
    return validation_receipts_root(root) / filename


def find_latest_validation_receipt(root: Path, issue_id: int) -> Path | None:
    receipts_root = validation_receipts_root(root)
    if not receipts_root.exists():
        return None
    matches = sorted(
        receipts_root.glob(f"issue-{issue_id}-*.json"),
        key=lambda candidate: candidate.stat().st_mtime,
    )
    return matches[-1] if matches else None


def git_issue_branches(root: Path, issue_id: int) -> dict[str, list[str]]:
    from scripts.issue_tool.git_utils import run

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
    from scripts.issue_tool.git_utils import run

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
    from scripts.issue_tool.git_utils import run

    branches = git_issue_branches(root, issue_id)
    preferred_branch = next(iter(branches["local"]), None) or next(iter(branches["remote"]), None)
    branch_tip: dict[str, str] | None = None
    divergence: dict[str, int] | None = None
    if preferred_branch:
        tip = run(
            ["git", "log", "-1", "--format=%H%x09%cI%x09%s", preferred_branch],
            cwd=root,
            check=False,
        ).stdout.strip()
        if tip:
            sha, ts, subject = (tip.split("\t", 2) + ["", ""])[:3]
            branch_tip = {"sha": sha, "timestamp": ts, "subject": subject}
        counts = run(
            ["git", "rev-list", "--left-right", "--count", f"origin/main...{preferred_branch}"],
            cwd=root,
            check=False,
        ).stdout.strip()
        if counts:
            behind, ahead = [int(part) for part in counts.split()]
            divergence = {"behind": behind, "ahead": ahead}
    log_matches = git_log_issue_matches(root, issue_id)
    if preferred_branch is None and not log_matches:
        return None
    return {
        "branches": branches,
        "preferred_branch": preferred_branch,
        "branch_tip": branch_tip,
        "divergence_vs_origin_main": divergence,
        "log_matches": log_matches,
    }


def write_validation_receipt(
    root: Path,
    *,
    issue_id: int,
    worktree_path: Path,
    branch: str | None,
    check_name: str,
) -> Path:
    from scripts.issue_tool.git_utils import run

    head_sha = run(["git", "rev-parse", "HEAD"], cwd=worktree_path).stdout.strip()
    payload = {
        "issue_number": issue_id,
        "branch": branch,
        "worktree_path": str(worktree_path),
        "check": check_name,
        "result": "pass",
        "head_sha": head_sha,
        "generated_at": datetime.now(UTC).isoformat(),
    }
    return write_json_file(
        validation_receipt_path(root, issue_id, head_sha),
        payload,
    )


def issue_state_path(root: Path, issue_number: int) -> Path:
    return root / WORKTREE_STATE_DIR / f"issue-{issue_number}.json"


def audit_issue_handoff_evidence(
    *,
    root: Path,
    repo: str,
    issue_id: int,
    target: Any,
    report_path: Path,
) -> dict[str, object]:
    from scripts.issue_tool.closeout import read_closeout_report

    state_path = issue_state_path(root, issue_id)
    if not state_path.exists():
        raise CliError(f"Missing issue state evidence: {state_path}")
    if not report_path.exists():
        raise CliError(f"Missing closeout report: {report_path}")

    issue_state = read_json_file(state_path)
    if not isinstance(issue_state, dict):
        raise CliError(f"Invalid issue state evidence: {state_path}")
    closeout = read_closeout_report(report_path)
    if str(closeout.get("stage")) != "complete":
        raise CliError("Closeout report is not complete")

    events = issue_state.get("events")
    if not isinstance(events, list) or not events:
        raise CliError("Issue state evidence has no events")

    event_types = [
        str(event.get("event_type"))
        for event in events
        if isinstance(event, dict) and event.get("event_type")
    ]
    if not event_types:
        raise CliError("Issue state evidence has no typed events")

    required_any_start = {"worktree-created", "worktree-reused", "worktree-resumed"}
    if not any(event_type in required_any_start for event_type in event_types):
        raise CliError("Issue state evidence is missing a worktree start/resume event")
    if "closeout-started" not in event_types:
        raise CliError("Issue state evidence is missing closeout-started")
    if event_types[-1] != "closeout-complete":
        raise CliError(
            f"Final issue state event must be closeout-complete (found {event_types[-1]})"
        )

    summary_payload: dict[str, object] = {
        "issue_number": issue_id,
        "repo": repo,
        "branch": target.branch,
        "worktree_path": str(target.path),
        "final_state": issue_state.get("state"),
        "last_event_type": issue_state.get("last_event_type"),
        "event_types": event_types,
        "event_count": len(event_types),
        "cleanup_verified": bool(closeout.get("cleanup_verified")),
        "cleanup": closeout.get("cleanup"),
        "issue_closed": bool(closeout.get("issue_closed")),
        "closeout_stage": closeout.get("stage"),
        "report_path": str(report_path),
    }
    evidence_hash = hashlib.sha256(
        json.dumps(summary_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        **summary_payload,
        "evidence_hash": evidence_hash,
        "state_path": str(state_path),
    }


def build_issue_handback_comment(summary: dict[str, object]) -> str:
    event_types = summary.get("event_types")
    ordered = ", ".join(event_types) if isinstance(event_types, list) else ""
    return "\n".join(
        [
            "Execution evidence: PASS",
            f"Issue: #{summary['issue_number']}",
            f"Branch: {summary['branch']}",
            f"Worktree: {summary['worktree_path']}",
            f"Terminal state: {summary['final_state']}",
            f"Last event: {summary['last_event_type']}",
            f"Events ({summary['event_count']}): {ordered}",
            f"Cleanup verified: {summary['cleanup_verified']}",
            f"Closeout: {summary['closeout_stage']}",
            f"Evidence hash: {summary['evidence_hash']}",
        ]
    )


def issue_evidence_summary(
    root: Path,
    issue_id: int,
) -> dict[str, object]:
    from scripts.issue_tool.closeout import (
        latest_closeout_report_path,
    )
    from scripts.issue_tool.worktree import (
        find_linked_worktree_for_issue,
    )

    state_path = issue_state_path(root, issue_id)
    state = read_json_file(state_path)
    closeout_path = latest_closeout_report_path(root, issue_id)
    closeout = read_json_file(closeout_path) if closeout_path is not None else None
    validation_path = find_latest_validation_receipt(root, issue_id)
    validation_receipt = read_json_file(validation_path) if validation_path else None
    linked = find_linked_worktree_for_issue(root, issue_id)
    historical = None
    has_local_evidence = any(
        value is not None
        for value in (
            state,
            closeout,
            validation_receipt,
            linked,
        )
    )
    if not has_local_evidence:
        historical = historical_issue_evidence(root, issue_id)
    evidence_source = "local" if has_local_evidence else ("historical" if historical else "none")
    return {
        "issue_number": issue_id,
        "evidence_source": evidence_source,
        "linked_worktree": str(linked.path) if linked is not None else None,
        "linked_branch": linked.branch if linked is not None else None,
        "state_path": str(state_path) if state_path.exists() else None,
        "state": state,
        "closeout_path": str(closeout_path) if closeout_path is not None else None,
        "closeout": closeout,
        "validation_receipt_path": str(validation_path) if validation_path is not None else None,
        "validation_receipt": validation_receipt,
        "historical": historical,
    }
