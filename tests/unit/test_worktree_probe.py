from __future__ import annotations

import os

from scripts.worktree_probe import main, run_probe


def _touch_executable(path):
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(0o755)


def _make_ready_worktree(tmp_path, monkeypatch):
    for relative in (
        ".venv",
        "infra/cdk/node_modules",
        "spa/node_modules",
    ):
        (tmp_path / relative).mkdir(parents=True)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("uv", "node", "npm", "npx", "git", "glab"):
        _touch_executable(bin_dir / name)
    monkeypatch.setenv("PATH", str(bin_dir))


def test_run_probe_passes_when_worktree_dependencies_are_present(tmp_path, monkeypatch):
    _make_ready_worktree(tmp_path, monkeypatch)

    result = run_probe(tmp_path)

    assert result.ok is True
    assert result.missing_paths == []
    assert result.missing_binaries == []


def test_run_probe_reports_missing_dependency_directories_and_agent_binaries(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _touch_executable(bin_dir / "uv")
    monkeypatch.setenv("PATH", str(bin_dir))

    result = run_probe(tmp_path)

    assert result.ok is False
    assert result.missing_paths == [
        ".venv",
        "infra/cdk/node_modules",
        "spa/node_modules",
    ]
    assert result.missing_binaries == ["node", "npm", "npx", "git", "glab"]


def test_run_probe_test_mode_does_not_require_handoff_binaries(tmp_path, monkeypatch):
    _make_ready_worktree(tmp_path, monkeypatch)
    for name in ("git", "glab"):
        (tmp_path / "bin" / name).unlink()

    result = run_probe(tmp_path, mode="test")

    assert result.ok is True


def test_main_prints_ensure_tools_instruction_on_failure(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PATH", os.devnull)

    exit_code = main(["--root", str(tmp_path)])

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "Worktree probe: FAILED" in output
    assert "Run: make ensure-tools" in output
