from __future__ import annotations

from pathlib import Path

import pytest

from scripts import ensure_ubs


def test_ensure_reuses_valid_existing_runner(tmp_path: Path) -> None:
    runner = ensure_ubs.install_path(tmp_path)
    runner.parent.mkdir(parents=True)
    runner.write_bytes(b"known-good")
    expected = ensure_ubs.sha256(runner)

    original_sha = ensure_ubs.UBS_SHA256
    ensure_ubs.UBS_SHA256 = expected
    try:
        assert ensure_ubs.ensure(tmp_path) == runner
    finally:
        ensure_ubs.UBS_SHA256 = original_sha


def test_ensure_rejects_checksum_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(url: str, timeout: int = 30) -> object:
        _ = (url, timeout)

        class FakeResponse:
            def __enter__(self) -> FakeResponse:
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self) -> bytes:
                return b"unexpected"

        return FakeResponse()

    monkeypatch.setattr(ensure_ubs.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="checksum mismatch"):
        ensure_ubs.ensure(tmp_path)
