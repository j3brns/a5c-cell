from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "data-access-lib" / "src"))

from data_access.models import AgentStatus, TenantContext, TenantTier

from src.bridge import discovery_service, lock_manager
from src.bridge import runtime_dependencies as runtime_deps
from src.bridge.constants import AGENTS_TABLE


def test_get_agent_record_uses_platform_control_plane_db() -> None:
    agent_record = MagicMock()

    with (
        patch("src.bridge.runtime_dependencies.ControlPlaneDynamoDB") as mock_db_cls,
        patch(
            "src.bridge.runtime_dependencies.discovery_resolve_agent_record",
            return_value=agent_record,
        ) as mock_resolve,
    ):
        result = runtime_deps.get_agent_record("echo-agent", "1.0.0")

    assert result is agent_record
    ctx = mock_db_cls.call_args.args[0]
    assert ctx.tenant_id == "platform"
    assert ctx.app_id == "platform-bridge"
    assert ctx.tier == TenantTier.PREMIUM
    mock_resolve.assert_called_once_with(
        mock_db_cls.return_value,
        agents_table=AGENTS_TABLE,
        agent_name="echo-agent",
        agent_version="1.0.0",
    )


def test_resolve_agent_record_queries_control_plane_db_for_latest_promoted_version() -> None:
    mock_db = MagicMock()
    mock_db.query.return_value.items = [
        {
            "PK": "AGENT#echo-agent",
            "SK": "VERSION#1.1.0",
            "agent_name": "echo-agent",
            "version": "1.1.0",
            "owner_team": "platform",
            "tier_minimum": "basic",
            "layer_hash": "hash-1",
            "layer_s3_key": "layer-1",
            "script_s3_key": "script-1",
            "deployed_at": "2026-01-01T00:00:00Z",
            "invocation_mode": "sync",
            "streaming_enabled": False,
            "status": "built",
        },
        {
            "PK": "AGENT#echo-agent",
            "SK": "VERSION#1.0.0",
            "agent_name": "echo-agent",
            "version": "1.0.0",
            "owner_team": "platform",
            "tier_minimum": "basic",
            "layer_hash": "hash-0",
            "layer_s3_key": "layer-0",
            "script_s3_key": "script-0",
            "deployed_at": "2026-01-02T00:00:00Z",
            "invocation_mode": "sync",
            "streaming_enabled": False,
            "status": "promoted",
        },
    ]

    record = discovery_service.resolve_agent_record(
        mock_db,
        agents_table="platform-agents",
        agent_name="echo-agent",
    )

    assert record is not None
    assert record.version == "1.0.0"
    assert record.status == AgentStatus.PROMOTED
    mock_db.query.assert_called_once_with("platform-agents", pk_value="AGENT#echo-agent")


def test_resolve_agent_record_skips_promoted_records_with_missing_zip_layer_metadata() -> None:
    mock_db = MagicMock()
    mock_db.query.return_value.items = [
        {
            "PK": "AGENT#echo-agent",
            "SK": "VERSION#1.1.0",
            "agent_name": "echo-agent",
            "version": "1.1.0",
            "owner_team": "platform",
            "tier_minimum": "basic",
            "layer_hash": "",
            "layer_s3_key": "",
            "script_s3_key": "script-1",
            "deployed_at": "2026-01-03T00:00:00Z",
            "invocation_mode": "sync",
            "streaming_enabled": False,
            "status": "promoted",
        },
        {
            "PK": "AGENT#echo-agent",
            "SK": "VERSION#1.0.0",
            "agent_name": "echo-agent",
            "version": "1.0.0",
            "owner_team": "platform",
            "tier_minimum": "basic",
            "layer_hash": "hash-0",
            "layer_s3_key": "layer-0",
            "script_s3_key": "script-0",
            "deployed_at": "2026-01-02T00:00:00Z",
            "invocation_mode": "sync",
            "streaming_enabled": False,
            "status": "promoted",
        },
    ]

    record = discovery_service.resolve_agent_record(
        mock_db,
        agents_table="platform-agents",
        agent_name="echo-agent",
    )

    assert record is not None
    assert record.version == "1.0.0"


def test_resolve_agent_record_rejects_specific_legacy_async_version() -> None:
    mock_db = MagicMock()
    mock_db.get_item.return_value = {
        "PK": "AGENT#echo-agent",
        "SK": "VERSION#1.0.0",
        "agent_name": "echo-agent",
        "version": "1.0.0",
        "owner_team": "platform",
        "tier_minimum": "basic",
        "layer_hash": "hash-0",
        "layer_s3_key": "layer-0",
        "script_s3_key": "script-0",
        "deployed_at": "2026-01-02T00:00:00Z",
        "invocation_mode": "async",
        "streaming_enabled": False,
        "status": "promoted",
    }

    record = discovery_service.resolve_agent_record(
        mock_db,
        agents_table="platform-agents",
        agent_name="echo-agent",
        agent_version="1.0.0",
    )

    assert record is None
    mock_db.get_item.assert_called_once_with(
        "platform-agents", {"PK": "AGENT#echo-agent", "SK": "VERSION#1.0.0"}
    )


def test_get_agent_detail_uses_platform_context_db_factory() -> None:
    mock_db = MagicMock()
    mock_db.query.return_value.items = [
        {
            "agent_name": "echo-agent",
            "version": "1.0.0",
            "owner_team": "platform",
            "tier_minimum": "basic",
            "deployed_at": "2026-01-01T00:00:00Z",
            "invocation_mode": "sync",
            "streaming_enabled": False,
            "status": "promoted",
        }
    ]

    with (
        patch("src.bridge.discovery_service.ControlPlaneDynamoDB", return_value=mock_db),
        patch("src.bridge.runtime_dependencies.get_platform_context") as mock_get_ctx,
    ):
        mock_get_ctx.return_value = TenantContext(
            tenant_id="platform",
            app_id="bridge-discovery",
            tier=TenantTier.PREMIUM,
            sub="bridge-lambda",
        )
        response = discovery_service.get_agent_detail(
            {"agentName": "echo-agent"},
            "req-123",
        )

    assert response["statusCode"] == 200
    mock_get_ctx.assert_called_once()
    mock_db.query.assert_called_once_with(AGENTS_TABLE, pk_value="AGENT#echo-agent")


def test_trigger_failover_is_disabled_for_v0_2_topology() -> None:
    ssm = MagicMock()

    with (
        patch("src.bridge.lock_manager.ControlPlaneDynamoDB") as mock_db_cls,
        patch("src.bridge.lock_manager.acquire_lock", return_value="lock-123") as mock_acquire,
        patch("src.bridge.lock_manager.release_lock") as mock_release,
        patch("src.bridge.lock_manager.get_ssm", return_value=ssm),
    ):
        result = lock_manager.trigger_failover(
            current_region="eu-west-2",
        )

    assert result is None
    mock_db_cls.assert_not_called()
    mock_acquire.assert_not_called()
    mock_release.assert_not_called()
    ssm.get_parameter.assert_not_called()
