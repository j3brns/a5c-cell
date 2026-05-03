from __future__ import annotations

import json

from ._support import (
    _issue,
    evidence,
    models,
    worktree,
    worktree_issues,
)


def test_stale_evidence_findings_detects_orphaned_files(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)

    # Mock issue_evidence_summary and find_linked_worktree_for_issue to avoid git calls
    monkeypatch.setattr(
        evidence,
        "issue_evidence_summary",
        lambda _root, _id: {
            "linked_worktree": None,
            "linked_branch": None,
            "state_path": None,
            "closeout_path": None,
            "validation_receipt": None,
        },
    )
    monkeypatch.setattr(worktree, "list_resume_candidates", lambda *_args: [])

    # Create orphaned state file
    state_dir = root / ".build" / "worktree-state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "issue-999.json").write_text("{}", encoding="utf-8")

    # Create orphaned closeout file
    closeout_dir = root / ".build" / "worktree-closeouts"
    closeout_dir.mkdir(parents=True, exist_ok=True)
    (closeout_dir / "issue-888-wt_task_888-test.json").write_text("{}", encoding="utf-8")

    # Create orphaned receipt
    receipt_dir = root / ".build" / "validation-receipts"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    (receipt_dir / "issue-777-abc123456789.json").write_text("{}", encoding="utf-8")

    issues = [_issue(number=1, task_id="TASK-001", seq=10)]

    findings = worktree_issues.stale_evidence_findings(root, issues)

    messages = [f.message for f in findings]
    assert any(
        "orphaned state file: .build/worktree-state/issue-999.json" in msg for msg in messages
    )
    assert any(
        "orphaned closeout file: .build/worktree-closeouts/issue-888-wt_task_888-test.json" in msg
        for msg in messages
    )
    assert any(
        "orphaned receipt file: .build/validation-receipts/issue-777-abc123456789.json" in msg
        for msg in messages
    )


def test_stale_evidence_findings_detects_dead_worktree_path(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)

    issue = _issue(
        number=33, task_id="TASK-033", seq=330, labels=["type:task", "status:in-progress"]
    )

    # Mock list_resume_candidates to avoid git calls
    monkeypatch.setattr(worktree, "list_resume_candidates", lambda *_args: [])

    # Mock issue_evidence_summary to return a non-existent worktree path
    dead_path = tmp_path / "non-existent-wt"
    monkeypatch.setattr(
        evidence,
        "issue_evidence_summary",
        lambda _root, _issue_id: {
            "linked_worktree": str(dead_path),
            "linked_branch": "wt/task/33-test",
            "state_path": None,
            "closeout_path": None,
            "validation_receipt": None,
        },
    )

    wt = models.WorktreeInfo(path=dead_path, head="abc", branch="wt/task/33-test")
    monkeypatch.setattr(worktree, "list_resume_candidates", lambda _root: [wt])
    findings = worktree_issues.stale_evidence_findings(root, [issue])

    assert any(f"linked worktree path does not exist: {dead_path}" in f.message for f in findings)
    assert any(f.severity == "error" for f in findings)


def test_stale_evidence_findings_detects_missing_closeout_for_done_task(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)

    issue = _issue(number=33, task_id="TASK-033", seq=330, labels=["type:task", "status:done"])

    # Mock list_resume_candidates to avoid git calls
    monkeypatch.setattr(worktree, "list_resume_candidates", lambda *_args: [])

    monkeypatch.setattr(
        evidence,
        "issue_evidence_summary",
        lambda _root, _issue_id: {
            "linked_worktree": None,
            "linked_branch": None,
            "state_path": str(root / ".build" / "worktree-state" / "issue-33.json"),
            "closeout_path": None,
            "validation_receipt": None,
        },
    )

    dead_path = tmp_path / "non-existent-wt"
    wt = models.WorktreeInfo(path=dead_path, head="abc", branch="wt/task/33-test")
    monkeypatch.setattr(worktree, "list_resume_candidates", lambda _root: [wt])
    findings = worktree_issues.stale_evidence_findings(root, [issue])

    assert any("missing closeout report" in f.message for f in findings)
    assert any(f.severity == "warning" for f in findings)


def test_stale_evidence_findings_detects_stale_validation_receipt(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)

    issue = _issue(
        number=33, task_id="TASK-033", seq=330, labels=["type:task", "status:in-progress"]
    )

    # Create a validation receipt file
    receipt_dir = root / ".build" / "validation-receipts"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = receipt_dir / "issue-33-oldsha123456.json"
    receipt_path.write_text(json.dumps({"head_sha": "oldsha1234567890"}), encoding="utf-8")

    # Mock list_resume_candidates to return a linked worktree with different HEAD
    monkeypatch.setattr(
        worktree,
        "list_resume_candidates",
        lambda _root: [
            models.WorktreeInfo(
                path=root / "wt33",
                head="newsha7890123456",
                branch="wt/task/33-test",
            )
        ],
    )

    # Mock issue_evidence_summary to avoid git calls
    monkeypatch.setattr(
        evidence,
        "issue_evidence_summary",
        lambda _root, _id: {
            "linked_worktree": str(root / "wt33"),
            "linked_branch": "wt/task/33-test",
            "state_path": None,
            "closeout_path": None,
            "validation_receipt": None,
        },
    )

    findings = worktree_issues.stale_evidence_findings(root, [issue])

    assert any("stale validation receipt" in f.message for f in findings)
    assert any("oldsha1234567890"[:12] in f.message for f in findings)
    assert any("newsha7890123456"[:12] in f.message for f in findings)
