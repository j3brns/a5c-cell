from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from platform_config import settings
from scripts.issue_tool import git_utils
from scripts.issue_tool.constants import ANSI_ESCAPE_RE
from scripts.issue_tool.models import SessionPair
from scripts.issue_tool.shared import CliError, shell_quote


def worktree_env_preamble() -> str:
    return (
        "if [ -f .venv/bin/activate ]; then source .venv/bin/activate; fi; "
        'export CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"; '
        'case "$CODEX_HOME" in /*) ;; *) export CODEX_HOME="$PWD/$CODEX_HOME" ;; esac; '
        'mkdir -p "$CODEX_HOME"'
    )


def tmux_available() -> bool:
    return shutil.which("tmux") is not None


def tmux_session_exists(name: str) -> bool:
    result = git_utils.run(["tmux", "has-session", "-t", name], capture_output=True)
    return result.returncode == 0


def tmux_session_name_for_worktree(path: Path) -> str:
    return path.name


def worktree_session_pair(label: str) -> SessionPair:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    session_name = f"{label}-{stamp}-{os.getpid()}"
    return SessionPair(label=label, session_name=session_name)


def launch_tmux_session(
    *,
    path: Path,
    agent_command: str,
    session_name: str | None = None,
    attach: bool = True,
) -> None:
    name = session_name or tmux_session_name_for_worktree(path)
    path_str = str(path)
    venv_preamble = worktree_env_preamble()

    if tmux_session_exists(name):
        print(f"tmux session '{name}' already exists — attaching.")
        if attach:
            if settings.tmux:
                # If we're already inside tmux, try to switch to the session
                os.execvp("tmux", ["tmux", "switch-client", "-t", name])
            else:
                os.execvp("tmux", ["tmux", "attach-session", "-t", name])
        return

    # Create session and first window with agent command
    # Use -x and -y only if not inside an existing tmux session
    if not settings.tmux:
        # Provide a reasonable default for non-interactive sessions, but don't force it
        # if it might cause failure. Actually, best to let tmux default.
        pass

    try:
        # Create session, rename window, and start agent command in first pane
        git_utils.run(
            [
                "tmux",
                "new-session",
                "-d",
                "-s",
                name,
                "-n",
                name,
                "-c",
                path_str,
                f"{venv_preamble} && {agent_command}",
            ],
            check=True,
        )

        # Split window to provide a second pane for the interactive shell
        git_utils.run(
            ["tmux", "split-window", "-h", "-t", f"{name}:{name}", "-c", path_str],
            check=True,
        )

        # In the second pane, just run the preamble so it's ready for the user
        git_utils.run(
            ["tmux", "send-keys", "-t", f"{name}:{name}.1", venv_preamble, "Enter"],
            check=True,
        )

        # Ensure focus is on the agent pane
        git_utils.run(["tmux", "select-pane", "-t", f"{name}:{name}.0"], check=True)

    except subprocess.CalledProcessError as exc:
        raise CliError(f"Failed to initialize tmux session '{name}': {exc}")

    print(f"tmux session '{name}' launching in {path}")
    print(f"  Session name:  {name}")
    print("  Left pane:  agent running")
    print("  Right pane: shell ready")

    if attach:
        if settings.tmux:
            os.execvp("tmux", ["tmux", "switch-client", "-t", name])
        else:
            os.execvp("tmux", ["tmux", "attach-session", "-t", name])


def _launch_tmux_worktree_window(
    *,
    session_name: str,
    window_name: str,
    path: Path,
    agent_command: str,
    create_session: bool,
) -> None:
    path_str = str(path)
    venv_preamble = worktree_env_preamble()
    target = f"{session_name}:{window_name}"

    if create_session:
        git_utils.run(
            [
                "tmux",
                "new-session",
                "-d",
                "-s",
                session_name,
                "-n",
                window_name,
                "-c",
                path_str,
            ],
            check=True,
        )
    else:
        git_utils.run(
            [
                "tmux",
                "new-window",
                "-t",
                session_name,
                "-n",
                window_name,
                "-c",
                path_str,
            ],
            check=True,
        )

    git_utils.run(["tmux", "split-window", "-h", "-t", target, "-c", path_str], check=True)
    git_utils.run(["tmux", "send-keys", "-t", f"{target}.1", venv_preamble, "Enter"], check=True)
    git_utils.run(
        ["tmux", "send-keys", "-t", f"{target}.0", f"{venv_preamble} && {agent_command}", "Enter"],
        check=True,
    )
    git_utils.run(["tmux", "select-pane", "-t", f"{target}.0"], check=True)


