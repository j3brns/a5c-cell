from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from scripts import install_tools

CHECKSUM_PARTS = [
    "023588dd-",
    "585299ea-",
    "78bec745-",
    "2152bb8c-",
    "b15eebd4-",
    "4e9f0a08-",
    "38f0cdaa-",
    "c1b087f6",
]


def _joined_checksum(parts: list[str]) -> str:
    return "".join(parts).replace("-", "")


def test_resolve_sha256_accepts_chunked_manifest_value() -> None:
    expected = _joined_checksum(CHECKSUM_PARTS)

    assert install_tools.resolve_sha256({"sha256": CHECKSUM_PARTS}) == expected


def test_resolve_sha256_accepts_legacy_manifest_value() -> None:
    expected = _joined_checksum(CHECKSUM_PARTS)

    assert install_tools.resolve_sha256({"sha256": expected}) == expected


def test_resolve_sha256_rejects_non_sha256_manifest_value() -> None:
    with pytest.raises(ValueError, match="Expected 64 lowercase hex characters"):
        install_tools.resolve_sha256({"sha256": ["not-a-checksum"]})


def test_extract_archive_rejects_zip_path_traversal(tmp_path: Path) -> None:
    archive_path = tmp_path / "tool.zip"
    extract_dir = tmp_path / "extract"
    extract_dir.mkdir()
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../../escape", "owned")

    with pytest.raises(ValueError, match="Unsafe archive member path"):
        install_tools.extract_archive(archive_path, extract_dir)
    assert not (tmp_path / "escape").exists()


def test_extract_archive_rejects_tar_path_traversal(tmp_path: Path) -> None:
    archive_path = tmp_path / "tool.tar"
    extract_dir = tmp_path / "extract"
    extract_dir.mkdir()
    payload = b"owned"
    with tarfile.open(archive_path, "w") as archive:
        info = tarfile.TarInfo("../../escape")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))

    with pytest.raises(ValueError, match="Unsafe archive member path"):
        install_tools.extract_archive(archive_path, extract_dir)
    assert not (tmp_path / "escape").exists()


