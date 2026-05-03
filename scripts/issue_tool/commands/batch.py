from __future__ import annotations

import argparse
import contextlib
import io
import random
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from scripts.issue_tool import git_utils, gitnexus, issue_queue, multiplexer, shared, worktree
from scripts.issue_tool.commands import common
from scripts.issue_tool.models import (
    BatchLaunchResult,
    Issue,
    QueueItem,
    QueueSelection,
    WorktreeInfo,
)


def cmd_wt_batch(
    repo: str | None = None,
    mode: Literal["auto", "ready", "open-task"] = "auto",
    stream_label: str | None = None,
    from_issue: int | None = None,
    from_seq: int | None = None,
    base_dir: str | None = None,
    count: int = 1,
    agents_list: str = "gemini",
    agent_mode: str = "yolo",
    dry_run: bool = False,
    interactive: bool = False,
) -> int:
    """Create N worktrees and start detached or interactive agent runs."""

    raw_agents = agents_list.split(",") if agents_list else ["gemini"]
    agents = [agent.strip() for agent in raw_agents if agent.strip()]
    mode_val = agent_mode or "yolo"
    if not interactive:
        unsupported = sorted(agent for agent in agents if not common.agent_supports_detached(agent))
        if unsupported:
            names = ", ".join(unsupported)
            raise common.CliError(
                f"Detached wt-batch does not support agent(s): {names}. "
                "Use INTERACTIVE=1 or choose a detached-capable agent pool."
            )

    root = git_utils.repo_root()
    repo_slug = repo or git_utils.origin_repo_slug(root)
    issues = issue_queue.fetch_repo_issues(root, repo_slug, state="all")
    selection = issue_queue.build_queue(
        issues,
        stream_label=stream_label,
        from_issue=from_issue,
        from_seq=from_seq,
        mode=mode,
    )
    base_dir_path = (
        Path(base_dir).expanduser().resolve() if base_dir else worktree.default_worktrees_dir(root)
    )

    picked: list[tuple[QueueItem, Path | None]] = []
    for item in selection.items:
        if len(picked) >= count:
            break
        if not item.runnable:
            continue
        existing = worktree.find_linked_worktree_for_issue(root, item.issue.number)
        if existing is not None and worktree.worktree_agent_running(existing.path):
            print(f"Skipping #{item.issue.number}: agent already running in {existing.path}")
            continue
        picked.append((item, existing.path if existing is not None else None))

    if not picked:
        raise common.CliError("No runnable issues available for batch creation.")
    if len(picked) < count:
        print(f"WARNING: only {len(picked)} runnable issue(s) available (requested {count})")

    run_id = common.batch_run_id()
    run_dir_path = common.batch_run_dir(root, run_id)
    run_dir_path.mkdir(parents=True, exist_ok=True)
    manifest_entries: list[dict[str, object]] = []
    launch_results: list[BatchLaunchResult] = []
    interactive_launches: list[tuple[str, Path, str]] = []
    total = len(picked)

    print(f"Batch run: {total} issue(s)")
    print(f"Run id:   {run_id}")
    print(f"Manifest: {common.batch_manifest_path(root, run_id)}")
    for idx, (item, existing_path) in enumerate(picked, start=1):
        agent = random.choice(agents)
        issue = item.issue

        print(f"[{idx}/{total}] #{issue.number} -> starting ({agent})")
        if existing_path is not None:
            wt_path = existing_path
        else:
            with contextlib.redirect_stdout(io.StringIO()):
                wt_path = worktree.create_worktree_for_issue(
                    root=root,
                    repo=repo_slug,
                    issue=issue,
                    base_dir=base_dir_path,
                    base_ref=None,
                    scope=None,
                    slug=None,
                    folder_name=None,
                    auto_claim=True,
                    preflight=False,
                    dry_run=dry_run,
                    pre_provision=False,
                )

        if dry_run:
            print(f"[{idx}/{total}] #{issue.number} -> dry-run {wt_path}")
            manifest_entries.append(
                {
                    "issue_number": issue.number,
                    "agent": agent,
                    "worktree_path": str(wt_path),
                    "state": "dry-run",
                    "generated_at": datetime.now(UTC).isoformat(),
                }
            )
            continue

        with contextlib.redirect_stdout(io.StringIO()):
            gitnexus.prepare_gitnexus_for_worktree(wt_path)
        prompt = common.build_agent_prompt_for_worktree(wt_path, root, repo_slug)
        command = common.build_agent_command(agent, mode_val, prompt)
        branch = git_utils.run(["git", "branch", "--show-current"], cwd=wt_path).stdout.strip()
        if interactive:
            session = "pending"
            window_name = f"wt{issue.number}"
            result = common.record_tmux_agent_launch(
                root=root,
                run_id=run_id,
                issue_number=issue.number,
                path=wt_path,
                branch=branch,
                agent=agent,
                command=command,
                session_name=session,
                window_name=window_name,
            )
            interactive_launches.append((window_name, wt_path, command))
        else:
            result = common.launch_agent_detached(
                root=root,
                run_id=run_id,
                issue_number=item.issue.number,
                path=wt_path,
                branch=branch,
                agent=agent,
                command=command,
            )
        launch_results.append(result)
        common.write_batch_entry(root, run_id, result)
        manifest_entries.append(
            {
                "issue_number": result.issue_number,
                "agent": result.agent,
                "worktree_path": str(result.worktree_path),
                "branch": result.branch,
                "state": result.state,
                "pid": result.pid,
                "backend": result.backend,
                "session_name": result.session_name,
                "window_name": result.window_name,
                "local_status_path": (
                    str(result.local_status_path) if result.local_status_path else None
                ),
                "stdout_log_path": str(result.stdout_log_path) if result.stdout_log_path else None,
                "stderr_log_path": str(result.stderr_log_path) if result.stderr_log_path else None,
                "detail": result.detail,
                "generated_at": datetime.now(UTC).isoformat(),
            }
        )
        pid_note = f" pid={result.pid}" if result.pid is not None else ""
        print(f"[{idx}/{total}] #{issue.number} -> {result.state}{pid_note} {wt_path}")

    manifest_payload = {
        "run_id": run_id,
        "repo": repo_slug,
        "count_requested": count,
        "count_selected": total,
        "agent_pool": agents,
        "agent_mode": mode_val,
        "state": "dry-run" if dry_run else "started",
        "generated_at": datetime.now(UTC).isoformat(),
        "entries": manifest_entries,
    }
    shared.write_json_file(common.batch_manifest_path(root, run_id), manifest_payload)

    if not dry_run:
        started = sum(1 for item in launch_results if item.state in {"running", "interactive"})
        failed = sum(1 for item in launch_results if item.state not in {"running", "interactive"})
        print()
        print("Run summary:")
        print(f"  started:  {started}")
        print(f"  failed:   {failed}")
        print(f"  manifest: {common.batch_manifest_path(root, run_id)}")
        if interactive:
            if not multiplexer.tmux_available():
                raise common.CliError("wt-batch --interactive requires tmux")
            session = multiplexer.worktree_session_pair("wt-batch")
            print(f"  interactive: tmux session {session.session_name}")
            for result in launch_results:
                result.session_name = session.session_name
                if result.local_status_path:
                    payload = shared.read_json_file(result.local_status_path) or {}
                    payload["session_name"] = session.session_name
                    payload["updated_at"] = datetime.now(UTC).isoformat()
                    shared.write_json_file(result.local_status_path, payload)
                common.write_batch_entry(root, run_id, result)
            multiplexer.launch_tmux_batch_session(
                session_name=session.session_name,
                launches=interactive_launches,
                attach=True,
                announce_windows=True,
            )

    return 0
