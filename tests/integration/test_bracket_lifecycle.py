from __future__ import annotations

import json
import pytest
from types import SimpleNamespace

from mt5_mcp.schemas.tools import EABracketStartRequest, EABracketStopRequest


class TestEABracketStartRequestSchema:
    def test_valid_request_defaults(self):
        req = EABracketStartRequest(
            buy_order_ticket="1001",
            sell_order_ticket="1002",
            bracket_id="bracket-1",
        )
        assert req.buy_order_ticket == "1001"
        assert req.sell_order_ticket == "1002"
        assert req.bracket_id == "bracket-1"
        assert req.comment == ""
        assert req.magic_filter == 0

    def test_valid_request_custom_values(self):
        req = EABracketStartRequest(
            buy_order_ticket="2001",
            sell_order_ticket="2002",
            bracket_id="bracket-2",
            comment=" breakout XAUUSD",
            magic_filter=54321,
        )
        assert req.buy_order_ticket == "2001"
        assert req.sell_order_ticket == "2002"
        assert req.comment == " breakout XAUUSD"
        assert req.magic_filter == 54321

    def test_valid_request_with_ownership_fields(self):
        req = EABracketStartRequest(
            buy_order_ticket="100",
            sell_order_ticket="101",
            bracket_id="br-1",
            session_id="sess-1",
            strategy_id="strat-1",
            intent_id="intent-1",
            idempotency_key="idem-1",
        )
        assert req.session_id == "sess-1"
        assert req.strategy_id == "strat-1"
        assert req.intent_id == "intent-1"

    def test_single_leg_bracket_buy_only(self):
        req = EABracketStartRequest(
            buy_order_ticket="1001",
            sell_order_ticket="0",
            bracket_id="bracket-single",
        )
        assert req.sell_order_ticket == "0"

    def test_single_leg_bracket_sell_only(self):
        req = EABracketStartRequest(
            buy_order_ticket="0",
            sell_order_ticket="1002",
            bracket_id="bracket-single-sell",
        )
        assert req.buy_order_ticket == "0"


class TestEABracketStopRequestSchema:
    def test_valid_request(self):
        req = EABracketStopRequest(bracket_id="bracket-1")
        assert req.bracket_id == "bracket-1"

    def test_valid_with_ownership(self):
        req = EABracketStopRequest(
            bracket_id="br-1",
            session_id="sess-1",
            strategy_id="strat-1",
        )
        assert req.session_id == "sess-1"


