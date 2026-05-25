"""Audit local container runtime options for platform development."""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class ToolStatus:
    name: str
    command: str
    present: bool
    detail: str


@dataclass(frozen=True)
class RuntimeAudit:
    os: str
    wsl: bool
    tools: tuple[ToolStatus, ...]
    recommended_mode: str
    recommendation: str
    next_steps: tuple[str, ...]


def _run(command: list[str], timeout_seconds: int = 5) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)

    output = (result.stdout or result.stderr).strip().splitlines()
    detail = output[0] if output else f"exit {result.returncode}"
    return result.returncode == 0, detail


def _is_wsl() -> bool:
    version = Path("/proc/version")
    if not version.exists():
        return False
    return "microsoft" in version.read_text(encoding="utf-8", errors="ignore").lower()


def _tool_status(name: str, command: str, probe: list[str]) -> ToolStatus:
    path = shutil.which(command)
    if path is None:
        return ToolStatus(name=name, command=command, present=False, detail="not on PATH")
    ok, detail = _run(probe)
    return ToolStatus(name=name, command=command, present=ok, detail=detail)


def audit() -> RuntimeAudit:
    docker_cli = _tool_status("Docker CLI", "docker", ["docker", "--version"])
    docker_daemon = _tool_status("Docker daemon", "docker", ["docker", "info"])
    docker_compose = _tool_status(
        "Docker Compose plugin", "docker", ["docker", "compose", "version"]
    )
    podman = _tool_status("Podman", "podman", ["podman", "--version"])
    podman_compose = _tool_status(
        "Podman Compose provider", "podman", ["podman", "compose", "version"]
    )
    nerdctl = _tool_status("nerdctl", "nerdctl", ["nerdctl", "--version"])
    rancher = _tool_status("Rancher Desktop CLI", "rdctl", ["rdctl", "version"])

    tools = (docker_cli, docker_daemon, docker_compose, podman, podman_compose, nerdctl, rancher)
    wsl = _is_wsl()

    if docker_daemon.present and docker_compose.present:
        return RuntimeAudit(
            os=platform.platform(),
            wsl=wsl,
            tools=tools,
            recommended_mode="compose",
            recommendation=(
                "Docker-compatible Compose is available. `make dev` can use "
                "`make dev-compose` automatically."
            ),
            next_steps=("make dev",),
        )

    if podman.present:
        return RuntimeAudit(
            os=platform.platform(),
            wsl=wsl,
            tools=tools,
            recommended_mode="native",
            recommendation=(
                "Podman is available. Use it to run the local AWS emulator, then use the "
                "repo-native mocks with `make dev-native`."
            ),
            next_steps=(
                "podman run --rm -p 4566:4566 "
                "-e SERVICES=dynamodb,s3,sqs,ssm,secretsmanager,events "
                "docker.io/floci/floci:latest",
                "AWS_ENDPOINT_URL=http://localhost:4566 make dev-native",
            ),
        )

    if nerdctl.present or rancher.present:
        return RuntimeAudit(
            os=platform.platform(),
            wsl=wsl,
            tools=tools,
            recommended_mode="native",
            recommendation=(
                "Rancher Desktop/containerd tooling is present. Prefer dockerd/Moby mode if you "
                "want Compose; otherwise run only the local AWS emulator and use `make dev-native`."
            ),
            next_steps=(
                "nerdctl run --rm -p 4566:4566 "
                "-e SERVICES=dynamodb,s3,sqs,ssm,secretsmanager,events "
                "docker.io/floci/floci:latest",
                "AWS_ENDPOINT_URL=http://localhost:4566 make dev-native",
            ),
        )

    if wsl:
        next_steps = (
            "Minimal WSL path: install Podman in this Ubuntu WSL distro, run only the "
            "local AWS emulator with Podman, then use `make dev-native`.",
            "Compatibility path: install Docker Engine inside this Ubuntu WSL distro "
            "from Docker's apt repository.",
            "Windows-managed path: install Rancher Desktop on Windows, choose "
            "dockerd/Moby for Docker CLI compatibility, then enable WSL integration "
            "for this distro.",
        )
    else:
        next_steps = (
            "Install Docker Engine plus the Compose plugin, or install Podman.",
            "After installing a runtime, run `make bootstrap-runtime` again.",
        )

    return RuntimeAudit(
        os=platform.platform(),
        wsl=wsl,
        tools=tools,
        recommended_mode="none",
        recommendation=(
            "No supported container runtime is available on PATH. The repo can run its "
            "Python mocks natively, but you still need a way to run the local AWS emulator "
            "on `AWS_ENDPOINT_URL`."
        ),
        next_steps=next_steps,
    )


def print_text(result: RuntimeAudit) -> None:
    print("Container runtime audit")
    print(f"  OS: {result.os}")
    print(f"  WSL: {'yes' if result.wsl else 'no'}")
    print("")
    for tool in result.tools:
        status = "OK" if tool.present else "MISSING"
        print(f"  [{status}] {tool.name}: {tool.detail}")
    print("")
    print(f"Recommended mode: {result.recommended_mode}")
    print(result.recommendation)
    print("")
    print("Next steps:")
    for step in result.next_steps:
        print(f"  - {step}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable audit output")
    args = parser.parse_args(argv)

    result = audit()
    if args.json:
        print(json.dumps(asdict(result), indent=2))
    else:
        print_text(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
