from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ._support import _issue, worktree_issues


def test_create_worktree_for_issue_attaches_existing_local_branch(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    base_dir = tmp_path / "worktrees"
    issue = _issue(
        number=25,
        task_id="TASK-018",
        seq=180,
        labels=["type:task", "status:not-started"],
    )
    executed: list[list[str]] = []

    def _run(cmd, **_kwargs):
        executed.append(cmd)
        if cmd[:3] == ["git", "show-ref", "--verify"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:3] == ["git", "worktree", "add"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(worktree_issues, "run", _run)
    monkeypatch.setattr(worktree_issues, "ensure_uv_venv", lambda _path: None)
    monkeypatch.setattr(worktree_issues, "prepare_gitnexus_for_worktree", lambda _path: None)

    wt_path = worktree_issues.create_worktree_for_issue(
        root=root,
        repo="owner/repo",
        issue=issue,
        base_dir=base_dir,
        base_ref=None,
        scope="task",
        slug="write-src-bridge-handler-py",
        folder_name="wt25",
        auto_claim=False,
        preflight=False,
        dry_run=False,
    )

    assert wt_path == (base_dir / "wt25").resolve()
    assert [
        "git",
        "worktree",
        "add",
        str(wt_path),
        "wt/task/25-write-src-bridge-handler-py",
    ] in executed


def test_create_worktree_for_issue_can_start_background_pre_provision(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    base_dir = tmp_path / "worktrees"
    issue = _issue(
        number=45,
        task_id="TASK-045",
        seq=450,
        labels=["type:task", "status:not-started"],
    )
    started: list[Path] = []

    def _run(cmd, **_kwargs):
        if cmd[:3] == ["git", "show-ref", "--verify"]:
            return subprocess.CompletedProcess(cmd, 1, "", "")
        if cmd[:3] == ["git", "worktree", "add"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(worktree_issues, "run", _run)
    monkeypatch.setattr(worktree_issues, "ensure_uv_venv", lambda _path: None)
    monkeypatch.setattr(worktree_issues, "prepare_gitnexus_for_worktree", lambda _path: None)
    monkeypatch.setattr(
        worktree_issues, "start_worktree_pre_provision", lambda path: started.append(path)
    )

    wt_path = worktree_issues.create_worktree_for_issue(
        root=root,
        repo="owner/repo",
        issue=issue,
        base_dir=base_dir,
        base_ref="origin/main",
        scope="task",
        slug="pre-provision",
        folder_name="wt45",
        auto_claim=False,
        preflight=False,
        dry_run=False,
        pre_provision=True,
    )

    assert started == [wt_path]


def test_start_worktree_pre_provision_launches_dependency_install(monkeypatch, tmp_path):
    calls: list[dict[str, object]] = []

    class FakePopen:
        pid = 4242

        def __init__(self, cmd, **kwargs):
            calls.append({"cmd": cmd, **kwargs})

    monkeypatch.setattr(worktree_issues.subprocess, "Popen", FakePopen)

    worktree_issues.start_worktree_pre_provision(tmp_path)

    call = calls[0]
    assert call["cmd"][:2] == ["bash", "-lc"]
    script = call["cmd"][2]
    assert "uv sync" in script
    assert "npm install --prefix infra/cdk" in script
    assert "npm install --prefix spa" in script
    assert (tmp_path / ".build" / "worktree-provision" / "pid").read_text(
        encoding="utf-8"
    ) == "4242"


def test_handoff_waits_for_in_progress_pre_provision(monkeypatch, tmp_path):
    waited: list[Path] = []
    prompted: list[Path] = []

    monkeypatch.setattr(
        worktree_issues, "await_worktree_ready_if_provisioning", lambda path: waited.append(path)
    )
    monkeypatch.setattr(worktree_issues, "ensure_uv_venv", lambda _path: None)
    monkeypatch.setattr(worktree_issues, "choose_agent_interactive", lambda: "codex")
    monkeypatch.setattr(worktree_issues, "choose_agent_mode_interactive", lambda: "normal")
    monkeypatch.setattr(worktree_issues, "choose_handoff_action_interactive", lambda: "print-only")
    monkeypatch.setattr(
        worktree_issues,
        "build_agent_prompt_for_worktree",
        lambda path, *_args: prompted.append(path) or "prompt",
    )
    monkeypatch.setattr(worktree_issues, "build_agent_command", lambda *_args: "codex prompt")

    worktree_issues.handoff_to_agent_or_shell(path=tmp_path, root=tmp_path, repo="owner/repo")

    assert waited == [tmp_path]
    assert prompted == [tmp_path]


def test_await_worktree_ready_raises_when_background_provision_failed(tmp_path):
    provision_dir = tmp_path / ".build" / "worktree-provision"
    provision_dir.mkdir(parents=True)
    (provision_dir / "pid").write_text("99999", encoding="utf-8")
    (provision_dir / "failed").write_text("", encoding="utf-8")

    with pytest.raises(worktree_issues.CliError, match="pre-provisioning failed"):
        worktree_issues.await_worktree_ready_if_provisioning(tmp_path)


def test_build_agent_prompt_for_worktree_includes_explicit_dod_and_conflict_requirements(
    monkeypatch, tmp_path
):
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    wt = tmp_path / "worktrees" / "wt53"
    wt.mkdir(parents=True, exist_ok=True)

    def _run_prompt(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, "wt/infra/53-explicit-dod\n", "")

    monkeypatch.setattr(worktree_issues, "run", _run_prompt)
    monkeypatch.setattr(worktree_issues, "worktree_issue_id", lambda _path: 53)
    monkeypatch.setattr(
        worktree_issues, "fetch_issue_labels_for_prompt", lambda _root, _repo, _issue: "type:task"
    )

    prompt = worktree_issues.build_agent_prompt_for_worktree(wt, root, "owner/repo")

    assert "Context: GitLab issue #53;" in prompt
    assert "project owner/repo;" in prompt
    assert "branch wt/infra/53-explicit-dod;" in prompt
    assert f"worktree {wt};" in prompt
    assert "labels type:task." in prompt
    assert "Read: CLAUDE.md; docs/ARCHITECTURE.md;" in prompt
    assert "CLAUDE.md" in prompt
    assert "docs/ARCHITECTURE.md" in prompt
    assert "Operating mode: you are the implementation owner for this issue worktree." in prompt
    assert (
        "ask only for destructive actions, production access, or policy/security decisions"
        in prompt
    )
    assert "Scope: only this issue. Do not broaden scope" in prompt
    assert "Do not broaden scope, bundle opportunistic cleanup" in prompt
    assert "First step: inspect the current branch diff, linked GitLab issue" in prompt
    assert "issue labels, dependencies, relevant ADRs/docs" in prompt
    assert "Context lookup: prefer GitNexus for unfamiliar flows" in prompt
    assert "context/impact before editing shared symbols" in prompt
    assert "detect_changes before commit" in prompt
    assert "fall back to rg, git diff/log, and direct file reads" in prompt
    assert "Execution loop: inspect; form the smallest defensible plan" in prompt
    assert "run make preflight-session and make pre-validate-session before push" in prompt
    assert "Change shape: keep diffs small and reversible" in prompt
    assert "make preflight-session" in prompt
    assert "Do not stop at: MR creation, one passing test, a local commit" in prompt
    assert "Review gate: before claiming completion, run a senior-engineer review pass" in prompt
    assert "If a second agent is available, use it for that review" in prompt
    assert "Completion sequence: push through make worktree-push-issue" in prompt
    assert "merge to the target branch; close and normalize the issue" in prompt
    assert "then run make finish-worktree-close" in prompt
    assert "do not treat worktree or branch deletion as semantic completion" in prompt
    assert "Pause only if:" in prompt
    assert "repo rules mandate escalation" in prompt
    assert "required credentials or permissions are missing" in prompt
    assert "report blockers with the exact failed command" in prompt


def test_build_agent_prompt_for_non_issue_branch_warns_against_mainline_implementation(
    monkeypatch,
):
    root = Path("/tmp/repo")
    wt = Path("/tmp/repo")

    monkeypatch.setattr(
        worktree_issues,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "main\n", ""),
    )
    monkeypatch.setattr(worktree_issues, "worktree_issue_id", lambda _path: None)
    monkeypatch.setattr(worktree_issues, "fetch_issue_labels_for_prompt", lambda *_args: "")

    prompt = worktree_issues.build_agent_prompt_for_worktree(wt, root, "owner/repo")

    assert "Context: no linked GitLab issue;" in prompt
    assert "branch main;" in prompt
    assert "Worktree policy: this path is not an issue worktree branch." in prompt
    assert "Do not start new implementation from main" in prompt


def test_build_review_prompt_for_worktree_sets_reviewer_role(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    wt = tmp_path / "worktrees" / "wt53"
    wt.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        worktree_issues,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, "wt/infra/53-explicit-dod\n", ""
        ),
    )
    monkeypatch.setattr(worktree_issues, "worktree_issue_id", lambda _path: 53)
    monkeypatch.setattr(
        worktree_issues, "fetch_issue_labels_for_prompt", lambda _root, _repo, _issue: "type:task"
    )

    prompt = worktree_issues.build_review_prompt_for_worktree(
        wt,
        root,
        "owner/repo",
        implementation_agent="codex",
    )

    assert "Context: reviewer lane for GitLab issue #53;" in prompt
    assert "implementation agent codex." in prompt
    assert "Role: reviewer only." in prompt
    assert "Review focus: bugs, behavioral regressions" in prompt
    assert "Output: report concrete findings first" in prompt


def test_auto_detect_mux_prefers_tmux_over_zellij(monkeypatch):
    monkeypatch.setattr(worktree_issues, "tmux_available", lambda: True)
    monkeypatch.setattr(worktree_issues, "zellij_available", lambda: True)

    assert worktree_issues.auto_detect_mux() == "tmux"
