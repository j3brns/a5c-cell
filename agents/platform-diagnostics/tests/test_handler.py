import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_AGENT_ROOT = Path(__file__).parent.parent
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

strands_module = types.ModuleType("strands")
strands_module.Agent = MagicMock()
sys.modules.setdefault("strands", strands_module)

from handler import handler  # noqa: E402


@pytest.fixture
def mock_gateway_tools():
    with patch("handler.get_gateway_tools") as mock:
        mock.return_value = []
        yield mock


@pytest.fixture
def mock_agent():
    with patch("handler.Agent") as mock:
        agent_instance = mock.return_value
        agent_instance.return_value = MagicMock(message="Agent diagnostic result")
        yield mock


def test_handler_as_platform_tenant(mock_gateway_tools, mock_agent):
    payload = {"prompt": "Status report", "tenantId": "platform", "appid": "admin-ui"}
    context = MagicMock()

    response = handler(payload, context)

    assert "output" in response
    assert response["output"] == "Agent diagnostic result"
    mock_agent.assert_called_once()


def test_handler_access_denied_for_other_tenant(mock_gateway_tools, mock_agent):
    payload = {"prompt": "Status report", "tenantId": "t-test-001", "appid": "some-app"}
    context = MagicMock()

    response = handler(payload, context)

    assert "error" in response
    assert response["code"] == "ACCESS_DENIED"
    mock_agent.assert_not_called()
