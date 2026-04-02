from __future__ import annotations

import json
import os
from typing import Any

from src.tenant_api.constants import EVENT_BUS_ENV
from src.tenant_api.models import TenantApiDependencies
from src.tenant_api.utils import json_default


def event_bus_name() -> str:
    return os.environ.get(EVENT_BUS_ENV, "default")


def put_event(
    deps: TenantApiDependencies,
    *,
    detail_type: str,
    detail: dict[str, Any],
) -> None:
    deps.events.put_events(
        Entries=[
            {
                "Source": "platform.tenant_api",
                "DetailType": detail_type,
                "Detail": json.dumps(detail, default=json_default),
                "EventBusName": event_bus_name(),
            }
        ]
    )
