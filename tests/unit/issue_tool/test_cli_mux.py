from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ._support import worktree_issues


def test_launch_tmux_batch_session_starts_grid(monkeypatch, capsys):
    launches = [
        ("wt33", Path("/tmp/worktrees/wt33"), "codex --yolo"),
        ("wt35", Path("/tmp/worktrees/wt35"), "gemini --normal"),
    ]
    calls: list[list[str]] = []
    attached: dict[str, object] = {}

    monkeypatch.setattr(worktree_issues, "tmux_session_exists", lambda _name: False)

    def _run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def _execvp(bin_path, args):
        attached["bin_path"] = bin_path
        attached["args"] = args
        raise SystemExit(0)

    monkeypatch.setattr(worktree_issues.subprocess, "run", _run)
    monkeypatch.setattr(worktree_issues.os, "execvp", _execvp)

    with pytest.raises(SystemExit):
        worktree_issues.launch_tmux_batch_session(
            session_name="worktrees",
            launches=launches,
            attach=True,
            announce_windows=False,
        )

    out = capsys.readouterr().out
    assert "tmux session 'worktrees' launching with 2 worktree window(s)" in out
    assert calls[0][:4] == ["tmux", "new-session", "-d", "-s"]
    assert calls[0][4] == "worktrees"
    assert calls[1][:3] == ["tmux", "split-window", "-h"]
    assert any(cmd[:3] == ["tmux", "new-window", "-t"] for cmd in calls)
    assert any(cmd[:3] == ["tmux", "select-window", "-t"] for cmd in calls)
    assert attached["bin_path"] == "tmux"
    assert attached["args"] == ["tmux", "attach-session", "-t", "worktrees"]


def test_launch_tmux_batch_session_replaces_existing_session(monkeypatch, capsys):
    launches = [("wt33", Path("/tmp/worktrees/wt33"), "codex --yolo")]
    calls: list[list[str]] = []
    attached: dict[str, object] = {}

    monkeypatch.setattr(worktree_issues, "tmux_session_exists", lambda _name: True)

    def _run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def _execvp(bin_path, args):
        attached["bin_path"] = bin_path
        attached["args"] = args
        raise SystemExit(0)

    monkeypatch.setattr(worktree_issues.subprocess, "run", _run)
    monkeypatch.setattr(worktree_issues.os, "execvp", _execvp)

    with pytest.raises(SystemExit):
        worktree_issues.launch_tmux_batch_session(
            session_name="worktrees",
            launches=launches,
            attach=True,
            announce_windows=True,
        )

    out = capsys.readouterr().out
    assert "already exists — replacing." in out
    assert calls[0] == ["tmux", "kill-session", "-t", "worktrees"]
    assert attached["args"] == ["tmux", "attach-session", "-t", "worktrees"]


def test_launch_tmux_session_uses_reported_initial_window_index(monkeypatch, capsys):
    path = Path("/tmp/worktrees/wt318")
    calls: list[list[str]] = []
    attached: dict[str, object] = {}

    monkeypatch.setattr(worktree_issues, "tmux_session_exists", lambda _name: False)

    def _run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[:3] == ["tmux", "list-panes", "-t"]:
            return subprocess.CompletedProcess(cmd, 0, "wt318:1.0\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def _execvp(bin_path, args):
        attached["bin_path"] = bin_path
        attached["args"] = args
        raise SystemExit(0)

    monkeypatch.setattr(worktree_issues.subprocess, "run", _run)
    monkeypatch.setattr(worktree_issues.os, "execvp", _execvp)

    with pytest.raises(SystemExit):
        worktree_issues.launch_tmux_session(
            path=path,
            agent_command="claude --dangerously-skip-permissions prompt",
            attach=True,
        )

    out = capsys.readouterr().out
    assert "tmux session 'wt318' launching in /tmp/worktrees/wt318" in out
    assert calls[0][:4] == ["tmux", "new-session", "-d", "-s"]
    assert [
        "tmux",
        "list-panes",
        "-t",
        "wt318",
        "-F",
        "#{session_name}:#{window_index}.#{pane_index}",
    ] in calls
    assert ["tmux", "rename-window", "-t", "wt318:1", "wt318"] in calls
    assert ["tmux", "split-window", "-h", "-t", "wt318:1", "-c", "/tmp/worktrees/wt318"] in calls
    assert any(cmd[:4] == ["tmux", "send-keys", "-t", "wt318:1.1"] for cmd in calls)
    assert any(cmd[:4] == ["tmux", "send-keys", "-t", "wt318:1.0"] for cmd in calls)
    shell_init = next(
        cmd[4] for cmd in calls if cmd[:4] == ["tmux", "send-keys", "-t", "wt318:1.1"]
    )
    agent_launch = next(
        cmd[4] for cmd in calls if cmd[:4] == ["tmux", "send-keys", "-t", "wt318:1.0"]
    )
    assert 'export CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"' in shell_init
    assert (
        'case "$CODEX_HOME" in /*) ;; *) export CODEX_HOME="$PWD/$CODEX_HOME" ;; esac' in shell_init
    )
    assert 'mkdir -p "$CODEX_HOME"' in shell_init
    assert 'export CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"' in agent_launch
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

    monkeypatch.setattr(worktree_issues, "run", _run_prompt)
    monkeypatch.setattr(worktree_issues, "worktree_issue_id", lambda _path: 381)
    monkeypatch.setattr(
        worktree_issues,
        "fetch_issue_labels_for_prompt",
        lambda _root, _repo, _issue: "enhancement|type:task|status:in-progress",
    )
    monkeypatch.setattr(worktree_issues, "ensure_uv_venv", lambda _path: None)
    monkeypatch.setattr(
        worktree_issues,
        "launch_tmux_session",
        lambda **_kwargs: (_ for _ in ()).throw(
            subprocess.CalledProcessError(1, ["tmux", "list-panes"])
        ),
    )
    monkeypatch.setattr(worktree_issues.os, "execvp", _execvp)

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

    monkeypatch.setattr(worktree_issues, "ensure_uv_venv", lambda _path: None)
    monkeypatch.setattr(
        worktree_issues,
        "build_agent_prompt_for_worktree",
        lambda *_args: "implementation prompt",
    )
    monkeypatch.setattr(
        worktree_issues,
        "build_review_prompt_for_worktree",
        lambda *_args, **_kwargs: "review prompt",
    )
    monkeypatch.setattr(
        worktree_issues,
        "build_agent_command",
        lambda agent, mode, prompt: f"{agent}:{mode}:{prompt}",
    )
    monkeypatch.setattr(
        worktree_issues,
        "worktree_session_pair",
        lambda label: worktree_issues.SessionPair(label=label, session_name="wt381-review"),
    )
    monkeypatch.setattr(
        worktree_issues,
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
    monkeypatch.setattr(worktree_issues, "ensure_uv_venv", lambda _path: None)
    monkeypatch.setattr(worktree_issues, "build_agent_prompt_for_worktree", lambda *_args: "impl")
    monkeypatch.setattr(
        worktree_issues,
        "build_review_prompt_for_worktree",
        lambda *_args, **_kwargs: "review",
    )
    monkeypatch.setattr(
        worktree_issues,
        "build_agent_command",
        lambda agent, mode, prompt: f"{agent}:{mode}:{prompt}",
    )

    with pytest.raises(worktree_issues.CliError, match="Review lane requires tmux/zellij"):
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
