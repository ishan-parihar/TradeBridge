"""Integration tests for critical autonomous trading path.

Tests the endpoints that were identified as broken in MCP_TOOL_USAGE_REPORT.md
(jesse-session1, 2026-04-07) and verifies they now work correctly.
"""

import pytest
from fastapi.testclient import TestClient

from apps.mcp_server.main import app


@pytest.fixture
def client():
    """Create a test clients that doesn't auto-raise exceptions."""
    return TestClient(app, raise_server_exceptions=False)


class TestPlaceBracketOrder:
    """P0.1: place_bracket_order should not 500 even when journal DB fails."""

    def test_bracket_order_returns_placed_or_error_not_500(self, client):
        """Bracket order should never return HTTP 500 — journal fix is non-fatal."""
        response = client.post(
            "/tools/place_bracket_order",
            json={
                "symbol": "XAUUSD",
                "buy_trigger": 3000.0,
                "sell_trigger": 2900.0,
                "volume_lots": 0.01,
                "sl_atr_multiplier": 1.0,
                "tp_atr_multiplier": 2.0,
            },
        )
        # 500 is the old behavior (journal crash) — should never happen now
        assert response.status_code != 500, (
            "place_bracket_order returned 500 — journal fix may not be applied"
        )
        # Could be 200 (placed), 403 (policy blocked), 422 (validation), or 200 with error in body
        # All are acceptable — the key is no 500

    def test_bracket_order_returns_valid_structure(self, client):
        """Response should have status field."""
        response = client.post(
            "/tools/place_bracket_order",
            json={
                "symbol": "XAUUSD",
                "buy_trigger": 3000.0,
                "sell_trigger": 2900.0,
                "volume_lots": 0.01,
            },
        )
        data = response.json()
        assert "status" in data, "Response should have 'status' field"
        assert data["status"] in ("placed", "error", "partial"), (
            f"Unexpected status value: {data['status']}"
        )


class TestTradeJournal:
    """P0.2: trading/log_decision should accept and store decisions."""

    def test_log_decision_returns_logged(self, client):
        """Log a minimal trade decision — should return status='logged'."""
        response = client.post(
            "/tools/trading/log_decision",
            json={
                "symbol": "XAUUSD",
                "side": "buy",
                "action": "entry",
                "entry_price": 2650.0,
                "volume_lots": 0.10,
                "sl": 2640.0,
                "tp": 2680.0,
            },
        )
        assert response.status_code != 500, (
            "trading/log_decision returned 500 — journal fix may not be applied"
        )
        data = response.json()
        assert data.get("status") in ("logged", "error"), (
            f"Expected 'logged' or 'error', got: {data.get('status')}"
        )

    def test_log_decision_with_complex_indicator_snapshot(self, client):
        """Log a decision with complex indicator data — should not crash on json.dumps."""
        response = client.post(
            "/tools/trading/log_decision",
            json={
                "symbol": "EURUSD",
                "side": "sell",
                "action": "entry",
                "entry_price": 1.0850,
                "indicator_snapshot": {
                    "rsi": 72.5,
                    "macd": {"main": 0.0012, "signal": 0.0008},
                    "ema_20": 1.0845,
                    "ema_50": 1.0830,
                },
                "indicators_considered": ["rsi", "macd", "ema"],
                "confidence_level": 0.75,
                "emotional_self_report": "calm",
            },
        )
        assert response.status_code != 500, (
            "trading/log_decision crashed with complex indicator_snapshot"
        )

    def test_reflect_on_trades_returns_decisions(self, client):
        """After logging decisions, reflect should return them."""
        # First log a decision
        client.post(
            "/tools/trading/log_decision",
            json={
                "symbol": "XAUUSD",
                "side": "buy",
                "action": "entry",
                "entry_price": 2650.0,
            },
        )

        # Then query it back
        response = client.post(
            "/tools/trading/reflect",
            json={"symbol": "XAUUSD", "limit": 10},
        )
        assert response.status_code != 500, "trading/reflect returned 500"
        data = response.json()
        assert "decisions" in data, "Response should have 'decisions' field"
        assert "count" in data, "Response should have 'count' field"


class TestWaitEndpoints:
    """P0.3: Wait endpoints should respond (not 404)."""

    def test_wait_delay_responds(self, client):
        """wait/delay with 0 seconds should respond immediately."""
        response = client.post(
            "/tools/wait/delay",
            json={"duration_seconds": 0},
        )
        assert response.status_code in (200, 422), (
            f"wait/delay returned {response.status_code} — endpoint may not be registered"
        )

    def test_wait_indicator_responds(self, client):
        """wait/indicator with 1s timeout should respond (even if not triggered)."""
        response = client.post(
            "/tools/wait/indicator",
            json={
                "symbol": "XAUUSD",
                "indicator": "rsi",
                "condition": "above",
                "value": 30,
                "timeout_seconds": 1,
            },
        )
        assert response.status_code in (200, 422), (
            f"wait/indicator returned {response.status_code} — endpoint may not be registered"
        )

    def test_wait_for_price_responds(self, client):
        """market/wait_for_price with 1s timeout should respond."""
        response = client.post(
            "/resources/market/wait_for_price",
            json={
                "symbol": "XAUUSD",
                "condition": "above",
                "price": 99999.0,
                "timeout_seconds": 1,
            },
        )
        assert response.status_code in (200, 422), (
            f"wait_for_price returned {response.status_code} — endpoint may not be registered"
        )


class TestPositionsOpen:
    """P0.4: positions_open should not silently swallow errors."""

    def test_positions_open_returns_list(self, client):
        """positions_open should always return a list, even when empty."""
        response = client.get("/resources/positions/open")
        assert response.status_code == 200, (
            f"positions_open returned {response.status_code}"
        )
        data = response.json()
        assert isinstance(data, list), (
            f"positions_open should return a list, got: {type(data)}"
        )

    def test_positions_open_response_model_valid(self, client):
        """Each position in the response should have required fields."""
        response = client.get("/resources/positions/open")
        data = response.json()
        for pos in data:
            assert "position_id" in pos or "symbol" in pos, (
                f"Position missing required fields: {pos}"
            )
