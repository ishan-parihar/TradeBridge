"""Tests for Vibe-Trading MCP client."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.vibe_bridge.client import VibeBridgeClient, VIBE_TOOLS


@pytest.fixture
def client():
    """Create client with mocked lifecycle."""
    mock_lifecycle = MagicMock()
    mock_lifecycle.is_running = True
    mock_lifecycle.ensure_running = AsyncMock(return_value=True)
    with patch("apps.vibe_bridge.client.get_vibe_mcp_port", return_value=18900):
        yield VibeBridgeClient(lifecycle=mock_lifecycle)


class TestVibeBridgeClient:
    def test_valid_tools_list(self):
        assert len(VIBE_TOOLS) == 17
        assert "backtest" in VIBE_TOOLS
        assert "run_swarm" in VIBE_TOOLS
        assert "get_market_data" in VIBE_TOOLS

    @pytest.mark.asyncio
    async def test_call_invalid_tool_raises(self, client):
        with pytest.raises(ValueError, match="Unknown Vibe-Trading tool"):
            await client.call_tool("nonexistent_tool")

    @pytest.mark.asyncio
    async def test_call_tool_when_not_running_raises(self):
        mock_lifecycle = MagicMock()
        mock_lifecycle.is_running = False
        client = VibeBridgeClient(lifecycle=mock_lifecycle)
        with pytest.raises(RuntimeError, match="not running"):
            await client.call_tool("list_skills")

    @pytest.mark.asyncio
    async def test_ensure_ready_calls_lifecycle(self, client):
        result = await client.ensure_ready()
        client._lifecycle.ensure_running.assert_called_once()
        assert result is True

    @pytest.mark.asyncio
    async def test_get_status(self, client):
        client._lifecycle.get_status.return_value = {"running": True, "pid": 12345}
        status = client.get_status()
        assert status["running"] is True
        assert status["pid"] == 12345
