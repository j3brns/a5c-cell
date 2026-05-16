from pathlib import Path
from typing import Literal, Optional

import typer

from scripts.issue_tool.commands.agent import (
    cmd_agent_handoff,
)
from scripts.issue_tool.commands.batch import (
    cmd_wt_batch,
)
from scripts.issue_tool.commands.finish import (
    cmd_finish_close,
    cmd_finish_summary,
)
from scripts.issue_tool.commands.issue import (
    cmd_issue_create,
    cmd_issue_evidence,
    cmd_issue_queue,
    cmd_issue_repair_stale_locks,
    cmd_issue_status,
    cmd_issues_audit,
    cmd_issues_reconcile,
    cmd_write_validation_receipt,
)
from scripts.issue_tool.commands.menu import (
    cmd_menu,
)
from scripts.issue_tool.commands.worktree import (
    cmd_gitnexus_refresh,
    cmd_pre_validate,
    cmd_preflight,
    cmd_push_branch,
    cmd_worktree_create,
    cmd_worktree_next,
    cmd_worktree_resume,
)

# Create the main Typer app
app = typer.Typer(
    name="issue-tool",
    help="Issue-driven worktree workflow for AgentCore AaS.",
    add_completion=False,
    no_args_is_help=True,
)


@app.command("issue-queue")
def issue_queue(
    repo: str | None = typer.Option(None, help="GitLab project path"),
    mode: Literal["auto", "ready", "open-task"] = typer.Option("auto", help="Queue source mode"),
    stream_label: str | None = typer.Option(None, help="Optional label filter"),
    from_issue: int | None = typer.Option(None, help="Lower bound issue number"),
    from_seq: int | None = typer.Option(None, help="Lower bound Seq metadata"),
    limit: int | None = typer.Option(None, help="Limit displayed items"),
    runnable_only: bool = typer.Option(False, help="Show only runnable items"),
    json: bool = typer.Option(False, help="Emit JSON payload"),
):
    """Show issue queue"""
    raise typer.Exit(
        code=cmd_issue_queue(
            repo=repo,
            mode=mode,
            stream_label=stream_label,
            from_issue=from_issue,
            from_seq=from_seq,
            limit=limit,
            runnable_only=runnable_only,
            json_output=json,
        )
    )


@app.command("issue-create")
def issue_create(
    title: str = typer.Option(..., help="Issue title, must start with TASK-###:"),
    seq: int = typer.Option(..., help="Queue sequence number"),
    repo: str | None = typer.Option(None, help="GitLab project path"),
    depends: str = typer.Option("none", help="Dependency list"),
    problem: str = typer.Option("", help="Optional initial problem statement"),
    ready: bool = typer.Option(False, help="Add ready label after creation"),
):
    """Create a canonical GitLab task issue"""
    raise typer.Exit(
        code=cmd_issue_create(
            title=title, seq=seq, repo=repo, depends=depends, problem=problem, ready=ready
        )
    )


@app.command("issue-evidence")
def issue_evidence(
    repo: str | None = typer.Option(None, help="GitLab project path"),
    issue: int | None = typer.Option(None, help="Issue number"),
    path: str | None = typer.Option(None, help="Path to infer issue from"),
    json: bool = typer.Option(False, help="Emit JSON output"),
):
    """Show local linked-worktree and .build evidence for an issue"""
    raise typer.Exit(code=cmd_issue_evidence(repo=repo, issue=issue, path=path, json_output=json))


@app.command("issue-status")
def issue_status(
    repo: str | None = typer.Option(None, help="GitLab project path"),
    issue: int | None = typer.Option(None, help="Show one issue number"),
    all: bool = typer.Option(False, help="Include all known task issues"),
    json: bool = typer.Option(False, help="Emit JSON output"),
):
    """Show joined issue/worktree/agent launch status"""
    raise typer.Exit(
        code=cmd_issue_status(repo=repo, issue=issue, include_all=all, json_output=json)
    )


@app.command("write-validation-receipt")
def write_validation_receipt(
    repo: str | None = typer.Option(None, help="GitLab project path"),
    issue: int | None = typer.Option(None, help="Issue number"),
    path: str | None = typer.Option(None, help="Path to infer issue from"),
    check: str = typer.Option("validate-pre-push", help="Validation check name"),
):
    """Write a local validation receipt for the current issue worktree"""
    raise typer.Exit(
        code=cmd_write_validation_receipt(repo=repo, issue=issue, path=path, check=check)
    )


