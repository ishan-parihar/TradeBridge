from __future__ import annotations

import json
import pytest
from types import SimpleNamespace

from mt5_mcp.schemas.tools import EATrailingStartRequest, EATrailingStopRequest


class TestEATrailingStartRequestSchema:
    def test_valid_request_defaults(self):
        req = EATrailingStartRequest(ticket="123456")
        assert req.ticket == "123456"
        assert req.atr_multiplier == 1.5
        assert req.check_interval_seconds == 10
        assert req.lock_in_profit_atr == 0.0
        assert req.magic_filter == 0

    def test_valid_request_custom_values(self):
        req = EATrailingStartRequest(
            ticket="789",
            atr_multiplier=2.0,
            check_interval_seconds=5,
            lock_in_profit_atr=1.0,
            magic_filter=12345,
        )
        assert req.ticket == "789"
        assert req.atr_multiplier == 2.0
        assert req.check_interval_seconds == 5
        assert req.lock_in_profit_atr == 1.0
        assert req.magic_filter == 12345

    def test_valid_request_with_ownership_fields(self):
        req = EATrailingStartRequest(
            ticket="100",
            session_id="sess-1",
            strategy_id="strat-1",
            intent_id="intent-1",
            idempotency_key="idem-1",
        )
        assert req.session_id == "sess-1"
        assert req.strategy_id == "strat-1"

    def test_atr_multiplier_at_boundaries(self):
        req_low = EATrailingStartRequest(ticket="1", atr_multiplier=0.5)
        req_high = EATrailingStartRequest(ticket="1", atr_multiplier=5.0)
        assert req_low.atr_multiplier == 0.5
        assert req_high.atr_multiplier == 5.0


class TestEATrailingStopRequestSchema:
    def test_valid_request(self):
        req = EATrailingStopRequest(ticket="123456")
        assert req.ticket == "123456"

    def test_valid_with_ownership(self):
        req = EATrailingStopRequest(
            ticket="100",
            session_id="sess-1",
            strategy_id="strat-1",
        )
        assert req.session_id == "sess-1"


class TestEATrailingEndpointCommandTypes:
    """Test that endpoints send correct command types to the bridge."""

    import apps.mcp_server.main as mcp_main

    def test_trailing_start_sends_trailing_start_command(self, monkeypatch):
        captured = {}

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"id": "test-req-1"}

        def fake_post(*args, **kwargs):
            captured["params"] = kwargs.get("params", {})
            return FakeResponse()

        def fake_await(*args, **kwargs):
            return {"status": "completed", "result": {"payload": '{"status":"ok"}'}}

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
        monkeypatch.setattr(self.mcp_main, "_tcp_send_and_await", lambda *a, **kw: None)
        monkeypatch.setattr(self.mcp_main, "_await_result", fake_await)
        monkeypatch.setattr(self.mcp_main, "_http_client", None)

        self.mcp_main.tool_ea_trailing_start(
            EATrailingStartRequest(ticket="12345", atr_multiplier=1.5)
        )

        assert captured["params"]["type"] == "trailing_start"
        assert captured["params"]["ticket"] == "12345"
        assert captured["params"]["atr_multiplier"] == 1.5
        assert captured["params"]["check_interval"] == 10

    def test_trailing_stop_sends_trailing_stop_command(self, monkeypatch):
        captured = {}

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"id": "test-req-2"}

        def fake_post(*args, **kwargs):
            captured["params"] = kwargs.get("params", {})
            return FakeResponse()

        def fake_await(*args, **kwargs):
            return {"status": "completed", "result": {"payload": '{"status":"ok"}'}}

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
        monkeypatch.setattr(self.mcp_main, "_tcp_send_and_await", lambda *a, **kw: None)
        monkeypatch.setattr(self.mcp_main, "_await_result", fake_await)
        monkeypatch.setattr(self.mcp_main, "_http_client", None)

        self.mcp_main.tool_ea_trailing_stop(EATrailingStopRequest(ticket="12345"))

        assert captured["params"]["type"] == "trailing_stop"
        assert captured["params"]["ticket"] == "12345"

    def test_trailing_list_sends_trailing_list_command(self, monkeypatch):
        captured = {}

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"id": "test-req-3"}

        def fake_post(*args, **kwargs):
            captured["params"] = kwargs.get("params", {})
            return FakeResponse()

        def fake_await(*args, **kwargs):
            return {
                "status": "completed",
                "result": {"payload": '{"active_trailing":[],"count":0}'},
            }

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
        monkeypatch.setattr(self.mcp_main, "_tcp_send_and_await", lambda *a, **kw: None)
        monkeypatch.setattr(self.mcp_main, "_await_result", fake_await)
        monkeypatch.setattr(self.mcp_main, "_http_client", None)

        result = self.mcp_main.tool_ea_trailing_list()

        assert captured["params"]["type"] == "trailing_list"

    def test_trailing_tick_sends_trailing_tick_command(self, monkeypatch):
        captured = {}

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"id": "test-req-4"}

        def fake_post(*args, **kwargs):
            captured["params"] = kwargs.get("params", {})
            return FakeResponse()

        def fake_await(*args, **kwargs):
            return {
                "status": "completed",
                "result": {"payload": '{"processed":2,"active":2}'},
            }

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
        monkeypatch.setattr(self.mcp_main, "_tcp_send_and_await", lambda *a, **kw: None)
        monkeypatch.setattr(self.mcp_main, "_await_result", fake_await)
        monkeypatch.setattr(self.mcp_main, "_http_client", None)

        result = self.mcp_main.tool_ea_trailing_tick()

        assert captured["params"]["type"] == "trailing_tick"


