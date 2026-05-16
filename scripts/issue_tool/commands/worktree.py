from __future__ import annotations

import argparse
from pathlib import Path
from typing import Literal

from scripts.issue_tool import git_utils, gitnexus, issue_queue, logic, worktree
from scripts.issue_tool.commands import common


def cmd_worktree_next(
    repo: str | None = None,
    mode: Literal["auto", "ready", "open-task"] = "auto",
    stream_label: str | None = None,
    from_issue: int | None = None,
    from_seq: int | None = None,
    base_dir: str | None = None,
    base_ref: str | None = None,
    scope: str | None = None,
    slug: str | None = None,
    name: str | None = None,
    no_claim: bool = False,
    no_preflight: bool = False,
    dry_run: bool = False,
    pre_provision: bool = False,
    open_shell: bool = False,
    allow_blocked: bool = False,
    agent: str | None = None,
    agent_mode: str | None = None,
    review_agent: str | None = None,
    review_agent_mode: str | None = None,
    handoff: str | None = None,
    print_only: bool = False,
    tmux: bool | None = None,
    zellij: bool | None = None,
    no_mux: bool = False,
    mux: bool = False,
    choose: bool = False,
) -> int:
    root = git_utils.repo_root()
    repo = repo or git_utils.origin_repo_slug(root)
    issues = issue_queue.fetch_repo_issues(root, repo, state="all")
    selection = issue_queue.build_queue(
        issues,
        stream_label=stream_label,
        from_issue=from_issue,
        from_seq=from_seq,
        mode=mode,
    )
    if choose:
        issue = common.choose_issue_interactive(selection)
        queue_item = next(
            (item for item in selection.items if item.issue.number == issue.number), None
        )
        if queue_item and (not queue_item.runnable) and not allow_blocked:
            blocked_msg = "; ".join(queue_item.blocked_reasons)
            raise common.CliError(f"Selected issue #{issue.number} is blocked: {blocked_msg}")
        existing_wt = worktree.find_linked_worktree_for_issue(root, issue.number)
        if existing_wt is not None:
            print(f"Issue #{issue.number} already has linked worktree: {existing_wt.path}")
            gitnexus.prepare_gitnexus_for_worktree(existing_wt.path)
            worktree.record_issue_handoff_event(
                root=root,
                repo=repo,
                issue=issue,
                branch=existing_wt.branch,
                worktree_path=existing_wt.path,
                event_type="worktree-reused",
                state="worktree-ready",
                details={"source": "worktree-next", "choose": bool(choose)},
                idempotency_key=f"reuse:{issue.number}:{existing_wt.branch}:{existing_wt.path}",
            )
            if open_shell and not dry_run:
                if not no_preflight:
                    worktree.run_preflight(path=existing_wt.path, root=root, repo=repo)
                worktree.record_issue_handoff_event(
                    root=root,
                    repo=repo,
                    issue=issue,
                    branch=existing_wt.branch,
                    worktree_path=existing_wt.path,
                    event_type="shell-opened",
                    state="shell-active",
                    details={"source": "worktree-next"},
                    idempotency_key=f"shell:{issue.number}:{existing_wt.path}",
                )
                worktree.open_shell(existing_wt.path)
                return 0

            launch_wants = common.wants_agent_launch(
                agent=agent,
                agent_mode=agent_mode,
                review_agent=review_agent,
                review_agent_mode=review_agent_mode,
                handoff=handoff,
                print_only=print_only,
                tmux=tmux,
                zellij=zellij,
                no_mux=no_mux,
                mux=mux,
            )
            if launch_wants and not dry_run:
                agent_resolved, mode_resolved, handoff_resolved, _ = (
                    common.resolve_cli_launch_request(
                        agent=agent,
                        agent_mode=agent_mode,
                        review_agent=review_agent,
                        review_agent_mode=review_agent_mode,
                        handoff=handoff,
                        print_only=print_only,
                        tmux=tmux,
                        zellij=zellij,
                        no_mux=no_mux,
                        mux=mux,
                    )
                )
                mux_resolved = common.resolve_mux_flag(
                    no_mux=no_mux, tmux=tmux, zellij=zellij, mux=mux
                )
                common.handoff_to_agent_or_shell(
                    path=existing_wt.path,
                    root=root,
                    repo=repo,
                    agent=agent_resolved,
                    agent_mode=mode_resolved,
                    review_agent=review_agent,
                    review_agent_mode=review_agent_mode,
                    handoff=handoff_resolved,
                    print_only_override=print_only,
                    mux=mux_resolved,
                )
            return 0
    else:
        queue_item, skipped = worktree.choose_next_runnable_without_existing_worktree(
            root, selection
        )
        for issue_number, wt_path in skipped:
            print(f"Skipping issue #{issue_number}: existing linked worktree at {wt_path}")
        issue = queue_item.issue

    if (not allow_blocked) and queue_item and not queue_item.runnable:
        raise common.CliError(
            f"Issue #{issue.number} is blocked: {'; '.join(queue_item.blocked_reasons)}"
        )

    base_dir_path = (
        Path(base_dir).expanduser().resolve() if base_dir else worktree.default_worktrees_dir(root)
    )
    auto_claim = not no_claim

    wt_path = worktree.create_worktree_for_issue(
        root=root,
        repo=repo,
        issue=issue,
        base_dir=base_dir_path,
        base_ref=base_ref,
        scope=scope,
        slug=slug,
        folder_name=name,
        auto_claim=auto_claim,
        preflight=(not no_preflight),
        dry_run=dry_run,
        pre_provision=bool(pre_provision),
    )
    if open_shell and not dry_run:
        worktree.record_issue_handoff_event(
            root=root,
            repo=repo,
            issue=issue,
            branch=(
                f"wt/{scope or worktree.infer_scope(issue)}/"
                f"{issue.number}-{slug or worktree.slugify_text(issue.title)}"
            ),
            worktree_path=wt_path,
            event_type="shell-opened",
            state="shell-active",
            details={"source": "worktree-next"},
            idempotency_key=f"shell:{issue.number}:{wt_path}",
        )
        worktree.open_shell(wt_path)
        return 0

    launch_wants = common.wants_agent_launch(
        agent=agent,
        agent_mode=agent_mode,
        review_agent=review_agent,
        review_agent_mode=review_agent_mode,
        handoff=handoff,
        print_only=print_only,
        tmux=tmux,
        zellij=zellij,
        no_mux=no_mux,
    )
    if launch_wants and not dry_run:
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
        )
        mux_resolved = common.resolve_mux_flag(no_mux=no_mux, tmux=tmux, zellij=zellij)
        common.handoff_to_agent_or_shell(
            path=wt_path,
            root=root,
            repo=repo,
            agent=agent_resolved,
            agent_mode=mode_resolved,
            review_agent=review_agent,
            review_agent_mode=review_agent_mode,
            handoff=handoff_resolved,
            print_only_override=print_only,
            mux=mux_resolved,
        )
    return 0


