"""Tests for Vibe-Trading lifecycle management."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.vibe_bridge.lifecycle import VibeBridgeLifecycle


@pytest.fixture
def lifecycle():
    """Create lifecycle with mocked subprocess."""
    with patch("apps.vibe_bridge.lifecycle.get_vibe_trading_dir") as mock_dir:
        mock_dir.return_value = MagicMock()
        mock_dir.return_value.__truediv__.return_value.exists.return_value = True
        with patch("apps.vibe_bridge.lifecycle.get_vibe_mcp_port", return_value=18900):
            with patch("apps.vibe_bridge.lifecycle.get_vibe_env_overrides", return_value={}):
                yield VibeBridgeLifecycle()


class TestVibeBridgeLifecycle:
    def test_is_running_false_initially(self, lifecycle):
        assert lifecycle.is_running is False

    def test_get_status_initial(self, lifecycle):
        status = lifecycle.get_status()
        assert status["running"] is False
        assert status["pid"] is None
        assert status["port"] == 18900

    @pytest.mark.asyncio
    async def test_start_process_not_found(self, lifecycle):
        """Should return False when mcp_server.py doesn't exist."""
        lifecycle._vibe_dir.__truediv__.return_value.exists.return_value = False
        result = await lifecycle.start()
        assert result is False

    @pytest.mark.asyncio
    async def test_ensure_running_starts_if_not_running(self, lifecycle):
        """Should start the process when not running."""
        with patch.object(lifecycle, "start", new_callable=AsyncMock) as mock_start:
            mock_start.return_value = True
            result = await lifecycle.ensure_running()
            mock_start.assert_called_once()
            assert result is True
