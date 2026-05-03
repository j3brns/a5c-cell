from __future__ import annotations

import argparse
import io
import json
import os
import random
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from scripts.issue_tool import (
    evidence,
    git_utils,
    logic,
    multiplexer,
    shared,
    tracker_client,
    worktree,
)
from scripts.issue_tool.agent_launch import (
    AGENT_CAPABILITIES,
    DEFAULT_INTERACTIVE_AGENT_POOL,
    resolve_launch_request,
)
from scripts.issue_tool.constants import (
    DETACHED_STARTUP_PROBE_SECONDS,
    VALIDATION_RECEIPTS_DIR,
    WORKTREE_BRANCH_REGEX,
    WORKTREE_CLOSEOUT_DIR,
    WORKTREE_STATE_DIR,
)
from scripts.issue_tool.models import (
    AuditFinding,
    BatchLaunchResult,
    Issue,
    QueueSelection,
    WorktreeInfo,
)
from scripts.issue_tool.pre_provisioning import (
    await_worktree_ready_if_provisioning,
)
from scripts.issue_tool.shared import CliError


def print_queue(
    selection: QueueSelection, *, limit: int | None = None, show_blocked: bool = True
) -> None:
    items = selection.items if show_blocked else selection.runnable
    if limit is not None:
        items = items[: max(0, limit)]
    if not items:
        print(f"No issues in queue (source={selection.source_mode}).")
        return
    print(f"Issue queue (source={selection.source_mode}; order=Seq -> priority -> createdAt)")
    if selection.source_note:
        print(f"  note: {selection.source_note}")
    for idx, item in enumerate(items, start=1):
        issue = item.issue
        seq_text = str(issue.seq) if issue.seq is not None else "unset"
        labels = "|".join(issue.labels) if issue.labels else "-"
        status = "RUNNABLE" if item.runnable else "BLOCKED"
        print(f"{idx:>2}. #{issue.number} [{status}] Seq:{seq_text} {issue.title}")
        print(f"    labels: {labels}")
        if item.blocked_reasons:
            print(f"    why:    {'; '.join(item.blocked_reasons)}")


def worktree_issue_id(path: Path) -> int | None:
    try:
        branch = worktree.current_branch(path)
    except shared.CliError:
        return None
    return worktree.extract_issue_id_from_branch(branch)


def issue_status_rows(
    root: Path,
    repo: str | None,
    issues: list[Issue],
    *,
    issue_filter: int | None = None,
    include_all: bool = False,
) -> list[dict[str, object]]:
    issue_map = {issue.number: issue for issue in logic.queue_task_issues(issues)}
    if issue_filter is not None:
        numbers = {issue_filter}
    elif include_all:
        numbers = set(issue_map) | local_issue_numbers(root)
    else:
        active = {
            issue.number
            for issue in issue_map.values()
            if issue.state == "open"
            and (logic.lifecycle_status(issue) == "in-progress" or "ready" in issue.labels)
        }
        numbers = active | local_issue_numbers(root, active_only=True)

    rows: list[dict[str, object]] = []
    mr_status_cache: dict[str, str] = {}
    for issue_number in sorted(
        numbers,
        key=lambda number: (
            issue_map[number].seq
            if number in issue_map and issue_map[number].seq is not None
            else 10**9,
            number,
        ),
    ):
        issue = issue_map.get(issue_number)
        evidence_summary = evidence.issue_evidence_summary(root, issue_number)
        state = cast(dict[str, Any], evidence_summary.get("state") or {})
        closeout = cast(
            dict[str, Any],
            evidence_summary.get("closeout")
            if isinstance(evidence_summary.get("closeout"), dict)
            else {},
        )
        validation = cast(
            dict[str, Any],
            evidence_summary.get("validation_receipt")
            if isinstance(evidence_summary.get("validation_receipt"), dict)
            else {},
        )
        linked_worktree = evidence_summary.get("linked_worktree") or state.get("worktree_path")
        wt_path = Path(str(linked_worktree)) if linked_worktree else None
        agent_status = (
            worktree.worktree_agent_status(wt_path) if wt_path and wt_path.exists() else None
        )
        details = cast(
            dict[str, Any],
            state.get("details") if isinstance(state.get("details"), dict) else {},
        )
        agent = (
            agent_status.get("agent")
            if isinstance(agent_status, dict)
            else details.get("agent")
            if isinstance(details, dict)
            else None
        )
        backend = agent_status.get("backend") if isinstance(agent_status, dict) else None
        runtime_state = agent_status.get("state") if isinstance(agent_status, dict) else None
        session_name = agent_status.get("session_name") if isinstance(agent_status, dict) else None
        live = "-"
        if wt_path and isinstance(agent_status, dict):
            live = "yes" if worktree.worktree_agent_running(wt_path) else "no"
        validation_text = "-"
        if validation:
            validation_text = f"{validation.get('check', 'check')}:pass"
        closeout_text = "-"
        if closeout:
            stage = closeout.get("stage") or "present"
            cleanup = closeout.get("cleanup_verified")
            closeout_text = f"{stage}:{cleanup}" if cleanup is not None else str(stage)
        branch = (
            evidence_summary.get("linked_branch")
            or state.get("branch")
            or (agent_status.get("branch") if isinstance(agent_status, dict) else None)
            or "-"
        )
        branch_text = str(branch)
        mr_status = "-"
        if repo and branch_text != "-":
            if branch_text not in mr_status_cache:
                try:
                    mr_status_cache[branch_text] = format_merge_request_status(
                        merge_request_for_source_branch(root, repo, branch_text, "all")
                    )
                except shared.CliError:
                    mr_status_cache[branch_text] = "unknown"
            mr_status = mr_status_cache[branch_text]
        rows.append(
            {
                "issue": issue_number,
                "seq": issue.seq if issue is not None else None,
                "title": issue.title if issue is not None else str(state.get("issue_title") or "-"),
                "issue_status": logic.lifecycle_status(issue) if issue is not None else "-",
                "issue_state": issue.state if issue is not None else "-",
                "worktree": str(linked_worktree or "-"),
                "branch": branch_text,
                "mr": mr_status,
                "agent": str(agent or "-"),
                "runtime": ":".join(
                    part
                    for part in (
                        str(backend or ""),
                        str(runtime_state or ""),
                        str(session_name or ""),
                    )
                    if part
                )
                or "-",
                "live": live,
                "validation": validation_text,
                "closeout": closeout_text,
                "last_event": str(state.get("last_event_type") or "-"),
            }
        )
    return rows


