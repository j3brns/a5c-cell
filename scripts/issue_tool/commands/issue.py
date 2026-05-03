from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Literal

from scripts.issue_tool import evidence, git_utils, issue_queue, logic, tracker_client, worktree
from scripts.issue_tool.audit import audit_issues
from scripts.issue_tool.commands import common


def cmd_issue_queue(
    repo: str | None = None,
    stream_label: str | None = None,
    from_issue: int | None = None,
    from_seq: int | None = None,
    mode: Literal["auto", "ready", "open-task"] = "auto",
    limit: int | None = None,
    runnable_only: bool = False,
    json_output: bool = False,
) -> int:
    root = git_utils.repo_root()
    repo = repo or git_utils.origin_repo_slug(root)
    issues = issue_queue.fetch_repo_issues(root, repo, state="all")
    selection = issue_queue.build_queue(
        issues,
        stream_label=stream_label,
        from_issue=from_issue,
        from_seq=from_seq,
        mode=mode,
    )
    common.print_queue(selection, limit=limit, show_blocked=not runnable_only)
    if json_output:
        payload = []
        items = selection.runnable if runnable_only else selection.items
        if limit is not None:
            items = items[:limit]
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


def cmd_issue_create(
    title: str,
    seq: int,
    repo: str | None = None,
    depends: str = "none",
    problem: str = "",
    ready: bool = False,
) -> int:
    from scripts.issue_tool.constants import TITLE_TASK_RE

    root = git_utils.repo_root()
    repo = repo or git_utils.origin_repo_slug(root)
    title = title.strip()
    if not title:
        raise common.CliError("TITLE is required")
    if not TITLE_TASK_RE.match(title):
        raise common.CliError("Task issue title must start with TASK-###: ")
    depends = depends.strip() if depends else "none"
    labels = ["type:task", "status:not-started"]
    if ready:
        labels.append("ready")
    body = issue_queue.build_task_issue_body(seq=seq, depends=depends, problem=problem or "")
    output = tracker_client.create_issue(root, repo, title=title, description=body, labels=labels)
    print(output.strip())
    return 0


def cmd_issue_evidence(
    repo: str | None = None,
    issue: int | None = None,
    path: str | None = None,
    json_output: bool = False,
) -> int:
    root = git_utils.repo_root()
    issue_id = issue
    if issue_id is None:
        issue_id = common.worktree_issue_id(
            Path(path).resolve() if path else git_utils.current_path()
        )
    if issue_id is None:
        raise common.CliError(
            "Could not determine issue id; pass --issue or run inside an issue worktree"
        )
    summary = evidence.issue_evidence_summary(root, issue_id)
    if json_output:
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


def cmd_issue_status(
    repo: str | None = None,
    issue: int | None = None,
    include_all: bool = False,
    json_output: bool = False,
) -> int:
    root = git_utils.repo_root()
    try:
        repo = repo or git_utils.origin_repo_slug(root)
        issues = issue_queue.fetch_repo_issues(root, repo, state="all")
    except common.CliError:
        repo = None
        issues = []
    rows = common.issue_status_rows(
        root,
        repo,
        issues,
        issue_filter=issue,
        include_all=include_all,
    )
    if json_output:
        print(json.dumps(rows, indent=2, sort_keys=True))
        return 0
    common.print_issue_status_rows(rows)
    return 0


def cmd_write_validation_receipt(
    repo: str | None = None,
    issue: int | None = None,
    path: str | None = None,
    check: str = "validate-pre-push",
) -> int:
    root = git_utils.repo_root()
    target_path = Path(path).resolve() if path else git_utils.current_path()
    issue_id = issue if issue is not None else common.worktree_issue_id(target_path)
    if issue_id is None:
        print("Validation receipt: skipped (not in issue worktree)")
        return 0
    branch = (
        git_utils.run(["git", "branch", "--show-current"], cwd=target_path).stdout.strip() or None
    )
    receipt_path = evidence.write_validation_receipt(
        root,
        issue_id=issue_id,
        worktree_path=target_path,
        branch=branch,
        check_name=check,
    )
    print(f"Validation receipt: {receipt_path}")
    return 0


def cmd_issues_audit(
    repo: str | None = None,
    json_output: bool = False,
) -> int:
    root = git_utils.repo_root()
    repo = repo or git_utils.origin_repo_slug(root)
    issues = issue_queue.fetch_repo_issues(root, repo, state="all")
    findings = audit_issues(issues)
    findings.extend(common.evidence_drift_findings(root, issues))
    findings.extend(common.stale_evidence_findings(root, issues))
    findings.extend(common.stale_lock_findings(root, repo, issues))
    errors = [f for f in findings if f.severity == "error"]
    warnings = [f for f in findings if f.severity == "warning"]

    if json_output:
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


def cmd_issues_reconcile(
    repo: str | None = None,
    dry_run: bool = False,
) -> int:
    root = git_utils.repo_root()
    repo = repo or git_utils.origin_repo_slug(root)
    issues = issue_queue.fetch_repo_issues(root, repo, state="all")
    task_issues = issue_queue.queue_task_issues(issues)

    changed = 0
    for issue in task_issues:
        add_labels, remove_labels = logic.reconcile_issue_label_changes(issue)
        if not add_labels and not remove_labels:
            continue
        changed += 1
        print(f"#{issue.number}: +{','.join(add_labels) or '-'} -{','.join(remove_labels) or '-'}")
        if dry_run:
            continue
        tracker_client.update_issue_labels(
            root, repo, issue.number, add=add_labels, remove=remove_labels
        )

    print(f"Issues reconciled: {changed} issue(s) {'(dry-run)' if dry_run else ''}".strip())
    return 0


def cmd_issue_repair_stale_locks(
    repo: str | None = None,
    apply: bool = False,
    ready: bool = False,
) -> int:
    root = git_utils.repo_root()
    repo = repo or git_utils.origin_repo_slug(root)
    issues = issue_queue.fetch_repo_issues(root, repo, state="all")
    repairs = [
        issue
        for issue in issue_queue.queue_task_issues(issues)
        if issue.state == "open"
        and logic.lifecycle_status(issue) == "in-progress"
        and worktree.find_linked_worktree_for_issue(root, issue.number) is None
        and not run_mr_check(root, repo, issue)
    ]
    for issue in repairs:
        print(f"#{issue.number}: status:in-progress -> status:not-started")
        if apply:
            tracker_client.update_issue_labels(
                root,
                repo,
                issue.number,
                add=["status:not-started", "ready"] if ready else ["status:not-started"],
                remove=["status:in-progress"],
            )
            tracker_client.comment_issue(
                root,
                repo,
                issue.number,
                "Repaired stale issue lock: no linked local worktree or open MR was detected.",
            )
    suffix = "(applied)" if apply else "(dry-run)"
    print(f"Stale locks repaired: {len(repairs)} issue(s) {suffix}")
    return 0


def run_mr_check(root: Path, repo: str, issue) -> bool:
    expected_branch = (
        f"wt/{worktree.infer_scope(issue)}/{issue.number}-{worktree.slugify_text(issue.title)}"
    )
    try:
        if common.merge_request_for_source_branch(root, repo, expected_branch, "open"):
            return True
    except common.CliError:
        pass
    return False
