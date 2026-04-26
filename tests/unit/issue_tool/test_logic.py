from __future__ import annotations

import pytest

from scripts.issue_tool.logic import assert_issue_startable, reconcile_issue_label_changes
from scripts.issue_tool.shared import CliError

from ._support import _issue


def test_reconcile_issue_label_changes_closed_in_progress_moves_to_done():
    issue = _issue(
        number=40,
        task_id="TASK-040",
        seq=400,
        state="closed",
        labels=["type:task", "status:in-progress", "ready"],
    )
    add_labels, remove_labels = reconcile_issue_label_changes(issue)
    assert add_labels == ["status:done"]
    assert set(remove_labels) == {"ready", "status:in-progress"}


def test_assert_issue_startable_rejects_in_progress():
    issue = _issue(
        number=41,
        task_id="TASK-041",
        seq=410,
        labels=["type:task", "status:in-progress"],
    )
    with pytest.raises(CliError, match="already status:in-progress"):
        assert_issue_startable(issue, allow_blocked=False)