def local_issue_numbers(root: Path, *, active_only: bool = False) -> set[int]:
    numbers: set[int] = set()
    state_root_path = worktree.worktree_state_root(root)
    terminal_states = {"done", "closed", "cleanup-failed", "handback-failed"}
    if state_root_path.exists():
        for path in state_root_path.glob("issue-*.json"):
            import re

            match = re.match(r"issue-(\d+)\.json$", path.name)
            if not match:
                continue
            payload = shared.read_json_file(path) or {}
            if active_only and payload.get("state") in terminal_states:
                continue
            numbers.add(int(match.group(1)))
    for wt in worktree.list_resume_candidates(root):
        issue_id = worktree.extract_issue_id_from_branch(wt.branch)
        if issue_id is not None:
            numbers.add(issue_id)
    return numbers


def format_merge_request_status(mr: dict[str, object] | None) -> str:
    if not mr:
        return "-"
    number = mr.get("number")
    state = str(mr.get("state") or "").lower()
    if not state and mr.get("mergedAt"):
        state = "merged"
    if not state:
        state = "open"
    if mr.get("isDraft") and state == "opened":
        state = "draft"
    prefix = f"!{number}" if number else "mr"
    return f"{prefix}:{state}"


def merge_request_for_source_branch(root: Path, repo: str, branch: str, state: str) -> dict | None:
    return tracker_client.merge_request_for_branch(root, repo, branch, state)


def _clip(value: object, width: int) -> str:
    text = "-" if value is None else str(value)
    if len(text) <= width:
        return text
    return text[: max(0, width - 1)] + "…"


def print_issue_status_rows(rows: list[dict[str, object]]) -> None:
    columns = [
        ("issue", "Issue", 7),
        ("seq", "Seq", 5),
        ("issue_status", "Status", 12),
        ("worktree", "Worktree", 34),
        ("mr", "MR", 12),
        ("agent", "Agent", 8),
        ("runtime", "Runtime", 24),
        ("live", "Live", 5),
        ("validation", "Validation", 24),
        ("closeout", "Closeout", 16),
        ("last_event", "Last event", 24),
    ]
    if not rows:
        print("No issue/worktree/agent status rows found.")
        return
    header = "  ".join(label.ljust(width) for _, label, width in columns)
    print(header)
    print("  ".join("-" * width for _, _, width in columns))
    for row in rows:
        print("  ".join(_clip(row.get(key), width).ljust(width) for key, _, width in columns))


def evidence_drift_findings(root: Path, issues: list[Issue]) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    for issue in logic.queue_task_issues(issues):
        if issue.state != "open" or logic.lifecycle_status(issue) != "in-progress":
            continue
        evidence_summary = evidence.issue_evidence_summary(root, issue.number)
        if evidence_summary["linked_worktree"] is None and evidence_summary["state_path"] is None:
            findings.append(
                AuditFinding(
                    severity="warning",
                    issue_number=issue.number,
                    message=(
                        "status:in-progress but no local linked worktree "
                        "or .build evidence in this clone"
                    ),
                )
            )
    return findings


def stale_evidence_findings(root: Path, issues: list[Issue]) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    known_issue_numbers = {issue.number for issue in issues}

    evidence_dirs = [
        (WORKTREE_STATE_DIR, "state"),
        (WORKTREE_CLOSEOUT_DIR, "closeout"),
        (VALIDATION_RECEIPTS_DIR, "receipt"),
    ]

    # Pre-cache linked worktrees for performance
    linked_wts = {
        worktree.extract_issue_id_from_branch(wt.branch): wt
        for wt in worktree.list_resume_candidates(root)
        if worktree.extract_issue_id_from_branch(wt.branch) is not None
    }

    # Check for orphaned or stale evidence files
    for rel_dir, kind in evidence_dirs:
        dir_path = root / rel_dir
        if not dir_path.exists():
            continue
        for p in dir_path.glob("issue-*.json"):
            parts = p.stem.split("-")
            if len(parts) < 2:
                continue
            try:
                issue_num = int(parts[1])
            except ValueError:
                continue

            if issue_num not in known_issue_numbers:
                findings.append(
                    AuditFinding(
                        severity="warning",
                        issue_number=issue_num,
                        message=f"orphaned {kind} file: {rel_dir}/{p.name} (issue not in tracker)",
                    )
                )
                continue

            # If it's a validation receipt, check if it matches current HEAD
            if kind == "receipt" and issue_num in linked_wts:
                linked = linked_wts[issue_num]
                try:
                    with open(p) as f:
                        receipt = json.load(f)
                    receipt_sha = receipt.get("head_sha")
                    if receipt_sha and receipt_sha != linked.head:
                        findings.append(
                            AuditFinding(
                                severity="warning",
                                issue_number=issue_num,
                                message=(
                                    f"stale validation receipt: {rel_dir}/{p.name} "
                                    f"(does not match worktree HEAD {linked.head[:12]})"
                                ),
                            )
                        )
                except (json.JSONDecodeError, OSError):
                    pass

    # Check for evidence consistency for known issues
    for issue in logic.queue_task_issues(issues):
        evidence_summary = evidence.issue_evidence_summary(root, issue.number)

        # 1. Check worktree path existence if linked
        if evidence_summary["linked_worktree"]:
            wt_path = Path(str(evidence_summary["linked_worktree"]))
            if not wt_path.exists():
                findings.append(
                    AuditFinding(
                        severity="error",
                        issue_number=issue.number,
                        message=f"linked worktree path does not exist: {wt_path}",
                    )
                )

        # 2. Check for missing closeout report on done/closed tasks
        status = logic.lifecycle_status(issue)
        if (status == "done" or issue.state == "closed") and evidence_summary["state_path"]:
            if not evidence_summary["closeout_path"]:
                findings.append(
                    AuditFinding(
                        severity="warning",
                        issue_number=issue.number,
                        message=(
                            "task is done/closed with execution state but missing closeout report"
                        ),
                    )
                )

    return findings