class TestEABracketEndpointCommandTypes:
    import apps.mcp_server.main as mcp_main

    def test_bracket_start_sends_bracket_start_command(self, monkeypatch):
        captured = {}

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"id": "bracket-start-1"}

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

        self.mcp_main.tool_ea_bracket_start(
            EABracketStartRequest(
                buy_order_ticket="1001",
                sell_order_ticket="1002",
                bracket_id="br-1",
            )
        )

        assert captured["params"]["type"] == "bracket_start"
        assert captured["params"]["buy_order_ticket"] == "1001"
        assert captured["params"]["sell_order_ticket"] == "1002"
        assert captured["params"]["bracket_id"] == "br-1"

    def test_bracket_start_includes_magic_filter(self, monkeypatch):
        captured = {}

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"id": "bracket-start-2"}

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

        self.mcp_main.tool_ea_bracket_start(
            EABracketStartRequest(
                buy_order_ticket="1001",
                sell_order_ticket="1002",
                bracket_id="br-2",
                magic_filter=99999,
            )
        )

        assert captured["params"]["magic_filter"] == 99999

    def test_bracket_start_omits_magic_filter_when_zero(self, monkeypatch):
        captured = {}

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"id": "bracket-start-3"}

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

        self.mcp_main.tool_ea_bracket_start(
            EABracketStartRequest(
                buy_order_ticket="1001",
                sell_order_ticket="1002",
                bracket_id="br-3",
                magic_filter=0,
            )
        )

        assert "magic_filter" not in captured["params"]

    def test_bracket_stop_sends_bracket_stop_command(self, monkeypatch):
        captured = {}

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"id": "bracket-stop-1"}

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

        self.mcp_main.tool_ea_bracket_stop(EABracketStopRequest(bracket_id="br-1"))

        assert captured["params"]["type"] == "bracket_stop"
        assert captured["params"]["bracket_id"] == "br-1"

    def test_bracket_list_sends_bracket_list_command(self, monkeypatch):
        captured = {}

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"id": "bracket-list-1"}

        def fake_post(*args, **kwargs):
            captured["params"] = kwargs.get("params", {})
            return FakeResponse()

        def fake_await(*args, **kwargs):
            return {
                "status": "completed",
                "result": {"payload": '{"brackets":[],"count":0}'},
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

        result = self.mcp_main.tool_ea_bracket_list()

        assert captured["params"]["type"] == "bracket_list"

    def test_bracket_tick_sends_bracket_tick_command(self, monkeypatch):
        captured = {}

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"id": "bracket-tick-1"}

        def fake_post(*args, **kwargs):
            captured["params"] = kwargs.get("params", {})
            return FakeResponse()

        def fake_await(*args, **kwargs):
            return {
                "status": "completed",
                "result": {
                    "payload": '{"processed":1,"events":[],"errors":0,"active":1}'
                },
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

        result = self.mcp_main.tool_ea_bracket_tick()

        assert captured["params"]["type"] == "bracket_tick"


class TestBracketListResponseParsing:
    def test_empty_bracket_list(self):
        payload = '{"brackets":[],"count":0}'
        data = json.loads(payload)
        assert data["count"] == 0
        assert data["brackets"] == []

    def test_populated_bracket_list(self):
        payload = json.dumps(
            {
                "brackets": [
                    {
                        "bracket_id": "br-1",
                        "buy_ticket": "1001",
                        "sell_ticket": "1002",
                        "magic_filter": 0,
                        "created_at": 1712500000,
                        "buy_exists": True,
                        "sell_exists": True,
                    }
                ],
                "count": 1,
            }
        )
        data = json.loads(payload)
        assert data["count"] == 1
        entry = data["brackets"][0]
        assert entry["bracket_id"] == "br-1"
        assert entry["buy_ticket"] == "1001"
        assert entry["sell_ticket"] == "1002"
        assert entry["buy_exists"] is True

    def test_bracket_with_missing_leg(self):
        payload = json.dumps(
            {
                "brackets": [
                    {
                        "bracket_id": "br-2",
                        "buy_ticket": "1001",
                        "sell_ticket": "1002",
                        "magic_filter": 0,
                        "created_at": 1712500000,
                        "buy_exists": True,
                        "sell_exists": False,
                    }
                ],
                "count": 1,
            }
        )
        data = json.loads(payload)
        entry = data["brackets"][0]
        assert entry["buy_exists"] is True
        assert entry["sell_exists"] is False


class TestBracketTickResponseParsing:
    def test_tick_with_fill_event(self):
        payload = json.dumps(
            {
                "processed": 1,
                "events": [
                    {
                        "bracket_id": "br-1",
                        "filled_leg": "buy",
                        "filled_ticket": "1001",
                        "cancelled_ticket": "1002",
                        "fill_price": 2650.50,
                    }
                ],
                "errors": 0,
                "active": 0,
            }
        )
        data = json.loads(payload)
        assert data["processed"] == 1
        assert data["errors"] == 0
        assert data["active"] == 0
        assert len(data["events"]) == 1
        event = data["events"][0]
        assert event["filled_leg"] == "buy"
        assert event["fill_price"] == 2650.50

    def test_tick_no_events(self):
        payload = '{"processed":1,"events":[],"errors":0,"active":1}'
        data = json.loads(payload)
        assert data["processed"] == 1
        assert data["events"] == []
        assert data["active"] == 1


class TestBracketErrorHandling:
    import apps.mcp_server.main as mcp_main

    def test_error_response_for_nonexistent_bracket(self, monkeypatch):
        def fake_await(*args, **kwargs):
            return {
                "status": "error",
                "error": '{"error":"bracket_stop_failed","bracket_id":"nonexistent"}',
            }

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"id": "req-bracket-err"}

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

        req = EABracketStopRequest(bracket_id="nonexistent")
        result = self.mcp_main.tool_ea_bracket_stop(req)

        assert "error" in result


