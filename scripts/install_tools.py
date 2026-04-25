#!/usr/bin/env python3
"""
install_tools.py — Declarative tool installer.
Reads scripts/tools.json and ensures all tools are installed and version-verified.
"""

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from platform_config import env_optional

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_JSON = REPO_ROOT / "scripts" / "tools.json"


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


def extract_archive(archive_path: Path, extract_dir: Path) -> None:
    info(f"Extracting {archive_path.name}...")
    if archive_path.suffix == ".zip":
        with zipfile.ZipFile(archive_path, "r") as zip_ref:
            for info_item in zip_ref.infolist():
                zip_ref.extract(info_item, extract_dir)
                out_path = extract_dir / info_item.filename
                if out_path.is_file():
                    mode = info_item.external_attr >> 16
                    if mode:
                        out_path.chmod(mode)
    elif archive_path.suffix in (".gz", ".tgz"):
        with tarfile.open(archive_path, "r:gz") as tar_ref:
            tar_ref.extractall(extract_dir)
    elif archive_path.suffix == ".tar":
        with tarfile.open(archive_path, "r:") as tar_ref:
            tar_ref.extractall(extract_dir)
    else:
        # Fallback to just copying if it's not a known archive
        shutil.copy(archive_path, extract_dir)


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
    expected_sha256 = platform_data["sha256"]

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

        raw_command = tool["install_command"]
        install_prefix = Path.home() / ".local"
        install_dir = f"{install_prefix}/{name}" if not sudo_available else f"/usr/local/{name}"

        # If no sudo but command uses sudo, strip sudo
        if not sudo_available:
            raw_command = raw_command.replace("sudo ", "")

        install_command = raw_command.format(
            archive=archive_path, tmpdir=tmpdir_path, bin_dir=bin_dir, install_dir=install_dir
        )

        info(f"Running install command for {name}...")
        try:
            # We run from tmpdir where the archive was extracted
            subprocess.run(install_command, shell=True, check=True, cwd=tmpdir_path)
            ok(f"{name} installed successfully")
            return True
        except subprocess.CalledProcessError as e:
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
