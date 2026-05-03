from __future__ import annotations

import subprocess
from typing import Literal

from scripts.issue_tool.commands.agent import (
    cmd_agent_handoff,
)
from scripts.issue_tool.commands.batch import (
    cmd_wt_batch,
)
from scripts.issue_tool.commands.common import (
    choose_issue_interactive,
)
from scripts.issue_tool.commands.finish import (
    cmd_finish_close,
    cmd_finish_summary,
)
from scripts.issue_tool.commands.issue import (
    cmd_issue_queue,
    cmd_issue_repair_stale_locks,
    cmd_issue_status,
    cmd_issues_audit,
    cmd_issues_reconcile,
)
from scripts.issue_tool.commands.worktree import (
    cmd_pre_validate,
    cmd_preflight,
    cmd_push_branch,
    cmd_worktree_create,
    cmd_worktree_next,
    cmd_worktree_resume,
)
from scripts.issue_tool.shared import (
    CliError,
)


def choose_post_create_action_interactive() -> str:
    while True:
        print("Next action after worktree creation:")
        print("  1) Open shell with agent handoff (default)")
        print("  2) Return to menu")
        print("  0) Back")
        raw = input("Choice [1]: ").strip() or "1"
        if raw in {"1", "shell"}:
            return "shell"
        if raw in {"2", "return"}:
            return "return"
        if raw in {"0", "back"}:
            raise CliError("Back")
        print("Invalid choice.")


def cmd_menu(
    repo: str | None = None,
    stream_label: str | None = None,
    from_issue: int | None = None,
    from_seq: int | None = None,
    mode: Literal["auto", "ready", "open-task"] = "auto",
    base_dir: str | None = None,
) -> int:
    # Lightweight interactive wrapper. Keep policies in the underlying commands.
    while True:
        print()
        print("Issue Worktree Menu")
        print("  1) Show queue")
        print("  2) Create next runnable worktree")
        print("  3) Create worktree from queue (pick issue)")
        print("  4) Resume worktree (shell)")
        print("  5) Resume worktree (print path)")
        print("  6) Preflight current worktree")
        print("  7) Pre-validate current worktree (make validate-pre-push)")
        print("  8) Push current worktree branch (preflight + pre-validate enforced)")
        print("  9) Finish summary (current worktree)")
        print("  10) Close issue done (current worktree, requires merged MR)")
        print("  0) Exit")
        choice = input("Choice [1]: ").strip() or "1"
        try:
            if choice == "1":
                cmd_issue_queue(
                    repo=repo,
                    stream_label=stream_label,
                    from_issue=from_issue,
                    from_seq=from_seq,
                    mode=mode,
                    limit=None,
                    runnable_only=False,
                    json_output=False,
                )
            elif choice == "2":
                post_create = choose_post_create_action_interactive()
                cmd_worktree_next(
                    repo=repo,
                    stream_label=stream_label,
                    from_issue=from_issue,
                    from_seq=from_seq,
                    mode=mode,
                    choose=False,
                    allow_blocked=False,
                    base_dir=base_dir,
                    base_ref=None,
                    scope=None,
                    slug=None,
                    name=None,
                    no_claim=False,
                    no_preflight=False,
                    dry_run=False,
                    open_shell=(post_create == "shell"),
                    agent=None,
                    agent_mode=None,
                    handoff=None,
                    print_only=False,
                )
            elif choice == "3":
                post_create = choose_post_create_action_interactive()
                cmd_worktree_next(
                    repo=repo,
                    stream_label=stream_label,
                    from_issue=from_issue,
                    from_seq=from_seq,
                    mode=mode,
                    choose=True,
                    allow_blocked=False,
                    base_dir=base_dir,
                    base_ref=None,
                    scope=None,
                    slug=None,
                    name=None,
                    no_claim=False,
                    no_preflight=False,
                    dry_run=False,
                    open_shell=(post_create == "shell"),
                    agent=None,
                    agent_mode=None,
                    handoff=None,
                    print_only=False,
                )
            elif choice == "4":
                cmd_worktree_resume(
                    path=None,
                    no_preflight=False,
                    open_shell=True,
                    command=None,
                    agent=None,
                    agent_mode=None,
                    handoff=None,
                    print_only=False,
                )
            elif choice == "5":
                cmd_worktree_resume(
                    path=None,
                    no_preflight=False,
                    open_shell=False,
                    command=None,
                    agent=None,
                    agent_mode=None,
                    handoff=None,
                    print_only=False,
                )
            elif choice == "6":
                cmd_preflight(repo=repo, path=None)
            elif choice == "7":
                cmd_pre_validate(path=None, dry_run=False)
            elif choice == "8":
                cmd_push_branch(path=None, dry_run=False)
            elif choice == "9":
                cmd_finish_summary(path=None)
            elif choice == "10":
                cmd_finish_close(path=None, force=False)
            elif choice in {"0", "exit", "quit"}:
                return 0
            else:
                print("Invalid choice.")
        except CliError as exc:
            print(f"ERROR: {exc}")
        except subprocess.CalledProcessError as exc:
            print(f"ERROR: command failed ({exc.returncode}): {' '.join(exc.cmd)}")
