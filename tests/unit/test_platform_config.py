"""Tests for typed platform configuration models."""

from __future__ import annotations

from pathlib import Path

import pytest

from platform_config import (
    env_first,
    env_optional,
    get_settings,
    process_env_required,
    settings,
)
from platform_config.settings import PlatformSettings


def test_settings_loads_documented_local_defaults() -> None:
    loaded = get_settings()

    assert loaded.aws.region == "eu-west-2"
    assert settings.aws_region == "eu-west-2"
    assert env_optional("AWS_REGION") == "eu-west-2"


def test_environment_overrides_env_example(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_REGION", "eu-central-1")
    monkeypatch.setenv("API_BASE_URL", "https://api.example.test")
    monkeypatch.setenv("PLATFORM_ACCESS_TOKEN", "token")

    loaded = get_settings()

    assert loaded.aws.region == "eu-central-1"
    assert loaded.agents.resolved_api_base_url == "https://api.example.test"
    assert loaded.agents.resolved_access_token == "token"


def test_blank_required_aws_region_fails_fast() -> None:
    with pytest.raises(ValueError, match="AWS_REGION must be set"):
        PlatformSettings(_env_file=None, AWS_REGION="")  # type: ignore[call-arg]


def test_generic_env_accessors_follow_current_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRIMARY_TOKEN", "")
    monkeypatch.setenv("FALLBACK_TOKEN", "fallback")

    assert env_optional("PRIMARY_TOKEN", "default") == "default"
    assert env_first("PRIMARY_TOKEN", "FALLBACK_TOKEN") == "fallback"


def test_process_required_env_does_not_use_env_example(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)

    with pytest.raises(RuntimeError, match="AWS_REGION must be set"):
        process_env_required("AWS_REGION")


def test_scripts_do_not_read_environment_directly() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    offenders: list[str] = []
    for path in sorted((repo_root / "scripts").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if "os.environ.get" in text or "os.getenv" in text:
            offenders.append(path.relative_to(repo_root).as_posix())

    assert offenders == []
