from __future__ import annotations
from scripts.issue_tool.models import Issue
from scripts.issue_tool.constants import (
    MANAGED_TASK_ID_RE,
    TITLE_TASK_RE,
    TASK_ID_TOKEN_RE,
    STATUS_LABELS,
)

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
    for token in TASK_ID_TOKEN_RE.findall(text.upper()):
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
