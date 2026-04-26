from __future__ import annotations

from scripts.issue_tool.issue_queue import build_queue, choose_next_runnable
from scripts.issue_tool.logic import lifecycle_status, queue_task_issues, status_labels
from scripts.issue_tool.models import AuditFinding, Issue
from scripts.issue_tool.shared import CliError


def audit_issues(issues: list[Issue]) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    task_issues = queue_task_issues(issues)

    for issue in issues:
        if issue.is_parent_cr and "type:task" in issue.labels:
            findings.append(
                AuditFinding(
                    severity="error",
                    issue_number=issue.number,
                    message=(
                        "parent CR issue must not carry type:task; only child issues are queueable"
                    ),
                )
            )
        parent_statuses = status_labels(issue) if issue.is_parent_cr else []
        if issue.is_parent_cr and "status:in-progress" in parent_statuses:
            findings.append(
                AuditFinding(
                    severity="error",
                    issue_number=issue.number,
                    message=(
                        "parent CR issue must not carry status:in-progress; "
                        "WIP is tracked on child task issues"
                    ),
                )
            )
        if issue.is_parent_cr and any(
            status in parent_statuses for status in ("status:not-started", "status:blocked")
        ):
            findings.append(
                AuditFinding(
                    severity="warning",
                    issue_number=issue.number,
                    message="parent CR issue should generally avoid task lifecycle labels",
                )
            )
        if issue.is_parent_cr and issue.seq is not None:
            findings.append(
                AuditFinding(
                    severity="warning",
                    issue_number=issue.number,
                    message=(
                        "parent CR issue should not carry Seq; "
                        "ordering belongs on child task issues"
                    ),
                )
            )
        if issue.is_parent_cr and issue.depends_on:
            findings.append(
                AuditFinding(
                    severity="warning",
                    issue_number=issue.number,
                    message=(
                        "parent CR issue should not carry Depends on; "
                        "dependency gating belongs on child task issues"
                    ),
                )
            )

    for issue in task_issues:
        states = status_labels(issue)
        state_set = set(states)
        if len(state_set) != 1:
            findings.append(
                AuditFinding(
                    severity="error",
                    issue_number=issue.number,
                    message=(
                        f"expected exactly one status:* label, found {sorted(state_set) or 'none'}"
                    ),
                )
            )
            continue

        status = states[0]
        if issue.state == "open" and status == "status:done":
            findings.append(
                AuditFinding(
                    severity="error",
                    issue_number=issue.number,
                    message="open task cannot be status:done",
                )
            )
        if issue.state == "closed" and status != "status:done":
            findings.append(
                AuditFinding(
                    severity="error",
                    issue_number=issue.number,
                    message=f"closed task must be status:done (found {status})",
                )
            )
        if "ready" in issue.labels and status != "status:not-started":
            findings.append(
                AuditFinding(
                    severity="error",
                    issue_number=issue.number,
                    message=f"ready label requires status:not-started (found {status})",
                )
            )
        if issue.state == "open" and issue.seq is None:
            findings.append(
                AuditFinding(
                    severity="error",
                    issue_number=issue.number,
                    message="open task is missing Seq marker",
                )
            )
        if issue.state == "open" and issue.task_id is None:
            findings.append(
                AuditFinding(
                    severity="error",
                    issue_number=issue.number,
                    message="open task is missing TASK-### title prefix or managed task marker",
                )
            )
        for dependency in issue.depends_on:
            if dependency.startswith("TASK-") and dependency == issue.task_id:
                findings.append(
                    AuditFinding(
                        severity="error",
                        issue_number=issue.number,
                        message=f"task cannot depend on itself ({dependency})",
                    )
                )

    # Objective gate: next runnable item must be a startable task, never in-progress/blocked/done.
    selection = build_queue(issues, mode="auto")
    try:
        next_item = choose_next_runnable(selection)
        next_status = lifecycle_status(next_item.issue)
        if next_status != "not-started":
            findings.append(
                AuditFinding(
                    severity="error",
                    issue_number=next_item.issue.number,
                    message=(
                        "next runnable queue item must be status:not-started "
                        f"(found status:{next_status})"
                    ),
                )
            )
    except CliError:
        # Empty/runnable-none queue is valid during full blockage or completion.
        pass

    return findings
