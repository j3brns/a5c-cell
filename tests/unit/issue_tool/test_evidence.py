from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ._support import _issue, worktree_issues


def test_evidence_drift_findings_warns_for_in_progress_issue_without_local_evidence(
    tmp_path, monkeypatch
):
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    issue = _issue(
        number=22,
        task_id="TASK-015",
        seq=150,
        labels=["type:task", "status:in-progress"],
    )

    monkeypatch.setattr(worktree_issues, "find_linked_worktree_for_issue", lambda *_args: None)

    findings = worktree_issues.evidence_drift_findings(root, [issue])

    assert len(findings) == 1
    assert findings[0].severity == "warning"
    assert "no local linked worktree or .build evidence" in findings[0].message


def test_record_issue_handoff_event_dedupes_by_idempotency_key(tmp_path):
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    issue = _issue(number=33, task_id="TASK-033", seq=330)

    first = worktree_issues.record_issue_handoff_event(
        root=root,
        repo="owner/repo",
        issue=issue,
        branch="wt/task/33-test-issue-33",
        worktree_path=tmp_path / "worktrees" / "wt33",
        event_type="worktree-created",
        state="worktree-ready",
        details={"source": "test"},
        idempotency_key="create:33:wt33",
    )
    second = worktree_issues.record_issue_handoff_event(
        root=root,
        repo="owner/repo",
        issue=issue,
        branch="wt/task/33-test-issue-33",
        worktree_path=tmp_path / "worktrees" / "wt33",
        event_type="worktree-created",
        state="worktree-ready",
        details={"source": "test"},
        idempotency_key="create:33:wt33",
    )

    assert first == second
    payload = json.loads(first.read_text(encoding="utf-8"))
    assert payload["state"] == "worktree-ready"
    assert payload["last_event_type"] == "worktree-created"
    assert len(payload["events"]) == 1
    assert payload["events"][0]["idempotency_key"] == "create:33:wt33"


def test_record_issue_handoff_event_resets_completed_session_on_new_start(tmp_path):
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)

    worktree_issues.record_issue_handoff_event(
        root=root,
        repo="owner/repo",
        issue_number=33,
        issue_title="TASK-033: Test issue 33",
        branch="wt/task/33-old",
        worktree_path=tmp_path / "worktrees" / "wt33-old",
        event_type="handback-complete",
        state="done",
        details={"source": "old"},
        idempotency_key="done:33",
    )
    state_path = root / ".build" / "worktree-state" / "issue-33.json"
    old_payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert old_payload["events"][-1]["event_type"] == "handback-complete"

    worktree_issues.record_issue_handoff_event(
        root=root,
        repo="owner/repo",
        issue_number=33,
        issue_title="TASK-033: Test issue 33",
        branch="wt/task/33-new",
        worktree_path=tmp_path / "worktrees" / "wt33-new",
        event_type="worktree-created",
        state="worktree-ready",
        details={"source": "new"},
        idempotency_key="create:33:new",
    )
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert [event["event_type"] for event in payload["events"]] == ["worktree-created"]
    assert payload["branch"] == "wt/task/33-new"


def test_issue_evidence_summary_reports_state_and_closeout(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    wt = tmp_path / "worktrees" / "wt33"
    wt.mkdir(parents=True, exist_ok=True)
    state_dir = root / ".build" / "worktree-state"
    state_dir.mkdir(parents=True, exist_ok=True)
    closeout_dir = root / ".build" / "worktree-closeouts"
    closeout_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / "issue-33.json"
    closeout_path = closeout_dir / "issue-33-wt_task_33-test.json"
    state_path.write_text(
        json.dumps(
            {
                "issue_number": 33,
                "state": "done",
                "last_event_type": "handback-complete",
                "last_updated_at": "2026-01-01T00:00:00Z",
                "events": [],
            }
        ),
        encoding="utf-8",
    )
    closeout_path.write_text(
        json.dumps({"stage": "complete", "cleanup_verified": True}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        worktree_issues,
        "find_linked_worktree_for_issue",
        lambda *_args: worktree_issues.WorktreeInfo(
            path=wt,
            head="abc123",
            branch="wt/task/33-test",
            is_primary=False,
        ),
    )

    summary = worktree_issues.issue_evidence_summary(root, 33)

    assert summary["linked_worktree"] == str(wt)
    assert summary["evidence_source"] == "local"
    assert summary["state_path"] == str(state_path)
    assert summary["closeout_path"] == str(closeout_path)
    assert summary["state"]["last_event_type"] == "handback-complete"
    assert summary["closeout"]["cleanup_verified"] is True


def test_issue_evidence_summary_falls_back_to_historical_when_local_evidence_missing(
    tmp_path, monkeypatch
):
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(worktree_issues, "find_linked_worktree_for_issue", lambda *_args: None)
    monkeypatch.setattr(
        worktree_issues,
        "historical_issue_evidence",
        lambda *_args: {
            "preferred_branch": "wt/task/33-test",
            "branch_tip": {
                "sha": "abc123",
                "timestamp": "2026-01-01T00:00:00Z",
                "subject": "feat: test",
            },
            "log_matches": [
                {"sha": "abc123", "timestamp": "2026-01-01T00:00:00Z", "subject": "feat: test"}
            ],
        },
    )

    summary = worktree_issues.issue_evidence_summary(root, 33)

    assert summary["evidence_source"] == "historical"
    assert summary["historical"]["preferred_branch"] == "wt/task/33-test"
    assert summary["state"] is None
    assert summary["validation_receipt"] is None


def test_write_validation_receipt_writes_issue_scoped_receipt(tmp_path):
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    wt = root / "worktrees" / "wt33"
    wt.mkdir(parents=True, exist_ok=True)

    def fake_run(cmd, *, cwd=None, **_kwargs):
        joined = " ".join(cmd)
        if joined == "git rev-parse HEAD":
            return subprocess.CompletedProcess(cmd, 0, stdout="abc123def456\n", stderr="")
        raise AssertionError(f"unexpected command: {joined}")

    original_run = worktree_issues.run
    worktree_issues.run = fake_run
    try:
        receipt_path = worktree_issues.write_validation_receipt(
            root,
            issue_id=33,
            worktree_path=wt,
            branch="wt/task/33-test",
            check_name="validate-pre-push",
        )
    finally:
        worktree_issues.run = original_run

    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert payload["issue_number"] == 33
    assert payload["branch"] == "wt/task/33-test"
    assert payload["head_sha"] == "abc123def456"  # pragma: allowlist secret
    assert payload["check"] == "validate-pre-push"
    assert payload["result"] == "pass"


def test_append_issue_handback_comment_skips_existing_hash(monkeypatch):
    posted: list[str] = []

    monkeypatch.setattr(
        worktree_issues,
        "get_issue",
        lambda *_args, **_kwargs: {
            "comments": [{"body": "Execution evidence: PASS\nEvidence hash: abc123"}]
        },
    )
    monkeypatch.setattr(
        worktree_issues,
        "comment_issue",
        lambda _root, _repo, _issue_id, body: posted.append(body),
    )

    worktree_issues.append_issue_handback_comment(
        root=Path("/tmp/repo"),
        repo="owner/repo",
        issue_id=153,
        summary={"evidence_hash": "abc123"},
    )

    assert posted == []