@app.command("issues-audit")
def issues_audit(
    repo: str | None = typer.Option(None, help="GitLab project path"),
    json: bool = typer.Option(False, help="Emit JSON output"),
):
    """Audit issue lifecycle/queue invariants"""
    raise typer.Exit(code=cmd_issues_audit(repo=repo, json_output=json))


@app.command("issues-reconcile")
def issues_reconcile(
    repo: str | None = typer.Option(None, help="GitLab project path"),
    dry_run: bool = typer.Option(False, help="Show changes without editing"),
):
    """Reconcile task issue labels to lifecycle rules"""
    raise typer.Exit(code=cmd_issues_reconcile(repo=repo, dry_run=dry_run))


@app.command("issue-repair-stale-locks")
def issue_repair_stale_locks(
    repo: str | None = typer.Option(None, help="GitLab project path"),
    apply: bool = typer.Option(False, help="Apply repairs"),
    ready: bool = typer.Option(False, help="Add ready when resetting"),
):
    """Repair in-progress task issues with no linked worktree or open MR"""
    raise typer.Exit(code=cmd_issue_repair_stale_locks(repo=repo, apply=apply, ready=ready))


@app.command("preflight")
def preflight(
    repo: str | None = typer.Option(None, help="GitLab project path"),
    path: str | None = typer.Option(None, help="Path to check"),
):
    """Run session preflight checks"""
    raise typer.Exit(code=cmd_preflight(repo=repo, path=path))


@app.command("pre-validate")
def pre_validate(
    path: str | None = typer.Option(None, help="Worktree path"),
    dry_run: bool = typer.Option(False, help="Print command without running"),
):
    """Run pre-push validation"""
    raise typer.Exit(code=cmd_pre_validate(path=path, dry_run=dry_run))


@app.command("gitnexus-refresh")
def gitnexus_refresh(
    path: str | None = typer.Option(None, help="Worktree path"),
):
    """Refresh local GitNexus index for a worktree"""
    raise typer.Exit(code=cmd_gitnexus_refresh(path=path))


@app.command("worktree-next")
def worktree_next(
    repo: str | None = typer.Option(None),
    mode: Literal["auto", "ready", "open-task"] = typer.Option("auto"),
    stream_label: str | None = typer.Option(None),
    from_issue: int | None = typer.Option(None),
    from_seq: int | None = typer.Option(None),
    base_dir: str | None = typer.Option(None),
    base_ref: str | None = typer.Option(None),
    scope: str | None = typer.Option(None),
    slug: str | None = typer.Option(None),
    name: str | None = typer.Option(None),
    no_claim: bool = typer.Option(False),
    no_preflight: bool = typer.Option(False),
    dry_run: bool = typer.Option(False),
    pre_provision: bool = typer.Option(False),
    open_shell: bool = typer.Option(False),
    allow_blocked: bool = typer.Option(False),
    agent: str | None = typer.Option(None),
    agent_mode: str | None = typer.Option(None),
    review_agent: str | None = typer.Option(None),
    review_agent_mode: str | None = typer.Option(None),
    handoff: str | None = typer.Option(None),
    print_only: bool = typer.Option(False),
    tmux: bool | None = typer.Option(None),
    zellij: bool | None = typer.Option(None),
    no_mux: bool = typer.Option(False),
    mux: bool = typer.Option(False),
    choose: bool = typer.Option(False),
):
    """Create worktree for next runnable queued issue"""
    raise typer.Exit(
        code=cmd_worktree_next(
            repo=repo,
            mode=mode,
            stream_label=stream_label,
            from_issue=from_issue,
            from_seq=from_seq,
            base_dir=base_dir,
            base_ref=base_ref,
            scope=scope,
            slug=slug,
            name=name,
            no_claim=no_claim,
            no_preflight=no_preflight,
            dry_run=dry_run,
            pre_provision=pre_provision,
            open_shell=open_shell,
            allow_blocked=allow_blocked,
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
            choose=choose,
        )
    )


