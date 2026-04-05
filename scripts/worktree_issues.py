#!/usr/bin/env python3
"""Legacy compatibility shim for the issue-tool CLI.

Canonical invocation is `python -m scripts.issue_tool ...`.
This module remains only so older local entry paths keep delegating without
using an exec-based loader.
"""

from __future__ import annotations

import sys

from scripts.issue_tool import main
from scripts.issue_tool.shared import CliError

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CliError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        raise SystemExit(130)
