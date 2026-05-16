from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from platform_config import settings
from scripts.issue_tool.shared import CliError


def run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    capture_output: bool = True,
    text: bool = True,
    input_text: str | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        check=check,
        capture_output=capture_output,
        text=text,
        input=input_text,
        timeout=timeout,
    )


def eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


def repo_root() -> Path:
    try:
        common_dir = Path(
            run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"]).stdout.strip()
        )
        if common_dir.name == ".git":
            return common_dir.parent.resolve()
        return Path(run(["git", "rev-parse", "--show-toplevel"]).stdout.strip()).resolve()
    except subprocess.CalledProcessError as exc:
        raise CliError("Not inside a git repository") from exc


def current_path() -> Path:
    return Path.cwd().resolve()


def _remote_url(root: Path, remote: str) -> str | None:
    try:
        return run(["git", "remote", "get-url", remote], cwd=root).stdout.strip()
    except subprocess.CalledProcessError:
        return None


def _repo_slug_from_url(url: str) -> str | None:
    if url.startswith("git@") and "gitlab.com:" in url:
        path = url.split("gitlab.com:", 1)[1]
    elif "gitlab.com/" in url:
        path = url.split("gitlab.com/", 1)[1]
    else:
        return None
    return path.removesuffix(".git").strip("/")


def origin_repo_slug(root: Path) -> str:
    preferred_remote = settings.ops.issue_tracker_remote or "gitlab"
    remotes = [preferred_remote, "origin"]
    for remote in dict.fromkeys(remotes):
        url = _remote_url(root, remote)
        if not url:
            continue
        slug = _repo_slug_from_url(url)
        if slug:
            return slug
    raise CliError("Could not resolve a GitLab repository slug from git remotes")
