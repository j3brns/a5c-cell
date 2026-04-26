from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Any


def _load_generated_state_audit_module() -> Any:
    repo_root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "generated_state_audit", repo_root / "scripts" / "generated_state_audit.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


generated_state_audit = _load_generated_state_audit_module()


class _Completed:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_cmd_check_passes_when_cdk_out_is_ignored_and_untracked(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    monkeypatch.setattr(generated_state_audit, "ROOT", tmp_path)

    def _runner(cmd, **kwargs):
        _ = kwargs
        if cmd[:2] == ["git", "check-ignore"]:
            return _Completed(0, stdout=cmd[-1])
        return _Completed(0, stdout="")

    rc = generated_state_audit.cmd_check(runner=_runner)
    out = capsys.readouterr().out

    assert rc == 0
    assert "Generated state audit: PASS" in out


def test_cmd_check_fails_for_retired_generated_cache(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setattr(generated_state_audit, "ROOT", tmp_path)
    (tmp_path / "infra" / "cdk" / "generated").mkdir(parents=True)

    def _runner(cmd, **kwargs):
        _ = cmd, kwargs
        return _Completed(0, stdout="")

    rc = generated_state_audit.cmd_check(runner=_runner)
    out = capsys.readouterr().out

    assert rc == 1
    assert "retired generated cache directory still exists" in out


def test_cmd_check_fails_for_tracked_cdk_output(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setattr(generated_state_audit, "ROOT", tmp_path)

    def _runner(cmd, **kwargs):
        _ = kwargs
        if cmd[:2] == ["git", "check-ignore"]:
            return _Completed(0, stdout=cmd[-1])
        if cmd[:2] == ["git", "ls-files"]:
            return _Completed(0, stdout="infra/cdk/cdk.out/platform.template.json\n")
        return _Completed(0, stdout="")

    rc = generated_state_audit.cmd_check(runner=_runner)
    out = capsys.readouterr().out

    assert rc == 1
    assert "generated CDK artifact is tracked" in out


def test_cmd_check_fails_when_cdk_output_is_not_ignored(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    monkeypatch.setattr(generated_state_audit, "ROOT", tmp_path)

    def _runner(cmd, **kwargs):
        _ = kwargs
        if cmd[:2] == ["git", "check-ignore"]:
            return _Completed(1, stderr="not ignored")
        return _Completed(0, stdout="")

    rc = generated_state_audit.cmd_check(runner=_runner)
    out = capsys.readouterr().out

    assert rc == 1
    assert "generated CDK path is not ignored" in out


def test_cmd_check_reports_git_failures(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setattr(generated_state_audit, "ROOT", tmp_path)

    def _runner(cmd, **kwargs):
        _ = kwargs
        if cmd[:2] == ["git", "check-ignore"]:
            return _Completed(0, stdout=cmd[-1])
        raise subprocess.SubprocessError("git unavailable")

    rc = generated_state_audit.cmd_check(runner=_runner)
    out = capsys.readouterr().out

    assert rc == 1
    assert "generated state audit could not inspect git state" in out
