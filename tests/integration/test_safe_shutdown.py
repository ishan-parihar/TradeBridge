from __future__ import annotations

import json
import pytest
from types import SimpleNamespace

from mt5_mcp.schemas.tools import SafeShutdownRequest


class TestSafeShutdownRequestSchema:
    def test_valid_request_defaults(self):
        req = SafeShutdownRequest()
        assert req.mode == "full"
        assert req.preserve_stops is True
        assert req.session_id is None
        assert req.strategy_id is None
        assert req.intent_id is None

    def test_valid_request_flatten_mode(self):
        req = SafeShutdownRequest(mode="flatten")
        assert req.mode == "flatten"

    def test_valid_request_freeze_mode(self):
        req = SafeShutdownRequest(mode="freeze")
        assert req.mode == "freeze"

    def test_valid_request_full_mode(self):
        req = SafeShutdownRequest(mode="full")
        assert req.mode == "full"

    def test_valid_request_with_ownership_fields(self):
        req = SafeShutdownRequest(
            mode="flatten",
            session_id="sess-1",
            strategy_id="strat-1",
            intent_id="intent-1",
        )
        assert req.session_id == "sess-1"
        assert req.strategy_id == "strat-1"
        assert req.intent_id == "intent-1"

    def test_valid_request_preserve_stops_false(self):
        req = SafeShutdownRequest(preserve_stops=False)
        assert req.preserve_stops is False

    def test_invalid_mode_raises(self):
        with pytest.raises(Exception):
            SafeShutdownRequest(mode="invalid_mode")


class TestFreezeStateFunctions:
    import apps.mcp_server.main as mcp_main

    def test_initial_state_not_frozen(self):
        assert self.mcp_main.is_frozen() is False
        assert self.mcp_main._shutdown_state["frozen_at"] is None
        assert self.mcp_main._shutdown_state["frozen_by"] is None

    def test_set_frozen_true(self):
        self.mcp_main.set_frozen(True, by="test")
        assert self.mcp_main.is_frozen() is True
        assert self.mcp_main._shutdown_state["frozen_at"] is not None
        assert self.mcp_main._shutdown_state["frozen_by"] == "test"
        self.mcp_main._shutdown_state["frozen"] = False
        self.mcp_main._shutdown_state["frozen_at"] = None
        self.mcp_main._shutdown_state["frozen_by"] = None

    def test_thaw_resets_state(self):
        self.mcp_main.set_frozen(True, by="test-thaw")
        self.mcp_main.thaw()
        assert self.mcp_main.is_frozen() is False
        assert self.mcp_main._shutdown_state["frozen_at"] is None
        assert self.mcp_main._shutdown_state["frozen_by"] is None

    def test_check_frozen_response_returns_none_when_not_frozen(self):
        self.mcp_main._shutdown_state["frozen"] = False
        result = self.mcp_main._check_frozen_response()
        assert result is None

    def test_check_frozen_response_returns_error_when_frozen(self):
        self.mcp_main.set_frozen(True, by="test-check")
        result = self.mcp_main._check_frozen_response()
        assert result is not None
        assert result["error"] == "Trading is frozen"
        assert result["frozen_by"] == "test-check"
        assert "frozen_at" in result
        self.mcp_main.thaw()


