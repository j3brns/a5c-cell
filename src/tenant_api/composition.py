from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from src.tenant_api import bootstrap, dependency_factories, utils
from src.tenant_api.models import TenantApiDependencies


@dataclass(frozen=True)
class TenantApiConfig:
    aws_region: str

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> TenantApiConfig:
        source = os.environ if environ is None else environ
        aws_region = utils.str_or_none(source.get("AWS_REGION"))
        if aws_region is None:
            raise RuntimeError("AWS_REGION environment variable is required")
        return cls(aws_region=aws_region)


def build_dependencies(config: TenantApiConfig) -> TenantApiDependencies:
    return dependency_factories.build_tenant_api_dependencies(region=config.aws_region)


def build_runtime(
    event: dict[str, Any],
    *,
    config: TenantApiConfig | None = None,
    dependencies: TenantApiDependencies | None = None,
) -> bootstrap.TenantApiRuntime:
    deps = (
        dependencies
        if dependencies is not None
        else build_dependencies(config or TenantApiConfig.from_env())
    )
    return bootstrap.build_runtime(event, dependencies=deps)
