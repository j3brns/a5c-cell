from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import pytest

from ._support import (
    _issue,
    commands_common,
    git_utils,
    gitnexus,
    issue_queue,
    models,
    multiplexer,
    shared,
    worktree,
    worktree_issues,
)


def test_cmd_wt_batch_writes_manifest_and_launches_detached_agents(monkeypatch, capsys, tmp_path):
    repo = "owner/repo"
    issue_33 = _issue(
        number=33,
        task_id="TASK-026",
        seq=260,
        labels=["type:task", "status:not-started", "ready"],
    )
    issue_35 = _issue(
        number=35,
        task_id="TASK-028",
        seq=280,
        labels=["type:task", "status:not-started", "ready"],
    )
    created: list[int] = []
    launched: list[tuple[int, str, Path, str]] = []
    manifest_payloads: dict[Path, dict[str, object]] = {}
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(git_utils, "repo_root", lambda: root)
    monkeypatch.setattr(git_utils, "origin_repo_slug", lambda _root: repo)
    monkeypatch.setattr(
        issue_queue,
        "fetch_repo_issues",
        lambda *_args, **_kwargs: [issue_33, issue_35],
    )
    monkeypatch.setattr(
        issue_queue,
        "build_queue",
        lambda _issues, **_kwargs: models.QueueSelection(
            source_mode="open-task",
            items=[
                models.QueueItem(issue=issue_33, runnable=True),
                models.QueueItem(issue=issue_35, runnable=True),
            ],
        ),
    )
    monkeypatch.setattr(worktree, "find_linked_worktree_for_issue", lambda *_args: None)
    monkeypatch.setattr(
        worktree,
        "create_worktree_for_issue",
        lambda **kwargs: (
            created.append(kwargs["issue"].number)
            or Path(f"/tmp/worktrees/wt{kwargs['issue'].number}")
        ),
    )
    monkeypatch.setattr(
        gitnexus, "prepare_gitnexus_for_worktree", lambda *args, **kwargs: None
    ) or monkeypatch.setattr(
        gitnexus, "prepare_gitnexus_for_worktree", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(commands_common, "build_agent_prompt_for_worktree", lambda *args: "prompt")
    monkeypatch.setattr(
        commands_common,
        "build_agent_command",
        lambda agent, mode, prompt: f"{agent}:{mode}:{prompt}",
    )
    monkeypatch.setattr(commands_common, "batch_run_id", lambda: "run-20260320-000001")
    monkeypatch.setattr(
        git_utils,
        "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0, "wt/task/test\n", ""),
    )

    def _write_json(path, payload):
        manifest_payloads[path] = payload
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path

    monkeypatch.setattr(shared, "write_json_file", _write_json)

    def _launch(**kwargs):
        launched.append(
            (
                kwargs["issue_number"],
                kwargs["agent"],
                kwargs["path"],
                kwargs["command"],
            )
        )
        issue_number = kwargs["issue_number"]
        wt_path = kwargs["path"]
        return models.BatchLaunchResult(
            issue_number=issue_number,
            agent=kwargs["agent"],
            worktree_path=wt_path,
            branch="wt/task/test",
            command=kwargs["command"],
            state="running",
            pid=2000 + issue_number,
            local_status_path=wt_path / ".build" / "agent-run" / "status.json",
            stdout_log_path=wt_path / ".build" / "agent-run" / "stdout.log",
            stderr_log_path=wt_path / ".build" / "agent-run" / "stderr.log",
            detail="started detached agent process",
        )

    monkeypatch.setattr(commands_common, "launch_agent_detached", _launch)

    rc = worktree_issues.cmd_wt_batch(
        argparse.Namespace(
            repo=None,
            stream_label=None,
            mode="auto",
            count=2,
            agents="gemini",
            agent_mode="yolo",
            base_dir=None,
            interactive=False,
            dry_run=False,
        )
    )

    out = capsys.readouterr().out

    assert rc == 0
    assert created == [33, 35]
    assert [item[0] for item in launched] == [33, 35]
    manifest_path = root / ".build" / "worktree-runs" / "run-20260320-000001" / "manifest.json"
    assert manifest_path in manifest_payloads
    assert manifest_payloads[manifest_path]["run_id"] == "run-20260320-000001"
    assert manifest_payloads[manifest_path]["count_selected"] == 2
    assert len(manifest_payloads[manifest_path]["entries"]) == 2
    assert "Batch run: 2 issue(s)" in out
    assert "Run id:   run-20260320-000001" in out
    assert f"Manifest: {manifest_path}" in out
    assert "[1/2] #33 -> starting" in out
    assert "[1/2] #33 -> running pid=2033" in out
    assert "[2/2] #35 -> starting" in out
    assert "[2/2] #35 -> running pid=2035" in out
    assert "Run summary:" in out


def test_cmd_wt_batch_reuses_existing_worktree_when_agent_not_running(
    monkeypatch, capsys, tmp_path
):
    repo = "owner/repo"
    issue_41 = _issue(
        number=41,
        task_id="TASK-041",
        seq=410,
        labels=["type:task", "status:not-started", "ready"],
    )
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    existing = models.WorktreeInfo(
        path=tmp_path / "worktrees" / "wt41",
        head="abc123",
        branch="wt/infra/41-test",
        is_primary=False,
    )
    launched: list[Path] = []

    monkeypatch.setattr(git_utils, "repo_root", lambda: root)
    monkeypatch.setattr(git_utils, "origin_repo_slug", lambda _root: repo)
    monkeypatch.setattr(
        issue_queue,
        "fetch_repo_issues",
        lambda *_args, **_kwargs: [issue_41],
    )
    monkeypatch.setattr(
        issue_queue,
        "build_queue",
        lambda _issues, **_kwargs: models.QueueSelection(
            source_mode="open-task",
            items=[models.QueueItem(issue=issue_41, runnable=True)],
        ),
    )
    monkeypatch.setattr(worktree, "find_linked_worktree_for_issue", lambda *_args: existing)
    monkeypatch.setattr(worktree, "worktree_agent_running", lambda path: False)
    monkeypatch.setattr(
        gitnexus, "prepare_gitnexus_for_worktree", lambda *args, **kwargs: None
    ) or monkeypatch.setattr(
        gitnexus, "prepare_gitnexus_for_worktree", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(commands_common, "build_agent_prompt_for_worktree", lambda *args: "prompt")
    monkeypatch.setattr(
        commands_common,
        "build_agent_command",
        lambda agent, mode, prompt: f"{agent}:{mode}:{prompt}",
    )
    monkeypatch.setattr(commands_common, "batch_run_id", lambda: "run-20260320-000002")
    monkeypatch.setattr(
        git_utils,
        "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0, "wt/infra/41-test\n", ""),
    )

    def _launch(**kwargs):
        launched.append(kwargs["path"])
        return models.BatchLaunchResult(
            issue_number=41,
            agent=kwargs["agent"],
            worktree_path=kwargs["path"],
            branch="wt/infra/41-test",
            command=kwargs["command"],
            state="running",
            pid=2041,
            local_status_path=kwargs["path"] / ".build" / "agent-run" / "status.json",
            stdout_log_path=kwargs["path"] / ".build" / "agent-run" / "stdout.log",
            stderr_log_path=kwargs["path"] / ".build" / "agent-run" / "stderr.log",
            detail="started detached agent process",
        )

    monkeypatch.setattr(commands_common, "launch_agent_detached", _launch)
    from scripts.issue_tool.shared import write_json_file

    monkeypatch.setattr(shared, "write_json_file", write_json_file)
    monkeypatch.setattr(
        worktree,
        "create_worktree_for_issue",
        lambda **kwargs: pytest.fail("create_worktree_for_issue should not be used"),
    )

    rc = worktree_issues.cmd_wt_batch(
        argparse.Namespace(
            repo=None,
            stream_label=None,
            mode="auto",
            count=1,
            agents="gemini",
            agent_mode="yolo",
            base_dir=None,
            interactive=False,
            dry_run=False,
        )
    )

    out = capsys.readouterr().out

    assert rc == 0
    assert launched == [existing.path]
    assert "Batch run: 1 issue(s)" in out
    assert "[1/1] #41 -> starting" in out
    assert "[1/1] #41 -> running pid=2041" in out


def test_cmd_wt_batch_skips_existing_worktree_with_running_agent(monkeypatch, capsys, tmp_path):
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    repo = "owner/repo"
    issue_41 = _issue(
        number=41,
        task_id="TASK-041",
        seq=410,
        labels=["type:task", "status:not-started", "ready"],
    )
    issue_42 = _issue(
        number=42,
        task_id="TASK-042",
        seq=420,
        labels=["type:task", "status:not-started", "ready"],
    )
    existing = models.WorktreeInfo(
        path=tmp_path / "worktrees" / "wt41",
        head="abc123",
        branch="wt/infra/41-test",
        is_primary=False,
    )
    created: list[int] = []

    monkeypatch.setattr(git_utils, "repo_root", lambda: root)
    monkeypatch.setattr(git_utils, "origin_repo_slug", lambda _root: repo)
    monkeypatch.setattr(
        issue_queue,
        "fetch_repo_issues",
        lambda *_args, **_kwargs: [issue_41, issue_42],
    )
    monkeypatch.setattr(
        issue_queue,
        "build_queue",
        lambda _issues, **_kwargs: models.QueueSelection(
            source_mode="open-task",
            items=[
                models.QueueItem(issue=issue_41, runnable=True),
                models.QueueItem(issue=issue_42, runnable=True),
            ],
        ),
    )
    monkeypatch.setattr(
        worktree,
        "find_linked_worktree_for_issue",
        lambda _root, issue_number: existing if issue_number == 41 else None,
    )
    monkeypatch.setattr(worktree, "worktree_agent_running", lambda path: path == existing.path)
    monkeypatch.setattr(
        worktree,
        "create_worktree_for_issue",
        lambda **kwargs: (
            created.append(kwargs["issue"].number)
            or tmp_path / "worktrees" / f"wt{kwargs['issue'].number}"
        ),
    )
    monkeypatch.setattr(
        gitnexus, "prepare_gitnexus_for_worktree", lambda *args, **kwargs: None
    ) or monkeypatch.setattr(
        gitnexus, "prepare_gitnexus_for_worktree", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(commands_common, "build_agent_prompt_for_worktree", lambda *args: "prompt")
    monkeypatch.setattr(
        commands_common,
        "build_agent_command",
        lambda agent, mode, prompt: f"{agent}:{mode}:{prompt}",
    )
    monkeypatch.setattr(commands_common, "batch_run_id", lambda: "run-20260320-000003")
    monkeypatch.setattr(
        git_utils,
        "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0, "wt/infra/42-test\n", ""),
    )
    monkeypatch.setattr(
        commands_common,
        "launch_agent_detached",
        lambda **kwargs: models.BatchLaunchResult(
            issue_number=kwargs["issue_number"],
            agent=kwargs["agent"],
            worktree_path=kwargs["path"],
            branch="wt/infra/42-test",
            command=kwargs["command"],
            state="running",
            pid=2042,
            local_status_path=kwargs["path"] / ".build" / "agent-run" / "status.json",
            stdout_log_path=kwargs["path"] / ".build" / "agent-run" / "stdout.log",
            stderr_log_path=kwargs["path"] / ".build" / "agent-run" / "stderr.log",
            detail="started detached agent process",
        ),
    )

    rc = worktree_issues.cmd_wt_batch(
        argparse.Namespace(
            repo=None,
            stream_label=None,
            mode="auto",
            count=2,
            agents="gemini",
            agent_mode="yolo",
            base_dir=None,
            interactive=False,
            dry_run=False,
        )
    )
    out = capsys.readouterr().out

    assert rc == 0
    assert created == [42]
    assert f"Skipping #41: agent already running in {existing.path}" in out
    assert "WARNING: only 1 runnable issue(s) available (requested 2)" in out
    assert "[1/1] #42 -> running pid=2042" in out


def test_cmd_wt_batch_rejects_tty_only_agent_pool_in_detached_mode(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(git_utils, "repo_root", lambda: root)

    from scripts.issue_tool.shared import CliError

    with pytest.raises(CliError, match="Detached wt-batch does not support"):
        worktree_issues.cmd_wt_batch(
            argparse.Namespace(
                repo=None,
                stream_label=None,
                mode="auto",
                count=1,
                agents="codex",
                agent_mode="yolo",
                base_dir=None,
                interactive=False,
                dry_run=False,
            )
        )


def test_cmd_wt_batch_interactive_launches_tmux_session(monkeypatch, capsys, tmp_path):
    repo = "owner/repo"
    issue_33 = _issue(
        number=33,
        task_id="TASK-026",
        seq=260,
        labels=["type:task", "status:not-started", "ready"],
    )
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    tmux_calls: dict[str, object] = {}

    monkeypatch.setattr(git_utils, "repo_root", lambda: root)
    monkeypatch.setattr(git_utils, "origin_repo_slug", lambda _root: repo)
    monkeypatch.setattr(issue_queue, "fetch_repo_issues", lambda *_args, **_kwargs: [issue_33])
    monkeypatch.setattr(
        issue_queue,
        "build_queue",
        lambda _issues, **_kwargs: models.QueueSelection(
            source_mode="open-task",
            items=[models.QueueItem(issue=issue_33, runnable=True)],
        ),
    )
    monkeypatch.setattr(worktree, "find_linked_worktree_for_issue", lambda *_args: None)
    monkeypatch.setattr(
        worktree,
        "create_worktree_for_issue",
        lambda **kwargs: tmp_path / "worktrees" / f"wt{kwargs['issue'].number}",
    )
    monkeypatch.setattr(
        gitnexus, "prepare_gitnexus_for_worktree", lambda *args, **kwargs: None
    ) or monkeypatch.setattr(
        gitnexus, "prepare_gitnexus_for_worktree", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(commands_common, "build_agent_prompt_for_worktree", lambda *args: "prompt")
    monkeypatch.setattr(
        commands_common,
        "build_agent_command",
        lambda agent, mode, prompt: f"{agent}:{mode}:{prompt}",
    )
    monkeypatch.setattr(commands_common, "batch_run_id", lambda: "run-20260320-000005")
    monkeypatch.setattr(
        git_utils,
        "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0, "wt/task/test\n", ""),
    )
    monkeypatch.setattr(multiplexer, "tmux_available", lambda: True)
    monkeypatch.setattr(
        multiplexer,
        "worktree_session_pair",
        lambda label: models.SessionPair(label=label, session_name="wt-batch-20260320"),
    )
    monkeypatch.setattr(
        multiplexer,
        "launch_tmux_batch_session",
        lambda **kwargs: tmux_calls.update(kwargs),
    )

    rc = worktree_issues.cmd_wt_batch(
        argparse.Namespace(
            repo=None,
            stream_label=None,
            mode="auto",
            count=1,
            agents="codex",
            agent_mode="yolo",
            base_dir=None,
            interactive=True,
            dry_run=False,
        )
    )
    out = capsys.readouterr().out

    assert rc == 0
    assert tmux_calls["session_name"] == "wt-batch-20260320"
    assert tmux_calls["attach"] is True
    assert tmux_calls["announce_windows"] is True
    assert tmux_calls["launches"] == [
        (
            "wt33",
            tmp_path / "worktrees" / "wt33",
            "codex:yolo:prompt",
        )
    ]
    assert "interactive: tmux session wt-batch-20260320" in out
    status_path = tmp_path / "worktrees" / "wt33" / ".build" / "agent-run" / "status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["backend"] == "tmux"
    assert status["state"] == "interactive"
    assert status["session_name"] == "wt-batch-20260320"
