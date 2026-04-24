from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.issue_tool import agent_launch  # noqa: E402


def test_worktree_env_preamble_sources_nvm_and_venv():
    preamble = agent_launch.worktree_env_preamble(Path("/tmp/worktree"))

    assert 'export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:$PATH"' in preamble
    assert 'export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"' in preamble
    assert "nvm use --silent" in preamble
    assert "[ -f .venv/bin/activate ] && . .venv/bin/activate || true" in preamble


def test_worktree_prereq_check_requires_node_uv_and_pyright():
    checks = agent_launch.worktree_prereq_check()

    assert "command -v node" in checks
    assert "command -v uv" in checks
    assert "npx --no-install pyright --version" in checks


def test_launch_interactive_session_no_mux_uses_bash_with_wrapped_command(monkeypatch):
    captured: dict[str, object] = {}

    def _execvp(bin_path: str, args: list[str]) -> None:
        captured["bin_path"] = bin_path
        captured["args"] = args
        raise SystemExit(0)

    monkeypatch.setattr(agent_launch.os, "execvp", _execvp)

    with pytest.raises(SystemExit):
        agent_launch.launch_interactive_session(
            command="codex 'fix it'",
            path=Path("/tmp/worktree"),
            mux="no-mux",
        )

    assert captured["bin_path"] == "bash"
    args = captured["args"]
    assert args[0:2] == ["bash", "-lc"]
    command = args[2]
    assert 'export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"' in command
    assert "npx --no-install pyright --version" in command
    assert "codex 'fix it'" in command