class TestTrailingListResponseParsing:
    def test_empty_trailing_list(self):
        payload = '{"active_trailing":[],"count":0}'
        data = json.loads(payload)
        assert data["count"] == 0
        assert data["active_trailing"] == []

    def test_populated_trailing_list(self):
        payload = json.dumps(
            {
                "active_trailing": [
                    {
                        "ticket": "123456",
                        "symbol": "XAUUSD",
                        "atr_multiplier": 1.5,
                        "lock_in_atr": 0.0,
                        "last_sl": 2600.0,
                        "entry_price": 2620.0,
                        "check_interval": 10,
                    }
                ],
                "count": 1,
            }
        )
        data = json.loads(payload)
        assert data["count"] == 1
        entry = data["active_trailing"][0]
        assert entry["ticket"] == "123456"
        assert entry["symbol"] == "XAUUSD"
        assert entry["atr_multiplier"] == 1.5
        assert entry["lock_in_atr"] == 0.0


class TestATRMultiplierValidation:
    """ATR multiplier validation (0.5-5.0 range)."""

    import apps.mcp_server.main as mcp_main

    def test_atr_multiplier_below_range_rejected(self, monkeypatch):
        monkeypatch.setattr(
            self.mcp_main,
            "get_settings_cached",
            lambda: SimpleNamespace(gateway_url="http://127.0.0.1:8020"),
        )
        monkeypatch.setattr(self.mcp_main, "_tcp_send_and_await", lambda *a, **kw: None)
        monkeypatch.setattr(self.mcp_main, "_http_client", None)

        req = EATrailingStartRequest(ticket="1", atr_multiplier=0.3)
        result = self.mcp_main.tool_ea_trailing_start(req)

        assert result["status"] == "error"
        assert "atr_multiplier" in result["error"]

    def test_atr_multiplier_above_range_rejected(self, monkeypatch):
        monkeypatch.setattr(
            self.mcp_main,
            "get_settings_cached",
            lambda: SimpleNamespace(gateway_url="http://127.0.0.1:8020"),
        )
        monkeypatch.setattr(self.mcp_main, "_tcp_send_and_await", lambda *a, **kw: None)
        monkeypatch.setattr(self.mcp_main, "_http_client", None)

        req = EATrailingStartRequest(ticket="1", atr_multiplier=6.0)
        result = self.mcp_main.tool_ea_trailing_start(req)

        assert result["status"] == "error"
        assert "atr_multiplier" in result["error"]

    def test_atr_multiplier_at_lower_boundary_accepted(self, monkeypatch):
        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"id": "req-1"}

        def fake_post(*args, **kwargs):
            return FakeResponse()

        def fake_await(*args, **kwargs):
            return {"status": "completed", "result": {"payload": '{"status":"ok"}'}}

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
        monkeypatch.setattr(self.mcp_main, "_tcp_send_and_await", lambda *a, **kw: None)
        monkeypatch.setattr(self.mcp_main, "_await_result", fake_await)
        monkeypatch.setattr(self.mcp_main, "_http_client", None)

        req = EATrailingStartRequest(ticket="1", atr_multiplier=0.5)
        result = self.mcp_main.tool_ea_trailing_start(req)

        assert "error" not in result


