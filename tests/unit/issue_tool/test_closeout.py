from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from ._support import worktree_issues


def test_finish_summary_prints_explicit_dod_conflict_and_cleanup_steps(monkeypatch, capsys):
    root = Path("/tmp/repo")
    primary = worktree_issues.WorktreeInfo(
        path=Path("/tmp/repo"),
        head="abc123",
        branch="main",
        is_primary=True,
    )
    target = worktree_issues.WorktreeInfo(
        path=Path("/tmp/worktrees/wt53"),
        head="def456",
        branch="wt/infra/53-explicit-dod",
        is_primary=False,
    )

    def _list_worktrees(_root):
        return [primary, target] if target.path.exists() else [primary]

    monkeypatch.setattr(worktree_issues, "list_worktrees", _list_worktrees)
    monkeypatch.setattr(worktree_issues, "resolve_current_worktree", lambda _path, _wts: target)
    monkeypatch.setattr(worktree_issues, "current_path", lambda: target.path)
    monkeypatch.setattr(worktree_issues, "tracker_repo_ready", lambda _root: (False, None))
    monkeypatch.setattr(worktree_issues, "finish_stage", lambda *_args, **_kwargs: "merged")

    def _run_summary(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, "## wt/infra/53-explicit-dod\n", "")

    monkeypatch.setattr(worktree_issues, "run", _run_summary)

    worktree_issues.finish_summary(root, path=target.path)
    out = capsys.readouterr().out

    assert "dod:      merged MR + closed issue + cleaned worktree/branch" in out
    assert "next:     make finish-worktree-close" in out
    assert "conflict: if merge/rebase conflicts appear:" in out
    assert "cleanup:  git worktree remove <this-worktree-path>" in out
    assert "git worktree prune" in out


def test_cleanup_finished_worktree_changes_out_of_target_before_remove(
    monkeypatch, capsys, tmp_path
):
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    target = worktree_issues.WorktreeInfo(
        path=tmp_path / "worktrees" / "wt153",
        head="abc123",
        branch="wt/task/153-sample",
        is_primary=False,
    )
    target.path.mkdir(parents=True, exist_ok=True)
    changed_to: list[Path] = []
    branch_deleted = False

    monkeypatch.setattr(worktree_issues.os, "getcwd", lambda: str(target.path))
    monkeypatch.setattr(worktree_issues.os, "chdir", lambda path: changed_to.append(Path(path)))
    monkeypatch.setattr(
        worktree_issues,
        "local_branch_exists",
        lambda _root, _branch: not branch_deleted,
    )

    def _run(cmd, *, cwd=None, **_kwargs):
        nonlocal branch_deleted
        if cmd[:3] == ["git", "worktree", "remove"]:
            target.path.rmdir()
        if cmd[:3] == ["git", "branch", "-d"]:
            branch_deleted = True
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(worktree_issues, "run", _run)

    result = worktree_issues.cleanup_finished_worktree(root, target)
    out = capsys.readouterr().out

    assert changed_to == [root]
    assert result == {
        "worktree_removed": True,
        "branch_deleted": True,
        "worktree_pruned": True,
    }
    assert f"Removed worktree {target.path}" in out