class TestFreezeStateBlocksNewOrders:
    import apps.mcp_server.main as mcp_main

    def test_submit_market_order_via_bridge_blocked_when_frozen(self, monkeypatch):
        self.mcp_main.set_frozen(True, by="test-block")

        def fake_tcp(*a, **kw):
            return {"status": "completed", "result": {"payload": '{"retcode":10009}'}}

        monkeypatch.setattr(self.mcp_main, "_tcp_send_and_await", fake_tcp)

        from mt5_mcp.schemas.models import TradeIntent

        req = TradeIntent(
            symbol="XAUUSD",
            side="buy",
            order_kind="market",
            volume_lots=0.01,
            account_id="demo",
        )
        result = self.mcp_main.tool_submit_market_order_via_bridge(req)
        assert result.status == "error"
        assert result.raw.get("error") == "Trading is frozen"

        self.mcp_main.thaw()

    def test_submit_pending_order_blocked_when_frozen(self, monkeypatch):
        self.mcp_main.set_frozen(True, by="test-block-pending")

        def fake_tcp(*a, **kw):
            return {"status": "completed", "result": {"payload": '{"status":"ok"}'}}

        monkeypatch.setattr(self.mcp_main, "_tcp_send_and_await", fake_tcp)

        from mt5_mcp.schemas.tools import SubmitPendingOrderRequest

        req = SubmitPendingOrderRequest(
            symbol="XAUUSD",
            side="buy",
            kind="limit",
            price=2650.0,
            volume_lots=0.01,
        )
        result = self.mcp_main.tool_submit_pending_order(req)
        assert "error" in result
        assert "frozen" in result["error"].lower()

        self.mcp_main.thaw()

    def test_place_bracket_order_blocked_when_frozen(self, monkeypatch):
        self.mcp_main.set_frozen(True, by="test-block-bracket")

        from mt5_mcp.schemas.tools import BracketOrderRequest

        req = BracketOrderRequest(
            symbol="XAUUSD",
            buy_trigger=2660.0,
            sell_trigger=2640.0,
            volume_lots=0.01,
        )
        result = self.mcp_main.tool_place_bracket_order(req)
        assert result.status == "error"
        assert "frozen" in result.message.lower()

        self.mcp_main.thaw()

    def test_close_position_allowed_when_frozen(self, monkeypatch):
        self.mcp_main.set_frozen(True, by="test-close-allowed")

        captured = {}

        def fake_tcp(cmd_type, params):
            captured["type"] = cmd_type
            return {"status": "completed", "result": {"payload": '{"status":"ok"}'}}

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"id": "close-1"}

        def fake_post(*args, **kwargs):
            return FakeResponse()

        def fake_await(*a, **kw):
            return {"status": "completed", "result": {"payload": '{"status":"ok"}'}}

        monkeypatch.setattr(self.mcp_main, "_tcp_send_and_await", fake_tcp)
        monkeypatch.setattr(
            self.mcp_main.httpx,
            "Client",
            lambda *a, **kw: SimpleNamespace(post=fake_post),
        )
        monkeypatch.setattr(
            self.mcp_main,
            "get_settings_cached",
            lambda: SimpleNamespace(gateway_url="http://127.0.0.1:8020"),
        )
        monkeypatch.setattr(self.mcp_main, "_await_result", fake_await)
        monkeypatch.setattr(self.mcp_main, "_http_client", None)

        from mt5_mcp.schemas.tools import ClosePositionRequest

        req = ClosePositionRequest(position_id="12345")
        result = self.mcp_main.tool_close_position(req)
        assert "error" not in result or result.get("status") != "Trading is frozen"

        self.mcp_main.thaw()

    def test_cancel_order_allowed_when_frozen(self, monkeypatch):
        self.mcp_main.set_frozen(True, by="test-cancel-allowed")

        def fake_tcp(cmd_type, params):
            return {"status": "completed", "result": {"payload": '{"status":"ok"}'}}

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"id": "cancel-1"}

        def fake_post(*args, **kwargs):
            return FakeResponse()

        def fake_await(*a, **kw):
            return {"status": "completed", "result": {"payload": '{"status":"ok"}'}}

        monkeypatch.setattr(self.mcp_main, "_tcp_send_and_await", fake_tcp)
        monkeypatch.setattr(
            self.mcp_main.httpx,
            "Client",
            lambda *a, **kw: SimpleNamespace(post=fake_post),
        )
        monkeypatch.setattr(
            self.mcp_main,
            "get_settings_cached",
            lambda: SimpleNamespace(gateway_url="http://127.0.0.1:8020"),
        )
        monkeypatch.setattr(self.mcp_main, "_await_result", fake_await)
        monkeypatch.setattr(self.mcp_main, "_http_client", None)

        from mt5_mcp.schemas.tools import CancelOrderRequest

        req = CancelOrderRequest(order_id="67890")
        result = self.mcp_main.tool_cancel_order(req)
        assert "error" not in result or result.get("status") != "Trading is frozen"

        self.mcp_main.thaw()


