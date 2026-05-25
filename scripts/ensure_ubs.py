#!/usr/bin/env python3
"""Install the pinned UBS runner into .build without mutating the workstation."""

from __future__ import annotations

import argparse
import hashlib
import os
import stat
import sys
import urllib.error
import urllib.request
from pathlib import Path

UBS_VERSION = "5.2.76"
UBS_TAG = f"v{UBS_VERSION}"
UBS_SHA256 = "c53f88c9265410feaa418684370d87f680c98e6b0096a97aa6cf9da2810b7b97"
UBS_URL = (
    "https://raw.githubusercontent.com/"
    f"Dicklesworthstone/ultimate_bug_scanner/refs/tags/{UBS_TAG}/ubs"
)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def install_path(root: Path) -> Path:
    return root / ".build" / "tools" / "ubs" / UBS_VERSION / "ubs"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def make_executable(path: Path) -> None:
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = destination.with_suffix(".tmp")
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            tmp_path.write_bytes(response.read())
    except (OSError, urllib.error.URLError) as exc:
        tmp_path.unlink(missing_ok=True)
        raise RuntimeError(f"failed to download UBS from {url}: {exc}") from exc
    actual = sha256(tmp_path)
    if actual != UBS_SHA256:
        tmp_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"UBS checksum mismatch: expected {UBS_SHA256}, got {actual}; refusing to install"
        )
    tmp_path.replace(destination)
    make_executable(destination)


def ensure(root: Path, *, force: bool = False) -> Path:
    path = install_path(root)
    if path.exists() and not force:
        actual = sha256(path)
        if actual == UBS_SHA256:
            make_executable(path)
            return path
        path.unlink()
    download(UBS_URL, path)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Install the pinned UBS runner locally")
    parser.add_argument("--force", action="store_true", help="redownload even when present")
    parser.add_argument(
        "--print-path",
        action="store_true",
        help="print only the resolved executable path on success",
    )
    args = parser.parse_args()

    root = repo_root()
    try:
        path = ensure(root, force=args.force)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.print_path:
        print(path)
    else:
        rel_path = os.fspath(path.relative_to(root))
        print(f"UBS {UBS_VERSION} ready at {rel_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
