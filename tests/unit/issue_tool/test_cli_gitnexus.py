from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ._support import gitnexus


def test_run_gitnexus_command_clears_corrupt_npx_cache_and_retries(monkeypatch, capsys):
    calls: list[list[str]] = []

    def _subprocess_run(cmd, **kwargs):
        calls.append(cmd)
        if len(calls) == 1:
            return subprocess.CompletedProcess(
                cmd,
                217,
                "",
                (
                    "npm error code ENOTEMPTY\n"
                    "npm error path /home/julesb/.npm/_npx/hash/node_modules/chownr\n"
                ),
            )
        return subprocess.CompletedProcess(cmd, 0, "GitNexus ready\n", "")

    removed: list[Path] = []

    monkeypatch.setattr(gitnexus.subprocess, "run", _subprocess_run)
    monkeypatch.setattr(gitnexus, "gitnexus_cli_path", lambda: None)
    monkeypatch.setattr(
        gitnexus,
        "gitnexus_npx_cache_dir",
        lambda: Path("/home/julesb/.npm/_npx"),
    )
    monkeypatch.setattr(
        gitnexus.shutil,
        "rmtree",
        lambda path, ignore_errors: removed.append(path),
    )

    proc = gitnexus.run_gitnexus_command(Path("/tmp/repo"), ["status"], check=False)

    assert proc.returncode == 0
    assert calls == [["npx", "--yes", "gitnexus", "status"], ["npx", "--yes", "gitnexus", "status"]]
    assert removed == [Path("/home/julesb/.npm/_npx")]
    assert "clearing corrupt npx cache" in capsys.readouterr().out


def test_run_gitnexus_command_prefers_local_gitnexus_cli(monkeypatch):
    calls: list[list[str]] = []

    def _subprocess_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "GitNexus ready\n", "")

    monkeypatch.setattr(gitnexus.subprocess, "run", _subprocess_run)
    monkeypatch.setattr(
        gitnexus,
        "gitnexus_cli_path",
        lambda: Path("/mnt/c/Users/julia/gitnexus/gitnexus/dist/cli/index.js"),
    )
    monkeypatch.setattr(gitnexus, "shutil_which", lambda name: f"/usr/bin/{name}")

    proc = gitnexus.run_gitnexus_command(Path("/tmp/repo"), ["status"], check=False)

    assert proc.returncode == 0
    assert calls == [
        ["/usr/bin/node", "/mnt/c/Users/julia/gitnexus/gitnexus/dist/cli/index.js", "status"]
    ]


def test_prepare_gitnexus_for_worktree_warns_when_npm_cache_path_unavailable(monkeypatch, capsys):
    calls: list[list[str]] = []

    def _subprocess_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(
            cmd,
            217,
            "",
            (
                "npm error code ENOTEMPTY\n"
                "npm error path /home/julesb/.npm/_npx/hash/node_modules/chownr\n"
            ),
        )

    monkeypatch.setattr(gitnexus, "gitnexus_cli_path", lambda: None)
    monkeypatch.setattr(gitnexus, "shutil_which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(gitnexus.subprocess, "run", _subprocess_run)
    monkeypatch.setattr(gitnexus, "gitnexus_npx_cache_dir", lambda: None)

    gitnexus.prepare_gitnexus_for_worktree(Path("/tmp/repo"))

    captured = capsys.readouterr()
    assert calls == [
        ["npx", "--yes", "gitnexus", "status"],
        ["npx", "--yes", "gitnexus", "analyze", "--help"],
        ["npx", "--yes", "gitnexus", "analyze", "--help"],
        ["npx", "--yes", "gitnexus", "analyze"],
    ]
    assert "npm cache path unavailable" in captured.err
    assert "rebuilding local index" in captured.out


def test_gitnexus_analyze_supports_detects_latest_flags(monkeypatch):
    def _run_gitnexus_command(_path, args, *, check, timeout_seconds=None):
        assert args == ["analyze", "--help"]
        assert timeout_seconds == 30
        return subprocess.CompletedProcess(
            args,
            0,
            "--embeddings\n--skip-agents-md\n--no-stats\n",
            "",
        )

    monkeypatch.setattr(gitnexus, "run_gitnexus_command", _run_gitnexus_command)

    assert gitnexus.gitnexus_analyze_supports("--skip-agents-md")
    assert gitnexus.gitnexus_analyze_supports("--no-stats")
    assert not gitnexus.gitnexus_analyze_supports("--missing-option")


def test_prepare_gitnexus_preserves_existing_embeddings(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    (repo / ".gitnexus").mkdir(parents=True)
    (repo / ".gitnexus" / "meta.json").write_text(
        json.dumps({"stats": {"embeddings": 12}}),
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    def _run_gitnexus_command(_path, args, *, check, timeout_seconds=None):
        calls.append(args)
        if args == ["status"]:
            return subprocess.CompletedProcess(args, 1, "stale index", "")
        if args == ["analyze", "--help"]:
            return subprocess.CompletedProcess(args, 0, "--skip-agents-md\n--no-stats\n", "")
        return subprocess.CompletedProcess(args, 0, "analyzed", "")

    monkeypatch.setattr(gitnexus, "gitnexus_cli_path", lambda: Path("/usr/bin/gitnexus"))
    monkeypatch.setattr(gitnexus, "shutil_which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(gitnexus, "run_gitnexus_command", _run_gitnexus_command)

    gitnexus.prepare_gitnexus_for_worktree(repo)

    assert calls == [
        ["status"],
        ["analyze", "--help"],
        ["analyze", "--help"],
        ["analyze", "--embeddings", "--skip-agents-md", "--no-stats"],
    ]
