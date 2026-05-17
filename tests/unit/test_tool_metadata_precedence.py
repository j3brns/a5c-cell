from __future__ import annotations

from unittest.mock import MagicMock

from data_access.models import TenantContext, TenantTier

from gateway.interceptors import request_tools, response_tools


class MockDB:
    def __init__(self, items):
        self.items = items
        self.calls = []

    def get_item(self, table_name, key, **kwargs):
        self.calls.append(key)
        return self.items.get((key["PK"], key["SK"]))


def test_request_tools_precedence_repro():
    """
    Reproduce the issue where request_tools prefers GLOBAL if TENANT is disabled.
    Wait, the requirement is 'Tenant-specific records win consistently'.
    If a tenant record exists, it should be the final word.
    """
    tenant_id = "tenant-1"
    tool_name = "test-tool"

    # TENANT record is disabled, GLOBAL is enabled.
    items = {
        (f"TOOL#{tool_name}", f"TENANT#{tenant_id}"): {"enabled": False, "tier_minimum": "basic"},
        (f"TOOL#{tool_name}", "GLOBAL"): {"enabled": True, "tier_minimum": "basic"},
    }
    mock_db = MockDB(items)

    record = request_tools.get_tool_record(
        tool_name=tool_name,
        tenant_id=tenant_id,
        db_factory=lambda ctx: mock_db,
        get_platform_context=lambda: None,
        tools_table="tools",
    )

    # EXPECTED BEHAVIOR: It should stop at the TENANT record and return None because it is disabled.
    assert record is None, "Should stop at TENANT record (disabled) and not fall back to GLOBAL"
    assert mock_db.calls == [
        {"PK": f"TOOL#{tool_name}", "SK": f"TENANT#{tenant_id}"},
    ]


def test_response_tools_precedence_repro():
    """
    Reproduce the issue where response_tools prefers GLOBAL over TENANT.
    """
    tenant_id = "tenant-1"
    tool_name = "test-tool"

    # TENANT has different tier than GLOBAL.
    items = {
        (f"TOOL#{tool_name}", f"TENANT#{tenant_id}"): {"enabled": True, "tier_minimum": "premium"},
        (f"TOOL#{tool_name}", "GLOBAL"): {"enabled": True, "tier_minimum": "basic"},
    }
    mock_db = MockDB(items)

    context = TenantContext(tenant_id=tenant_id, app_id="app", tier=TenantTier.BASIC, sub="sub")

    tier = response_tools.resolve_tool_minimum_tier(
        tool={"name": tool_name},
        context=context,
        db=mock_db,
        tools_table="tools",
        logger=MagicMock(),
    )

    # EXPECTED BEHAVIOR: It should pick TENANT (premium).
    assert tier == TenantTier.PREMIUM, "Should prefer TENANT record over GLOBAL"
    assert mock_db.calls == [{"PK": f"TOOL#{tool_name}", "SK": f"TENANT#{tenant_id}"}]


def test_response_tools_precedence_registry_over_payload():
    """
    Test that registry records win over payload tier.
    """
    tenant_id = "tenant-1"
    tool_name = "test-tool"

    # Payload says BASIC, Registry says PREMIUM.
    items = {
        (f"TOOL#{tool_name}", "GLOBAL"): {"enabled": True, "tier_minimum": "premium"},
    }
    mock_db = MockDB(items)

    context = TenantContext(tenant_id=tenant_id, app_id="app", tier=TenantTier.BASIC, sub="sub")

    tier = response_tools.resolve_tool_minimum_tier(
        tool={"name": tool_name, "tierMinimum": "basic"},
        context=context,
        db=mock_db,
        tools_table="tools",
        logger=MagicMock(),
    )

    assert tier == TenantTier.PREMIUM, "Registry should override payload tier"
    assert mock_db.calls == [
        {"PK": f"TOOL#{tool_name}", "SK": f"TENANT#{tenant_id}"},
        {"PK": f"TOOL#{tool_name}", "SK": "GLOBAL"},
    ]


def test_response_tools_precedence_disabled_in_registry():
    """
    Test that a tool disabled in registry is filtered out even if in payload.
    """
    tenant_id = "tenant-1"
    tool_name = "test-tool"

    # Payload has tool, Registry disables it.
    items = {
        (f"TOOL#{tool_name}", "GLOBAL"): {"enabled": False, "tier_minimum": "basic"},
    }
    mock_db = MockDB(items)

    context = TenantContext(tenant_id=tenant_id, app_id="app", tier=TenantTier.BASIC, sub="sub")

    tier = response_tools.resolve_tool_minimum_tier(
        tool={"name": tool_name, "tierMinimum": "basic"},
        context=context,
        db=mock_db,
        tools_table="tools",
        logger=MagicMock(),
    )

    assert tier is None, "Disabled in registry should return None"
