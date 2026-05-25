from __future__ import annotations

import json
import subprocess
from pathlib import Path
from shutil import which
from urllib.parse import quote

from scripts.issue_tool.shared import CliError

WORKFLOW_LABEL_DEFAULTS: dict[str, tuple[str, str]] = {
    "ready": ("#0E8A16", "Ready to start"),
    "in-progress": ("#FBCA04", "Work in progress"),
    "review": ("#5319E7", "In review"),
    "done": ("#1D76DB", "Completed"),
    "status:in-progress": ("#FBCA04", "Execution started"),
    "status:not-started": ("#C2E0C6", "Not started"),
    "status:done": ("#1D76DB", "Completed"),
    "status:blocked": ("#B60205", "Blocked"),
    "type:task": ("#BFDADC", "Queueable implementation task"),
}


def shutil_which(binary: str) -> str | None:
    return which(binary)


def tracker_available() -> bool:
    return shutil_which("glab") is not None


def run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    capture_output: bool = True,
    text: bool = True,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        check=check,
        capture_output=capture_output,
        text=text,
        input=input_text,
    )


def _project_path(repo: str) -> str:
    return quote(repo.removesuffix(".git").strip("/"), safe="")


def _state_for_gitlab(state: str) -> str:
    return {"open": "opened", "closed": "closed", "all": "all"}.get(state, state)


def _normalise_issue(raw: dict) -> dict:
    labels = raw.get("labels") or []
    return {
        "number": raw.get("iid") or raw.get("number"),
        "title": raw.get("title") or "",
        "body": raw.get("description") or raw.get("body") or "",
        "labels": [{"name": str(label)} for label in labels],
        "state": "closed" if raw.get("state") == "closed" else "open",
        "createdAt": raw.get("created_at") or raw.get("createdAt") or "",
        "updatedAt": raw.get("updated_at") or raw.get("updatedAt") or "",
        "url": raw.get("web_url") or raw.get("url") or "",
    }


def _normalise_mr(raw: dict) -> dict:
    return {
        "number": raw.get("iid") or raw.get("number"),
        "url": raw.get("web_url") or raw.get("url") or "",
        "title": raw.get("title") or "",
        "state": raw.get("state") or "",
        "isDraft": bool(raw.get("draft") or raw.get("work_in_progress")),
        "mergedAt": raw.get("merged_at") or raw.get("mergedAt"),
    }


def _run_json(args: list[str], *, root: Path) -> object:
    if not tracker_available():
        raise CliError("glab CLI not found in PATH")
    cmd = ["glab", *args]
    try:
        proc = run(cmd, cwd=root)
    except subprocess.CalledProcessError as exc:
        raise CliError(
            f"glab command failed ({exc.returncode}): {' '.join(cmd)}\n"
            f"{(exc.stderr or exc.stdout or '').strip()}"
        ) from exc
    return _parse_glab_json(proc.stdout) if proc.stdout.strip() else {}


def _parse_glab_json(stdout: str) -> object:
    """Parse glab JSON, including --paginate output with concatenated page arrays."""
    text = stdout.strip()
    decoder = json.JSONDecoder()
    values: list[object] = []
    index = 0

    while index < len(text):
        value, end = decoder.raw_decode(text, index)
        values.append(value)
        index = end
        while index < len(text) and text[index].isspace():
            index += 1

    if len(values) == 1:
        return values[0]
    if all(isinstance(value, list) for value in values):
        merged: list[object] = []
        for value in values:
            if isinstance(value, list):
                merged.extend(value)
        return merged
    return values


def _run_text(cmd: list[str], *, root: Path) -> str:
    if not tracker_available():
        raise CliError("glab CLI not found in PATH")
    try:
        return run(cmd, cwd=root).stdout
    except subprocess.CalledProcessError as exc:
        raise CliError(
            f"glab command failed ({exc.returncode}): {' '.join(cmd)}\n"
            f"{(exc.stderr or exc.stdout or '').strip()}"
        ) from exc