def stale_lock_findings(root: Path, repo: str, issues: list[Issue]) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    for issue in logic.queue_task_issues(issues):
        if issue.state != "open" or logic.lifecycle_status(issue) != "in-progress":
            continue
        if worktree.find_linked_worktree_for_issue(root, issue.number) is not None:
            continue
        expected_branch = (
            f"wt/{worktree.infer_scope(issue)}/{issue.number}-{worktree.slugify_text(issue.title)}"
        )
        try:
            if merge_request_for_source_branch(root, repo, expected_branch, "open"):
                continue
        except shared.CliError:
            pass
        findings.append(
            AuditFinding(
                severity="error",
                issue_number=issue.number,
                message=(
                    "status:in-progress has no linked local worktree and no detected open MR; "
                    f"repair the stale lock or recreate branch {expected_branch}"
                ),
            )
        )
    return findings


def choose_issue_interactive(selection: QueueSelection) -> Issue:
    if not selection.items:
        raise shared.CliError("Queue is empty")
    print_queue(selection)
    while True:
        raw = input("Pick queue index [1] (0=back): ").strip() or "1"
        if raw in {"0", "back"}:
            raise shared.CliError("Back")
        if raw.isdigit():
            idx = int(raw)
            if 1 <= idx <= len(selection.items):
                return selection.items[idx - 1].issue
        print("Invalid choice.")


