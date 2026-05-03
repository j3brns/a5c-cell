from __future__ import annotations

import importlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

worktree_issues_legacy = (
    importlib.import_module("scripts.issue_tool.cli") if False else None
)  # Deleted soon
gitnexus = importlib.import_module("scripts.issue_tool.gitnexus")
multiplexer = importlib.import_module("scripts.issue_tool.multiplexer")
pre_provisioning = importlib.import_module("scripts.issue_tool.pre_provisioning")
evidence = importlib.import_module("scripts.issue_tool.evidence")
closeout = importlib.import_module("scripts.issue_tool.closeout")
worktree = importlib.import_module("scripts.issue_tool.worktree")
git_utils = importlib.import_module("scripts.issue_tool.git_utils")
issue_queue = importlib.import_module("scripts.issue_tool.issue_queue")
tracker_client = importlib.import_module("scripts.issue_tool.tracker_client")
shared = importlib.import_module("scripts.issue_tool.shared")
logic = importlib.import_module("scripts.issue_tool.logic")
models = importlib.import_module("scripts.issue_tool.models")

# Commands
commands_common = importlib.import_module("scripts.issue_tool.commands.common")
commands_issue = importlib.import_module("scripts.issue_tool.commands.issue")
commands_worktree = importlib.import_module("scripts.issue_tool.commands.worktree")
commands_finish = importlib.import_module("scripts.issue_tool.commands.finish")
commands_batch = importlib.import_module("scripts.issue_tool.commands.batch")
commands_agent = importlib.import_module("scripts.issue_tool.commands.agent")
commands_menu = importlib.import_module("scripts.issue_tool.commands.menu")


class WorktreeIssuesProxy:
    def __init__(self):
        # Expose models and shared logic
        self.Issue = models.Issue
        self.QueueItem = models.QueueItem
        self.QueueSelection = models.QueueSelection
        self.WorktreeInfo = models.WorktreeInfo
        self.CliError = shared.CliError

        # Expose common utilities
        self.issue_status_rows = commands_common.issue_status_rows
        self.evidence_drift_findings = commands_common.evidence_drift_findings
        self.stale_evidence_findings = commands_common.stale_evidence_findings
        self.auto_detect_mux = multiplexer.auto_detect_mux
        self.pid_is_running = worktree.pid_is_running
        self.worktree_agent_running = worktree.worktree_agent_running
        self.create_worktree_for_issue = worktree.create_worktree_for_issue
        self.start_worktree_pre_provision = pre_provisioning.start_worktree_pre_provision
        self.handoff_to_agent_or_shell = commands_common.handoff_to_agent_or_shell
        self.await_worktree_ready_if_provisioning = (
            pre_provisioning.await_worktree_ready_if_provisioning
        )
        self.build_agent_prompt_for_worktree = commands_common.build_agent_prompt_for_worktree
        self.build_review_prompt_for_worktree = commands_common.build_review_prompt_for_worktree
        self.launch_agent_detached = commands_common.launch_agent_detached

        # Additional attributes for test compatibility
        self.current_path = git_utils.current_path
        self.worktree_issue_id = commands_common.worktree_issue_id
        self.find_linked_worktree_for_issue = worktree.find_linked_worktree_for_issue
        self.fetch_issue_labels_for_prompt = commands_common.fetch_issue_labels_for_prompt

    def _shim(self, fn, args):
        if args is not None and hasattr(args, "__dict__"):
            # Convert argparse.Namespace to kwargs
            d = vars(args).copy()
            # Handle some renames if necessary (e.g. json -> json_output)
            if "json" in d:
                d["json_output"] = d.pop("json")
            if "all" in d:
                d["include_all"] = d.pop("all")
            if "agents" in d:
                d["agents_list"] = d.pop("agents")
            if "issue" in d and fn.__name__ == "cmd_worktree_create":
                d["issue_number"] = d.pop("issue")
            # Remove any keys that are not in the function signature
            import inspect

            sig = inspect.signature(fn)
            filtered = {k: v for k, v in d.items() if k in sig.parameters}
            return fn(**filtered)
        return fn(args)

    def cmd_agent_handoff(self, args):
        return self._shim(commands_agent.cmd_agent_handoff, args)

    def cmd_wt_batch(self, args):
        return self._shim(commands_batch.cmd_wt_batch, args)

    def cmd_issue_status(self, args):
        return self._shim(commands_issue.cmd_issue_status, args)

    def cmd_worktree_resume(self, args):
        return self._shim(commands_worktree.cmd_worktree_resume, args)

    def cmd_worktree_next(self, args):
        return self._shim(commands_worktree.cmd_worktree_next, args)

    def cmd_finish_close(self, args):
        return self._shim(commands_finish.cmd_finish_close, args)

    def cmd_issue_queue(self, args):
        return self._shim(commands_issue.cmd_issue_queue, args)

    def cmd_issue_create(self, args):
        return self._shim(commands_issue.cmd_issue_create, args)

    def cmd_issue_evidence(self, args):
        return self._shim(commands_issue.cmd_issue_evidence, args)

    def cmd_write_validation_receipt(self, args):
        return self._shim(commands_issue.cmd_write_validation_receipt, args)

    def cmd_issues_audit(self, args):
        return self._shim(commands_issue.cmd_issues_audit, args)

    def cmd_issues_reconcile(self, args):
        return self._shim(commands_issue.cmd_issues_reconcile, args)

    def cmd_issue_repair_stale_locks(self, args):
        return self._shim(commands_issue.cmd_issue_repair_stale_locks, args)

    def cmd_preflight(self, args):
        return self._shim(commands_worktree.cmd_preflight, args)

    def cmd_pre_validate(self, args):
        return self._shim(commands_worktree.cmd_pre_validate, args)

    def cmd_gitnexus_refresh(self, args):
        return self._shim(commands_worktree.cmd_gitnexus_refresh, args)

    def cmd_push_branch(self, args):
        return self._shim(commands_worktree.cmd_push_branch, args)

    def cmd_finish_summary(self, args):
        return self._shim(commands_finish.cmd_finish_summary, args)

    def cmd_menu(self, args=None):
        if args is None:
            return commands_menu.cmd_menu()
        return self._shim(commands_menu.cmd_menu, args)


worktree_issues = WorktreeIssuesProxy()


def _issue(
    *,
    number: int,
    task_id: str,
    seq: int,
    state: str = "open",
    labels: list[str] | None = None,
    depends_on: list[str] | None = None,
):
    return models.Issue(
        number=number,
        title=f"{task_id}: Test issue {number}",
        state=state,
        created_at="2026-01-01T00:00:00Z",
        body=f"Seq: {seq}\nDepends on: none",
        labels=labels or ["type:task", "status:not-started"],
        url=f"https://example.test/issues/{number}",
        task_id=task_id,
        seq=seq,
        depends_on=depends_on or [],
    )
