#!/usr/bin/env python3
"""
install_tools.py — Declarative tool installer.
Reads scripts/tools.json and ensures all tools are installed and version-verified.
"""

import argparse
import glob
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Any
from zipfile import ZipInfo

from platform_config import env_optional

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_JSON = REPO_ROOT / "scripts" / "tools.json"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
INSTALL_ACTIONS = {"chmod", "copy", "copy_glob", "run"}


def info(msg: str) -> None:
    print(f"[install] {msg}")


def ok(msg: str) -> None:
    print(f"[ok]      {msg}")


def warn(msg: str) -> None:
    print(f"[warn]    {msg}", file=sys.stderr)


def fail(msg: str) -> None:
    print(f"[fail]    {msg}", file=sys.stderr)


def get_sha256(file_path: Path) -> str:
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


def resolve_sha256(platform_data: dict[str, Any]) -> str:
    raw_checksum = platform_data["sha256"]
    if isinstance(raw_checksum, str):
        checksum = raw_checksum
    elif isinstance(raw_checksum, list) and all(isinstance(part, str) for part in raw_checksum):
        checksum = "".join(raw_checksum).replace("-", "")
    else:
        raise ValueError("sha256 must be a string or list of strings")

    if not SHA256_RE.fullmatch(checksum):
        raise ValueError("Expected 64 lowercase hex characters for sha256")
    return checksum


