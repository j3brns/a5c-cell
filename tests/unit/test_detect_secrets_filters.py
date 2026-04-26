from __future__ import annotations

import json
import re
from pathlib import Path

from scripts.detect_secrets_filters import is_docs_sync_stamp_commit

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_SYNC_FILTER = "file://scripts/detect_secrets_filters.py::is_docs_sync_stamp_commit"


def _commit_line() -> str:
    commit = "".join(
        [
            "ee95d1f4",
            "bffae6d5",
            "3728a6d0",
            "a08c041d",
            "5c71e947",
        ]
    )
    return f'  "commit": "{commit}",'


def test_docs_sync_stamp_commit_filter_allows_exact_generated_commit_line() -> None:
    line = _commit_line()

    assert is_docs_sync_stamp_commit("docs/DOCS_SYNC.json", line)


def test_docs_sync_stamp_commit_filter_is_path_and_shape_scoped() -> None:
    line = _commit_line()

    assert not is_docs_sync_stamp_commit("other/DOCS_SYNC.json", line)
    assert not is_docs_sync_stamp_commit("docs/DOCS_SYNC.json", '  "secret": "x"')
    assert not is_docs_sync_stamp_commit(
        "docs/DOCS_SYNC.json",
        '  "commit": "not-a-40-character-hex-string",',
    )


def test_docs_sync_filter_is_not_registered_in_shared_baseline() -> None:
    baseline = json.loads((REPO_ROOT / ".secrets.baseline").read_text(encoding="utf-8"))

    assert all(item.get("path") != DOCS_SYNC_FILTER for item in baseline["filters_used"])


def test_docs_sync_filter_is_registered_on_all_secret_scan_targets() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")

    target_blocks = dict(
        re.findall(
            r"^## (validate-secrets-(?:diff|push|full)):[\s\S]*?\n"
            r"\1:\n([\s\S]*?)(?=\n## |\n[a-zA-Z0-9_-]+:|\Z)",
            makefile,
            flags=re.MULTILINE,
        )
    )

    assert DOCS_SYNC_FILTER in target_blocks["validate-secrets-full"]
    assert DOCS_SYNC_FILTER in target_blocks["validate-secrets-diff"]
    assert DOCS_SYNC_FILTER in target_blocks["validate-secrets-push"]