def cmd_worktree_create(
    issue_number: int,
    repo: str | None = None,
    mode: Literal["auto", "ready", "open-task"] = "auto",
    stream_label: str | None = None,
    from_issue: int | None = None,
    from_seq: int | None = None,
    base_dir: str | None = None,
    base_ref: str | None = None,
    scope: str | None = None,
    slug: str | None = None,
    name: str | None = None,
    no_claim: bool = False,
    no_preflight: bool = False,
    dry_run: bool = False,
    pre_provision: bool = False,
    open_shell: bool = False,
    allow_blocked: bool = False,
    agent: str | None = None,
    agent_mode: str | None = None,
    review_agent: str | None = None,
    review_agent_mode: str | None = None,
    handoff: str | None = None,
    print_only: bool = False,
    tmux: bool | None = None,
    zellij: bool | None = None,
    no_mux: bool = False,
    mux: bool = False,
) -> int:
    root = git_utils.repo_root()
    repo = repo or git_utils.origin_repo_slug(root)
    issues = issue_queue.fetch_repo_issues(root, repo, state="all")
    issue = worktree.issue_by_number(issues, issue_number)
    existing_wt = worktree.find_linked_worktree_for_issue(root, issue.number)
    if existing_wt is not None:
        print(f"Issue #{issue.number} already has linked worktree: {existing_wt.path}")
        gitnexus.prepare_gitnexus_for_worktree(existing_wt.path)
        worktree.record_issue_handoff_event(
            root=root,
            repo=repo,
            issue=issue,
            branch=existing_wt.branch,
            worktree_path=existing_wt.path,
            event_type="worktree-reused",
            state="worktree-ready",
            details={"source": "worktree-create"},
            idempotency_key=f"reuse:{issue.number}:{existing_wt.branch}:{existing_wt.path}",
        )
        if open_shell and not dry_run:
            if not no_preflight:
                worktree.run_preflight(path=existing_wt.path, root=root, repo=repo)
            worktree.record_issue_handoff_event(
                root=root,
                repo=repo,
                issue=issue,
                branch=existing_wt.branch,
                worktree_path=existing_wt.path,
                event_type="shell-opened",
                state="shell-active",
                details={"source": "worktree-create"},
                idempotency_key=f"shell:{issue.number}:{existing_wt.path}",
            )
            worktree.open_shell(existing_wt.path)
            return 0

        launch_wants = common.wants_agent_launch(
            agent=agent,
            agent_mode=agent_mode,
            review_agent=review_agent,
            review_agent_mode=review_agent_mode,
            handoff=handoff,
            print_only=print_only,
            tmux=tmux,
            zellij=zellij,
            no_mux=no_mux,
            mux=mux,
        )
        if launch_wants and not dry_run:
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
                mux=mux,
            )
            mux_resolved = common.resolve_mux_flag(no_mux=no_mux, tmux=tmux, zellij=zellij, mux=mux)
            common.handoff_to_agent_or_shell(
                path=existing_wt.path,
                root=root,
                repo=repo,
                agent=agent_resolved,
                agent_mode=mode_resolved,
                review_agent=review_agent,
                review_agent_mode=review_agent_mode,
                handoff=handoff_resolved,
                print_only_override=print_only,
                mux=mux_resolved,
            )
        return 0
    logic.assert_issue_startable(issue, allow_blocked=allow_blocked)
    selection = issue_queue.build_queue(
        issues,
        stream_label=stream_label,
        from_issue=from_issue,
        from_seq=from_seq,
        mode=mode,
    )
    item = next((x for x in selection.items if x.issue.number == issue.number), None)
    if item and (not item.runnable) and not allow_blocked:
        raise common.CliError(
            f"Issue #{issue.number} is blocked: {'; '.join(item.blocked_reasons)}"
        )
    base_dir_path = (
        Path(base_dir).expanduser().resolve() if base_dir else worktree.default_worktrees_dir(root)
    )
    auto_claim = not no_claim

    wt_path = worktree.create_worktree_for_issue(
        root=root,
        repo=repo,
        issue=issue,
        base_dir=base_dir_path,
        base_ref=base_ref,
        scope=scope,
        slug=slug,
        folder_name=name,
        auto_claim=auto_claim,
        preflight=(not no_preflight),
        dry_run=dry_run,
        pre_provision=bool(pre_provision),
    )
    if open_shell and not dry_run:
        branch = (
            f"wt/{scope or worktree.infer_scope(issue)}/"
            f"{issue.number}-{slug or worktree.slugify_text(issue.title)}"
        )
        worktree.record_issue_handoff_event(
            root=root,
            repo=repo,
            issue=issue,
            branch=branch,
            worktree_path=wt_path,
            event_type="shell-opened",
            state="shell-active",
            details={"source": "worktree-create"},
            idempotency_key=f"shell:{issue.number}:{wt_path}",
        )
        worktree.open_shell(wt_path)
        return 0

    launch_wants = common.wants_agent_launch(
        agent=agent,
        agent_mode=agent_mode,
        review_agent=review_agent,
        review_agent_mode=review_agent_mode,
        handoff=handoff,
        print_only=print_only,
        tmux=tmux,
        zellij=zellij,
        no_mux=no_mux,
        mux=mux,
    )
    if launch_wants and not dry_run:
        branch = (
            f"wt/{scope or worktree.infer_scope(issue)}/"
            f"{issue.number}-{slug or worktree.slugify_text(issue.title)}"
        )
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
            mux=mux,
        )
        mux_resolved = common.resolve_mux_flag(no_mux=no_mux, tmux=tmux, zellij=zellij, mux=mux)
        common.handoff_to_agent_or_shell(
            path=wt_path,
            root=root,
            repo=repo,
            agent=agent_resolved,
            agent_mode=mode_resolved,
            review_agent=review_agent,
            review_agent_mode=review_agent_mode,
            handoff=handoff_resolved,
            print_only_override=print_only,
            mux=mux_resolved,
        )
    return 0