def resolve_cli_launch_request(
    agent: str | None = None,
    agent_mode: str | None = None,
    review_agent: str | None = None,
    review_agent_mode: str | None = None,
    handoff: str | None = None,
    print_only: bool = False,
    tmux: bool | None = None,
    zellij: bool | None = None,
    no_mux: bool = False,
    default_agent: str = "codex",
) -> tuple[str, str, str, str]:
    # Pass individual fields to resolve_launch_request
    agent_out, agent_mode_out, handoff_out, mux_out = resolve_launch_request(
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

    if agent == "random":
        agent_out = choose_default_launch_agent()
    elif agent is None:
        agent_out = default_agent

    return agent_out, agent_mode_out, handoff_out, mux_out


def choose_default_launch_agent(pool: tuple[str, ...] = DEFAULT_INTERACTIVE_AGENT_POOL) -> str:
    return random.choice(pool)


def resolve_mux_flag(
    no_mux: bool = False,
    tmux: bool | None = None,
    zellij: bool | None = None,
) -> str | None:
    if no_mux:
        return "none"
    if zellij:
        return "zellij"
    if tmux:
        return "tmux"
    return None


def handoff_to_agent_or_shell(
    *,
    path: Path,
    root: Path,
    repo: str | None,
    agent: str | None = None,
    agent_mode: str | None = None,
    review_agent: str | None = None,
    review_agent_mode: str | None = None,
    handoff: str | None = None,
    print_only_override: bool = False,
    mux: str | None = None,
) -> None:
    await_worktree_ready_if_provisioning(path)
    worktree.ensure_uv_venv(path)
    agent_val = (agent or choose_agent_interactive()).lower()
    mode_val = (agent_mode or choose_agent_mode_interactive()).lower()
    review_agent_val = review_agent.lower() if review_agent else None
    review_mode_val = (review_agent_mode or "normal").lower() if review_agent_val else None
    handoff_val = (handoff or choose_handoff_action_interactive()).lower()
    if print_only_override:
        handoff_val = "print-only"

    prompt = build_agent_prompt_for_worktree(path, root, repo)
    command = build_agent_command(agent_val, mode_val, prompt)
    review_prompt = (
        build_review_prompt_for_worktree(
            path,
            root,
            repo,
            implementation_agent=agent_val,
        )
        if review_agent_val
        else None
    )
    review_command = (
        build_agent_command(review_agent_val, review_mode_val or "normal", review_prompt)
        if review_agent_val and review_prompt
        else None
    )

    if mux is None:
        mux = multiplexer.auto_detect_mux() if handoff_val == "execute-now" else "none"

    print()
    print(f"Target: {path}")
    print(f"Agent:  {agent_val} ({mode_val})")
    if review_agent_val:
        print(f"Review: {review_agent_val} ({review_mode_val})")
    print(f"Mux:    {mux}")
    print(f"Prompt: {prompt}")
    if review_prompt is not None:
        print(f"Review prompt: {review_prompt}")
    sys.stdout.flush()

    if review_agent_val and handoff_val == "execute-now" and mux == "none":
        raise shared.CliError(
            "Review lane requires tmux/zellij or print-only handoff; rerun without --no-mux"
        )

    if review_agent_val and handoff_val == "execute-now" and mux == "zellij":
        session = multiplexer.worktree_session_pair(path.name)
        try:
            multiplexer.launch_zellij_batch_session(
                session_name=session.session_name,
                launches=[
                    ("implement", path, command),
                    ("review", path, review_command or ""),
                ],
            )
            return
        except (subprocess.CalledProcessError, OSError) as exc:
            raise shared.CliError(
                f"Review lane launch failed via zellij: {exc}. "
                "Rerun with a working mux or use HANDOFF=print-only."
            ) from exc

    if review_agent_val and handoff_val == "execute-now" and mux == "tmux":
        session = multiplexer.worktree_session_pair(path.name)
        try:
            multiplexer.launch_tmux_batch_session(
                session_name=session.session_name,
                launches=[
                    ("implement", path, command),
                    ("review", path, review_command or ""),
                ],
            )
            return
        except (subprocess.CalledProcessError, OSError) as exc:
            raise shared.CliError(
                f"Review lane launch failed via tmux: {exc}. "
                "Rerun with a working mux or use HANDOFF=print-only."
            ) from exc

    if mux == "zellij" and handoff_val == "execute-now":
        try:
            multiplexer.launch_zellij_session(path=path, agent_command=command)
            return
        except (subprocess.CalledProcessError, OSError) as exc:
            git_utils.eprint(
                f"WARNING: zellij launch failed ({exc}); falling back to direct shell execution"
            )
            mux = "none"

    if mux == "tmux" and handoff_val == "execute-now":
        try:
            multiplexer.launch_tmux_session(path=path, agent_command=command)
            return
        except (subprocess.CalledProcessError, OSError) as exc:
            git_utils.eprint(
                f"WARNING: tmux launch failed ({exc}); falling back to direct shell execution"
            )
            mux = "none"

    if handoff_val == "execute-now":
        path_q = shared.shell_quote(str(path))
        cmd = f"cd {path_q} && {multiplexer.worktree_env_preamble()}; {command}"
        os.execvp("bash", ["bash", "-lc", cmd])

    if not sys.stdin.isatty():
        return
    worktree.open_shell(path)


def choose_agent_interactive(default: str = "codex") -> str:
    mapping = {
        "1": "gemini",
        "gemini": "gemini",
        "2": "claude",
        "claude": "claude",
        "3": "codex",
        "codex": "codex",
    }
    while True:
        print("Choose agent:")
        print("  1) gemini")
        print("  2) claude")
        print("  3) codex")
        print("  0) Back")
        default_choice = {"gemini": "1", "claude": "2", "codex": "3"}.get(default, "3")
        raw = input(f"Choice [{default_choice}]: ").strip()
        if not raw:
            return default
        if raw in {"0", "back"}:
            raise shared.CliError("Back")
        if raw.lower() in mapping:
            return mapping[raw.lower()]
        print("Invalid choice.")


def choose_agent_mode_interactive(default: str = "yolo") -> str:
    mapping = {
        "1": "normal",
        "normal": "normal",
        "2": "yolo",
        "yolo": "yolo",
    }
    while True:
        print(f"Choose launch mode ({default} default):")
        print("  1) normal")
        print("  2) yolo / equivalent")
        print("  0) Back")
        raw = input(f"Choice [{'2' if default == 'yolo' else '1'}]: ").strip()
        if not raw:
            return default
        if raw in {"0", "back"}:
            raise shared.CliError("Back")
        if raw.lower() in mapping:
            return mapping[raw.lower()]
        print("Invalid choice.")


def choose_handoff_action_interactive(default: str = "execute-now") -> str:
    mapping = {
        "1": "execute-now",
        "execute-now": "execute-now",
        "execute": "execute-now",
        "2": "print-only",
        "print-only": "print-only",
        "print": "print-only",
    }
    while True:
        print("Choose handoff behavior:")
        print("  1) execute-now")
        print("  2) print-only (open shell, do not launch agent)")
        print("  0) Back")
        raw = input(f"Choice [{'1' if default == 'execute-now' else '2'}]: ").strip()
        if not raw:
            return default
        if raw in {"0", "back"}:
            raise shared.CliError("Back")
        if raw.lower() in mapping:
            return mapping[raw.lower()]
        print("Invalid choice.")


def build_agent_prompt_for_worktree(path: Path, root: Path, repo: str | None) -> str:
    branch = (
        git_utils.run(["git", "branch", "--show-current"], cwd=path).stdout.strip() or "(detached)"
    )
    issue_id = worktree_issue_id(path)
    issue_ref = f"GitLab issue #{issue_id}" if issue_id is not None else "no linked GitLab issue"
    issue_labels = fetch_issue_labels_for_prompt(root, repo, issue_id)
    labels_clause = issue_labels or "-"
    prompt_lines = [
        (
            f"Context: {issue_ref}; project {repo or '(gitlab remote unavailable)'}; "
            f"branch {branch}; worktree {path}; labels {labels_clause}."
        ),
        (
            "Read: CLAUDE.md; docs/ARCHITECTURE.md; "
            "issue-linked ADRs if easy to identify from repo or issue context."
        ),
    ]
    if issue_id is None:
        prompt_lines.append(
            "Worktree policy: this path is not an issue worktree branch. Do not start new "
            "implementation from main or another non-issue branch; create or resume the "
            "correct issue worktree first unless the operator explicitly directs otherwise."
        )
    prompt_lines.extend(
        [
            (
                "Operating mode: you are the implementation owner for this issue worktree. "
                "Proceed without asking for permission on clear, reversible next steps; ask "
                "only for destructive actions, production access, or policy/security decisions "
                "that the repo rules require escalating."
            ),
            (
                "Scope: only this issue. Do not broaden scope, bundle opportunistic cleanup, "
                "or repair unrelated failures in the same branch. If unrelated drift blocks "
                "validation, document it as a separate follow-up and keep this branch focused."
            ),
            (
                "First step: inspect the current branch diff, linked GitLab issue, issue "
                "labels, dependencies, relevant ADRs/docs, and the smallest set of files that "
                "control the behavior before editing."
            ),
            (
                "Context lookup: prefer GitNexus for unfamiliar flows and blast-radius checks "
                "when it is available. Use context/impact before editing shared symbols, then "
                "run detect_changes before commit. If GitNexus is unavailable or stale, fall "
                "back to rg, git diff/log, and direct file reads; do not block on GitNexus."
            ),
            (
                "Execution loop: inspect; form the smallest defensible plan; add or update "
                "tests before behavior changes when practical; run make worktree-probe MODE=test "
                "before attempting tests; if it fails, stop the test attempt and run make "
                "ensure-tools before continuing; run make worktree-probe before agent handoff; "
                "implement; run the narrowest useful checks; then run make preflight-session and "
                "make pre-validate-session before push. Fix failures and repeat until the issue "
                "is actually complete."
            ),
            (
                "Change shape: keep diffs small and reversible; prefer deletion over addition; "
                "reuse existing patterns before introducing new abstractions; do not add "
                "dependencies without explicit need."
            ),
            (
                "Documentation: reconcile implementation with relevant ADRs, runbooks, and "
                "architecture docs. Adhere to the project's pithy, narrative technical style "
                "without unduly dehydrating critical content; a feature is incomplete while "
                "its documentation remains stale."
            ),
            (
                "Do not stop at: MR creation, one passing test, a local commit, a pushed "
                "branch, or a partial implementation. Those are intermediate states."
            ),
            (
                "Review gate: before claiming completion, run a senior-engineer review pass "
                "focused on bugs, regressions, security/operability risks, and missing tests. "
                "If a second agent is available, use it for that review; otherwise perform the "
                "review yourself with the same standard."
            ),
            (
                "Completion sequence: push through make worktree-push-issue or an equivalent "
                "prevalidated push; create/update the MR; address review feedback; merge to "
                "the target branch; close and normalize the issue; record validation evidence; "
                "finalize .build hand-back evidence; then run make finish-worktree-close. "
                "Report cleanup residue explicitly, but do not treat worktree or branch "
                "deletion as semantic completion."
            ),
            (
                "Pause only if: repo rules mandate escalation, a security/policy decision is "
                "unsafe to infer, required credentials or permissions are missing, or a "
                "destructive operation is unavoidable. Otherwise make a reasonable local "
                "decision, keep moving, and report blockers with the exact failed command, "
                "evidence, and next command needed."
            ),
        ]
    )
    return "\n".join(prompt_lines)


def fetch_issue_labels_for_prompt(root: Path, repo: str | None, issue_id: int | None) -> str:
    if repo is None or issue_id is None or not tracker_client.tracker_available():
        return ""
    try:
        data = tracker_client.get_issue(root, repo, issue_id)
    except shared.CliError:
        return ""
    if not isinstance(data, dict):
        return ""
    labels = [x["name"] for x in data.get("labels", []) if isinstance(x, dict) and "name" in x]
    return "|".join(labels)


def build_review_prompt_for_worktree(
    path: Path,
    root: Path,
    repo: str | None,
    *,
    implementation_agent: str,
) -> str:
    branch = (
        git_utils.run(["git", "branch", "--show-current"], cwd=path).stdout.strip() or "(detached)"
    )
    issue_id = worktree_issue_id(path)
    issue_ref = f"GitLab issue #{issue_id}" if issue_id is not None else "no linked GitLab issue"
    issue_labels = fetch_issue_labels_for_prompt(root, repo, issue_id)
    labels_clause = issue_labels or "-"
    prompt_lines = [
        (
            f"Context: reviewer lane for {issue_ref}; "
            f"project {repo or '(gitlab remote unavailable)'}; "
            f"branch {branch}; worktree {path}; labels {labels_clause}; implementation agent "
            f"{implementation_agent}."
        ),
        (
            "Read: CLAUDE.md; docs/ARCHITECTURE.md; "
            "issue-linked ADRs if easy to identify from repo or issue context."
        ),
        (
            "Role: reviewer only. Do not take ownership of implementation unless the primary "
            "agent is blocked or the operator explicitly redirects you."
        ),
        (
            "Review focus: bugs, behavioral regressions, security/operability risks, contract "
            "drift, cleanup gaps, documentation staleness, and missing tests."
        ),
        (
            "Method: inspect the current branch diff first, then read only the smallest set of "
            "surrounding files needed to validate correctness."
        ),
        (
            "Output: report concrete findings first with file references and the specific risk. "
            "If no findings remain, say that explicitly and note any residual test gaps."
        ),
        (
            "Do not approve based only on a passing test subset, an open merge request, or the "
            "primary agent's summary."
        ),
    ]
    return "\n".join(prompt_lines)


def build_agent_command(agent: str, mode: str, prompt: str) -> str:
    quoted = shared.shell_quote(prompt)
    if agent == "gemini":
        approval_flag = "--approval-mode=yolo " if mode == "yolo" else ""
        return f"gemini {approval_flag}-i {quoted}".strip()
    if agent == "claude":
        flag = "--dangerously-skip-permissions " if mode == "yolo" else ""
        return f"claude {flag}{quoted}".strip()
    if agent == "codex":
        flag = "--yolo " if mode == "yolo" else ""
        return f"codex {flag}{quoted}".strip()
    raise shared.CliError(f"Unsupported agent '{agent}'")


def wants_agent_launch(
    agent: str | None = None,
    agent_mode: str | None = None,
    review_agent: str | None = None,
    review_agent_mode: str | None = None,
    handoff: str | None = None,
    print_only: bool = False,
    tmux: bool | None = None,
    zellij: bool | None = None,
    no_mux: bool = False,
) -> bool:
    return bool(
        agent
        or agent_mode
        or review_agent
        or review_agent_mode
        or handoff
        or print_only
        or tmux
        or zellij
        or no_mux
    )


def run_command_in_worktree(path: Path, command: str) -> None:
    print(f"Running in {path}: {command}")
    git_utils.run(["bash", "-lc", command], cwd=path, check=True)


def run_pre_validate(path: Path) -> None:
    print(f"Running pre-push validation in {path} (make validate-pre-push)")
    git_utils.run(["bash", "-lc", "make validate-pre-push"], cwd=path, check=True)


def push_branch_enforced(
    root: Path,
    *,
    path: Path | None = None,
    dry_run: bool = False,
) -> None:
    from scripts.issue_tool import git_utils

    worktrees = worktree.list_worktrees(root)
    target = worktree.resolve_current_worktree(path or git_utils.current_path(), worktrees)
    branch = worktree.current_branch(target.path)
    if target.is_primary:
        raise shared.CliError(
            "Refusing to push from primary worktree via issue-worktree push command"
        )
    if not WORKTREE_BRANCH_REGEX.fullmatch(branch):
        raise shared.CliError(f"Branch '{branch}' is not a policy-compliant worktree branch")

    try:
        repo = git_utils.origin_repo_slug(root)
    except shared.CliError:
        repo = None
    worktree.run_preflight(path=target.path, root=root, repo=repo)
    run_pre_validate(target.path)

    push_cmd = ["git", "push", "-u", "origin", branch]
    print(f"Push command: {' '.join(push_cmd)}")
    if dry_run:
        print("Dry run: push not executed.")
        return
    git_utils.run(push_cmd, cwd=target.path, check=True)
    print("Push complete.")


def finish_summary(root: Path, *, path: Path | None = None) -> None:
    from scripts.issue_tool import git_utils

    worktrees = worktree.list_worktrees(root)
    target = worktree.resolve_current_worktree(path or git_utils.current_path(), worktrees)
    ready, repo = tracker_repo_ready(root)
    branch = target.branch
    issue_id = worktree.extract_issue_id_from_branch(branch) if branch else None
    stage = finish_stage(root, target, repo if ready else None)
    print("Finish Worktree Summary")
    print(f"  worktree: {target.path}")
    print(f"  primary:  {worktrees[0].path}")
    print(f"  branch:   {branch}")
    print(f"  issue:    #{issue_id}" if issue_id else "  issue:    (unparsed)")
    print(f"  stage:    {stage}")
    print(f"  git:      {git_utils.run(['git', 'status', '-sb'], cwd=target.path).stdout.strip()}")

    if ready and repo and issue_id:
        info = issue_state_info(root, repo, issue_id)
        if info:
            labels = "|".join(x["name"] for x in info.get("labels", []) if isinstance(x, dict))
            print(f"  issue:    {info.get('state')} - {info.get('title')}")
            print(f"  labels:   {labels}")
            print(f"  issueurl: {info.get('url')}")
        open_mr = merge_request_for_source_branch(root, repo, branch, "open")
        merged_mr = merge_request_for_source_branch(root, repo, branch, "merged")
        if open_mr:
            print(f"  mr:       #{open_mr.get('number')} OPEN - {open_mr.get('title')}")
            print(f"  mrurl:    {open_mr.get('url')}")
        elif merged_mr:
            print(f"  mr:       #{merged_mr.get('number')} MERGED")
            print(f"  mrurl:    {merged_mr.get('url')}")
            print(f"  mergedAt: {merged_mr.get('mergedAt')}")
        else:
            print("  mr:       none")
    else:
        if not (ready and repo):
            print("  mr:       (glab unavailable)")
        elif issue_id is None:
            print("  mr:       (not an issue worktree branch)")
        else:
            print("  mr:       (unavailable)")

    print("  policy:   pushes must run preflight + validate-pre-push")
    print("  dod:      merged MR + closed issue + cleaned worktree/branch")
    if stage == "implementing":
        print("  next:     complete implementation/tests; keep git status clean before push")
    elif stage == "ready-to-push":
        print("  next:     make worktree-push-issue")
        if branch and branch != "(detached)":
            print(f"  then:     glab mr create --fill --source-branch {branch}")
    elif stage in {"review", "mr-open"}:
        print(
            "  next:     merge MR; do not stop at MR open. "
            "If conflicts appear, resolve in this worktree and re-validate"
        )
    elif stage == "merged":
        print("  next:     make finish-worktree-close")
    print("  conflict: if merge/rebase conflicts appear:")
    print("            resolve files -> git add <files> -> complete merge/rebase")
    print("            rerun: make preflight-session && make pre-validate-session")
    print("            push conflict-resolution commits before merge")
    print("  cleanup:  git worktree remove <this-worktree-path>")
    if branch and WORKTREE_BRANCH_REGEX.fullmatch(branch):
        print(f"            git branch -d {branch}")
    print("            git worktree prune")


def close_issue_done(root: Path, *, path: Path | None = None, force: bool = False) -> None:
    from scripts.issue_tool import closeout, git_utils

    worktrees = worktree.list_worktrees(root)
    target = worktree.resolve_current_worktree(path or git_utils.current_path(), worktrees)
    ready, repo = tracker_repo_ready(root)
    if not ready or not repo:
        raise shared.CliError("glab/GitLab repo not available")
    issue_id = worktree.extract_issue_id_from_branch(target.branch)
    if issue_id is None:
        raise shared.CliError(f"Could not parse issue id from branch {target.branch}")
    report_path = closeout.closeout_report_path(
        root, target, extract_issue_id_from_branch_fn=worktree.extract_issue_id_from_branch
    )
    report_base: dict[str, object] = {
        "issue_id": issue_id,
        "repo": repo,
        "merged_mr_required": not force,
        "stage": "starting",
        "events": [],
    }
    events = report_base["events"]
    if isinstance(events, list):
        events.append(
            closeout.closeout_event(
                stage="starting",
                message="closeout started",
                target=target,
                repo=repo,
                issue_id=issue_id,
            )
        )
    worktree.record_issue_handoff_event(
        root=root,
        repo=repo,
        issue_number=issue_id,
        issue_title=target.branch,
        branch=target.branch,
        worktree_path=target.path,
        event_type="closeout-started",
        state="closeout-started",
        details={"force": force, "report_path": str(report_path)},
        idempotency_key=f"closeout-started:{issue_id}:{target.branch}:{target.path}",
    )
    closeout.write_closeout_report(
        root,
        target,
        report_base,
        extract_issue_id_from_branch_fn=worktree.extract_issue_id_from_branch,
    )
    try:
        merged_mr = merge_request_for_source_branch(root, repo, target.branch, "merged")
        if isinstance(events, list):
            events.append(
                closeout.closeout_event(
                    stage="merge-check",
                    message=f"merged MR lookup {'found' if merged_mr else 'missed'}",
                    target=target,
                    repo=repo,
                    issue_id=issue_id,
                )
            )
        if not merged_mr and not force:
            raise shared.CliError(
                "No merged MR found for branch; refusing to close issue without --force"
            )
        info = issue_state_info(root, repo, issue_id)
        issue_closed = False
        if info and str(info.get("state", "")).upper() == "CLOSED":
            normalized = logic.normalize_closed_issue_labels(root, repo, issue_id, info)
            print(f"Issue #{issue_id} already closed.")
            if normalized:
                print("Normalized closed-issue lifecycle labels.")
            issue_closed = True
            if isinstance(events, list):
                events.append(
                    closeout.closeout_event(
                        stage="issue-close",
                        message="issue already closed; labels normalized",
                        target=target,
                        repo=repo,
                        issue_id=issue_id,
                    )
                )
        else:
            tracker_client.close_issue(root, repo, issue_id)
            print(f"Closed issue #{issue_id}.")
            refreshed_info = issue_state_info(root, repo, issue_id)
            if not refreshed_info and info:
                refreshed_info = {**info, "state": "closed"}
            normalized = logic.normalize_closed_issue_labels(root, repo, issue_id, refreshed_info)
            if normalized:
                print("Normalized closed-issue lifecycle labels.")
            issue_closed = True
            if isinstance(events, list):
                events.append(
                    closeout.closeout_event(
                        stage="issue-close",
                        message="issue closed via glab",
                        target=target,
                        repo=repo,
                        issue_id=issue_id,
                    )
                )
        closeout.write_closeout_report(
            root,
            target,
            {
                **report_base,
                "stage": "issue-closed",
                "issue_closed": issue_closed,
            },
            extract_issue_id_from_branch_fn=worktree.extract_issue_id_from_branch,
        )
        if isinstance(events, list):
            events.append(
                closeout.closeout_event(
                    stage="cleanup",
                    message="cleanup started",
                    target=target,
                    repo=repo,
                    issue_id=issue_id,
                )
            )
        cleanup_result: dict[str, object] = {}
        cleanup_verified = False
        cleanup_error: str | None = None
        try:
            cleanup_result = dict(
                closeout.cleanup_finished_worktree(
                    root, target, local_branch_exists_fn=worktree.local_branch_exists
                )
            )
            cleanup_problems = closeout.verify_cleanup_finished(
                root,
                target,
                list_worktrees_fn=worktree.list_worktrees,
                local_branch_exists_fn=worktree.local_branch_exists,
            )
            if cleanup_problems:
                raise shared.CliError("Cleanup verification failed: " + "; ".join(cleanup_problems))
            cleanup_verified = True
            if isinstance(events, list):
                events.append(
                    closeout.closeout_event(
                        stage="cleanup-verified",
                        message="cleanup verified",
                        target=target,
                        repo=repo,
                        issue_id=issue_id,
                    )
                )
        except Exception as exc:
            cleanup_error = str(exc)
            print(f"Cleanup deferred: {cleanup_error}")
            print(f"Manual cleanup: git worktree remove {target.path}")
            if target.branch and target.branch != "(detached)":
                print(f"Manual cleanup: git branch -d {target.branch}")
            print("Manual cleanup: git worktree prune")
            if isinstance(events, list):
                events.append(
                    closeout.closeout_event(
                        stage="cleanup-deferred",
                        message=cleanup_error,
                        target=target,
                        repo=repo,
                        issue_id=issue_id,
                    )
                )
        closeout.write_closeout_report(
            root,
            target,
            {
                **report_base,
                "stage": "complete",
                "issue_closed": issue_closed,
                "cleanup": cleanup_result,
                "cleanup_verified": cleanup_verified,
                **({"cleanup_error": cleanup_error} if cleanup_error else {}),
            },
            extract_issue_id_from_branch_fn=worktree.extract_issue_id_from_branch,
        )
        worktree.record_issue_handoff_event(
            root=root,
            repo=repo,
            issue_number=issue_id,
            issue_title=target.branch,
            branch=target.branch,
            worktree_path=target.path,
            event_type="closeout-complete",
            state="closed",
            details={
                "report_path": str(report_path),
                "issue_closed": issue_closed,
                "cleanup_verified": cleanup_verified,
                **({"cleanup_error": cleanup_error} if cleanup_error else {}),
            },
            idempotency_key=f"closeout-complete:{issue_id}:{target.branch}:{target.path}",
        )
        handback_summary = evidence.audit_issue_handoff_evidence(
            root=root,
            repo=repo,
            issue_id=issue_id,
            target=target,
            report_path=report_path,
        )
        worktree.record_issue_handoff_event(
            root=root,
            repo=repo,
            issue_number=issue_id,
            issue_title=target.branch,
            branch=target.branch,
            worktree_path=target.path,
            event_type="handback-audited",
            state="evidence-audited",
            details={
                "report_path": str(report_path),
                "evidence_hash": handback_summary["evidence_hash"],
            },
            idempotency_key=f"handback-audited:{issue_id}:{handback_summary['evidence_hash']}",
        )
        worktree.append_issue_handback_comment(
            root=root,
            repo=repo,
            issue_id=issue_id,
            summary=handback_summary,
        )
        worktree.record_issue_handoff_event(
            root=root,
            repo=repo,
            issue_number=issue_id,
            issue_title=target.branch,
            branch=target.branch,
            worktree_path=target.path,
            event_type="handback-complete",
            state="done",
            details={
                "report_path": str(report_path),
                "evidence_hash": handback_summary["evidence_hash"],
            },
            idempotency_key=f"handback-complete:{issue_id}:{handback_summary['evidence_hash']}",
        )
        print(f"Closeout report: {report_path}")
    except Exception as exc:
        if isinstance(events, list):
            events.append(
                closeout.closeout_event(
                    stage="failed",
                    message=str(exc),
                    target=target,
                    repo=repo,
                    issue_id=issue_id,
                )
            )
        closeout.write_closeout_report(
            root,
            target,
            {
                **report_base,
                "stage": "failed",
                "error": str(exc),
            },
            extract_issue_id_from_branch_fn=worktree.extract_issue_id_from_branch,
        )
        worktree.record_issue_handoff_event(
            root=root,
            repo=repo,
            issue_number=issue_id,
            issue_title=target.branch,
            branch=target.branch,
            worktree_path=target.path,
            event_type="closeout-failed",
            state="cleanup-failed",
            details={"report_path": str(report_path), "error": str(exc)},
            idempotency_key=f"closeout-failed:{issue_id}:{target.branch}:{target.path}",
        )
        worktree.record_issue_handoff_event(
            root=root,
            repo=repo,
            issue_number=issue_id,
            issue_title=target.branch,
            branch=target.branch,
            worktree_path=target.path,
            event_type="handback-failed",
            state="handback-failed",
            details={"report_path": str(report_path), "error": str(exc)},
            idempotency_key=f"handback-failed:{issue_id}:{target.branch}:{target.path}:{str(exc)}",
        )
        print(f"Closeout report: {report_path}")
        raise


def finish_stage(root: Path, wt: WorktreeInfo, repo: str | None) -> str:
    dirty = git_utils.run(["git", "status", "--porcelain"], cwd=wt.path).stdout.strip()
    if dirty:
        return "implementing"
    branch = wt.branch
    if branch and branch != "(detached)" and repo:
        open_mr = merge_request_for_source_branch(root, repo, branch, "open")
        if open_mr:
            return "review"
        merged_mr = merge_request_for_source_branch(root, repo, branch, "merged")
        if merged_mr:
            return "merged"
    try:
        upstream = git_utils.run(
            ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
            cwd=wt.path,
        ).stdout.strip()
    except subprocess.CalledProcessError:
        return "ready-to-push"
    if upstream:
        ab = git_utils.run(
            ["git", "rev-list", "--left-right", "--count", f"{upstream}...HEAD"],
            cwd=wt.path,
        ).stdout.strip()
        if ab:
            behind, ahead = [int(x) for x in ab.split()]
            if ahead > 0:
                return "ready-to-push"
            if ahead == 0 and behind == 0:
                return "mr-open"
    return "mr-open"


def tracker_repo_ready(root: Path) -> tuple[bool, str | None]:
    if not tracker_client.tracker_available():
        return False, None
    try:
        return True, git_utils.origin_repo_slug(root)
    except shared.CliError:
        return False, None


def issue_state_info(root: Path, repo: str, issue_id: int) -> dict | None:
    return tracker_client.get_issue(root, repo, issue_id)


def batch_run_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    suffix = f"{os.getpid():x}"
    return f"run-{stamp}-{suffix}"


def batch_run_dir(root: Path, run_id: str) -> Path:

    return worktree.worktree_runs_root(root) / run_id


def batch_manifest_path(root: Path, run_id: str) -> Path:
    return batch_run_dir(root, run_id) / "manifest.json"


def batch_entry_path(root: Path, run_id: str, issue_number: int, agent: str) -> Path:
    return batch_run_dir(root, run_id) / f"issue-{issue_number}-{agent}.json"


def write_batch_entry(root: Path, run_id: str, entry: BatchLaunchResult) -> Path:
    payload = {
        "issue_number": entry.issue_number,
        "agent": entry.agent,
        "worktree_path": str(entry.worktree_path),
        "branch": entry.branch,
        "command": entry.command,
        "state": entry.state,
        "pid": entry.pid,
        "backend": entry.backend,
        "session_name": entry.session_name,
        "window_name": entry.window_name,
        "local_status_path": str(entry.local_status_path) if entry.local_status_path else None,
        "stdout_log_path": str(entry.stdout_log_path) if entry.stdout_log_path else None,
        "stderr_log_path": str(entry.stderr_log_path) if entry.stderr_log_path else None,
        "detail": entry.detail,
        "generated_at": datetime.now(UTC).isoformat(),
    }
    return shared.write_json_file(
        batch_entry_path(root, run_id, entry.issue_number, entry.agent), payload
    )


def agent_requires_tty(agent: str) -> bool:
    return AGENT_CAPABILITIES.get(agent, {}).get("requires_tty", False)


def agent_supports_detached(agent: str) -> bool:
    return AGENT_CAPABILITIES.get(agent, {}).get("supports_detached", False)


def read_log_tail(path: Path, *, line_count: int = 5) -> str:
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-line_count:]).strip()