class TestEABridgeAdapterBracketMethods:
    from mt5_mcp.adapters.ea_bridge_adapter.adapter import EABridgeAdapter

    def test_ea_bracket_start_sends_correct_payload(self, monkeypatch):
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
        result = adapter.ea_bracket_start(
            buy_order_ticket="1001",
            sell_order_ticket="1002",
            bracket_id="br-1",
            comment="test bracket",
            magic_filter=54321,
        )

        assert captured["type"] == "bracket_start"
        assert captured["payload"]["buy_order_ticket"] == "1001"
        assert captured["payload"]["sell_order_ticket"] == "1002"
        assert captured["payload"]["bracket_id"] == "br-1"
        assert captured["payload"]["comment"] == "test bracket"
        assert captured["payload"]["magic_filter"] == 54321

    def test_ea_bracket_start_omits_comment_when_empty(self, monkeypatch):
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
        adapter.ea_bracket_start(
            buy_order_ticket="1001",
            sell_order_ticket="1002",
            bracket_id="br-2",
        )

        assert "comment" not in captured["payload"]
        assert "magic_filter" not in captured["payload"]

    def test_ea_bracket_stop_sends_correct_payload(self, monkeypatch):
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
        adapter.ea_bracket_stop(bracket_id="br-1")

        assert captured["type"] == "bracket_stop"
        assert captured["payload"]["bracket_id"] == "br-1"

    def test_ea_bracket_list_sends_correct_payload(self, monkeypatch):
        captured = {}

        def fake_send(self, cmd_type, payload, timeout_s=10.0):
            captured["type"] = cmd_type
            captured["payload"] = payload
            return {
                "status": "completed",
                "result": {"payload": '{"brackets":[],"count":0}'},
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
        adapter.ea_bracket_list()

        assert captured["type"] == "bracket_list"

    def test_ea_bracket_tick_sends_correct_payload(self, monkeypatch):
        captured = {}

        def fake_send(self, cmd_type, payload, timeout_s=10.0):
            captured["type"] = cmd_type
            captured["payload"] = payload
            return {
                "status": "completed",
                "result": {
                    "payload": '{"processed":1,"events":[],"errors":0,"active":1}'
                },
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
        adapter.ea_bracket_tick()

        assert captured["type"] == "bracket_tick"

    def test_bracket_state_tracking(self, monkeypatch):
        state = {"active": {}}

        def fake_send(self, cmd_type, payload, timeout_s=10.0):
            if cmd_type == "bracket_start":
                state["active"][payload["bracket_id"]] = {
                    "buy_order_ticket": payload["buy_order_ticket"],
                    "sell_order_ticket": payload["sell_order_ticket"],
                    "magic_filter": payload.get("magic_filter", 0),
                }
                return {"status": "completed", "result": {"payload": '{"status":"ok"}'}}
            elif cmd_type == "bracket_stop":
                state["active"].pop(payload.get("bracket_id"), None)
                return {"status": "completed", "result": {"payload": '{"status":"ok"}'}}
            elif cmd_type == "bracket_list":
                items = []
                for bid, info in state["active"].items():
                    items.append(
                        {
                            "bracket_id": bid,
                            "buy_ticket": info["buy_order_ticket"],
                            "sell_ticket": info["sell_order_ticket"],
                            "magic_filter": info["magic_filter"],
                            "created_at": 0,
                            "buy_exists": True,
                            "sell_exists": True,
                        }
                    )
                return {
                    "status": "completed",
                    "result": {
                        "payload": json.dumps({"brackets": items, "count": len(items)})
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

        # Start bracket
        adapter.ea_bracket_start(
            buy_order_ticket="1001",
            sell_order_ticket="1002",
            bracket_id="br-1",
            magic_filter=99999,
        )
        assert "br-1" in state["active"]
        assert state["active"]["br-1"]["magic_filter"] == 99999

        # List should show it
        result = adapter.ea_bracket_list()
        payload = json.loads(result.get("result", {}).get("payload", "{}"))
        assert payload["count"] == 1

        # Stop bracket
        adapter.ea_bracket_stop(bracket_id="br-1")
        assert "br-1" not in state["active"]

        # List should be empty
        result = adapter.ea_bracket_list()
        payload = json.loads(result.get("result", {}).get("payload", "{}"))
        assert payload["count"] == 0

    def test_bracket_with_multiple_brackets(self, monkeypatch):
        state = {"active": {}}

        def fake_send(self, cmd_type, payload, timeout_s=10.0):
            if cmd_type == "bracket_start":
                state["active"][payload["bracket_id"]] = payload
                return {"status": "completed", "result": {"payload": '{"status":"ok"}'}}
            elif cmd_type == "bracket_stop":
                state["active"].pop(payload.get("bracket_id"), None)
                return {"status": "completed", "result": {"payload": '{"status":"ok"}'}}
            elif cmd_type == "bracket_list":
                items = [
                    {
                        "bracket_id": bid,
                        "buy_ticket": info.get("buy_order_ticket", "0"),
                        "sell_ticket": info.get("sell_order_ticket", "0"),
                        "magic_filter": info.get("magic_filter", 0),
                        "created_at": 0,
                        "buy_exists": True,
                        "sell_exists": True,
                    }
                    for bid, info in state["active"].items()
                ]
                return {
                    "status": "completed",
                    "result": {
                        "payload": json.dumps({"brackets": items, "count": len(items)})
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

        # Start two brackets
        adapter.ea_bracket_start(
            buy_order_ticket="1001", sell_order_ticket="1002", bracket_id="br-1"
        )
        adapter.ea_bracket_start(
            buy_order_ticket="2001", sell_order_ticket="2002", bracket_id="br-2"
        )
        assert len(state["active"]) == 2

        # List should show both
        result = adapter.ea_bracket_list()
        payload = json.loads(result.get("result", {}).get("payload", "{}"))
        assert payload["count"] == 2

        # Stop one
        adapter.ea_bracket_stop(bracket_id="br-1")
        assert len(state["active"]) == 1
        assert "br-1" not in state["active"]
        assert "br-2" in state["active"]
