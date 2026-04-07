from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import apps.mcp_server.main as mcp_main
from mt5_mcp.schemas.tools import CustomIndicatorRequest


class DummyEnqueueResponse:
    def __init__(self, req_id: str = "req-ci-1") -> None:
        self._req_id = req_id

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, str]:
        return {"id": self._req_id}


def _make_recording_client() -> tuple[type, list[dict]]:
    calls: list[dict] = []

    class RecordingHttpClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def post(self, *args, **kwargs) -> DummyEnqueueResponse:
            params = kwargs.get("params", {})
            calls.append(params)
            return DummyEnqueueResponse()

    return RecordingHttpClient, calls


def _patch_bridge(monkeypatch) -> None:
    monkeypatch.setattr(
        mcp_main,
        "get_settings_cached",
        lambda: SimpleNamespace(
            gateway_url="http://127.0.0.1:8020", environment="demo"
        ),
    )
    monkeypatch.setattr(mcp_main, "normalize_symbol", lambda s: s)
    monkeypatch.setattr(mcp_main, "denormalize_symbol", lambda s: s)
    monkeypatch.setattr(mcp_main, "_tcp_send_and_await", lambda *a, **kw: None)


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


def test_custom_indicator_request_validates_required_fields() -> None:
    req = CustomIndicatorRequest(
        symbol="XAUUSD",
        timeframe="H1",
        indicator_name="Examples\\MACD",
    )
    assert req.symbol == "XAUUSD"
    assert req.timeframe == "H1"
    assert req.indicator_name == "Examples\\MACD"


def test_custom_indicator_request_has_sensible_defaults() -> None:
    req = CustomIndicatorRequest(
        symbol="EURUSD",
        timeframe="M15",
        indicator_name="Custom\\MyIndicator",
    )
    assert req.buffer_index == 0
    assert req.count == 100
    assert req.params == ""


def test_custom_indicator_request_accepts_optional_params() -> None:
    req = CustomIndicatorRequest(
        symbol="GBPUSD",
        timeframe="D1",
        indicator_name="Examples\\MACD",
        params="period=14,deviation=2.0",
        buffer_index=1,
        count=50,
    )
    assert req.params == "period=14,deviation=2.0"
    assert req.buffer_index == 1
    assert req.count == 50


# ---------------------------------------------------------------------------
# Endpoint tests
# ---------------------------------------------------------------------------


def test_custom_indicator_tcp_response_parsed_correctly(monkeypatch) -> None:
    _patch_bridge(monkeypatch)
    monkeypatch.setattr(
        mcp_main,
        "_tcp_send_and_await",
        lambda *a, **kw: {
            "status": "completed",
            "result": {
                "payload": json.dumps(
                    {
                        "indicator": "Examples\\MACD",
                        "buffer_index": 0,
                        "count": 5,
                        "values": [0.0012, -0.0008, 0.0015, -0.0003, 0.0021],
                        "error": None,
                    }
                )
            },
        },
    )

    req = CustomIndicatorRequest(
        symbol="XAUUSD",
        timeframe="H1",
        indicator_name="Examples\\MACD",
        count=5,
    )
    result = mcp_main.tool_custom_indicator(req)

    assert result["indicator"] == "Examples\\MACD"
    assert result["buffer_index"] == 0
    assert len(result["values"]) == 5
    assert result["values"][0] == 0.0012
    assert result["error"] is None


def test_custom_indicator_http_fallback_response(monkeypatch) -> None:
    _patch_bridge(monkeypatch)
    RecClient, calls = _make_recording_client()
    monkeypatch.setattr(mcp_main, "_http_client", None)
    monkeypatch.setattr(mcp_main.httpx, "Client", RecClient)
    monkeypatch.setattr(
        mcp_main,
        "_await_result",
        lambda *args, **kwargs: {
            "status": "completed",
            "result": {
                "payload": json.dumps(
                    {
                        "indicator": "Custom\\MyIndicator",
                        "buffer_index": 0,
                        "count": 3,
                        "values": [1.5, 2.3, 3.1],
                        "error": None,
                    }
                )
            },
        },
    )

    req = CustomIndicatorRequest(
        symbol="EURUSD",
        timeframe="M5",
        indicator_name="Custom\\MyIndicator",
        params="period=14",
        buffer_index=0,
        count=3,
    )
    result = mcp_main.tool_custom_indicator(req)

    assert len(calls) == 1
    params = calls[0]
    assert params["type"] == "get_custom_indicator"
    assert params["symbol"] == "EURUSD"
    assert params["indicator_name"] == "Custom\\MyIndicator"
    assert params["params"] == "period=14"
    assert params["buffer_index"] == 0
    assert params["count"] == 3

    assert result["indicator"] == "Custom\\MyIndicator"
    assert result["values"] == [1.5, 2.3, 3.1]


