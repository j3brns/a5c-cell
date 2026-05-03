from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.issue_tool import agent_launch, multiplexer  # noqa: E402


def test_worktree_env_preamble_sources_nvm_and_venv():
    preamble = multiplexer.worktree_env_preamble()

    assert "source .venv/bin/activate" in preamble
    assert 'export CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"' in preamble


def test_launch_interactive_session_no_mux_uses_direct_exec(monkeypatch):
    captured: dict[str, object] = {}

    def _execvp(bin_path: str, args: list[str]) -> None:
        captured["bin_path"] = bin_path
        captured["args"] = args
        raise SystemExit(0)

    monkeypatch.setattr(agent_launch.os, "execvp", _execvp)
    # Ensure directory exists for chdir
    monkeypatch.setattr(agent_launch.os, "chdir", lambda _: None)

    with pytest.raises(SystemExit):
        agent_launch.launch_interactive_session(
            command="codex run",
            path=Path("/tmp/worktree"),
            mux="no-mux",
        )

    assert captured["bin_path"] == "codex"
    assert captured["args"] == ["codex", "run"]