def write_worktree_runtime_status(path: Path, payload: dict[str, object]) -> Path:
    runtime_dir = worktree.worktree_agent_run_dir(path)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    return shared.write_json_file(runtime_dir / "status.json", payload)


def launch_agent_detached(
    *,
    root: Path,
    run_id: str,
    issue_number: int,
    path: Path,
    branch: str,
    agent: str,
    command: str,
) -> BatchLaunchResult:
    if not agent_supports_detached(agent):
        raise shared.CliError(f"Agent '{agent}' does not support detached startup")
    worktree.ensure_uv_venv(path)
    runtime_dir = worktree.worktree_agent_run_dir(path)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    stdout_log = runtime_dir / "stdout.log"
    stderr_log = runtime_dir / "stderr.log"
    status_path = runtime_dir / "status.json"
    pid_path = runtime_dir / "pid"
    shell_cmd = f"if [ -f .venv/bin/activate ]; then source .venv/bin/activate; fi; exec {command}"
    started_at = datetime.now(UTC).isoformat()
    with stdout_log.open("ab") as stdout_fp, stderr_log.open("ab") as stderr_fp:
        proc = subprocess.Popen(
            ["bash", "-lc", shell_cmd],
            cwd=path,
            stdout=stdout_fp,
            stderr=stderr_fp,
            start_new_session=True,
        )
    try:
        proc.wait(timeout=DETACHED_STARTUP_PROBE_SECONDS)
        exited_early = True
    except subprocess.TimeoutExpired:
        exited_early = False

    if not exited_early:
        state = "running"
        detail = "started detached agent process"
    else:
        state = "failed"
        stderr_tail = read_log_tail(stderr_log)
        detail = stderr_tail or "agent exited during detached startup probe"
    status_payload: dict[str, object] = {
        "run_id": run_id,
        "issue_number": issue_number,
        "agent": agent,
        "branch": branch,
        "command": command,
        "backend": "detached",
        "state": state,
        "pid": proc.pid,
        "started_at": started_at,
        "stdout_log_path": str(stdout_log),
        "stderr_log_path": str(stderr_log),
        "orchestrator_manifest": str(batch_manifest_path(root, run_id)),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    shared.write_json_file(status_path, status_payload)
    pid_path.write_text(f"{proc.pid}\n", encoding="utf-8")
    return BatchLaunchResult(
        issue_number=issue_number,
        agent=agent,
        worktree_path=path,
        branch=branch,
        command=command,
        state=state,
        pid=proc.pid,
        local_status_path=status_path,
        stdout_log_path=stdout_log,
        stderr_log_path=stderr_log,
        backend="detached",
        detail=detail,
    )


def record_tmux_agent_launch(
    *,
    root: Path,
    run_id: str,
    issue_number: int,
    path: Path,
    branch: str,
    agent: str,
    command: str,
    session_name: str,
    window_name: str,
) -> BatchLaunchResult:
    runtime_dir = worktree.worktree_agent_run_dir(path)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    stdout_log = runtime_dir / "stdout.log"
    stderr_log = runtime_dir / "stderr.log"
    status_payload: dict[str, object] = {
        "run_id": run_id,
        "issue_number": issue_number,
        "agent": agent,
        "branch": branch,
        "command": command,
        "backend": "tmux",
        "state": "interactive",
        "pid": None,
        "session_name": session_name,
        "window_name": window_name,
        "stdout_log_path": str(stdout_log),
        "stderr_log_path": str(stderr_log),
        "orchestrator_manifest": str(batch_manifest_path(root, run_id)),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    status_path = write_worktree_runtime_status(path, status_payload)
    return BatchLaunchResult(
        issue_number=issue_number,
        agent=agent,
        worktree_path=path,
        branch=branch,
        command=command,
        state="interactive",
        pid=None,
        local_status_path=status_path,
        stdout_log_path=stdout_log,
        stderr_log_path=stderr_log,
        backend="tmux",
        session_name=session_name,
        window_name=window_name,
        detail="started tmux interactive agent session",
    )
