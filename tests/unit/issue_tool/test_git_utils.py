from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.issue_tool import git_utils


def test_repo_root_prefers_git_common_dir_parent(monkeypatch) -> None:
    root = Path("/tmp/repo")

    def _run(cmd, **_kwargs):
        if cmd == ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"]:
            return subprocess.CompletedProcess(cmd, 0, str(root / ".git"), "")
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(git_utils, "run", _run)

    assert git_utils.repo_root() == root


def test_origin_repo_slug_defaults_to_gitlab_remote(monkeypatch) -> None:
    root = Path("/tmp/repo")

    def _run(cmd, **_kwargs):
        if cmd == ["git", "remote", "get-url", "gitlab"]:
            return subprocess.CompletedProcess(cmd, 0, "git@gitlab.com:owner/repo.git", "")
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(git_utils, "run", _run)
    monkeypatch.delenv("ISSUE_TRACKER_REMOTE", raising=False)

    assert git_utils.origin_repo_slug(root) == "owner/repo"


def test_origin_repo_slug_respects_issue_tracker_remote_env_var(monkeypatch) -> None:
    # Regression guard: previously used env_optional which was removed; now uses settings.ops.
    root = Path("/tmp/repo")

    def _run(cmd, **_kwargs):
        if cmd == ["git", "remote", "get-url", "upstream"]:
            return subprocess.CompletedProcess(cmd, 0, "git@gitlab.com:owner/upstream-repo.git", "")
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(git_utils, "run", _run)
    monkeypatch.setenv("ISSUE_TRACKER_REMOTE", "upstream")

    assert git_utils.origin_repo_slug(root) == "owner/upstream-repo"


def test_origin_repo_slug_falls_back_to_origin_when_gitlab_missing(monkeypatch) -> None:
    root = Path("/tmp/repo")

    def _run(cmd, **_kwargs):
        if cmd == ["git", "remote", "get-url", "gitlab"]:
            raise subprocess.CalledProcessError(128, cmd)
        if cmd == ["git", "remote", "get-url", "origin"]:
            return subprocess.CompletedProcess(cmd, 0, "https://gitlab.com/owner/repo", "")
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(git_utils, "run", _run)
    monkeypatch.delenv("ISSUE_TRACKER_REMOTE", raising=False)

    assert git_utils.origin_repo_slug(root) == "owner/repo"


def test_origin_repo_slug_raises_when_no_valid_slug(monkeypatch) -> None:
    root = Path("/tmp/repo")

    def _run(cmd, **_kwargs):
        raise subprocess.CalledProcessError(128, cmd)

    monkeypatch.setattr(git_utils, "run", _run)
    monkeypatch.delenv("ISSUE_TRACKER_REMOTE", raising=False)

    with pytest.raises(git_utils.CliError):
        git_utils.origin_repo_slug(root)
