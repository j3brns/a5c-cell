from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from platform_config import settings
from scripts.issue_tool.git_utils import eprint, run
from scripts.issue_tool.shared import parse_bool_env
from scripts.issue_tool.tracker_client import shutil_which


def gitnexus_refresh_enabled() -> bool:
    return parse_bool_env("WORKTREE_GITNEXUS_REFRESH", True)


def gitnexus_npx_cache_dir() -> Path | None:
    if shutil_which("npm") is None:
        return None
    try:
        cache_dir = run(["npm", "config", "get", "cache"]).stdout.strip()
    except subprocess.CalledProcessError:
        return None
    if not cache_dir or cache_dir == "undefined":
        return None
    return Path(cache_dir) / "_npx"


def gitnexus_npx_cache_corrupted(output: str) -> bool:
    lowered = output.lower()
    return "enotempty" in lowered and "/_npx/" in lowered


def gitnexus_embeddings_present(path: Path) -> bool:
    meta_path = path / ".gitnexus" / "meta.json"
    if not meta_path.exists():
        return False
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    embeddings = meta.get("stats", {}).get("embeddings", 0)
    try:
        return int(embeddings) > 0
    except (TypeError, ValueError):
        return False


def gitnexus_analyze_supports(option: str) -> bool:
    try:
        proc = run_gitnexus_command(
            Path.cwd(),
            ["analyze", "--help"],
            check=False,
            timeout_seconds=30,
        )
    except subprocess.CalledProcessError:
        return False
    output = "\n".join(
        part.strip() for part in (proc.stdout or "", proc.stderr or "") if part.strip()
    )
    return option in output


def gitnexus_cli_path() -> Path | None:
    override = settings.ops.worktree_gitnexus_cli
    if override:
        candidate = Path(override).expanduser()
        if candidate.exists():
            return candidate
    which = shutil_which("gitnexus")
    if which:
        candidate = Path(which).expanduser()
        if candidate.exists():
            return candidate
    return None


def gitnexus_timeout_seconds() -> float:
    raw = settings.ops.worktree_gitnexus_timeout_seconds or "300"
    try:
        value = float(raw)
    except ValueError:
        return 300.0
    return max(value, 1.0)


def run_gitnexus_command(
    path: Path,
    args: list[str],
    *,
    check: bool,
    timeout_seconds: float | None = None,
) -> subprocess.CompletedProcess[str]:
    cli_path = gitnexus_cli_path()
    node = shutil_which("node")
    if cli_path is not None and node is not None:
        if cli_path.suffix == ".js":
            cmd = [node, str(cli_path), *args]
        else:
            cmd = [str(cli_path), *args]
    else:
        cmd = ["npx", "--yes", "gitnexus", *args]
    attempts = 0
    while True:
        attempts += 1
        try:
            proc = subprocess.run(
                cmd,
                cwd=path,
                capture_output=True,
                text=True,
                check=False,
                timeout=(
                    timeout_seconds if timeout_seconds is not None else gitnexus_timeout_seconds()
                ),
            )
        except subprocess.TimeoutExpired as exc:
            raise subprocess.CalledProcessError(
                124,
                cmd,
                output=exc.stdout,
                stderr=exc.stderr,
            ) from exc
        combined_output = "\n".join(
            part.strip() for part in (proc.stdout or "", proc.stderr or "") if part.strip()
        )
        if attempts == 1 and gitnexus_npx_cache_corrupted(combined_output):
            npx_cache_dir = gitnexus_npx_cache_dir()
            if npx_cache_dir is None:
                eprint("WARNING: npm cache path unavailable; cannot repair GitNexus npx cache")
            else:
                print(f"GitNexus: clearing corrupt npx cache at {npx_cache_dir}")
                shutil.rmtree(npx_cache_dir, ignore_errors=True)
                continue
        if check and proc.returncode != 0:
            raise subprocess.CalledProcessError(
                proc.returncode,
                cmd,
                output=proc.stdout,
                stderr=proc.stderr,
            )
        return proc


def prepare_gitnexus_for_worktree(path: Path) -> None:
    if not gitnexus_refresh_enabled():
        print("GitNexus: refresh disabled by WORKTREE_GITNEXUS_REFRESH=0")
        return
    if gitnexus_cli_path() is None and shutil_which("npx") is None:
        eprint("WARNING: gitnexus CLI and npx not found; skipping GitNexus refresh")
        return

    print(f"GitNexus: checking local index in {path}")
    status_proc = run_gitnexus_command(path, ["status"], check=False)
    status_output = "\n".join(
        part.strip()
        for part in (status_proc.stdout or "", status_proc.stderr or "")
        if part.strip()
    )
    if status_output:
        print(status_output)

    needs_refresh = status_proc.returncode != 0
    lowered = status_output.lower()
    refresh_markers = (
        "stale",
        "not indexed",
        "not analyzed",
        "not analysed",
        "missing",
        "out of date",
    )
    if any(marker in lowered for marker in refresh_markers):
        needs_refresh = True

    if not needs_refresh:
        print("GitNexus: local index already fresh")
        return

    analyze_args = ["analyze"]
    if gitnexus_embeddings_present(path):
        analyze_args.append("--embeddings")
    if gitnexus_analyze_supports("--skip-agents-md"):
        analyze_args.append("--skip-agents-md")
    if gitnexus_analyze_supports("--no-stats"):
        analyze_args.append("--no-stats")

    print(f"GitNexus: rebuilding local index for this worktree ({' '.join(analyze_args)})")
    try:
        run_gitnexus_command(path, analyze_args, check=True)
    except subprocess.CalledProcessError as exc:
        eprint(f"WARNING: GitNexus analyze failed in {path}: {exc}")
