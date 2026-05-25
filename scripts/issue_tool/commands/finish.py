from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.issue_tool import closeout, git_utils, worktree
from scripts.issue_tool.commands import common


def cmd_finish_summary(path: str | None = None) -> int:
    root = git_utils.repo_root()
    common.finish_summary(root, path=Path(path).resolve() if path else None)
    return 0


def cmd_finish_close(
    path: str | None = None, force: bool = False, json_output: bool = False
) -> int:
    root = git_utils.repo_root()
    target_path = Path(path).resolve() if path else None
    report_path = None
    if json_output:
        worktrees = worktree.list_worktrees(root)
        target = worktree.resolve_current_worktree(
            target_path or git_utils.current_path(), worktrees
        )
        report_path = closeout.closeout_report_path(
            root,
            target,
            extract_issue_id_from_branch_fn=worktree.extract_issue_id_from_branch,
        )
    common.close_issue_done(root, path=target_path, force=force)
    if report_path is not None:
        print(
            json.dumps(
                closeout.read_closeout_report(report_path),
                sort_keys=True,
            )
        )
    return 0