def test_close_issue_done_normalizes_labels_for_already_closed_issue(monkeypatch, capsys, tmp_path):
    from scripts.issue_tool import tracker_client

    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    target = worktree_issues.WorktreeInfo(
        path=tmp_path / "worktrees" / "wt153",
        head="abc123",
        branch="wt/task/153-sample",
        is_primary=False,
    )
    primary = worktree_issues.WorktreeInfo(
        path=root,
        head="def456",
        branch="main",
        is_primary=True,
    )
    edits: list[list[str]] = []
    comments: list[list[str]] = []
    cleanup_calls: list[tuple[list[str], Path | None]] = []
    branch_deleted = False

    target.path.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(worktree_issues, "list_worktrees", lambda _root: [primary, target])
    monkeypatch.setattr(worktree_issues, "resolve_current_worktree", lambda _path, _wts: target)
    monkeypatch.setattr(worktree_issues, "current_path", lambda: target.path)
    monkeypatch.setattr(worktree_issues, "tracker_repo_ready", lambda _root: (True, "owner/repo"))
    monkeypatch.setattr(
        worktree_issues,
        "merge_request_for_source_branch",
        lambda _root, _repo, _branch, _state: {"number": 157},
    )
    monkeypatch.setattr(
        worktree_issues,
        "issue_state_info",
        lambda _root, _repo, _issue_id: {
            "state": "CLOSED",
            "title": "TASK-153: sample",
            "url": "https://example.test/issues/153",
            "labels": [
                {"name": "type:task"},
                {"name": "status:in-progress"},
                {"name": "ready"},
            ],
        },
    )

    def _update_issue_labels(_root, _repo, issue_id, *, add=None, remove=None):
        edits.append([str(issue_id), sorted(add or []), sorted(remove or [])])

    def _comment_issue(_root, _repo, issue_id, body):
        comments.append([str(issue_id), body])
        return ""

    monkeypatch.setattr(worktree_issues, "update_issue_labels", _update_issue_labels)
    monkeypatch.setattr(tracker_client, "update_issue_labels", _update_issue_labels)
    monkeypatch.setattr(worktree_issues, "comment_issue", _comment_issue)
    monkeypatch.setattr(worktree_issues, "issue_has_handback_comment", lambda **_kwargs: False)
    monkeypatch.setattr(tracker_client, "ensure_label_exists", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        worktree_issues,
        "local_branch_exists",
        lambda _root, _branch: not branch_deleted,
    )

    def _run(cmd, *, cwd=None, **_kwargs):
        nonlocal branch_deleted
        cleanup_calls.append((cmd, cwd))
        if cmd[:3] == ["git", "worktree", "remove"]:
            target.path.rmdir()
        if cmd[:3] == ["git", "branch", "-d"]:
            branch_deleted = True
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(worktree_issues, "run", _run)

    worktree_issues.record_issue_handoff_event(
        root=root,
        repo="owner/repo",
        issue_number=153,
        issue_title=target.branch,
        branch=target.branch,
        worktree_path=target.path,
        event_type="worktree-resumed",
        state="worktree-ready",
        details={"source": "test"},
        idempotency_key="resume:153:test",
    )

    worktree_issues.close_issue_done(root, path=target.path, force=False)
    out = capsys.readouterr().out

    assert edits == [["153", ["status:done"], ["ready", "status:in-progress"]]]
    assert len(comments) == 1
    assert comments[0][0] == "153"
    assert "Execution evidence: PASS" in comments[0][1]
    assert "Evidence hash:" in comments[0][1]
    assert cleanup_calls == [
        (["git", "worktree", "remove", str(target.path)], root),
        (["git", "branch", "-d", "wt/task/153-sample"], root),
        (["git", "worktree", "prune"], root),
    ]
    assert "Issue #153 already closed." in out
    assert "Normalized closed-issue lifecycle labels." in out
    assert "Cleaning up worktree..." in out
    assert f"Removed worktree {target.path}" in out
    assert "Deleted branch wt/task/153-sample" in out
    assert "Pruned stale worktree refs" in out
    report_path = root / ".build" / "worktree-closeouts" / "issue-153-wt_task_153-sample.json"
    assert f"Closeout report: {report_path}" in out
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["stage"] == "complete"
    assert report["issue_closed"] is True
    assert report["cleanup_verified"] is True
    assert report["cleanup"] == {
        "branch_deleted": True,
        "worktree_pruned": True,
        "worktree_removed": True,
    }
    assert [event["stage"] for event in report["events"]] == [
        "starting",
        "merge-check",
        "issue-close",
        "cleanup",
        "cleanup-verified",
    ]
    assert report["events"][0]["message"] == "closeout started"
    assert report["events"][-1]["message"] == "cleanup verified"
    assert all(isinstance(event["ts"], str) for event in report["events"])
    assert all(isinstance(event["pid"], int) for event in report["events"])
    state_path = root / ".build" / "worktree-state" / "issue-153.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["state"] == "done"
    assert state["last_event_type"] == "handback-complete"
    assert [event["event_type"] for event in state["events"]] == [
        "worktree-resumed",
        "closeout-started",
        "closeout-complete",
        "handback-audited",
        "handback-complete",
    ]


