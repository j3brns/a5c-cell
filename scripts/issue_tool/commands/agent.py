from __future__ import annotations

import argparse
from pathlib import Path

from scripts.issue_tool import git_utils, worktree
from scripts.issue_tool.commands import common


def cmd_agent_handoff(
    repo: str | None = None,
    path: str | None = None,
    agent: str | None = None,
    agent_mode: str | None = None,
    review_agent: str | None = None,
    review_agent_mode: str | None = None,
    handoff: str | None = None,
    print_only: bool = False,
    tmux: bool | None = None,
    zellij: bool | None = None,
    no_mux: bool = False,
) -> int:
    root = git_utils.repo_root()
    try:
        repo_slug = repo or git_utils.origin_repo_slug(root)
    except common.CliError:
        repo_slug = None
    target_path = Path(path).resolve() if path else git_utils.current_path()
    branch = worktree.current_branch(target_path)
    issue_id = worktree.extract_issue_id_from_branch(branch)
    agent_resolved, mode_resolved, handoff_resolved, _ = common.resolve_cli_launch_request(
        agent=agent,
        agent_mode=agent_mode,
        review_agent=review_agent,
        review_agent_mode=review_agent_mode,
        handoff=handoff,
        print_only=print_only,
        tmux=tmux,
        zellij=zellij,
        no_mux=no_mux,
        default_agent="codex",
    )
    mux_resolved = common.resolve_mux_flag(no_mux=no_mux, tmux=tmux, zellij=zellij)
    worktree.record_issue_handoff_event(
        root=root,
        repo=repo_slug,
        issue_number=issue_id,
        issue_title=branch,
        branch=branch,
        worktree_path=target_path,
        event_type="agent-launch-requested",
        state="agent-launching",
        details={
            "source": "agent-handoff",
            "agent": agent_resolved,
            "agent_mode": mode_resolved,
            "review_agent": review_agent,
            "review_agent_mode": review_agent_mode,
            "handoff": handoff_resolved,
            "mux": mux_resolved,
        },
        idempotency_key=(
            f"agent:{issue_id}:{target_path}:{agent_resolved}:{mode_resolved}:"
            f"{handoff_resolved}:{mux_resolved}"
        ),
    )
    common.handoff_to_agent_or_shell(
        path=target_path,
        root=root,
        repo=repo_slug,
        agent=agent_resolved,
        agent_mode=mode_resolved,
        review_agent=review_agent,
        review_agent_mode=review_agent_mode,
        handoff=handoff_resolved,
        print_only_override=print_only or handoff_resolved == "print-only",
        mux=mux_resolved,
    )
    return 0
