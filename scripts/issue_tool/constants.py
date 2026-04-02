from __future__ import annotations
import re

WORKTREE_BRANCH_REGEX = re.compile(r"^wt/[a-z0-9._-]+/[0-9]+-[a-z0-9._-]+$")
WORKTREE_BRANCH_ISSUE_RE = re.compile(r"^wt/[^/]+/([0-9]+)-")
MANAGED_TASK_ID_RE = re.compile(r"<!--\s*codex-task-id:\s*(TASK-\d+)\s*-->", re.I)
SEQ_RE = re.compile(r"(?mi)^Seq:\s*(\d+)\s*$")
DEPENDS_RE = re.compile(r"(?mi)^Depends on:\s*(.+?)\s*$")
TASK_ID_TOKEN_RE = re.compile(r"TASK-\d+")
TITLE_TASK_RE = re.compile(r"^(TASK-\d+):\s")
CR_TITLE_RE = re.compile(r"^CR-\d+\b", re.I)

STATUS_LABELS = {"status:not-started", "status:in-progress", "status:blocked", "status:done"}
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

WORKTREE_CLOSEOUT_DIR = ".build/worktree-closeouts"
WORKTREE_RUNS_DIR = ".build/worktree-runs"
WORKTREE_AGENT_RUN_DIR = ".build/agent-run"
WORKTREE_STATE_DIR = ".build/worktree-state"
VALIDATION_RECEIPTS_DIR = ".build/validation-receipts"

DETACHED_STARTUP_PROBE_SECONDS = 0.5
DETACHED_STARTUP_PROBE_INTERVAL_SECONDS = 0.1
