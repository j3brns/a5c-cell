from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import pytest

from ._support import worktree_issues


def test_launch_zellij_session_adds_layout_to_existing_session(monkeypatch, capsys):
    path = Path("/tmp/worktrees/wt33")
    captured: dict[str, object] = {}

    monkeypatch.setattr(worktree_issues, "zellij_bin", lambda: "/home/julesb/bin/zellij")
    monkeypatch.setattr(worktree_issues, "zellij_session_exists", lambda _name: True)
    monkeypatch.setattr(
        worktree_issues,
        "worktree_session_pair",
        lambda label: worktree_issues.SessionPair(
            label=label, session_name="wt33-20260319-213333-000003"
        ),
    )

    def _execvp(bin_path, args):
        captured["bin_path"] = bin_path
        captured["args"] = args

    monkeypatch.setattr(worktree_issues.os, "execvp", _execvp)

    worktree_issues.launch_zellij_session(
        path=path,
        agent_command="codex --yolo",
        attach=True,
    )

    out = capsys.readouterr().out
    assert "already exists — attaching." in out
    assert captured["bin_path"] == "/home/julesb/bin/zellij"
    assert captured["args"] == ["/home/julesb/bin/zellij", "attach", "wt33-20260319-213333-000003"]
    assert "Session label: wt33" in out
    assert "Session name:  wt33-20260319-213333-000003" in out


def test_launch_zellij_batch_session_adds_tabs_to_existing_session(monkeypatch, capsys):
    launches = [
        ("wt33", Path("/tmp/worktrees/wt33"), "codex --yolo"),
        ("wt35", Path("/tmp/worktrees/wt35"), "gemini --normal"),
    ]
    captured: dict[str, object] = {}
    run_calls: list[list[str]] = []
    asset_dir = Path("/tmp/batch-assets-existing")

    monkeypatch.setattr(worktree_issues, "zellij_bin", lambda: "/home/julesb/bin/zellij")
    monkeypatch.setattr(worktree_issues, "zellij_session_exists", lambda _name: True)

    def _mkdtemp(*, prefix):
        asset_dir.mkdir(parents=True, exist_ok=True)
        return str(asset_dir)

    def _run(cmd, **kwargs):
        run_calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def _execvp(bin_path, args):
        captured["bin_path"] = bin_path
        captured["args"] = args

    monkeypatch.setattr(tempfile, "mkdtemp", _mkdtemp)
    monkeypatch.setattr(worktree_issues, "run", _run)
    monkeypatch.setattr(worktree_issues.os, "execvp", _execvp)

    worktree_issues.launch_zellij_batch_session(
        session_name="worktrees",
        launches=launches,
        attach=True,
    )

    out = capsys.readouterr().out
    assert "already exists — replacing." in out
    assert run_calls == [["/home/julesb/bin/zellij", "delete-session", "worktrees"]]
    assert captured["bin_path"] == "bash"
    assert captured["args"][0] == "bash"
    assert captured["args"][1] == "-lc"
    assert "--session worktrees" in captured["args"][2]
    layout = (asset_dir / "layout.kdl").read_text(encoding="utf-8")
    assert f'pane command="{asset_dir / "wt33-agent.sh"}"' in layout
    assert f'pane command="{asset_dir / "wt35-agent.sh"}"' in layout


def test_launch_zellij_session_starts_or_adds_with_layout(monkeypatch, tmp_path):
    calls: list[list[str]] = []
    asset_dir = tmp_path / "session-assets"

    monkeypatch.setattr(worktree_issues, "zellij_bin", lambda: "/home/julesb/bin/zellij")
    monkeypatch.setattr(worktree_issues, "zellij_session_exists", lambda _name: False)

    def _mkdtemp(*, prefix):
        asset_dir.mkdir(parents=True, exist_ok=True)
        return str(asset_dir)

    monkeypatch.setattr(tempfile, "mkdtemp", _mkdtemp)

    def _execvp(file, args):
        calls.append([file, *args[1:]])
        raise SystemExit(0)

    monkeypatch.setattr(worktree_issues.os, "execvp", _execvp)

    with pytest.raises(SystemExit):
        worktree_issues.launch_zellij_session(
            path=tmp_path,
            agent_command="echo agent",
            session_name="wt123",
            attach=True,
        )

    assert calls
    assert calls[0][0] == "bash"
    assert calls[0][1] == "-lc"
    assert "rm -rf " in calls[0][2]
    assert "--new-session-with-layout" in calls[0][2]
    assert "--session wt123" in calls[0][2]
    layout = (asset_dir / "layout.kdl").read_text(encoding="utf-8")
    assert 'args "-lc"' not in layout
    assert f'pane command="{asset_dir / "agent.sh"}"' in layout
    assert f'pane command="{asset_dir / "shell.sh"}"' in layout
    assert (asset_dir / "agent.sh").read_text(encoding="utf-8").endswith("\n")
    assert (asset_dir / "shell.sh").read_text(encoding="utf-8").endswith("\n")


