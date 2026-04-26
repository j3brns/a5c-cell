from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.issue_tool import tracker_client


class _Completed:
    def __init__(self, returncode: int = 0, stdout: str = "[]", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_merge_request_for_branch_includes_json_output_flag(monkeypatch, tmp_path) -> None:
    captured: list[list[str]] = []

    def _fake_run(cmd, **_kwargs):
        captured.append(cmd)
        return _Completed(stdout="[]")

    monkeypatch.setattr(tracker_client, "run", _fake_run)
    monkeypatch.setattr(tracker_client, "tracker_available", lambda: True)

    tracker_client.merge_request_for_branch(tmp_path, "owner/repo", "my-branch", "merged")

    assert captured, "expected glab to be called"
    cmd = captured[0]
    assert "-F" in cmd and "json" in cmd, (
        f"glab mr list must include '-F json' for _run_json to parse output; got: {cmd}"
    )


def test_merge_request_for_branch_returns_none_for_empty_list(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(tracker_client, "run", lambda *_a, **_kw: _Completed(stdout="[]"))
    monkeypatch.setattr(tracker_client, "tracker_available", lambda: True)

    result = tracker_client.merge_request_for_branch(tmp_path, "owner/repo", "branch", "merged")

    assert result is None


def test_merge_request_for_branch_normalises_merged_result(monkeypatch, tmp_path) -> None:
    payload = (
        '[{"iid": 42, "web_url": "https://example.com/mr/42", "title": "Fix it",'
        ' "draft": false, "work_in_progress": false, "merged_at": "2026-01-01T00:00:00Z"}]'
    )
    monkeypatch.setattr(tracker_client, "run", lambda *_a, **_kw: _Completed(stdout=payload))
    monkeypatch.setattr(tracker_client, "tracker_available", lambda: True)

    result = tracker_client.merge_request_for_branch(tmp_path, "owner/repo", "branch", "merged")

    assert result is not None
    assert result["number"] == 42
    assert result["url"] == "https://example.com/mr/42"
    assert result["mergedAt"] == "2026-01-01T00:00:00Z"
