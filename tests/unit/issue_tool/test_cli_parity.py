from __future__ import annotations

from typer.testing import CliRunner

from scripts.issue_tool.main import app as typer_app

runner = CliRunner()


def test_help_parity():
    """Verify that both versions show the same set of subcommands."""
    # Typer help
    result = runner.invoke(typer_app, ["--help"])
    assert result.exit_code == 0
    typer_help = result.output

    # The subcommands we expect to see (mapping hyphens/underscores if needed)
    expected_subcommands = [
        "issue-queue",
        "issue-create",
        "issue-evidence",
        "issue-status",
        "write-validation-receipt",
        "issues-audit",
        "issues-reconcile",
        "issue-repair-stale-locks",
        "preflight",
        "pre-validate",
        "gitnexus-refresh",
        "worktree-next",
        "worktree-create",
        "worktree-resume",
        "finish-summary",
        "finish-close",
        "push-branch",
        "agent-handoff",
        "wt-batch",
        "menu",
    ]

    for cmd in expected_subcommands:
        assert cmd in typer_help


def test_queue_arguments_parity():
    """Verify typer queue arguments."""
    result = runner.invoke(typer_app, ["issue-queue", "--help"])
    assert result.exit_code == 0
    queue_help = result.output

    assert "--limit" in queue_help
    assert "--repo" in queue_help
    assert "--runnable-only" in queue_help
    assert "--mode" in queue_help