class TestOwnershipFiltering:
    """Test that magic_filter is passed through to the EA."""

    import apps.mcp_server.main as mcp_main

    def test_magic_filter_included_when_set(self, monkeypatch):
        captured = {}

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"id": "req-magic"}

        def fake_post(*args, **kwargs):
            captured["params"] = kwargs.get("params", {})
            return FakeResponse()

        def fake_await(*args, **kwargs):
            return {"status": "completed", "result": {"payload": '{"status":"ok"}'}}

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
        monkeypatch.setattr(self.mcp_main, "_tcp_send_and_await", lambda *a, **kw: None)
        monkeypatch.setattr(self.mcp_main, "_await_result", fake_await)
        monkeypatch.setattr(self.mcp_main, "_http_client", None)

        req = EATrailingStartRequest(ticket="123", magic_filter=54321)
        self.mcp_main.tool_ea_trailing_start(req)

        assert captured["params"]["magic_filter"] == 54321

    def test_magic_filter_omitted_when_zero(self, monkeypatch):
        captured = {}

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"id": "req-nofilter"}

        def fake_post(*args, **kwargs):
            captured["params"] = kwargs.get("params", {})
            return FakeResponse()

        def fake_await(*args, **kwargs):
            return {"status": "completed", "result": {"payload": '{"status":"ok"}'}}

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
        monkeypatch.setattr(self.mcp_main, "_tcp_send_and_await", lambda *a, **kw: None)
        monkeypatch.setattr(self.mcp_main, "_await_result", fake_await)
        monkeypatch.setattr(self.mcp_main, "_http_client", None)

        req = EATrailingStartRequest(ticket="123", magic_filter=0)
        self.mcp_main.tool_ea_trailing_start(req)

        assert "magic_filter" not in captured["params"]


class TestErrorResponseForInvalidTicket:
    """Test error response handling for invalid ticket."""

    import apps.mcp_server.main as mcp_main

    def test_error_response_parsed(self, monkeypatch):
        def fake_await(*args, **kwargs):
            return {
                "status": "error",
                "error": '{"error":"trailing_start_failed","ticket":"999"}',
            }

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"id": "req-err"}

        def fake_post(*args, **kwargs):
            return FakeResponse()

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
        monkeypatch.setattr(self.mcp_main, "_tcp_send_and_await", lambda *a, **kw: None)
        monkeypatch.setattr(self.mcp_main, "_await_result", fake_await)
        monkeypatch.setattr(self.mcp_main, "_http_client", None)

        req = EATrailingStartRequest(ticket="999", atr_multiplier=1.5)
        result = self.mcp_main.tool_ea_trailing_start(req)

        assert "error" in result