def test_extract_archive_rejects_tar_links(tmp_path: Path) -> None:
    archive_path = tmp_path / "tool.tar"
    extract_dir = tmp_path / "extract"
    extract_dir.mkdir()
    with tarfile.open(archive_path, "w") as archive:
        info = tarfile.TarInfo("link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/tmp/escape"
        archive.addfile(info)

    with pytest.raises(ValueError, match="Refusing to extract archive link"):
        install_tools.extract_archive(archive_path, extract_dir)


def test_extract_archive_keeps_zip_executable_mode(tmp_path: Path) -> None:
    archive_path = tmp_path / "tool.zip"
    extract_dir = tmp_path / "extract"
    extract_dir.mkdir()
    member = zipfile.ZipInfo("bin/tool")
    member.external_attr = 0o755 << 16
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(member, "#!/bin/sh\n")

    install_tools.extract_archive(archive_path, extract_dir)

    extracted = extract_dir / "bin" / "tool"
    assert extracted.is_file()
    assert extracted.stat().st_mode & 0o111


def test_install_tool_verifies_exact_resolved_sha256(tmp_path, monkeypatch) -> None:
    payload = b"tool-archive"
    expected_checksum = install_tools.hashlib.sha256(payload).hexdigest()
    installed: list[tuple[list[str], dict[str, object]]] = []

    def fake_download_file(_url, dest) -> None:
        dest.write_bytes(payload)

    def fake_run(cmd, **_kwargs) -> None:
        installed.append((cmd, _kwargs))

    monkeypatch.setattr(install_tools, "can_sudo", lambda: False)
    monkeypatch.setattr(install_tools, "is_tool_installed", lambda _binary: False)
    monkeypatch.setattr(install_tools, "download_file", fake_download_file)
    monkeypatch.setattr(install_tools, "extract_archive", lambda _archive, _dest: None)
    monkeypatch.setattr(install_tools.subprocess, "run", fake_run)
    monkeypatch.setenv("HOME", str(tmp_path))

    assert install_tools.install_tool(
        {
            "name": "demo",
            "binary": "demo",
            "platforms": {
                "x86_64": {
                    "url": "https://example.invalid/demo.tar.gz",
                    "sha256": [
                        expected_checksum[:8] + "-",
                        expected_checksum[8:16] + "-",
                        expected_checksum[16:24] + "-",
                        expected_checksum[24:32] + "-",
                        expected_checksum[32:40] + "-",
                        expected_checksum[40:48] + "-",
                        expected_checksum[48:56] + "-",
                        expected_checksum[56:],
                    ],
                }
            },
            "install_steps": [
                {
                    "action": "run",
                    "argv": ["install-demo", "--bin-dir", "{bin_dir}"],
                }
            ],
        },
        "x86_64",
    )
    assert installed[0][0] == ["install-demo", "--bin-dir", f"{tmp_path}/.local/bin"]
    assert installed[0][1]["check"] is True
    assert isinstance(installed[0][1]["cwd"], Path)
    assert "shell" not in installed[0][1]


def test_install_tool_copies_glob_without_shell(tmp_path, monkeypatch) -> None:
    payload = b"tool-archive"
    expected_checksum = install_tools.hashlib.sha256(payload).hexdigest()

    def fake_download_file(_url, dest) -> None:
        dest.write_bytes(payload)

    def fake_extract_archive(_archive: Path, dest: Path) -> None:
        source_dir = dest / "package"
        source_dir.mkdir()
        (source_dir / "demo").write_text("demo", encoding="utf-8")

    monkeypatch.setattr(install_tools, "can_sudo", lambda: False)
    monkeypatch.setattr(install_tools, "is_tool_installed", lambda _binary: False)
    monkeypatch.setattr(install_tools, "download_file", fake_download_file)
    monkeypatch.setattr(install_tools, "extract_archive", fake_extract_archive)
    monkeypatch.setattr(install_tools.subprocess, "run", pytest.fail)
    monkeypatch.setenv("HOME", str(tmp_path))

    assert install_tools.install_tool(
        {
            "name": "demo",
            "binary": "demo",
            "platforms": {
                "x86_64": {
                    "url": "https://example.invalid/demo.tar.gz",
                    "sha256": expected_checksum,
                }
            },
            "install_steps": [
                {
                    "action": "copy_glob",
                    "src": "*/demo",
                    "dest": "{bin_dir}/demo",
                    "mode": "755",
                }
            ],
        },
        "x86_64",
    )
    installed = tmp_path / ".local" / "bin" / "demo"
    assert installed.read_text(encoding="utf-8") == "demo"
    assert installed.stat().st_mode & 0o111


def test_run_install_steps_prefixes_sudo_when_available(tmp_path: Path, monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: object) -> None:
        calls.append(cmd)

    monkeypatch.setattr(install_tools.subprocess, "run", fake_run)

    install_tools.run_install_steps(
        [
            {
                "action": "run",
                "argv": ["tool/install", "--prefix", "{install_dir}"],
                "sudo": True,
            }
        ],
        context={
            "archive": "/tmp/tool.tar.gz",
            "tmpdir": str(tmp_path),
            "bin_dir": "/usr/local/bin",
            "install_dir": "/usr/local/tool",
        },
        cwd=tmp_path,
        sudo_available=True,
    )

    assert calls == [["sudo", "tool/install", "--prefix", "/usr/local/tool"]]


def test_install_tool_rejects_checksum_mismatch_before_install(tmp_path, monkeypatch) -> None:
    installed: list[str] = []

    def fake_download_file(_url, dest) -> None:
        dest.write_bytes(b"unexpected")

    def fake_run(cmd, **_kwargs) -> None:
        installed.append(cmd)

    monkeypatch.setattr(install_tools, "can_sudo", lambda: False)
    monkeypatch.setattr(install_tools, "is_tool_installed", lambda _binary: False)
    monkeypatch.setattr(install_tools, "download_file", fake_download_file)
    monkeypatch.setattr(install_tools.subprocess, "run", fake_run)
    monkeypatch.setenv("HOME", str(tmp_path))

    assert not install_tools.install_tool(
        {
            "name": "demo",
            "binary": "demo",
            "platforms": {
                "x86_64": {
                    "url": "https://example.invalid/demo.tar.gz",
                    "sha256": [
                        "00000000-",
                        "00000000-",
                        "00000000-",
                        "00000000-",
                        "00000000-",
                        "00000000-",
                        "00000000-",
                        "00000000",
                    ],
                }
            },
            "install_steps": [
                {
                    "action": "run",
                    "argv": ["install-demo"],
                }
            ],
        },
        "x86_64",
    )
    assert installed == []