def test_close_issue_done_normalizes_open_issue_after_close(monkeypatch, capsys, tmp_path):
    from scripts.issue_tool import tracker_client

    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    target = worktree_issues.WorktreeInfo(
        path=tmp_path / "worktrees" / "wt153",
        head="abc123",
        branch="wt/task/153-sample",
        is_primary=False,
    )
    primary = worktree_issues.WorktreeInfo(
        path=root,
        head="def456",
        branch="main",
        is_primary=True,
    )
    edits: list[list[str]] = []
    close_calls: list[str] = []
    comments: list[list[str]] = []
    branch_deleted = False

    target.path.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(worktree_issues, "list_worktrees", lambda _root: [primary, target])
    monkeypatch.setattr(worktree_issues, "resolve_current_worktree", lambda _path, _wts: target)
    monkeypatch.setattr(worktree_issues, "current_path", lambda: target.path)
    monkeypatch.setattr(worktree_issues, "tracker_repo_ready", lambda _root: (True, "owner/repo"))
    monkeypatch.setattr(
        worktree_issues,
        "merge_request_for_source_branch",
        lambda _root, _repo, _branch, _state: {"number": 157},
    )

    def _issue_state_info(_root, _repo, _issue_id):
        return {
            "state": "OPEN",
            "title": "TASK-153: sample",
            "url": "https://example.test/issues/153",
            "labels": [
                {"name": "type:task"},
                {"name": "status:in-progress"},
                {"name": "ready"},
                {"name": "review"},
                {"name": "in-progress"},
            ],
        }

    def _close_issue(_root, _repo, issue_id):
        close_calls.append(str(issue_id))

    def _update_issue_labels(_root, _repo, issue_id, *, add=None, remove=None):
        edits.append([str(issue_id), sorted(add or []), sorted(remove or [])])

    def _comment_issue(_root, _repo, issue_id, body):
        comments.append([str(issue_id), body])
        return ""

    monkeypatch.setattr(worktree_issues, "issue_state_info", _issue_state_info)
    monkeypatch.setattr(worktree_issues, "close_issue", _close_issue)
    monkeypatch.setattr(worktree_issues, "update_issue_labels", _update_issue_labels)
    monkeypatch.setattr(tracker_client, "update_issue_labels", _update_issue_labels)
    monkeypatch.setattr(worktree_issues, "comment_issue", _comment_issue)
    monkeypatch.setattr(worktree_issues, "issue_has_handback_comment", lambda **_kwargs: False)
    monkeypatch.setattr(tracker_client, "ensure_label_exists", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        worktree_issues,
        "local_branch_exists",
        lambda _root, _branch: not branch_deleted,
    )

    def _run(cmd, *, cwd=None, **_kwargs):
        nonlocal branch_deleted
        if cmd[:3] == ["git", "worktree", "remove"]:
            target.path.rmdir()
        if cmd[:3] == ["git", "branch", "-d"]:
            branch_deleted = True
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(worktree_issues, "run", _run)

    worktree_issues.record_issue_handoff_event(
        root=root,
        repo="owner/repo",
        issue_number=153,
        issue_title=target.branch,
        branch=target.branch,
        worktree_path=target.path,
        event_type="worktree-resumed",
        state="worktree-ready",
        details={"source": "test"},
        idempotency_key="resume:153:test",
    )

    worktree_issues.close_issue_done(root, path=target.path, force=False)
    out = capsys.readouterr().out

    assert close_calls == ["153"]
    assert edits == [
        [
            "153",
            ["status:done"],
            ["in-progress", "ready", "review", "status:in-progress"],
        ]
    ]
    assert "Closed issue #153." in out
    assert "Normalized closed-issue lifecycle labels." in out
    assert comments and comments[0][0] == "153"