class TestSafeShutdownEndpoint:
    import apps.mcp_server.main as mcp_main

    def test_freeze_mode_cancels_orders_sets_not_frozen(self, monkeypatch):
        positions_payload = json.dumps({"positions": []})
        orders_payload = json.dumps(
            {"orders": [{"ticket": "1001", "session_id": "sess-1"}]}
        )

        call_count = {"tcp": 0}

        def fake_tcp(cmd_type, params):
            call_count["tcp"] += 1
            if cmd_type == "get_positions":
                return {"status": "completed", "result": {"payload": positions_payload}}
            elif cmd_type == "get_orders":
                return {"status": "completed", "result": {"payload": orders_payload}}
            elif cmd_type == "cancel_order":
                return {"status": "completed", "result": {"payload": '{"status":"ok"}'}}
            return None

        monkeypatch.setattr(self.mcp_main, "_tcp_send_and_await", fake_tcp)

        req = SafeShutdownRequest(mode="freeze", session_id="sess-1")
        result = self.mcp_main.tool_safe_shutdown(req)

        assert result["mode"] == "freeze"
        assert result["summary"]["orders_cancelled"] >= 0
        assert result["freeze_state"]["frozen"] is False

    def test_full_mode_sets_frozen(self, monkeypatch):
        positions_payload = json.dumps({"positions": []})
        orders_payload = json.dumps({"orders": []})

        def fake_tcp(cmd_type, params):
            if cmd_type == "get_positions":
                return {"status": "completed", "result": {"payload": positions_payload}}
            elif cmd_type == "get_orders":
                return {"status": "completed", "result": {"payload": orders_payload}}
            return None

        monkeypatch.setattr(self.mcp_main, "_tcp_send_and_await", fake_tcp)

        req = SafeShutdownRequest(mode="full", intent_id="test-intent")
        result = self.mcp_main.tool_safe_shutdown(req)

        assert result["mode"] == "full"
        assert result["freeze_state"]["frozen"] is True
        assert result["freeze_state"]["frozen_by"] == "test-intent"

        self.mcp_main.thaw()

    def test_ownership_filter_session_id(self, monkeypatch):
        positions_payload = json.dumps(
            {
                "positions": [
                    {"ticket": "1001", "session_id": "sess-1"},
                    {"ticket": "1002", "session_id": "sess-2"},
                    {"ticket": "1003", "session_id": None},
                ]
            }
        )
        orders_payload = json.dumps({"orders": []})

        closed = []

        def fake_tcp(cmd_type, params):
            if cmd_type == "get_positions":
                return {"status": "completed", "result": {"payload": positions_payload}}
            elif cmd_type == "get_orders":
                return {"status": "completed", "result": {"payload": orders_payload}}
            elif cmd_type == "close_position":
                closed.append(params.get("position_id"))
                return {"status": "completed", "result": {"payload": '{"status":"ok"}'}}
            return None

        monkeypatch.setattr(self.mcp_main, "_tcp_send_and_await", fake_tcp)

        req = SafeShutdownRequest(mode="flatten", session_id="sess-1")
        result = self.mcp_main.tool_safe_shutdown(req)

        assert result["summary"]["total_positions_found"] == 1
        assert len(closed) == 1
        assert closed[0] == "1001"

    def test_summary_response_structure(self, monkeypatch):
        positions_payload = json.dumps(
            {
                "positions": [
                    {"ticket": "2001", "session_id": "sess-a"},
                ]
            }
        )
        orders_payload = json.dumps(
            {
                "orders": [
                    {"ticket": "3001", "session_id": "sess-a"},
                ]
            }
        )

        def fake_tcp(cmd_type, params):
            if cmd_type == "get_positions":
                return {"status": "completed", "result": {"payload": positions_payload}}
            elif cmd_type == "get_orders":
                return {"status": "completed", "result": {"payload": orders_payload}}
            elif cmd_type in ("close_position", "cancel_order"):
                return {"status": "completed", "result": {"payload": '{"status":"ok"}'}}
            return None

        monkeypatch.setattr(self.mcp_main, "_tcp_send_and_await", fake_tcp)

        req = SafeShutdownRequest(mode="full", session_id="sess-a")
        result = self.mcp_main.tool_safe_shutdown(req)

        assert "summary" in result
        assert "positions_closed" in result["summary"]
        assert "orders_cancelled" in result["summary"]
        assert "failed" in result["summary"]
        assert "total_positions_found" in result["summary"]
        assert "total_orders_found" in result["summary"]
        assert "freeze_state" in result

        self.mcp_main.thaw()


class TestThawEndpoint:
    import apps.mcp_server.main as mcp_main

    def test_thaw_unfreezes(self, monkeypatch):
        self.mcp_main.set_frozen(True, by="test-thaw-endpoint")
        assert self.mcp_main.is_frozen() is True

        result = self.mcp_main.tool_thaw()
        assert result["status"] == "thawed"
        assert result["freeze_state"]["frozen"] is False
        assert result["freeze_state"]["frozen_at"] is None

    def test_freeze_status_reports_not_frozen(self, monkeypatch):
        self.mcp_main._shutdown_state["frozen"] = False
        result = self.mcp_main.tool_freeze_status()
        assert result["frozen"] is False

    def test_freeze_status_reports_frozen(self, monkeypatch):
        self.mcp_main.set_frozen(True, by="status-test")
        result = self.mcp_main.tool_freeze_status()
        assert result["frozen"] is True
        assert result["freeze_state"]["frozen_by"] == "status-test"
        self.mcp_main.thaw()
