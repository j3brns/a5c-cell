from __future__ import annotations

import pytest

from ._support import _issue, worktree_issues


def test_audit_issues_flags_invalid_status_and_ready_combinations():
    closed_wrong_status = _issue(
        number=30,
        task_id="TASK-023",
        seq=230,
        state="closed",
        labels=["type:task", "status:in-progress"],
    )
    open_done = _issue(
        number=31,
        task_id="TASK-024",
        seq=240,
        state="open",
        labels=["type:task", "status:done"],
    )
    ready_in_progress = _issue(
        number=32,
        task_id="TASK-025",
        seq=250,
        state="open",
        labels=["type:task", "status:in-progress", "ready"],
    )

    findings = worktree_issues.audit_issues([closed_wrong_status, open_done, ready_in_progress])
    messages = [f.message for f in findings if f.severity == "error"]

    assert any("closed task must be status:done" in msg for msg in messages)
    assert any("open task cannot be status:done" in msg for msg in messages)
    assert any("ready label requires status:not-started" in msg for msg in messages)


def test_audit_issues_passes_clean_state_with_next_startable():
    in_progress = _issue(
        number=22,
        task_id="TASK-015",
        seq=150,
        labels=["type:task", "status:in-progress"],
    )
    next_not_started = _issue(
        number=23,
        task_id="TASK-016",
        seq=160,
        labels=["type:task", "status:not-started"],
    )
    done = _issue(
        number=21,
        task_id="TASK-014",
        seq=140,
        state="closed",
        labels=["type:task", "status:done"],
    )

    findings = worktree_issues.audit_issues([in_progress, next_not_started, done])
    errors = [f for f in findings if f.severity == "error"]
    warnings = [f for f in findings if f.severity == "warning"]
    assert errors == []
    assert warnings == []


def test_reconcile_issue_label_changes_closed_in_progress_moves_to_done():
    issue = _issue(
        number=40,
        task_id="TASK-040",
        seq=400,
        state="closed",
        labels=["type:task", "status:in-progress", "ready"],
    )
    add_labels, remove_labels = worktree_issues.reconcile_issue_label_changes(issue)
    assert add_labels == ["status:done"]
    assert set(remove_labels) == {"ready", "status:in-progress"}


def test_assert_issue_startable_rejects_in_progress():
    issue = _issue(
        number=41,
        task_id="TASK-041",
        seq=410,
        labels=["type:task", "status:in-progress"],
    )
    with pytest.raises(worktree_issues.CliError, match="already status:in-progress"):
        worktree_issues.assert_issue_startable(issue, allow_blocked=False)