def _api(endpoint: str, *, root: Path, paginate: bool = False) -> object:
    args = ["api", endpoint]
    if paginate:
        args.append("--paginate")
    return _run_json(args, root=root)


def list_issues(root: Path, repo: str, *, state: str) -> list[dict]:
    endpoint = (
        f"projects/{_project_path(repo)}/issues?state={_state_for_gitlab(state)}&per_page=100"
    )
    data = _api(endpoint, root=root, paginate=True)
    if not isinstance(data, list):
        raise CliError("Unexpected GitLab issue list response")
    return [_normalise_issue(item) for item in data if isinstance(item, dict)]


def get_issue(root: Path, repo: str, issue_id: int | str, *, comments: bool = False) -> dict:
    issue = _api(f"projects/{_project_path(repo)}/issues/{issue_id}", root=root)
    if not isinstance(issue, dict):
        raise CliError(f"Unexpected GitLab issue response for #{issue_id}")
    normalised = _normalise_issue(issue)
    if comments:
        notes = _api(
            f"projects/{_project_path(repo)}/issues/{issue_id}/notes?per_page=100",
            root=root,
            paginate=True,
        )
        if not isinstance(notes, list):
            raise CliError(f"Unexpected GitLab notes response for #{issue_id}")
        normalised["comments"] = [
            {"body": str(note.get("body") or "")} for note in notes if isinstance(note, dict)
        ]
    return normalised


def create_issue(
    root: Path,
    repo: str,
    *,
    title: str,
    description: str,
    labels: list[str],
) -> str:
    for label in labels:
        ensure_label_exists(root, repo, label)
    return _run_text(
        [
            "glab",
            "issue",
            "create",
            "-R",
            repo,
            "--title",
            title,
            "--description",
            description,
            "--label",
            ",".join(labels),
            "--yes",
        ],
        root=root,
    )


def update_issue_labels(
    root: Path,
    repo: str,
    issue_id: int | str,
    *,
    add: list[str] | None = None,
    remove: list[str] | None = None,
) -> None:
    add = add or []
    remove = remove or []
    if not add and not remove:
        return
    for label in add:
        ensure_label_exists(root, repo, label)
    cmd = ["glab", "issue", "update", str(issue_id), "-R", repo]
    if add:
        cmd += ["--label", ",".join(add)]
    if remove:
        cmd += ["--unlabel", ",".join(remove)]
    _run_text(cmd, root=root)


def close_issue(root: Path, repo: str, issue_id: int | str) -> None:
    _run_text(["glab", "issue", "close", str(issue_id), "-R", repo], root=root)


def comment_issue(root: Path, repo: str, issue_id: int | str, body: str) -> None:
    _run_text(
        ["glab", "issue", "note", str(issue_id), "-R", repo, "--message", body],
        root=root,
    )


def merge_request_for_branch(root: Path, repo: str, branch: str, state: str) -> dict | None:
    cmd = [
        "mr",
        "list",
        "-R",
        repo,
        "--source-branch",
        branch,
        "--per-page",
        "1",
        "-F",
        "json",
    ]
    if state == "all":
        cmd.append("--all")
    elif state == "merged":
        cmd.append("--merged")
    elif state == "closed":
        cmd.append("--closed")
    data = _run_json(cmd, root=root)
    if not isinstance(data, list) or not data:
        return None
    return _normalise_mr(data[0]) if isinstance(data[0], dict) else None


def ensure_label_exists(root: Path, repo: str, label: str) -> None:
    color, desc = WORKFLOW_LABEL_DEFAULTS.get(label, ("#BFDADC", "Workflow label"))
    try:
        _run_text(
            [
                "glab",
                "label",
                "create",
                "-R",
                repo,
                "--name",
                label,
                "--color",
                color,
                "--description",
                desc,
            ],
            root=root,
        )
    except CliError as exc:
        if "already exists" not in str(exc).lower():
            raise
