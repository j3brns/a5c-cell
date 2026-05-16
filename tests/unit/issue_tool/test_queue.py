from __future__ import annotations

from scripts.issue_tool.issue_queue import (
    build_queue,
    build_task_issue_body,
    choose_next_runnable,
    parse_issue_meta,
)

from ._support import _issue


def test_build_queue_auto_excludes_in_progress_from_candidates():
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

    selection = build_queue([in_progress, next_not_started], mode="auto")

    assert selection.source_mode == "open-task"
    assert "excludes status:in-progress" in selection.source_note
    assert [item.issue.number for item in selection.items] == [23]


def test_build_queue_can_start_from_issue_number():
    lower = _issue(
        number=22,
        task_id="TASK-015",
        seq=150,
        labels=["type:task", "status:not-started"],
    )
    higher = _issue(
        number=23,
        task_id="TASK-016",
        seq=160,
        labels=["type:task", "status:not-started"],
    )

    selection = build_queue([lower, higher], mode="open-task", from_issue=23)

    assert "starting from issue #23" in selection.source_note
    assert [item.issue.number for item in selection.items] == [23]


def test_build_queue_can_start_from_seq():
    lower = _issue(
        number=50,
        task_id="TASK-901",
        seq=901,
        labels=["type:task", "status:not-started"],
    )
    higher = _issue(
        number=51,
        task_id="TASK-902",
        seq=902,
        labels=["type:task", "status:not-started"],
    )

    selection = build_queue([lower, higher], mode="open-task", from_seq=902)

    assert "starting from Seq:902" in selection.source_note
    assert [item.issue.number for item in selection.items] == [51]


def test_choose_next_runnable_requires_not_blocked_and_dependencies_closed():
    blocked_by_label = _issue(
        number=23,
        task_id="TASK-016",
        seq=160,
        labels=["type:task", "status:blocked"],
    )
    blocked_by_dep = _issue(
        number=24,
        task_id="TASK-017",
        seq=170,
        labels=["type:task", "status:not-started"],
        depends_on=["TASK-099"],
    )
    closed_dependency = _issue(
        number=25,
        task_id="TASK-018",
        seq=180,
        state="closed",
        labels=["type:task", "status:done"],
    )
    runnable = _issue(
        number=26,
        task_id="TASK-019",
        seq=190,
        labels=["type:task", "status:not-started"],
        depends_on=["TASK-018"],
    )

    selection = build_queue(
        [blocked_by_label, blocked_by_dep, closed_dependency, runnable], mode="open-task"
    )

    next_item = choose_next_runnable(selection)
    assert next_item.issue.number == 26


def test_build_queue_supports_issue_number_dependencies():
    dependency = _issue(
        number=25,
        task_id="TASK-018",
        seq=180,
        state="closed",
        labels=["type:task", "status:done"],
    )
    runnable = _issue(
        number=26,
        task_id="TASK-019",
        seq=190,
        labels=["type:task", "status:not-started"],
        depends_on=["#25"],
    )

    selection = build_queue([dependency, runnable], mode="open-task")

    assert selection.items[0].issue.number == 26
    assert selection.items[0].runnable


def test_build_task_issue_body_uses_parser_contract():
    body = build_task_issue_body(seq=42, depends="#41", problem="Fix drift")

    assert parse_issue_meta(body) == (42, ["#41"])
    assert "## Trade-offs / Deferred work" in body
    assert "indirection deliberately added or avoided" in body


def test_parse_issue_meta_accepts_gitlab_indented_description_markers():
    body = "    Seq: 1110\n    Depends on: #1109\n\n    ## Problem\n\n    Fix drift."

    assert parse_issue_meta(body) == (1110, ["#1109"])