def test_close_issue_done_defers_stalled_cleanup(monkeypatch, capsys, tmp_path):
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    target = worktree_issues.WorktreeInfo(
        path=tmp_path / "worktrees" / "wt153",
        head="abc123",
        branch="wt/task/153-sample",
        is_primary=False,
    )
    primary = worktree_issues.WorktreeInfo(
        path=root,
        head="def456",
        branch="main",
        is_primary=True,
    )
    comments: list[list[str]] = []

    target.path.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(worktree_issues, "list_worktrees", lambda _root: [primary, target])
    monkeypatch.setattr(worktree_issues, "resolve_current_worktree", lambda _path, _wts: target)
    monkeypatch.setattr(worktree_issues, "current_path", lambda: target.path)
    monkeypatch.setattr(worktree_issues, "tracker_repo_ready", lambda _root: (True, "owner/repo"))
    monkeypatch.setattr(
        worktree_issues,
        "merge_request_for_source_branch",
        lambda _root, _repo, _branch, _state: {"number": 157},
    )
    monkeypatch.setattr(
        worktree_issues,
        "issue_state_info",
        lambda _root, _repo, _issue_id: {
            "state": "CLOSED",
            "title": "TASK-153: sample",
            "url": "https://example.test/issues/153",
            "labels": [{"name": "type:task"}, {"name": "status:done"}],
        },
    )
    monkeypatch.setattr(worktree_issues, "issue_has_handback_comment", lambda **_kwargs: False)
    monkeypatch.setattr(
        worktree_issues,
        "comment_issue",
        lambda _root, _repo, issue_id, body: comments.append([str(issue_id), body]) or "",
    )

    def _cleanup_timeout(_root, _target):
        raise subprocess.TimeoutExpired(["git", "worktree", "remove", str(target.path)], 30)

    monkeypatch.setattr(worktree_issues, "cleanup_finished_worktree", _cleanup_timeout)
    monkeypatch.setattr(
        worktree_issues,
        "verify_cleanup_finished",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not verify")),
    )

    worktree_issues.record_issue_handoff_event(
        root=root,
        repo="owner/repo",
        issue_number=153,
        issue_title=target.branch,
        branch=target.branch,
        worktree_path=target.path,
        event_type="worktree-resumed",
        state="worktree-ready",
        details={"source": "test"},
        idempotency_key="resume:153:test",
    )

    worktree_issues.close_issue_done(root, path=target.path, force=False)
    out = capsys.readouterr().out

    assert "Cleanup deferred:" in out
    assert f"Manual cleanup: git worktree remove {target.path}" in out
    assert "Manual cleanup: git branch -d wt/task/153-sample" in out
    assert comments and comments[0][0] == "153"

    report_path = root / ".build" / "worktree-closeouts" / "issue-153-wt_task_153-sample.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["stage"] == "complete"
    assert report["issue_closed"] is True
    assert report["cleanup_verified"] is False
    assert "cleanup_error" in report
    assert report["events"][-1]["stage"] == "cleanup-deferred"

    state_path = root / ".build" / "worktree-state" / "issue-153.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["state"] == "done"
    assert state["last_event_type"] == "handback-complete"


def test_cmd_finish_close_json_prints_closeout_report(monkeypatch, capsys, tmp_path):
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    target = worktree_issues.WorktreeInfo(
        path=tmp_path / "worktrees" / "wt153",
        head="abc123",
        branch="wt/task/153-sample",
        is_primary=False,
    )
    report_path = root / ".build" / "worktree-closeouts" / "issue-153-wt_task_153-sample.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_payload = {
        "branch": target.branch,
        "events": [
            {
                "stage": "complete",
                "message": "done",
                "pid": 1,
                "ts": "2026-01-01T00:00:00Z",
            }
        ],
        "issue_closed": True,
        "issue_id": 153,
        "merged_pr_required": True,
        "repo": "owner/repo",
        "stage": "complete",
        "worktree_path": str(target.path),
    }

    monkeypatch.setattr(worktree_issues, "repo_root", lambda: root)
    monkeypatch.setattr(worktree_issues, "list_worktrees", lambda _root: [target])
    monkeypatch.setattr(worktree_issues, "resolve_current_worktree", lambda _path, _wts: target)
    monkeypatch.setattr(worktree_issues, "current_path", lambda: target.path)
    monkeypatch.setattr(worktree_issues, "closeout_report_path", lambda _root, _target: report_path)
    monkeypatch.setattr(
        worktree_issues,
        "close_issue_done",
        lambda *_args, **_kwargs: report_path.write_text(
            json.dumps(report_payload, indent=2) + "\n", encoding="utf-8"
        ),
    )

    rc = worktree_issues.cmd_finish_close(argparse.Namespace(path=None, force=False, json=True))
    out = capsys.readouterr().out

    assert rc == 0
    assert json.loads(out.splitlines()[-1]) == report_payload
