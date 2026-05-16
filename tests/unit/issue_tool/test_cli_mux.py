from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from ._support import commands_common as common
from ._support import git_utils, multiplexer, worktree, worktree_issues


def test_worktree_session_pair_generates_stamped_label():
    pair = multiplexer.worktree_session_pair("test")
    assert pair.label == "test"
    assert pair.session_name.startswith("test-")
    assert len(pair.session_name.split("-")) >= 3


def test_launch_tmux_session_initialization_sequence(monkeypatch, capsys):
    path = Path("/tmp/worktrees/wt318")
    attached = {}

    def _execvp(bin_path, args):
        attached["bin_path"] = bin_path
        attached["args"] = args
        raise SystemExit(0)

    monkeypatch.setattr(multiplexer.os, "execvp", _execvp)

    with (
        patch.object(multiplexer, "tmux_session_exists", return_value=False),
        patch.object(
            multiplexer.git_utils,
            "run",
            return_value=subprocess.CompletedProcess([], 0, "", ""),
        ) as mock_run,
    ):
        try:
            multiplexer.launch_tmux_session(
                path=path,
                agent_command="claude --dangerously-skip-permissions prompt",
                attach=True,
            )
        except SystemExit:
            pass

        calls = [args[0] for args, _ in mock_run.call_args_list]

    out = capsys.readouterr().out
    assert "tmux session 'wt318' launching in /tmp/worktrees/wt318" in out

    # Verify new-session call
    assert any(cmd[:6] == ["tmux", "new-session", "-d", "-s", "wt318", "-n"] for cmd in calls)
    new_session_cmd = next(cmd for cmd in calls if cmd[1] == "new-session")
    assert new_session_cmd[6] == "wt318"
    assert "claude --dangerously-skip-permissions prompt" in new_session_cmd[-1]

    # Verify split-window call
    split_cmd = calls[1]
    assert split_cmd == [
        "tmux",
        "split-window",
        "-h",
        "-t",
        "wt318:wt318",
        "-c",
        "/tmp/worktrees/wt318",
    ]

    # Verify send-keys and focus
    assert calls[2] == [
        "tmux",
        "send-keys",
        "-t",
        "wt318:wt318.1",
        multiplexer.worktree_env_preamble(),
        "Enter",
    ]
    assert calls[3] == ["tmux", "select-pane", "-t", "wt318:wt318.0"]

    assert attached["args"] == ["tmux", "attach-session", "-t", "wt318"]


def test_handoff_to_agent_or_shell_falls_back_when_tmux_launch_fails(monkeypatch, tmp_path, capsys):
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    wt = tmp_path / "worktrees" / "wt381"
    wt.mkdir(parents=True, exist_ok=True)
    execvp_call: dict[str, object] = {}

    def _run_prompt(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, "wt/task/381-something\n", "")

    def _execvp(bin_path, args):
        execvp_call["bin_path"] = bin_path
        execvp_call["args"] = args
        raise SystemExit(0)

    monkeypatch.setattr(git_utils, "run", _run_prompt)
    monkeypatch.setattr(worktree_issues, "worktree_issue_id", lambda _path: 381)
    monkeypatch.setattr(
        worktree_issues,
        "fetch_issue_labels_for_prompt",
        lambda _root, _repo, _issue: "enhancement|type:task|status:in-progress",
    )
    monkeypatch.setattr(worktree, "ensure_uv_venv", lambda _path: None)
    monkeypatch.setattr(
        multiplexer,
        "launch_tmux_session",
        lambda **_kwargs: (_ for _ in ()).throw(
            subprocess.CalledProcessError(1, ["tmux", "list-panes"])
        ),
    )
    monkeypatch.setattr(multiplexer.os, "execvp", _execvp)

    with pytest.raises(SystemExit):
        worktree_issues.handoff_to_agent_or_shell(
            path=wt,
            root=root,
            repo="owner/repo",
            agent="codex",
            agent_mode="yolo",
            handoff="execute-now",
            mux="tmux",
        )

    captured = capsys.readouterr()
    assert "WARNING: tmux launch failed" in captured.err
    assert execvp_call["bin_path"] == "bash"
    assert execvp_call["args"][0:2] == ["bash", "-lc"]
    assert 'export CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"' in execvp_call["args"][2]
    assert (
        'case "$CODEX_HOME" in /*) ;; *) export CODEX_HOME="$PWD/$CODEX_HOME" ;; esac'
        in execvp_call["args"][2]
    )


def test_handoff_with_review_agent_launches_tmux_batch(monkeypatch, capsys):
    root = Path("/tmp/repo")
    wt = Path("/tmp/worktrees/wt381")
    launches: dict[str, object] = {}
    monkeypatch.setattr(
        git_utils, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "", "")
    )

    monkeypatch.setattr(worktree, "ensure_uv_venv", lambda _path: None)
    monkeypatch.setattr(
        common,
        "build_agent_prompt_for_worktree",
        lambda *_args: "implementation prompt",
    )
    monkeypatch.setattr(
        common,
        "build_review_prompt_for_worktree",
        lambda *_args, **_kwargs: "review prompt",
    )
    monkeypatch.setattr(
        common,
        "build_agent_command",
        lambda agent, mode, prompt: f"{agent}:{mode}:{prompt}",
    )
    monkeypatch.setattr(
        multiplexer,
        "worktree_session_pair",
        lambda label: multiplexer.SessionPair(label=label, session_name="wt381-review"),
    )
    monkeypatch.setattr(
        multiplexer,
        "launch_tmux_batch_session",
        lambda **kwargs: launches.update(kwargs),
    )

    worktree_issues.handoff_to_agent_or_shell(
        path=wt,
        root=root,
        repo="owner/repo",
        agent="codex",
        agent_mode="yolo",
        review_agent="gemini",
        review_agent_mode="normal",
        handoff="execute-now",
        mux="tmux",
    )

    out = capsys.readouterr().out
    assert "Review: gemini (normal)" in out
    assert launches == {
        "session_name": "wt381-review",
        "launches": [
            ("implement", wt, "codex:yolo:implementation prompt"),
            ("review", wt, "gemini:normal:review prompt"),
        ],
    }


def test_handoff_with_review_agent_and_no_mux_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(worktree, "ensure_uv_venv", lambda _path: None)
    monkeypatch.setattr(common, "build_agent_prompt_for_worktree", lambda *_args: "impl")
    monkeypatch.setattr(
        common,
        "build_review_prompt_for_worktree",
        lambda *_args, **_kwargs: "review",
    )
    monkeypatch.setattr(
        common,
        "build_agent_command",
        lambda agent, mode, prompt: f"{agent}:{mode}:{prompt}",
    )

    with pytest.raises(multiplexer.CliError, match="Review lane requires tmux/zellij"):
        worktree_issues.handoff_to_agent_or_shell(
            path=tmp_path,
            root=tmp_path,
            repo="owner/repo",
            agent="codex",
            agent_mode="yolo",
            review_agent="claude",
            review_agent_mode="normal",
            handoff="execute-now",
            mux="none",
        )
