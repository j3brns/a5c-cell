from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from ._support import _issue, git_utils, multiplexer, worktree, worktree_issues


def test_cmd_agent_handoff_defaults_to_codex_yolo_execute_now(monkeypatch):
    root = Path("/tmp/repo")
    wt = Path("/tmp/worktrees/wt314")
    recorded: dict[str, object] = {}

    monkeypatch.setattr(git_utils, "repo_root", lambda: root)
    monkeypatch.setattr(git_utils, "origin_repo_slug", lambda _root: "owner/repo")
    monkeypatch.setattr(
        git_utils, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "", "")
    )
    monkeypatch.setattr(git_utils, "current_path", lambda: wt)
    monkeypatch.setattr(
        worktree,
        "current_branch",
        lambda _path: "wt/task/314-reserved-platform-tenant-and-control-plane-agent-model",
    )

    import scripts.issue_tool.commands.common as common_cmd

    monkeypatch.setattr(
        common_cmd,
        "handoff_to_agent_or_shell",
        lambda **kwargs: recorded.update(kwargs),
    )
    # Patch the record function in worktree module
    monkeypatch.setattr(worktree, "record_issue_handoff_event", lambda **_kwargs: None)

    # Also mock os.execvp just in case
    import os

    monkeypatch.setattr(os, "execvp", lambda *args: None)

    rc = worktree_issues.cmd_agent_handoff(
        argparse.Namespace(
            repo=None,
            path=None,
            agent=None,
            agent_mode=None,
            review_agent=None,
            review_agent_mode=None,
            handoff=None,
            print_only=False,
            tmux=None,
            zellij=None,
            no_mux=False,
        )
    )

    assert rc == 0
    assert recorded["path"] == wt
    assert recorded["agent"] == "codex"
    assert recorded["agent_mode"] == "yolo"
    assert recorded["review_agent"] is None
    assert recorded["handoff"] == "execute-now"
    # resolve_mux_flag returns None if no flags are set
    assert recorded["mux"] is None


def test_cmd_agent_handoff_passes_review_lane_and_auto_mux(monkeypatch):
    root = Path("/tmp/repo")
    wt = Path("/tmp/worktrees/wt314")
    recorded: dict[str, object] = {}

    monkeypatch.setattr(git_utils, "repo_root", lambda: root)
    monkeypatch.setattr(git_utils, "origin_repo_slug", lambda _root: "owner/repo")
    monkeypatch.setattr(
        git_utils, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "", "")
    )
    monkeypatch.setattr(git_utils, "current_path", lambda: wt)
    monkeypatch.setattr(
        worktree,
        "current_branch",
        lambda _path: "wt/task/314-reserved-platform-tenant-and-control-plane-agent-model",
    )

    import scripts.issue_tool.commands.common as common_cmd

    monkeypatch.setattr(
        common_cmd,
        "handoff_to_agent_or_shell",
        lambda **kwargs: recorded.update(kwargs),
    )
    monkeypatch.setattr(worktree, "record_issue_handoff_event", lambda **_kwargs: None)
    monkeypatch.setattr(multiplexer.os, "execvp", lambda *args: None)

    rc = worktree_issues.cmd_agent_handoff(
        argparse.Namespace(
            repo=None,
            path=None,
            agent="codex",
            agent_mode="yolo",
            review_agent="gemini",
            review_agent_mode="normal",
            handoff="execute-now",
            print_only=False,
            tmux=None,
            zellij=None,
            no_mux=False,
        )
    )

    assert rc == 0
    assert recorded["review_agent"] == "gemini"
    assert recorded["review_agent_mode"] == "normal"
    assert recorded["mux"] is None


def test_issue_status_rows_join_issue_worktree_agent_and_validation(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    wt = tmp_path / "worktrees" / "wt33"
    wt.mkdir(parents=True)
    issue = _issue(
        number=33,
        task_id="TASK-033",
        seq=330,
        labels=["type:task", "status:in-progress"],
    )

    import scripts.issue_tool.commands.common as common_cmd

    monkeypatch.setattr(common_cmd, "local_issue_numbers", lambda _root, **_kwargs: {33})
    monkeypatch.setattr(
        common_cmd.evidence,
        "issue_evidence_summary",
        lambda _root, _issue_number: {
            "linked_worktree": str(wt),
            "linked_branch": "wt/task/33-test",
            "state": {
                "last_event_type": "agent-launch-requested",
                "details": {"agent": "codex"},
            },
            "closeout": None,
            "validation_receipt": {"check": "validate-pre-push"},
        },
    )
    monkeypatch.setattr(
        worktree,
        "worktree_agent_status",
        lambda _path: {
            "agent": "codex",
            "backend": "tmux",
            "state": "interactive",
            "session_name": "wt33",
        },
    )
    monkeypatch.setattr(worktree, "worktree_agent_running", lambda _path: True)
    monkeypatch.setattr(worktree, "list_worktrees", lambda _root: [])

    # merge_request_for_source_branch is in commands.common
    monkeypatch.setattr(
        common_cmd,
        "merge_request_for_source_branch",
        lambda _root, _repo, _branch, _state: {
            "number": 12,
            "state": "merged",
            "isDraft": False,
        },
    )

    rows = worktree_issues.issue_status_rows(root, "owner/repo", [issue])

    assert rows == [
        {
            "issue": 33,
            "seq": 330,
            "title": "TASK-033: Test issue 33",
            "issue_status": "in-progress",
            "issue_state": "open",
            "worktree": str(wt),
            "branch": "wt/task/33-test",
            "mr": "!12:merged",
            "agent": "codex",
            "runtime": "tmux:interactive:wt33",
            "live": "yes",
            "validation": "validate-pre-push:pass",
            "closeout": "-",
            "last_event": "agent-launch-requested",
        }
    ]


def test_cmd_issue_status_prints_joined_dashboard(monkeypatch, capsys, tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    issue = _issue(
        number=44,
        task_id="TASK-044",
        seq=440,
        labels=["type:task", "status:not-started", "ready"],
    )

    monkeypatch.setattr(git_utils, "repo_root", lambda: root)
    monkeypatch.setattr(git_utils, "origin_repo_slug", lambda _root: "owner/repo")
    monkeypatch.setattr(
        git_utils, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "", "")
    )

    from ._support import issue_queue

    monkeypatch.setattr(
        issue_queue,
        "fetch_repo_issues",
        lambda *_args, **_kwargs: [issue],
    )
    import scripts.issue_tool.commands.common as common_cmd

    monkeypatch.setattr(common_cmd, "local_issue_numbers", lambda _root, **_kwargs: set())
    monkeypatch.setattr(
        common_cmd.evidence,
        "issue_evidence_summary",
        lambda _root, _issue_number: {
            "linked_worktree": None,
            "linked_branch": None,
            "state": None,
            "closeout": None,
            "validation_receipt": None,
        },
    )
    monkeypatch.setattr(
        common_cmd,
        "merge_request_for_source_branch",
        lambda _root, _repo, _branch, _state: None,
    )

    rc = worktree_issues.cmd_issue_status(
        argparse.Namespace(repo=None, issue=None, all=False, json=False)
    )

    assert rc == 0
    out = capsys.readouterr().out
    assert "Issue" in out
    assert "MR" in out
    assert "Status" in out
    assert "44" in out
    assert "not-started" in out