def launch_tmux_batch_session(
    *,
    session_name: str,
    launches: list[tuple[str, Path, str]],
    attach: bool = True,
    announce_windows: bool = True,
) -> None:
    if tmux_session_exists(session_name):
        print(f"tmux session '{session_name}' already exists — replacing.")
        git_utils.run(["tmux", "kill-session", "-t", session_name], check=False)

    if not launches:
        raise CliError("No launches provided for tmux batch session.")

    print(f"tmux session '{session_name}' launching with {len(launches)} worktree window(s)")

    for idx, (window_name, path, agent_command) in enumerate(launches):
        _launch_tmux_worktree_window(
            session_name=session_name,
            window_name=window_name,
            path=path,
            agent_command=agent_command,
            create_session=(idx == 0),
        )

    git_utils.run(["tmux", "select-window", "-t", f"{session_name}:0"], check=True)

    if announce_windows:
        for window_name, path, _ in launches:
            print(f"  {window_name}: {path}")

    if attach:
        if settings.tmux:
            os.execvp("tmux", ["tmux", "switch-client", "-t", session_name])
        else:
            os.execvp("tmux", ["tmux", "attach-session", "-t", session_name])


def _launch_tmux_viewer_window(
    *,
    session_name: str,
    window_name: str,
    path: Path,
    stdout_log_path: Path,
    create_session: bool,
) -> None:
    path_str = str(path)
    target = f"{session_name}:{window_name}"
    venv_preamble = worktree_env_preamble()
    log_cmd = (
        f"touch {shell_quote(str(stdout_log_path))} && "
        f"tail -n 50 -f {shell_quote(str(stdout_log_path))}"
    )

    if create_session:
        git_utils.run(
            [
                "tmux",
                "new-session",
                "-d",
                "-s",
                session_name,
                "-n",
                window_name,
                "-c",
                path_str,
            ],
            check=True,
        )
    else:
        git_utils.run(
            [
                "tmux",
                "new-window",
                "-t",
                session_name,
                "-n",
                window_name,
                "-c",
                path_str,
            ],
            check=True,
        )

    git_utils.run(["tmux", "split-window", "-h", "-t", target, "-c", path_str], check=True)
    git_utils.run(["tmux", "send-keys", "-t", f"{target}.0", log_cmd, "Enter"], check=True)
    git_utils.run(["tmux", "send-keys", "-t", f"{target}.1", venv_preamble, "Enter"], check=True)
    git_utils.run(["tmux", "select-pane", "-t", f"{target}.1"], check=True)


def launch_tmux_batch_viewer(
    *,
    session_name: str,
    views: list[tuple[str, Path, Path]],
    attach: bool = True,
) -> None:
    if tmux_session_exists(session_name):
        print(f"tmux session '{session_name}' already exists — replacing.")
        git_utils.run(["tmux", "kill-session", "-t", session_name], check=False)

    if not views:
        raise CliError("No worktree views provided for tmux batch viewer.")

    print(f"tmux session '{session_name}' launching with {len(views)} worktree viewer(s)")

    for idx, (window_name, path, stdout_log_path) in enumerate(views):
        _launch_tmux_viewer_window(
            session_name=session_name,
            window_name=window_name,
            path=path,
            stdout_log_path=stdout_log_path,
            create_session=(idx == 0),
        )

    git_utils.run(["tmux", "select-window", "-t", f"{session_name}:0"], check=True)

    if attach:
        if settings.tmux:
            os.execvp("tmux", ["tmux", "switch-client", "-t", session_name])
        else:
            os.execvp("tmux", ["tmux", "attach-session", "-t", session_name])


def zellij_bin() -> str:
    return shutil.which("zellij") or os.path.expanduser("~/bin/zellij")


def zellij_available() -> bool:
    path = zellij_bin()
    return os.path.isfile(path) and os.access(path, os.X_OK)


def zellij_session_exists(name: str) -> bool:
    zj = zellij_bin()
    result = git_utils.run([zj, "list-sessions"], capture_output=True, text=True)
    for line in result.stdout.splitlines():
        cleaned = ANSI_ESCAPE_RE.sub("", line).strip()
        if cleaned.startswith(name):
            return True
    return False


def disable_terminal_flow_control() -> None:
    # Ctrl+S is used by our zellij config for scroll mode, so disable XON/XOFF
    # before handing the terminal over to zellij.
    git_utils.run(["stty", "-ixon"], check=False)


