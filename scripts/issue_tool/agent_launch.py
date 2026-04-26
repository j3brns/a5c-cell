from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

from scripts.issue_tool.git_utils import current_path, eprint, repo_root, run
from scripts.issue_tool.models import BatchLaunchResult, Issue
from scripts.issue_tool.shared import CliError

AGENT_CAPABILITIES: dict[str, dict[str, bool]] = {
    "gemini": {"requires_tty": False, "supports_detached": True},
    "claude": {"requires_tty": True, "supports_detached": False},
    "codex": {"requires_tty": True, "supports_detached": False},
}

DEFAULT_INTERACTIVE_AGENT_POOL = ("codex", "gemini", "claude")


def resolve_launch_request(args: argparse.Namespace) -> tuple[str, str, str, str]:
    agent = args.agent or "codex"
    agent_mode = args.agent_mode or "yolo"
    handoff = args.handoff or ("execute-now" if not args.print_only else "print-only")
    mux = "no-mux"
    if getattr(args, "tmux", False):
        mux = "tmux"
    elif getattr(args, "zellij", False):
        mux = "zellij"
    elif getattr(args, "no_mux", False):
        mux = "no-mux"
    return agent, agent_mode, handoff, mux


def build_agent_launch_command(
    agent: str,
    agent_mode: str,
    handoff: str,
    path: Path,
    issue: Issue | None = None,
    backend: str = "interactive",
) -> str:
    # This matches the internal protocol for gemini-cli / codex-cli
    cmd = []
    if agent == "gemini":
        cmd = ["gemini", "yolo" if agent_mode == "yolo" else "chat"]
    elif agent == "codex":
        cmd = ["codex", "run"] if agent_mode == "yolo" else ["codex", "chat"]
    else:
        cmd = ["claude", "yolo" if agent_mode == "yolo" else "chat"]

    if handoff == "print-only":
        return f"# Launch command for {agent}:\n" + " ".join(cmd)

    return " ".join(cmd)


def launch_interactive_session(
    command: str,
    path: Path,
    mux: str = "no-mux",
    session_name: str | None = None,
) -> None:
    # Implementation for tmux/zellij would go here, or be imported
    # For now, keeping the core delegation logic
    cwd = str(path)
    if mux == "no-mux":
        os.chdir(cwd)
        os.execvp(shlex.split(command)[0], shlex.split(command))
    elif mux == "tmux":
        from scripts.issue_tool.cli import launch_tmux_session

        launch_tmux_session(
            session_name=session_name or "agent-session", agent_command=command, path=path
        )
    elif mux == "zellij":
        from scripts.issue_tool.cli import launch_zellij_session

        launch_zellij_session(
            session_name=session_name or "agent-session", agent_command=command, path=path
        )
