from __future__ import annotations

import re
from pathlib import PurePosixPath

_DOCS_SYNC_COMMIT_RE = re.compile(r'^\s*"commit": "[0-9a-f]{40}",?\s*$')


def is_docs_sync_stamp_commit(filename: str, line: str) -> bool:
    """Allow the generated docs sync release commit stamp only."""
    normalized = str(PurePosixPath(filename.replace("\\", "/")))
    return normalized == "docs/DOCS_SYNC.json" and bool(_DOCS_SYNC_COMMIT_RE.fullmatch(line))