class TestEABridgeAdapterTrailingMethods:
    """Service-level test for adapter trailing methods."""

    from mt5_mcp.adapters.ea_bridge_adapter.adapter import EABridgeAdapter

    def test_ea_trailing_start_sends_correct_payload(self, monkeypatch):
        captured = {}

        def fake_send(self, cmd_type, payload, timeout_s=10.0):
            captured["type"] = cmd_type
            captured["payload"] = payload
            return {"status": "completed", "result": {"payload": '{"status":"ok"}'}}

        monkeypatch.setattr(
            self.EABridgeAdapter,
            "_send_command",
            fake_send,
        )
        monkeypatch.setattr(
            self.EABridgeAdapter,
            "__init__",
            lambda self: None,
        )

        adapter = self.EABridgeAdapter()
        result = adapter.ea_trailing_start(
            ticket="12345",
            atr_multiplier=2.0,
            check_interval_seconds=5,
            lock_in_profit_atr=1.0,
            magic_filter=99999,
        )

        assert captured["type"] == "trailing_start"
        assert captured["payload"]["ticket"] == "12345"
        assert captured["payload"]["atr_multiplier"] == 2.0
        assert captured["payload"]["check_interval"] == 5
        assert captured["payload"]["lock_in_profit_atr"] == 1.0
        assert captured["payload"]["magic_filter"] == 99999

    def test_ea_trailing_stop_sends_correct_payload(self, monkeypatch):
        captured = {}

        def fake_send(self, cmd_type, payload, timeout_s=10.0):
            captured["type"] = cmd_type
            captured["payload"] = payload
            return {"status": "completed", "result": {"payload": '{"status":"ok"}'}}

        monkeypatch.setattr(
            self.EABridgeAdapter,
            "_send_command",
            fake_send,
        )
        monkeypatch.setattr(
            self.EABridgeAdapter,
            "__init__",
            lambda self: None,
        )

        adapter = self.EABridgeAdapter()
        result = adapter.ea_trailing_stop(ticket="12345")

        assert captured["type"] == "trailing_stop"
        assert captured["payload"]["ticket"] == "12345"

    def test_ea_trailing_list_sends_correct_payload(self, monkeypatch):
        captured = {}

        def fake_send(self, cmd_type, payload, timeout_s=10.0):
            captured["type"] = cmd_type
            captured["payload"] = payload
            return {
                "status": "completed",
                "result": {"payload": '{"active_trailing":[],"count":0}'},
            }

        monkeypatch.setattr(
            self.EABridgeAdapter,
            "_send_command",
            fake_send,
        )
        monkeypatch.setattr(
            self.EABridgeAdapter,
            "__init__",
            lambda self: None,
        )

        adapter = self.EABridgeAdapter()
        result = adapter.ea_trailing_list()

        assert captured["type"] == "trailing_list"

    def test_ea_trailing_tick_sends_correct_payload(self, monkeypatch):
        captured = {}

        def fake_send(self, cmd_type, payload, timeout_s=10.0):
            captured["type"] = cmd_type
            captured["payload"] = payload
            return {
                "status": "completed",
                "result": {"payload": '{"processed":1,"active":1}'},
            }

        monkeypatch.setattr(
            self.EABridgeAdapter,
            "_send_command",
            fake_send,
        )
        monkeypatch.setattr(
            self.EABridgeAdapter,
            "__init__",
            lambda self: None,
        )

        adapter = self.EABridgeAdapter()
        result = adapter.ea_trailing_tick()

        assert captured["type"] == "trailing_tick"

    def test_trailing_state_tracking(self, monkeypatch):
        """Test that trailing state is tracked correctly through start/list/stop cycle."""
        state = {"active": {}}

        def fake_send(self, cmd_type, payload, timeout_s=10.0):
            if cmd_type == "trailing_start":
                state["active"][payload["ticket"]] = {
                    "atr_multiplier": payload["atr_multiplier"],
                    "check_interval": payload["check_interval"],
                    "lock_in_profit_atr": payload["lock_in_profit_atr"],
                }
                return {"status": "completed", "result": {"payload": '{"status":"ok"}'}}
            elif cmd_type == "trailing_stop":
                state["active"].pop(payload.get("ticket"), None)
                return {"status": "completed", "result": {"payload": '{"status":"ok"}'}}
            elif cmd_type == "trailing_list":
                items = []
                for ticket, info in state["active"].items():
                    items.append(
                        {
                            "ticket": ticket,
                            "symbol": "XAUUSD",
                            "atr_multiplier": info["atr_multiplier"],
                            "lock_in_atr": info["lock_in_profit_atr"],
                            "last_sl": 0.0,
                            "entry_price": 0.0,
                            "check_interval": info["check_interval"],
                        }
                    )
                return {
                    "status": "completed",
                    "result": {
                        "payload": json.dumps(
                            {"active_trailing": items, "count": len(items)}
                        )
                    },
                }
            return {"status": "completed", "result": {"payload": "{}"}}

        monkeypatch.setattr(
            self.EABridgeAdapter,
            "_send_command",
            fake_send,
        )
        monkeypatch.setattr(
            self.EABridgeAdapter,
            "__init__",
            lambda self: None,
        )

        adapter = self.EABridgeAdapter()

        # Start trailing
        adapter.ea_trailing_start(ticket="100", atr_multiplier=1.5)
        assert "100" in state["active"]

        # List should show it
        result = adapter.ea_trailing_list()
        payload = json.loads(result.get("result", {}).get("payload", "{}"))
        assert payload["count"] == 1

        # Stop trailing
        adapter.ea_trailing_stop(ticket="100")
        assert "100" not in state["active"]

        # List should be empty
        result = adapter.ea_trailing_list()
        payload = json.loads(result.get("result", {}).get("payload", "{}"))
        assert payload["count"] == 0