def cmd_worktree_resume(
    path: str | None = None,
    no_preflight: bool = False,
    open_shell: bool = False,
    command: str | None = None,
    agent: str | None = None,
    agent_mode: str | None = None,
    review_agent: str | None = None,
    review_agent_mode: str | None = None,
    handoff: str | None = None,
    print_only: bool = False,
    tmux: bool | None = None,
    zellij: bool | None = None,
    no_mux: bool = False,
    mux: bool = False,
) -> int:
    root = git_utils.repo_root()
    worktrees = worktree.list_resume_candidates(root)
    if not worktrees:
        print("No linked worktrees found.")
        return 0
    if path:
        target = next((wt for wt in worktrees if str(wt.path) == str(Path(path).resolve())), None)
        if target is None:
            raise common.CliError(f"Worktree not found: {path}")
    else:
        target = worktree.select_worktree_interactive(worktrees)
    if not no_preflight:
        try:
            repo = git_utils.origin_repo_slug(root)
        except common.CliError:
            repo = None
        worktree.run_preflight(path=target.path, root=root, repo=repo)
    else:
        try:
            repo = git_utils.origin_repo_slug(root)
        except common.CliError:
            repo = None
    gitnexus.prepare_gitnexus_for_worktree(target.path)
    issue_id = worktree.extract_issue_id_from_branch(target.branch)
    worktree.record_issue_handoff_event(
        root=root,
        repo=repo,
        issue_number=issue_id,
        issue_title=target.branch,
        branch=target.branch,
        worktree_path=target.path,
        event_type="worktree-resumed",
        state="worktree-ready",
        details={"source": "worktree-resume"},
        idempotency_key=f"resume:{issue_id}:{target.branch}:{target.path}",
    )
    if command:
        common.run_command_in_worktree(target.path, command)
    elif open_shell:
        worktree.record_issue_handoff_event(
            root=root,
            repo=repo,
            issue_number=issue_id,
            issue_title=target.branch,
            branch=target.branch,
            worktree_path=target.path,
            event_type="shell-opened",
            state="shell-active",
            details={"source": "worktree-resume"},
            idempotency_key=f"shell:{issue_id}:{target.path}",
        )
        worktree.open_shell(target.path)
    else:
        launch_wants = common.wants_agent_launch(
            agent=agent,
            agent_mode=agent_mode,
            review_agent=review_agent,
            review_agent_mode=review_agent_mode,
            handoff=handoff,
            print_only=print_only,
            tmux=tmux,
            zellij=zellij,
            no_mux=no_mux,
            mux=mux,
        )
        if launch_wants:
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
                mux=mux,
            )
            mux_resolved = common.resolve_mux_flag(no_mux=no_mux, tmux=tmux, zellij=zellij, mux=mux)
            common.handoff_to_agent_or_shell(
                path=target.path,
                root=root,
                repo=repo,
                agent=agent_resolved,
                agent_mode=mode_resolved,
                review_agent=review_agent,
                review_agent_mode=review_agent_mode,
                handoff=handoff_resolved,
                print_only_override=print_only,
                mux=mux_resolved,
            )
        else:
            print(target.path)
            print(f"branch={target.branch}")
    return 0


