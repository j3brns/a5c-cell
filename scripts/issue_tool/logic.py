from __future__ import annotations

import re
from pathlib import Path

from scripts.issue_tool.constants import (
    MANAGED_TASK_ID_RE,
    STATUS_LABELS,
    TASK_ID_TOKEN_RE,
    TITLE_TASK_RE,
)
from scripts.issue_tool.models import Issue


def parse_task_id_from_issue(issue: dict) -> str | None:
    body = str(issue.get("body") or "")
    title = str(issue.get("title") or "")
    if m := MANAGED_TASK_ID_RE.search(body):
        return m.group(1).upper()
    if m := TITLE_TASK_RE.match(title):
        return m.group(1).upper()
    return None


def parse_depends(text: str | None) -> list[str]:
    if not text:
        return []
    text = text.strip()
    if not text or text.lower() in {"none", "n/a", "-"}:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for token in re.findall(r"#\d+|TASK-\d+", text.upper()):
        if token not in seen:
            seen.add(token)
            out.append(token)
    return out


def lifecycle_status(issue: Issue) -> str:
    labels = set(issue.labels)
    if "status:blocked" in labels:
        return "blocked"
    if "status:in-progress" in labels:
        return "in-progress"
    if "status:done" in labels:
        return "done"
    if "status:not-started" in labels:
        return "not-started"
    return "unknown"


def queue_task_issues(issues: list[Issue]) -> list[Issue]:
    return [issue for issue in issues if "type:task" in issue.labels and not issue.is_parent_cr]


def status_labels(issue: Issue) -> list[str]:
    return [label for label in issue.labels if label in STATUS_LABELS]


def choose_reconciled_status(issue: Issue) -> str:
    statuses = status_labels(issue)
    state = issue.state
    if state == "closed":
        return "status:done"
    # open state
    if "status:in-progress" in statuses:
        return "status:in-progress"
    if "status:blocked" in statuses:
        return "status:blocked"
    if "status:not-started" in statuses:
        return "status:not-started"
    # open+done or missing/invalid status should return to startable backlog state
    return "status:not-started"


def reconcile_issue_label_changes(issue: Issue) -> tuple[list[str], list[str]]:
    """Return (add_labels, remove_labels) to enforce lifecycle label policy."""
    desired = choose_reconciled_status(issue)
    labels = set(issue.labels)
    current_status = set(status_labels(issue))
    remove_labels = sorted(current_status - {desired})
    add_labels: list[str] = []
    if desired not in labels:
        add_labels.append(desired)
    if "ready" in labels and desired != "status:not-started":
        remove_labels.append("ready")
    return add_labels, sorted(set(remove_labels))


def edit_issue_labels(root: Path, repo: str, issue_number: int, labels: list[str]) -> None:
    from scripts.issue_tool.tracker_client import update_issue_labels

    if not labels:
        return
    add_labels: list[str] = []
    remove_labels: list[str] = []
    for label in labels:
        if label in STATUS_LABELS:
            add_labels.append(label)
        else:
            remove_labels.append(label.removeprefix("-"))
    update_issue_labels(root, repo, issue_number, add=add_labels, remove=remove_labels)


def normalize_closed_issue_labels(root: Path, repo: str, issue_id: int, info: dict | None) -> bool:
    if not info:
        return False
    labels = [x["name"] for x in info.get("labels", []) if isinstance(x, dict) and "name" in x]
    issue = Issue(
        number=issue_id,
        title=str(info.get("title", "")),
        state=str(info.get("state", "")).lower(),
        created_at="",
        body="",
        labels=labels,
        url=str(info.get("url", "")),
        task_id=None,
        seq=None,
        depends_on=[],
    )
    add_labels, remove_labels = reconcile_issue_label_changes(issue)
    label_ops = add_labels + [f"-{label}" for label in remove_labels]
    if not label_ops:
        return False
    edit_issue_labels(root, repo, issue.number, label_ops)
    return True


def assert_issue_startable(issue: Issue, *, allow_blocked: bool) -> None:
    from scripts.issue_tool.shared import CliError

    if issue.state != "open":
        raise CliError(f"Issue #{issue.number} is {issue.state}; must be open to start work")
    status = lifecycle_status(issue)
    if status == "unknown":
        raise CliError(
            f"Issue #{issue.number} is missing/invalid status:* label. Run `make issues-reconcile`."
        )
    if status == "done":
        raise CliError(f"Issue #{issue.number} is status:done; cannot start new work")
    if status == "in-progress":
        raise CliError(f"Issue #{issue.number} is already status:in-progress. Use worktree-resume.")
    if status == "blocked" and not allow_blocked:
        raise CliError(f"Issue #{issue.number} is status:blocked (use --allow-blocked to override)")