def test_launch_zellij_session_adds_tab_to_existing_session(monkeypatch, tmp_path):
    calls: list[list[str]] = []
    subprocess_calls: list[list[str]] = []

    monkeypatch.setattr(worktree_issues, "zellij_bin", lambda: "/home/julesb/bin/zellij")
    monkeypatch.setattr(worktree_issues, "zellij_session_exists", lambda _name: True)
    monkeypatch.setattr(
        worktree_issues,
        "worktree_session_pair",
        lambda label: worktree_issues.SessionPair(
            label=label, session_name="wt123-20260319-213333-000004"
        ),
    )

    def _run(cmd, **kwargs):
        subprocess_calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def _execvp(file, args):
        calls.append([file, *args[1:]])
        raise SystemExit(0)

    monkeypatch.setattr(worktree_issues.subprocess, "run", _run)
    monkeypatch.setattr(worktree_issues.os, "execvp", _execvp)

    with pytest.raises(SystemExit):
        worktree_issues.launch_zellij_session(
            path=tmp_path,
            agent_command="echo agent",
            session_name="wt123",
            attach=True,
        )

    assert calls
    assert subprocess_calls == [["stty", "-ixon"]]
    assert calls[0][0] == "/home/julesb/bin/zellij"
    assert calls[0][1:] == ["attach", "wt123"]


def test_zellij_session_exists_handles_ansi_colored_output(monkeypatch):
    def _run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd,
            0,
            "\x1b[32;1mwt278\x1b[m [Created \x1b[35;1m0s\x1b[m ago]\n",
            "",
        )

    monkeypatch.setattr(worktree_issues, "zellij_bin", lambda: "/home/julesb/bin/zellij")
    monkeypatch.setattr(worktree_issues.subprocess, "run", _run)

    assert worktree_issues.zellij_session_exists("wt278") is True


def test_launch_zellij_batch_session_starts_or_adds_with_layout(monkeypatch, tmp_path):
    calls: list[list[str]] = []
    subprocess_calls: list[list[str]] = []
    asset_dir = tmp_path / "batch-assets"

    monkeypatch.setattr(worktree_issues, "zellij_bin", lambda: "/home/julesb/bin/zellij")
    monkeypatch.setattr(worktree_issues, "zellij_session_exists", lambda _name: False)

    def _mkdtemp(*, prefix):
        asset_dir.mkdir(parents=True, exist_ok=True)
        return str(asset_dir)

    monkeypatch.setattr(tempfile, "mkdtemp", _mkdtemp)

    def _run(cmd, **kwargs):
        subprocess_calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def _execvp(file, args):
        calls.append([file, *args[1:]])
        raise SystemExit(0)

    monkeypatch.setattr(worktree_issues.subprocess, "run", _run)
    monkeypatch.setattr(worktree_issues.os, "execvp", _execvp)

    with pytest.raises(SystemExit):
        worktree_issues.launch_zellij_batch_session(
            session_name="worktrees",
            launches=[("wt123", tmp_path, "echo agent")],
            attach=True,
        )

    assert calls
    assert subprocess_calls == [["stty", "-ixon"]]
    assert calls[0][0] == "bash"
    assert calls[0][1] == "-lc"
    assert "rm -rf " in calls[0][2]
    assert "--new-session-with-layout" in calls[0][2]
    assert "--session worktrees" in calls[0][2]
    layout = (asset_dir / "layout.kdl").read_text(encoding="utf-8")
    assert 'args "-lc"' not in layout
    assert f'pane command="{asset_dir / "wt123-agent.sh"}"' in layout
    assert f'pane command="{asset_dir / "wt123-shell.sh"}"' in layout
    assert 'export CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"' in (
        asset_dir / "wt123-agent.sh"
    ).read_text(encoding="utf-8")
    assert 'export CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"' in (
        asset_dir / "wt123-shell.sh"
    ).read_text(encoding="utf-8")


def test_launch_zellij_batch_session_adds_to_existing_session(monkeypatch, tmp_path):
    calls: list[list[str]] = []
    subprocess_calls: list[list[str]] = []
    asset_dir = tmp_path / "batch-assets-existing"

    monkeypatch.setattr(worktree_issues, "zellij_bin", lambda: "/home/julesb/bin/zellij")
    monkeypatch.setattr(worktree_issues, "zellij_session_exists", lambda _name: True)

    def _mkdtemp(*, prefix):
        asset_dir.mkdir(parents=True, exist_ok=True)
        return str(asset_dir)

    def _run(cmd, **kwargs):
        subprocess_calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def _execvp(file, args):
        calls.append([file, *args[1:]])
        raise SystemExit(0)

    monkeypatch.setattr(tempfile, "mkdtemp", _mkdtemp)
    monkeypatch.setattr(worktree_issues, "run", _run)
    monkeypatch.setattr(worktree_issues.os, "execvp", _execvp)

    with pytest.raises(SystemExit):
        worktree_issues.launch_zellij_batch_session(
            session_name="worktrees",
            launches=[("wt123", tmp_path, "echo agent")],
            attach=True,
        )

    assert calls
    assert subprocess_calls == [["/home/julesb/bin/zellij", "delete-session", "worktrees"]]
    assert calls[0][0] == "bash"
    assert calls[0][1] == "-lc"
    assert "--session worktrees" in calls[0][2]
    layout = (asset_dir / "layout.kdl").read_text(encoding="utf-8")
    assert f'pane command="{asset_dir / "wt123-agent.sh"}"' in layout
