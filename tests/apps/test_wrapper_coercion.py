from __future__ import annotations

import asyncio
import json

import tools.mcp_mt5_wrapper as wrapper


class TestCoerceNumericArgs:
    """Verify that string values for numeric fields are coerced to numbers."""

    def test_integer_string_coerced(self) -> None:
        args = {"symbol": "XAUUSD", "count": "100"}
        result = wrapper._coerce_numeric_args("get_bars", args)
        assert result["count"] == 100
        assert isinstance(result["count"], int)

    def test_float_string_coerced(self) -> None:
        args = {"volume_lots": "0.01", "sl": "4700.5"}
        result = wrapper._coerce_numeric_args("submit_market_order_via_bridge", args)
        assert result["volume_lots"] == 0.01
        assert isinstance(result["volume_lots"], float)
        assert result["sl"] == 4700.5

    def test_non_numeric_string_untouched(self) -> None:
        args = {"symbol": "XAUUSD", "count": "abc"}
        result = wrapper._coerce_numeric_args("get_bars", args)
        assert result["count"] == "abc"

    def test_none_value_untouched(self) -> None:
        args = {"sl": None, "tp": None}
        result = wrapper._coerce_numeric_args("submit_pending_order", args)
        assert result["sl"] is None
        assert result["tp"] is None

    def test_already_numeric_unchanged(self) -> None:
        args = {"volume_lots": 0.01, "price": 4685.0}
        result = wrapper._coerce_numeric_args("submit_pending_order", args)
        assert result["volume_lots"] == 0.01
        assert result["price"] == 4685.0

    def test_indicator_params_coerced(self) -> None:
        args = {
            "symbol": "XAUUSD",
            "period": "14",
            "fast": "12",
            "slow": "26",
            "signal": "9",
        }
        result = wrapper._coerce_numeric_args("get_indicator", args)
        assert result["period"] == 14
        assert result["fast"] == 12
        assert result["slow"] == 26
        assert result["signal"] == 9

    def test_unknown_tool_returns_unchanged(self) -> None:
        args = {"volume": "0.01"}
        result = wrapper._coerce_numeric_args("unknown_tool", args)
        assert result["volume"] == "0.01"

    def test_close_position_volume_coerced(self) -> None:
        args = {"position_id": "123", "volume": "0.5"}
        result = wrapper._coerce_numeric_args("close_position", args)
        assert result["volume"] == 0.5

    def test_modify_position_sl_tp_coerced(self) -> None:
        args = {"position_id": "123", "sl": "4700", "tp": "4600"}
        result = wrapper._coerce_numeric_args("modify_position_sl_tp", args)
        assert result["sl"] == 4700
        assert result["tp"] == 4600

    def test_screenshot_dimensions_coerced(self) -> None:
        args = {
            "symbol": "XAUUSD",
            "timeframe": "H1",
            "width": "1920",
            "height": "1080",
        }
        result = wrapper._coerce_numeric_args("get_chart_screenshot", args)
        assert result["width"] == 1920
        assert result["height"] == 1080


async def _run_call_tool(name: str, args: dict) -> str:
    result = await wrapper.call_tool(name, args)
    return result.content[0].text


def test_account_summary_tool_uses_resource_endpoint(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_get_json(path: str) -> dict:
        calls.append(path)
        return {"ok": True}

    monkeypatch.setattr(wrapper, "_get_json", fake_get_json)

    text = asyncio.run(_run_call_tool("account_summary", {}))

    assert calls == ["/resources/account/summary"]
    assert json.loads(text) == {"ok": True}


def test_positions_open_tool_uses_resource_endpoint(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_get_json(path: str) -> dict:
        calls.append(path)
        return []

    monkeypatch.setattr(wrapper, "_get_json", fake_get_json)

    text = asyncio.run(_run_call_tool("positions_open", {}))

    assert calls == ["/resources/positions/open"]
    assert json.loads(text) == []


def test_orders_pending_tool_uses_resource_endpoint(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_get_json(path: str) -> dict:
        calls.append(path)
        return []

    monkeypatch.setattr(wrapper, "_get_json", fake_get_json)

    text = asyncio.run(_run_call_tool("orders_pending", {}))

    assert calls == ["/resources/orders/pending"]
    assert json.loads(text) == []



def test_get_json_uses_longer_timeout(monkeypatch) -> None:
    captured = {}

    class DummyResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return []

    class DummyClient:
        def __init__(self, *args, **kwargs) -> None:
            captured["timeout"] = kwargs.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url: str):
            captured["url"] = url
            return DummyResponse()

    monkeypatch.setattr(wrapper.httpx, "AsyncClient", DummyClient)

    result = asyncio.run(wrapper._get_json("/resources/orders/pending"))

    assert captured["timeout"] == 45.0
    assert captured["url"].endswith("/resources/orders/pending")
    assert result == []
