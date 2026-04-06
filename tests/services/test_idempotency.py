"""Tests for Phase 1.2 — Idempotency enforcement.

Tests:
  - test_execution_gateway_idempotent_replay: same key returns cached result
  - test_gateway_queue_dedupes_by_idempotency_key: duplicate key doesn't enqueue twice
  - test_gateway_queue_ttl_expiry: expired key allows re-enqueue
  - test_mcp_server_auto_generates_idempotency_key: missing key gets uuid4
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from mt5_mcp.schemas.models import ExecutionResult, TradeIntent
from mt5_mcp.settings.config import Settings


class TestExecutionGatewayIdempotentReplay:
    """Tests for ExecutionGateway idempotency registry (1.2.1)."""

    def _make_gateway(self):
        """Create an ExecutionGateway with a mocked adapter."""
        from mt5_mcp.services.execution_gateway.service import ExecutionGateway

        mock_adapter = MagicMock()
        return ExecutionGateway(adapter=mock_adapter)

    def test_execution_gateway_idempotent_replay(self):
        gw = self._make_gateway()

        intent = TradeIntent(
            account_id="demo",
            symbol="XAUUSD",
            side="buy",
            order_kind="market",
            volume_lots=0.1,
            idempotency_key="key-1",
        )
        gw.adapter.submit_order.return_value = ExecutionResult(
            intent_id="test-intent",
            status="submitted",
            adapter="mock",
            broker_order_id="order-1",
        )

        result1 = gw.submit_order(intent)
        assert gw.adapter.submit_order.call_count == 1
        assert result1.broker_order_id == "order-1"

        result2 = gw.submit_order(intent)
        assert gw.adapter.submit_order.call_count == 1
        assert result2.broker_order_id == "order-1"
        assert result2 is result1

    def test_execution_gateway_different_keys_call_adapter(self):
        gw = self._make_gateway()
        gw.adapter.submit_order.return_value = ExecutionResult(
            intent_id="test-intent", status="submitted", adapter="mock"
        )

        intent1 = TradeIntent(
            account_id="demo",
            symbol="XAUUSD",
            side="buy",
            order_kind="market",
            volume_lots=0.1,
            idempotency_key="key-a",
        )
        intent2 = TradeIntent(
            account_id="demo",
            symbol="XAUUSD",
            side="buy",
            order_kind="market",
            volume_lots=0.1,
            idempotency_key="key-b",
        )

        gw.submit_order(intent1)
        gw.submit_order(intent2)

        assert gw.adapter.submit_order.call_count == 2

    def test_execution_gateway_no_key_calls_adapter_every_time(self):
        gw = self._make_gateway()
        gw.adapter.submit_order.return_value = ExecutionResult(
            intent_id="test-intent", status="submitted", adapter="mock"
        )

        intent = TradeIntent(
            account_id="demo",
            symbol="XAUUSD",
            side="buy",
            order_kind="market",
            volume_lots=0.1,
            idempotency_key=None,
        )

        gw.submit_order(intent)
        gw.submit_order(intent)

        assert gw.adapter.submit_order.call_count == 2


class TestGatewayQueueDedupe:
    """Tests for InMemoryQueue idempotency deduplication with TTL (1.2.2)."""

    def test_gateway_queue_dedupes_by_idempotency_key(self):
        """Duplicate idempotency_key returns same cmd_id without re-enqueueing."""
        from mt5_mcp.services.gateway_queue import InMemoryQueue

        q = InMemoryQueue()

        cmd_id1 = q.enqueue(
            "submit_order", {"symbol": "XAUUSD"}, idempotency_key="idem-1"
        )
        cmd_id2 = q.enqueue(
            "submit_order", {"symbol": "XAUUSD"}, idempotency_key="idem-1"
        )

        assert cmd_id1 == cmd_id2
        # Only one command in the queue
        cmds = list(q._cmds.values())
        assert len(cmds) == 1

    def test_gateway_queue_different_keys_enqueue_separately(self):
        """Different idempotency_keys create separate commands."""
        from mt5_mcp.services.gateway_queue import InMemoryQueue

        q = InMemoryQueue()

        cmd_id1 = q.enqueue(
            "submit_order", {"symbol": "XAUUSD"}, idempotency_key="idem-a"
        )
        cmd_id2 = q.enqueue(
            "submit_order", {"symbol": "XAUUSD"}, idempotency_key="idem-b"
        )

        assert cmd_id1 != cmd_id2
        assert len(q._cmds) == 2

    def test_gateway_queue_no_key_enqueues_every_time(self):
        """No idempotency_key means every enqueue creates a new command."""
        from mt5_mcp.services.gateway_queue import InMemoryQueue

        q = InMemoryQueue()

        cmd_id1 = q.enqueue("submit_order", {"symbol": "XAUUSD"})
        cmd_id2 = q.enqueue("submit_order", {"symbol": "XAUUSD"})

        assert cmd_id1 != cmd_id2
        assert len(q._cmds) == 2

    def test_gateway_queue_ttl_expiry(self):
        """Expired idempotency_key allows re-enqueue with a new cmd_id."""
        from mt5_mcp.services.gateway_queue import InMemoryQueue

        q = InMemoryQueue()
        # Set a very short TTL for testing
        q._idempotency_ttl = 0.1  # 100ms

        cmd_id1 = q.enqueue(
            "submit_order", {"symbol": "XAUUSD"}, idempotency_key="idem-ttl"
        )

        # Wait for TTL to expire
        time.sleep(0.15)

        cmd_id2 = q.enqueue(
            "submit_order", {"symbol": "XAUUSD"}, idempotency_key="idem-ttl"
        )

        assert cmd_id1 != cmd_id2
        assert len(q._cmds) == 2


class TestMCPServerAutoGenerateIdempotencyKey:
    """Tests for MCP server auto-generating idempotency_key (1.2.3)."""

    def test_mcp_server_auto_generates_idempotency_key(self):
        """When idempotency_key is None, MCP server auto-generates one using uuid4."""
        from apps.mcp_server.main import tool_submit_market_order_via_bridge
        import uuid

        # Create a TradeIntent without idempotency_key
        req = TradeIntent(
            account_id="demo",
            symbol="XAUUSD",
            side="buy",
            order_kind="market",
            volume_lots=0.1,
            idempotency_key=None,
        )

        # We need to mock the entire chain since we can't have a real bridge
        with (
            patch("apps.mcp_server.main.get_settings_cached") as mock_settings,
            patch("apps.mcp_server.main._tcp_send_and_await", return_value=None),
            patch("apps.mcp_server.main.get_http_client") as mock_http,
            patch("apps.mcp_server.main._await_result") as mock_await,
            patch("apps.mcp_server.main.get_policy") as mock_policy,
        ):
            # Setup mocks
            mock_settings.return_value.gateway_url = "http://127.0.0.1:8020"
            mock_settings.return_value.environment = "demo"
            mock_policy.return_value.validate_submit_order.return_value.allowed = True

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"id": "test-cmd-id"}
            mock_http.return_value.post.return_value = mock_response

            mock_await.return_value = {
                "status": "completed",
                "result": {"payload": '{"retcode": 10009, "order": "12345"}'},
            }

            try:
                tool_submit_market_order_via_bridge(req)
            except Exception:
                pass  # May fail for other reasons, we just want to check the idempotency_key

            # Check that the POST call included an auto-generated idempotency_key
            post_call = mock_http.return_value.post
            assert post_call.called
            call_kwargs = post_call.call_args
            # The params should include idempotency_key
            params = call_kwargs.kwargs.get("params", {})
            assert "idempotency_key" in params
            assert params["idempotency_key"] is not None
            # Should be a valid UUID
            try:
                uuid.UUID(params["idempotency_key"])
            except ValueError:
                pytest.fail(
                    f"idempotency_key is not a valid UUID: {params['idempotency_key']}"
                )

    def test_mcp_server_preserves_existing_idempotency_key(self):
        """When idempotency_key is provided, MCP server does not overwrite it."""
        from apps.mcp_server.main import tool_submit_market_order_via_bridge

        req = TradeIntent(
            account_id="demo",
            symbol="XAUUSD",
            side="buy",
            order_kind="market",
            volume_lots=0.1,
            idempotency_key="my-custom-key-123",
        )

        with (
            patch("apps.mcp_server.main.get_settings_cached") as mock_settings,
            patch("apps.mcp_server.main._tcp_send_and_await", return_value=None),
            patch("apps.mcp_server.main.get_http_client") as mock_http,
            patch("apps.mcp_server.main._await_result") as mock_await,
            patch("apps.mcp_server.main.get_policy") as mock_policy,
        ):
            mock_settings.return_value.gateway_url = "http://127.0.0.1:8020"
            mock_settings.return_value.environment = "demo"
            mock_policy.return_value.validate_submit_order.return_value.allowed = True

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"id": "test-cmd-id"}
            mock_http.return_value.post.return_value = mock_response

            mock_await.return_value = {
                "status": "completed",
                "result": {"payload": '{"retcode": 10009, "order": "12345"}'},
            }

            try:
                tool_submit_market_order_via_bridge(req)
            except Exception:
                pass

            post_call = mock_http.return_value.post
            assert post_call.called
            call_kwargs = post_call.call_args
            params = call_kwargs.kwargs.get("params", {})
            assert params.get("idempotency_key") == "my-custom-key-123"