@app.command("worktree-create")
def worktree_create(
    issue: int = typer.Option(..., help="Issue number"),
    repo: str | None = typer.Option(None),
    mode: Literal["auto", "ready", "open-task"] = typer.Option("auto"),
    stream_label: str | None = typer.Option(None),
    from_issue: int | None = typer.Option(None),
    from_seq: int | None = typer.Option(None),
    base_dir: str | None = typer.Option(None),
    base_ref: str | None = typer.Option(None),
    scope: str | None = typer.Option(None),
    slug: str | None = typer.Option(None),
    name: str | None = typer.Option(None),
    no_claim: bool = typer.Option(False),
    no_preflight: bool = typer.Option(False),
    dry_run: bool = typer.Option(False),
    pre_provision: bool = typer.Option(False),
    open_shell: bool = typer.Option(False),
    allow_blocked: bool = typer.Option(False),
    agent: str | None = typer.Option(None),
    agent_mode: str | None = typer.Option(None),
    review_agent: str | None = typer.Option(None),
    review_agent_mode: str | None = typer.Option(None),
    handoff: str | None = typer.Option(None),
    print_only: bool = typer.Option(False),
    tmux: bool | None = typer.Option(None),
    zellij: bool | None = typer.Option(None),
    no_mux: bool = typer.Option(False),
    mux: bool = typer.Option(False),
):
    """Create worktree for a specific issue number"""
    raise typer.Exit(
        code=cmd_worktree_create(
            issue_number=issue,
            repo=repo,
            mode=mode,
            stream_label=stream_label,
            from_issue=from_issue,
            from_seq=from_seq,
            base_dir=base_dir,
            base_ref=base_ref,
            scope=scope,
            slug=slug,
            name=name,
            no_claim=no_claim,
            no_preflight=no_preflight,
            dry_run=dry_run,
            pre_provision=pre_provision,
            open_shell=open_shell,
            allow_blocked=allow_blocked,
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


@app.command("worktree-resume")
def worktree_resume(
    path: str | None = typer.Option(None),
    no_preflight: bool = typer.Option(False),
    open_shell: bool = typer.Option(False),
    command: str | None = typer.Option(None),
    agent: str | None = typer.Option(None),
    agent_mode: str | None = typer.Option(None),
    review_agent: str | None = typer.Option(None),
    review_agent_mode: str | None = typer.Option(None),
    handoff: str | None = typer.Option(None),
    print_only: bool = typer.Option(False),
    tmux: bool | None = typer.Option(None),
    zellij: bool | None = typer.Option(None),
    mux: bool = typer.Option(False),
):
    """Resume a linked worktree"""
    raise typer.Exit(
        code=cmd_worktree_resume(
            path=path,
            no_preflight=no_preflight,
            open_shell=open_shell,
            command=command,
            agent=agent,
            agent_mode=agent_mode,
            review_agent=review_agent,
            review_agent_mode=review_agent_mode,
            handoff=handoff,
            print_only=print_only,
            tmux=tmux,
            zellij=zellij,
            mux=mux,
        )
    )


@app.command("finish-summary")
def finish_summary(
    path: str | None = typer.Option(None),
):
    """Show finish/handoff summary for a worktree"""
    raise typer.Exit(code=cmd_finish_summary(path=path))


@app.command("finish-close")
def finish_close(
    path: str | None = typer.Option(None),
    force: bool = typer.Option(False),
    json: bool = typer.Option(False),
):
    """Close issue for worktree after merge"""
    raise typer.Exit(code=cmd_finish_close(path=path, force=force, json_output=json))


@app.command("push-branch")
def push_branch(
    path: str | None = typer.Option(None),
    dry_run: bool = typer.Option(False),
):
    """Push current worktree branch"""
    raise typer.Exit(code=cmd_push_branch(path=path, dry_run=dry_run))


@app.command("agent-handoff")
def agent_handoff(
    repo: str | None = typer.Option(None),
    path: str | None = typer.Option(None),
    agent: str | None = typer.Option(None),
    agent_mode: str | None = typer.Option(None),
    review_agent: str | None = typer.Option(None),
    review_agent_mode: str | None = typer.Option(None),
    handoff: str | None = typer.Option(None),
    print_only: bool = typer.Option(False),
    tmux: bool | None = typer.Option(None),
    zellij: bool | None = typer.Option(None),
    no_mux: bool = typer.Option(False),
    mux: bool = typer.Option(False),
):
    """Agent selection/yolo handoff for current worktree"""
    raise typer.Exit(
        code=cmd_agent_handoff(
            repo=repo,
            path=path,
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


@app.command("wt-batch")
def wt_batch(
    repo: str | None = typer.Option(None),
    base_dir: str | None = typer.Option(None),
    count: int = typer.Option(1),
    agents: str = typer.Option("gemini"),
    mode: str = typer.Option("yolo"),
    dry_run: bool = typer.Option(False),
    interactive: bool = typer.Option(False),
):
    """Create N worktrees with randomly assigned agents"""
    raise typer.Exit(
        code=cmd_wt_batch(
            repo=repo,
            base_dir=base_dir,
            count=count,
            agents_list=agents,
            agent_mode=mode,
            dry_run=dry_run,
            interactive=interactive,
        )
    )


@app.command("menu")
def menu():
    """Interactive issue worktree menu"""
    raise typer.Exit(code=cmd_menu())


if __name__ == "__main__":
    app()
