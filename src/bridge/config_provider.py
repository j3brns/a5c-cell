from __future__ import annotations
import os
import time
from collections.abc import Callable
from typing import Any
from aws_lambda_powertools import Logger
from src.bridge.constants import RUNTIME_REGION_PARAM, MOCK_RUNTIME_URL_PARAM

logger = Logger(service="bridge-config-provider")

class ConfigProvider:
    """Cache-backed configuration provider for Bridge runtime settings."""

    def __init__(
        self,
        *,
        fetcher: Callable[[], dict[str, Any]],
        fallback_factory: Callable[[], dict[str, Any]],
        ttl_seconds: int = 60,
    ) -> None:
        self._fetcher = fetcher
        self._fallback_factory = fallback_factory
        self._ttl_seconds = ttl_seconds
        self._cache: dict[str, Any] | None = None
        self._expires_at: float = 0

    @property
    def expires_at(self) -> float:
        return self._expires_at

    def get(self, *, force_refresh: bool = False) -> dict[str, Any]:
        now = time.time()
        if not force_refresh and self._cache is not None and now < self._expires_at:
            return self._cache

        try:
            self._cache = self._fetcher()
        except Exception:
            if not self._cache:
                self._cache = self._fallback_factory()
        self._expires_at = now + self._ttl_seconds
        return self._cache

def fetch_appconfig_config(http_session: Any) -> dict[str, Any] | None:
    """Fetch configuration from the local AppConfig Lambda Extension."""
    app_id = os.environ.get("APPCONFIG_APPLICATION_ID")
    env_id = os.environ.get("APPCONFIG_ENVIRONMENT_ID")
    profile_id = os.environ.get("APPCONFIG_PROFILE_ID")

    if not all([app_id, env_id, profile_id]):
        return None

    url = f"http://localhost:2772/applications/{app_id}/environments/{env_id}/configurations/{profile_id}"
    try:
        response = http_session.get(url, timeout=1.0)
        if response.status_code == 200:
            config = response.json()
            return {
                "runtime_region": config.get("runtime_region", "eu-west-1"),
                "mock_runtime_url": config.get("mock_runtime_url"),
            }
    except Exception:
        logger.warning("Failed to fetch config from local AppConfig extension")
    return None

def fetch_ssm_config(ssm: Any, http_session: Any) -> dict[str, Any]:
    """Fetch Bridge runtime configuration from local AppConfig or SSM fallback."""
    config = fetch_appconfig_config(http_session)
    if config:
        return config

    try:
        names = [RUNTIME_REGION_PARAM, MOCK_RUNTIME_URL_PARAM]
        response = ssm.get_parameters(Names=names)
        params: dict[str, str] = {
            str(p.get("Name")): str(p.get("Value"))
            for p in response.get("Parameters", [])
            if p.get("Name") and p.get("Value")
        }
        return {
            "runtime_region": params.get(RUNTIME_REGION_PARAM, "eu-west-1"),
            "mock_runtime_url": params.get(MOCK_RUNTIME_URL_PARAM),
        }
    except Exception:
        logger.exception("Failed to fetch config from SSM fallback")
        raise

def config_defaults() -> dict[str, Any]:
    return {"runtime_region": "eu-west-1", "mock_runtime_url": None}
