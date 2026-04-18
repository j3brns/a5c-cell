from __future__ import annotations

from pathlib import Path
from typing import Literal

from scripts.issue_tool.constants import (
    DEPENDS_RE,
    SEQ_RE,
)
from scripts.issue_tool.logic import (
    lifecycle_status,
    parse_depends,
    parse_task_id_from_issue,
    queue_task_issues,
)
from scripts.issue_tool.models import Issue, QueueItem, QueueSelection
from scripts.issue_tool.shared import CliError
from scripts.issue_tool.tracker_client import list_issues


def parse_issue_meta(body: str) -> tuple[int | None, list[str]]:
    seq = int(m.group(1)) if (m := SEQ_RE.search(body or "")) else None
    depends = parse_depends(m.group(1)) if (m := DEPENDS_RE.search(body or "")) else []
    return seq, depends


def fetch_repo_issues(
    root: Path,
    repo: str,
    *,
    state: Literal["open", "closed", "all"] = "all",
) -> list[Issue]:
    data = list_issues(root, repo, state=state)
    if not isinstance(data, list):
        raise CliError("Unexpected issue list response")

    out: list[Issue] = []
    for raw in data:
        if not isinstance(raw, dict):
            continue
        labels = [x["name"] for x in raw.get("labels", []) if isinstance(x, dict) and "name" in x]
        body = str(raw.get("body") or "")
        seq, depends = parse_issue_meta(body)
        out.append(
            Issue(
                number=int(raw["number"]),
                title=str(raw.get("title") or ""),
                state=str(raw.get("state") or "").lower(),
                created_at=str(raw.get("createdAt") or raw.get("created_at") or ""),
                body=body,
                labels=labels,
                url=str(raw.get("url") or raw.get("html_url") or ""),
                task_id=parse_task_id_from_issue(raw),
                seq=seq,
                depends_on=depends,
            )
        )
    return out


def build_queue(
    issues: list[Issue],
    *,
    stream_label: str | None = None,
    from_issue: int | None = None,
    mode: Literal["auto", "ready", "open-task"] = "auto",
) -> QueueSelection:
    task_issues = queue_task_issues(issues)
    by_task_id = {i.task_id: i for i in task_issues if i.task_id}
    by_issue_ref = {f"#{i.number}": i for i in task_issues}
    source_notes: list[str] = []

    def stream_ok(issue: Issue) -> bool:
        return not stream_label or stream_label in issue.labels

    open_task = [i for i in task_issues if i.state == "open" and stream_ok(i)]
    if from_issue is not None:
        open_task = [i for i in open_task if i.number >= from_issue]
        source_notes.append(f"starting from issue #{from_issue}")

    queued_open_task = [i for i in open_task if lifecycle_status(i) != "in-progress"]
    open_ready = [i for i in queued_open_task if "ready" in i.labels]

    source_mode = mode
    if mode == "auto":
        if open_ready:
            source_mode = "ready"
        else:
            source_mode = "open-task"
            source_notes.append(
                "auto-fallback: no queued task issues labeled 'ready' (excludes status:in-progress)"
            )
    if source_mode == "ready":
        candidates = open_ready
    elif source_mode == "open-task":
        candidates = queued_open_task
    else:
        raise CliError(f"Unsupported queue mode: {mode}")

    items: list[QueueItem] = []
    for issue in candidates:
        reasons: list[str] = []
        if lifecycle_status(issue) == "blocked":
            reasons.append("blocked by status label (status:blocked)")
        for dep_task_id in issue.depends_on:
            dep = (
                by_issue_ref.get(dep_task_id)
                if dep_task_id.startswith("#")
                else by_task_id.get(dep_task_id)
            )
            if dep is None:
                reasons.append(f"missing dependency {dep_task_id}")
                continue
            if dep.state != "closed":
                reasons.append(f"blocked by {dep_task_id} (issue #{dep.number} is {dep.state})")
        items.append(QueueItem(issue=issue, runnable=(len(reasons) == 0), blocked_reasons=reasons))

    items.sort(
        key=lambda item: (
            item.issue.seq if item.issue.seq is not None else 999_999_999,
            item.issue.priority_rank(),
            item.issue.created_at or "",
            item.issue.number,
        )
    )
    return QueueSelection(
        source_mode=str(source_mode),
        items=items,
        source_note="; ".join(source_notes),
    )
