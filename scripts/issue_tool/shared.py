from __future__ import annotations

import json
import shlex
from pathlib import Path

from platform_config import env_optional


class CliError(RuntimeError):
    pass


def parse_bool_env(name: str, default: bool) -> bool:
    raw = env_optional(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def read_json_file(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def write_json_file(path: Path, payload: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def shell_quote(value: str) -> str:
    return shlex.quote(value)
