from __future__ import annotations

import json
import os
import signal
import subprocess

import pytest

from ._support import commands_common, worktree_issues
from ._support import worktree as worktree_mod


def test_launch_agent_detached_writes_runtime_state(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    worktree = tmp_path / "wt41"
    root.mkdir(parents=True, exist_ok=True)
    worktree.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(worktree_mod, "ensure_uv_venv", lambda path: None)

    result = worktree_issues.launch_agent_detached(
        root=root,
        run_id="run-20260320-000004",
        issue_number=41,
        path=worktree,
        branch="wt/infra/41-test",
        agent="gemini",
        command='python3 -c "import time; time.sleep(5)"',
    )

    try:
        assert result.state == "running"
        assert result.pid is not None
        assert worktree_issues.pid_is_running(result.pid) is True
        assert result.local_status_path is not None and result.local_status_path.exists()
        assert result.stdout_log_path is not None and result.stdout_log_path.exists()
        assert result.stderr_log_path is not None and result.stderr_log_path.exists()
        pid_path = worktree / ".build" / "agent-run" / "pid"
        assert pid_path.read_text(encoding="utf-8").strip() == str(result.pid)
        status = json.loads(result.local_status_path.read_text(encoding="utf-8"))
        assert status["run_id"] == "run-20260320-000004"
        assert status["issue_number"] == 41
        assert status["branch"] == "wt/infra/41-test"
        assert status["agent"] == "gemini"
        assert status["state"] == "running"
        assert status["backend"] == "detached"
        assert status["pid"] == result.pid
        assert status["orchestrator_manifest"].endswith(
            ".build/worktree-runs/run-20260320-000004/manifest.json"
        )
        assert worktree_issues.worktree_agent_running(worktree) is True
    finally:
        if result.pid is not None and worktree_issues.pid_is_running(result.pid):
            os.kill(result.pid, signal.SIGTERM)
            subprocess.run(["bash", "-lc", f"wait {result.pid}"], check=False)


def test_launch_agent_detached_rejects_tty_only_agents(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    worktree = tmp_path / "wt41"
    root.mkdir(parents=True, exist_ok=True)
    worktree.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(worktree_mod, "ensure_uv_venv", lambda path: None)

    with pytest.raises(worktree_issues.CliError, match="does not support detached startup"):
        worktree_issues.launch_agent_detached(
            root=root,
            run_id="run-20260320-tty-only",
            issue_number=41,
            path=worktree,
            branch="wt/infra/41-test",
            agent="codex",
            command="codex --yolo test",
        )


def test_launch_agent_detached_marks_early_exit_failed(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    worktree = tmp_path / "wt41"
    root.mkdir(parents=True, exist_ok=True)
    worktree.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(worktree_mod, "ensure_uv_venv", lambda path: None)
    created: dict[str, object] = {}

    class _FakeProc:
        pid = 4242

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def wait(self, timeout=None):
            return 1

    def _popen(cmd, **kwargs):
        created["cmd"] = cmd
        created["kwargs"] = kwargs
        return _FakeProc()

    monkeypatch.setattr(commands_common.subprocess, "Popen", _popen)

    result = worktree_issues.launch_agent_detached(
        root=root,
        run_id="run-20260320-fail-fast",
        issue_number=41,
        path=worktree,
        branch="wt/infra/41-test",
        agent="gemini",
        command="false",
    )

    assert result.state == "failed"
    assert "startup probe" in result.detail
    assert result.local_status_path is not None
    assert created["cmd"][:2] == ["bash", "-lc"]
    status = json.loads(result.local_status_path.read_text(encoding="utf-8"))
    assert status["state"] == "failed"
    assert worktree_issues.worktree_agent_running(worktree) is False