def can_sudo() -> bool:
    try:
        subprocess.run(["sudo", "-n", "true"], check=True, capture_output=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def is_tool_installed(binary: str) -> bool:
    return shutil.which(binary) is not None


def download_file(url: str, dest: Path) -> None:
    info(f"Downloading {url}...")
    urllib.request.urlretrieve(url, dest)


def _safe_archive_member_path(destination: Path, member_name: str) -> Path:
    member_path = Path(member_name)
    if member_path.is_absolute() or ".." in member_path.parts:
        raise ValueError(f"Unsafe archive member path: {member_name}")
    target = (destination / member_path).resolve()
    root = destination.resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Unsafe archive member path: {member_name}") from exc
    return target


def _is_zip_symlink(member: ZipInfo) -> bool:
    unix_mode = member.external_attr >> 16
    return (unix_mode & 0o170000) == 0o120000


def _extract_tar_members(tar_ref: tarfile.TarFile, extract_dir: Path) -> None:
    for member in tar_ref.getmembers():
        if member.issym() or member.islnk():
            raise ValueError(f"Refusing to extract archive link: {member.name}")
        _safe_archive_member_path(extract_dir, member.name)
    for member in tar_ref.getmembers():
        tar_ref.extract(member, extract_dir, filter="data")


def extract_archive(archive_path: Path, extract_dir: Path) -> None:
    info(f"Extracting {archive_path.name}...")
    if archive_path.suffix == ".zip":
        with zipfile.ZipFile(archive_path, "r") as zip_ref:
            for info_item in zip_ref.infolist():
                if _is_zip_symlink(info_item):
                    raise ValueError(f"Refusing to extract archive symlink: {info_item.filename}")
                _safe_archive_member_path(extract_dir, info_item.filename)
                zip_ref.extract(info_item, extract_dir)
                out_path = extract_dir / info_item.filename
                if out_path.is_file():
                    mode = info_item.external_attr >> 16
                    if mode:
                        out_path.chmod(mode)
    elif archive_path.suffix in (".gz", ".tgz"):
        with tarfile.open(archive_path, "r:gz") as tar_ref:
            _extract_tar_members(tar_ref, extract_dir)
    elif archive_path.suffix == ".tar":
        with tarfile.open(archive_path, "r:") as tar_ref:
            _extract_tar_members(tar_ref, extract_dir)
    else:
        # Fallback to just copying if it's not a known archive
        shutil.copy(archive_path, extract_dir)


def _format_template(value: object, context: dict[str, str]) -> str:
    if not isinstance(value, str):
        raise ValueError("install step values must be strings")
    return value.format(**context)


def _parse_mode(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 8)
    raise ValueError("install step mode must be an int or octal string")


def _resolve_step_path(raw_path: object, *, cwd: Path, context: dict[str, str]) -> Path:
    path = Path(_format_template(raw_path, context))
    return path if path.is_absolute() else cwd / path


def _copy_install_artifact(src: Path, dest: Path, *, mode: int | None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    if mode is not None:
        dest.chmod(mode)


def run_install_steps(
    steps: object,
    *,
    context: dict[str, str],
    cwd: Path,
    sudo_available: bool,
) -> None:
    if not isinstance(steps, list):
        raise ValueError("tool install_steps must be a list")

    for step in steps:
        if not isinstance(step, dict):
            raise ValueError("tool install_steps entries must be objects")
        action = step.get("action")
        if action not in INSTALL_ACTIONS:
            raise ValueError(f"Unsupported install action: {action!r}")

        mode = _parse_mode(step.get("mode"))
        if action == "chmod":
            target = _resolve_step_path(step["path"], cwd=cwd, context=context)
            if mode is None:
                raise ValueError("chmod install step requires mode")
            target.chmod(mode)
        elif action == "copy":
            src = _resolve_step_path(step["src"], cwd=cwd, context=context)
            dest = Path(_format_template(step["dest"], context))
            _copy_install_artifact(src, dest, mode=mode)
        elif action == "copy_glob":
            pattern = _format_template(step["src"], context)
            matches = [Path(match) for match in glob.glob(os.fspath(cwd / pattern))]
            if len(matches) != 1:
                raise ValueError(
                    f"Expected one match for install glob {pattern!r}, found {len(matches)}"
                )
            dest = Path(_format_template(step["dest"], context))
            _copy_install_artifact(matches[0], dest, mode=mode)
        elif action == "run":
            raw_argv = step.get("argv")
            if not isinstance(raw_argv, list) or not raw_argv:
                raise ValueError("run install step requires non-empty argv")
            argv = [_format_template(part, context) for part in raw_argv]
            if step.get("sudo") and sudo_available:
                argv = ["sudo", *argv]
            subprocess.run(argv, check=True, cwd=cwd)


def install_tool(tool: dict[str, Any], arch: str, force: bool = False) -> bool:
    name = tool["name"]
    binary = tool.get("binary", name)

    if is_tool_installed(binary) and not force:
        # For now, we just skip if binary exists.
        ok(f"{name} is already installed")
        return True

    tool_type = tool.get("type", "binary")
    sudo_available = can_sudo()
    local_bin = Path.home() / ".local" / "bin"
    bin_dir = "/usr/local/bin" if sudo_available else str(local_bin)

    if not sudo_available:
        local_bin.mkdir(parents=True, exist_ok=True)
        if str(local_bin) not in (env_optional("PATH", "") or ""):
            warn(f"{local_bin} not in PATH")

    if tool_type == "npm":
        package = tool["package"]
        version = tool["version"]
        if sudo_available:
            cmd = ["sudo", "npm", "install", "-g", f"{package}@{version}", "--quiet"]
        else:
            # No-sudo npm install
            npm_global = Path.home() / ".npm-global"
            npm_global.mkdir(parents=True, exist_ok=True)
            cmd = ["npm", "install", "--prefix", str(npm_global), f"{package}@{version}", "--quiet"]
            if f"{npm_global}/bin" not in (env_optional("PATH", "") or ""):
                warn(f"{npm_global}/bin not in PATH")

        info(f"Installing {name} via npm...")
        try:
            subprocess.run(cmd, check=True)
            ok(f"{name} installed")
            return True
        except subprocess.CalledProcessError as e:
            fail(f"Failed to install {name}: {e}")
            return False

    platforms = tool.get("platforms", {})
    if arch not in platforms:
        warn(f"Tool {name} does not support architecture {arch}")
        return False

    platform_data = platforms[arch]
    url = platform_data["url"]
    try:
        expected_sha256 = resolve_sha256(platform_data)
    except ValueError as e:
        fail(f"Invalid checksum for {name}: {e}")
        return False

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        archive_path = tmpdir_path / Path(url).name
        try:
            download_file(url, archive_path)
        except Exception as e:
            fail(f"Failed to download {name}: {e}")
            return False

        actual_sha256 = get_sha256(archive_path)
        if actual_sha256 != expected_sha256:
            fail(f"Checksum mismatch for {name}. Expected {expected_sha256}, got {actual_sha256}")
            return False
        ok(f"Checksum verified for {name}")

        extract_archive(archive_path, tmpdir_path)

        install_prefix = Path.home() / ".local"
        install_dir = f"{install_prefix}/{name}" if not sudo_available else f"/usr/local/{name}"
        context = {
            "archive": os.fspath(archive_path),
            "tmpdir": os.fspath(tmpdir_path),
            "bin_dir": bin_dir,
            "install_dir": install_dir,
        }

        info(f"Running install command for {name}...")
        try:
            run_install_steps(
                tool.get("install_steps"),
                context=context,
                cwd=tmpdir_path,
                sudo_available=sudo_available,
            )
            ok(f"{name} installed successfully")
            return True
        except (OSError, ValueError, subprocess.CalledProcessError) as e:
            fail(f"Install command failed for {name}: {e}")
            return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Install dev tools from manifest")
    parser.add_argument("--force", action="store_true", help="Force reinstallation")
    parser.add_argument("--manifest", type=Path, default=TOOLS_JSON, help="Path to tools.json")
    args = parser.parse_args()

    if not args.manifest.exists():
        fail(f"Manifest not found: {args.manifest}")
        sys.exit(1)

    with open(args.manifest) as f:
        data = json.load(f)

    tools = data.get("tools", [])
    arch = platform.machine()
    # Normalize arch
    if arch == "x86_64":
        pass
    elif arch in ("aarch64", "arm64"):
        arch = "aarch64"
    else:
        warn(f"Unknown architecture: {arch}")

    failed = []
    for tool in tools:
        if not install_tool(tool, arch, args.force):
            failed.append(tool["name"])

    if failed:
        fail(f"Failed to install tools: {', '.join(failed)}")
        sys.exit(1)

    ok("All tools processed")


if __name__ == "__main__":
    main()