def launch_zellij_session(
    *,
    path: Path,
    agent_command: str,
    session_name: str | None = None,
    attach: bool = True,
) -> None:
    import tempfile

    zj = zellij_bin()
    pair = worktree_session_pair(path.name)
    label = session_name or pair.label
    name = session_name or pair.session_name
    path_str = str(path)
    disable_terminal_flow_control()

    print(f"zellij session '{label}' launching in {path}")
    print(f"  Session label: {label}")
    print(f"  Session name:  {name}")

    if zellij_session_exists(name):
        print(f"zellij session '{name}' already exists — attaching.")
        if attach:
            os.execvp(zj, [zj, "attach", name])
        return

    temp_dir = Path(tempfile.mkdtemp(prefix=f"wt-layout-{name}-"))
    layout_file = temp_dir / "layout.kdl"
    agent_script = _write_zellij_worktree_wrapper_script(
        temp_dir / "agent.sh", path_str=path_str, command=agent_command
    )
    shell_script = _write_zellij_worktree_wrapper_script(
        temp_dir / "shell.sh", path_str=path_str, shell=True
    )
    layout_file.write_text(
        f"""\
layout {{
    cwd "{path_str}"
    pane split_direction="vertical" {{
        pane command={json.dumps(str(agent_script))} {{
            name "agent"
            focus true
        }}
        pane command={json.dumps(str(shell_script))} {{
            name "shell"
        }}
    }}
}}
""",
        encoding="utf-8",
    )

    print(f"  Attach:    zellij attach {name}")
    print("  List:      zellij ls")

    if attach:
        _exec_zellij_with_layout_cleanup(
            zj,
            ["--new-session-with-layout", str(layout_file), "--session", name],
            str(temp_dir),
        )
    else:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _zellij_worktree_pane_layout(
    temp_dir: Path, tab_name: str, path: Path, agent_command: str, *, focus: bool
) -> str:
    agent_script = _write_zellij_worktree_wrapper_script(
        temp_dir / f"{tab_name}-agent.sh", path_str=str(path), command=agent_command
    )
    shell_script = _write_zellij_worktree_wrapper_script(
        temp_dir / f"{tab_name}-shell.sh", path_str=str(path), shell=True
    )
    focus_str = "true" if focus else "false"
    return (
        '      pane split_direction="vertical" {\n'
        f"        pane command={json.dumps(str(agent_script))} {{\n"
        f'          name "agent"\n'
        f"          focus {focus_str}\n"
        "        }\n"
        f"        pane command={json.dumps(str(shell_script))} {{\n"
        f'          name "shell"\n'
        "        }\n"
        "      }"
    )


def launch_zellij_batch_session(
    *,
    session_name: str,
    launches: list[tuple[str, Path, str]],
    attach: bool = True,
    announce_tabs: bool = True,
) -> None:
    import tempfile

    zj = zellij_bin()
    disable_terminal_flow_control()
    if zellij_session_exists(session_name):
        print(f"zellij session '{session_name}' already exists — replacing.")
        git_utils.run([zj, "delete-session", session_name], check=False)

    print(f"zellij session '{session_name}' launching with {len(launches)} worktree tab(s)")

    temp_dir = Path(tempfile.mkdtemp(prefix=f"wt-batch-{session_name}-"))
    tabs: list[str] = []
    for idx, (tab_name, path, agent_command) in enumerate(launches):
        pane = _zellij_worktree_pane_layout(
            temp_dir, tab_name, path, agent_command, focus=(idx == 0)
        )
        tabs.append(
            f"    tab name={json.dumps(tab_name)} focus={'true' if idx == 0 else 'false'} {{\n"
            f"{pane}\n"
            "    }"
        )

    layout_file = temp_dir / "layout.kdl"
    layout_file.write_text("layout {\n" + "\n".join(tabs) + "\n}\n", encoding="utf-8")

    if announce_tabs:
        for tab_name, path, _ in launches:
            print(f"  {tab_name}: {path}")
    print(f"  Reattach:   zellij attach {session_name}")
    print("  List all:   zellij ls")

    if attach:
        _exec_zellij_with_layout_cleanup(
            zj,
            ["--new-session-with-layout", str(layout_file), "--session", session_name],
            str(temp_dir),
        )
    else:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _write_zellij_worktree_wrapper_script(
    path: Path, *, path_str: str, command: str | None = None, shell: bool = False
) -> Path:
    if command is None and not shell:
        raise ValueError("wrapper script requires command or shell")
    body: list[str] = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        f"cd {shlex.quote(path_str)}",
        worktree_env_preamble().replace("; ", "\n"),
    ]
    if shell:
        body.append("exec bash -l")
    else:
        body.append(f"exec bash -lc {shlex.quote(command or '')}")
    path.write_text("\n".join(body) + "\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _exec_zellij_with_layout_cleanup(zj: str, args: list[str], temp_dir: str) -> None:
    temp_dir_q = shlex.quote(temp_dir)
    args_q = " ".join(shlex.quote(arg) for arg in [zj, *args])
    cleanup_cmd = f"trap 'rm -rf {temp_dir_q}' EXIT; exec {args_q}"
    os.execvp("bash", ["bash", "-lc", cleanup_cmd])


def auto_detect_mux() -> str:
    if tmux_available():
        return "tmux"
    if zellij_available():
        return "zellij"
    return "none"