def test_custom_indicator_http_fallback_forwards_params(monkeypatch) -> None:
    _patch_bridge(monkeypatch)
    RecClient, calls = _make_recording_client()
    monkeypatch.setattr(mcp_main, "_http_client", None)
    monkeypatch.setattr(mcp_main.httpx, "Client", RecClient)
    monkeypatch.setattr(
        mcp_main,
        "_await_result",
        lambda *args, **kwargs: {
            "status": "completed",
            "result": {"payload": "{}"},
        },
    )

    req = CustomIndicatorRequest(
        symbol="GBPJPY",
        timeframe="H4",
        indicator_name="Examples\\Bands",
        params="period=20,deviation=2.5",
        buffer_index=2,
        count=200,
    )
    mcp_main.tool_custom_indicator(req)

    assert len(calls) == 1
    params = calls[0]
    assert params["indicator_name"] == "Examples\\Bands"
    assert params["params"] == "period=20,deviation=2.5"
    assert params["buffer_index"] == 2
    assert params["count"] == 200


def test_custom_indicator_handles_ea_error_response(monkeypatch) -> None:
    _patch_bridge(monkeypatch)
    monkeypatch.setattr(
        mcp_main,
        "_tcp_send_and_await",
        lambda *a, **kw: {
            "status": "completed",
            "result": {
                "payload": json.dumps(
                    {
                        "indicator": "NonExistent\\Indicator",
                        "buffer_index": 0,
                        "count": 100,
                        "error": "indicator_handle_failed",
                        "last_error": 4801,
                    }
                )
            },
        },
    )

    req = CustomIndicatorRequest(
        symbol="XAUUSD",
        timeframe="H1",
        indicator_name="NonExistent\\Indicator",
    )
    result = mcp_main.tool_custom_indicator(req)

    assert result["error"] == "indicator_handle_failed"
    assert result["indicator"] == "NonExistent\\Indicator"
    assert result["last_error"] == 4801


def test_custom_indicator_handles_timeout_gracefully(monkeypatch) -> None:
    _patch_bridge(monkeypatch)
    RecClient, calls = _make_recording_client()
    monkeypatch.setattr(mcp_main, "_http_client", None)
    monkeypatch.setattr(mcp_main.httpx, "Client", RecClient)
    monkeypatch.setattr(
        mcp_main,
        "_await_result",
        lambda *args, **kwargs: {"status": "timeout", "error": "timeout"},
    )

    req = CustomIndicatorRequest(
        symbol="XAUUSD",
        timeframe="H1",
        indicator_name="Examples\\MACD",
    )
    result = mcp_main.tool_custom_indicator(req)

    assert result["error"] == "timeout"


def test_custom_indicator_handles_copy_buffer_error(monkeypatch) -> None:
    _patch_bridge(monkeypatch)
    monkeypatch.setattr(
        mcp_main,
        "_tcp_send_and_await",
        lambda *a, **kw: {
            "status": "completed",
            "result": {
                "payload": json.dumps(
                    {
                        "indicator": "Examples\\MACD",
                        "buffer_index": 0,
                        "count": 100,
                        "error": "copy_buffer_failed",
                        "copied": -1,
                        "last_error": 4401,
                    }
                )
            },
        },
    )

    req = CustomIndicatorRequest(
        symbol="XAUUSD",
        timeframe="H1",
        indicator_name="Examples\\MACD",
        buffer_index=0,
        count=100,
    )
    result = mcp_main.tool_custom_indicator(req)

    assert result["error"] == "copy_buffer_failed"
    assert result["copied"] == -1


def test_custom_indicator_tcp_takes_priority_over_http(monkeypatch) -> None:
    _patch_bridge(monkeypatch)
    RecClient, calls = _make_recording_client()
    monkeypatch.setattr(mcp_main, "_http_client", None)
    monkeypatch.setattr(mcp_main.httpx, "Client", RecClient)
    monkeypatch.setattr(
        mcp_main,
        "_tcp_send_and_await",
        lambda *a, **kw: {
            "status": "completed",
            "result": {
                "payload": json.dumps(
                    {
                        "indicator": "Examples\\MACD",
                        "values": [0.001],
                        "error": None,
                    }
                )
            },
        },
    )

    req = CustomIndicatorRequest(
        symbol="XAUUSD",
        timeframe="H1",
        indicator_name="Examples\\MACD",
    )
    result = mcp_main.tool_custom_indicator(req)

    assert len(calls) == 0
    assert result["values"] == [0.001]