def cmd_push_branch(path: str | None = None, dry_run: bool = False) -> int:
    root = git_utils.repo_root()
    common.push_branch_enforced(
        root,
        path=Path(path).resolve() if path else None,
        dry_run=dry_run,
    )
    return 0


def cmd_preflight(repo: str | None = None, path: str | None = None) -> int:
    root = git_utils.repo_root()
    repo_slug = repo
    try:
        repo_slug = repo_slug or git_utils.origin_repo_slug(root)
    except common.CliError:
        from scripts.issue_tool import shared

        if shared.parse_bool_env("ENFORCE_TRACKER_ISSUE_LOOKUP", True):
            raise
    worktree.run_preflight(
        path=Path(path).resolve() if path else git_utils.current_path(),
        root=root,
        repo=repo_slug,
    )
    return 0


def cmd_pre_validate(path: str | None = None, dry_run: bool = False) -> int:
    target = Path(path).resolve() if path else git_utils.current_path()
    if dry_run:
        print(f"Would run in {target}: make validate-pre-push")
        return 0
    common.run_pre_validate(target)
    return 0


def cmd_gitnexus_refresh(path: str | None = None) -> int:
    target = Path(path).resolve() if path else git_utils.current_path()
    gitnexus.prepare_gitnexus_for_worktree(target)
    return 0
