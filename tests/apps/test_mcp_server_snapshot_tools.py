from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from mt5_mcp.services.snapshot_service import SymbolSnapshotService
from mt5_mcp.services.trading_coach import TradingCoach


SAMPLE_BARS = [
    {
        "open": 2650.0,
        "high": 2655.0,
        "low": 2648.0,
        "close": 2652.0,
        "volume": 100,
        "time": "2024-01-01T00:00:00",
    },
    {
        "open": 2652.0,
        "high": 2658.0,
        "low": 2650.0,
        "close": 2656.0,
        "volume": 120,
        "time": "2024-01-01T01:00:00",
    },
    {
        "open": 2656.0,
        "high": 2660.0,
        "low": 2654.0,
        "close": 2658.0,
        "volume": 110,
        "time": "2024-01-01T02:00:00",
    },
]

SAMPLE_ORDER_BOOK = {
    "bids": [{"price": 2657.5, "volume": 5.0}, {"price": 2657.0, "volume": 3.0}],
    "asks": [{"price": 2658.5, "volume": 4.0}, {"price": 2659.0, "volume": 2.0}],
}

SAMPLE_POSITIONS = [
    {
        "position_id": "12345",
        "symbol": "XAUUSD",
        "side": "buy",
        "volume": 0.10,
        "entry_price": 2650.0,
        "sl": 2640.0,
        "tp": 2680.0,
        "unrealized_pnl": 80.0,
        "strategy_id": "scalp",
    },
    {
        "position_id": "12346",
        "symbol": "XAUUSD",
        "side": "sell",
        "volume": 0.05,
        "entry_price": 2660.0,
        "sl": 2670.0,
        "tp": 2640.0,
        "unrealized_pnl": -10.0,
        "strategy_id": "swing",
    },
]


def _make_batch_result(status="completed", payload=None):
    """Helper to create a batch result dict."""
    if payload is None:
        payload = {}
    return {"status": status, "result": {"payload": payload}}


def _make_bars_payload(bars):
    return json.dumps({"data": bars})


def _make_indicator_payload(value):
    return json.dumps({"value": value})


class TestSymbolSnapshotServiceBuild:
    """Test the snapshot service assembles a complete payload."""

    def test_full_snapshot(self):
        svc = SymbolSnapshotService(coach=TradingCoach())
        result = svc.build(
            symbol="XAUUSD",
            timeframe="H1",
            bars_data=SAMPLE_BARS,
            atr_value=3.0,
            atr_percentile=55.0,
            rsi=62.0,
            ema_fast=2654.0,
            ema_slow=2648.0,
            macd={"main": 0.5, "signal": 0.3, "histogram": 0.2},
            order_book_data=SAMPLE_ORDER_BOOK,
            bid=2657.5,
            ask=2658.5,
            symbol_info_data={"trade_mode": "Full", "digits": 2},
            positions=SAMPLE_POSITIONS,
            include_coaching=True,
        )

        assert result["symbol"] == "XAUUSD"
        assert result["timeframe"] == "H1"
        assert "timestamp" in result

        assert result["price"]["bid"] == 2657.5
        assert result["price"]["ask"] == 2658.5
        assert result["price"]["spread_points"] == 1.0

        assert result["bars"]["count"] == 3
        assert result["bars"]["direction"] == "bullish"
        assert result["bars"]["last_bars"] is not None

        assert result["indicators"]["atr"]["value"] == 3.0
        assert result["indicators"]["rsi"]["value"] == 62.0
        assert result["indicators"]["rsi"]["state"] == "neutral"
        assert result["indicators"]["ema_alignment"] == "bullish"
        assert "macd" in result["indicators"]

        assert "regime" in result
        assert result["regime"]["regime"] in (
            "ranging",
            "trending_up",
            "trending_down",
            "compressing",
            "unknown",
        )

        assert "support_resistance" in result
        assert "order_book" in result
        assert result["order_book"]["bid_count"] == 2
        assert result["order_book"]["ask_count"] == 2

        assert "session_context" in result
        assert "calendar" in result

        assert "coaching" in result
        assert "buy" in result["coaching"]
        assert "sell" in result["coaching"]

        assert "viability_warnings" in result
        assert isinstance(result["viability_warnings"], list)

        assert result["current_exposure"]["position_count"] == 2
        assert result["current_exposure"]["buy_volume_lots"] == 0.10
        assert result["current_exposure"]["sell_volume_lots"] == 0.05

    def test_snapshot_without_coaching(self):
        svc = SymbolSnapshotService(coach=TradingCoach())
        result = svc.build(
            symbol="EURUSD",
            timeframe="H1",
            bars_data=SAMPLE_BARS,
            atr_value=0.0010,
            rsi=50.0,
            include_coaching=False,
        )
        assert "coaching" not in result

    def test_snapshot_with_empty_data(self):
        svc = SymbolSnapshotService(coach=TradingCoach())
        result = svc.build(
            symbol="GBPUSD",
            timeframe="H1",
            bars_data=[],
            atr_value=None,
            rsi=None,
            ema_fast=None,
            ema_slow=None,
            bid=None,
            ask=None,
        )

        assert result["symbol"] == "GBPUSD"
        assert result["bars"]["count"] == 0
        assert result["bars"]["last_bars"] == []
        assert result["indicators"] == {}
        assert result["regime"]["regime"] == "unknown"
        assert result["current_exposure"]["position_count"] == 0

    def test_viability_warnings_spread_too_wide(self):
        svc = SymbolSnapshotService(coach=TradingCoach())
        result = svc.build(
            symbol="XAUUSD",
            timeframe="H1",
            bars_data=SAMPLE_BARS,
            atr_value=3.0,
            bid=2657.5,
            ask=2658.5,
        )

        spread_ratio = 1.0 / 3.0
        assert spread_ratio > 0.10
        assert any(
            "spread" in w.lower() or "transaction" in w.lower()
            for w in result["viability_warnings"]
        )

    def test_viability_warnings_ranging_market(self):
        svc = SymbolSnapshotService(coach=TradingCoach())
        bars = [
            {
                "open": 1.0800,
                "high": 1.0802,
                "low": 1.0798,
                "close": 1.0800,
                "volume": 10,
                "time": "2024-01-01T00:00:00",
            },
        ] * 20
        result = svc.build(
            symbol="EURUSD",
            timeframe="H1",
            bars_data=bars,
            atr_value=0.0010,
            bid=1.0800,
            ask=1.0801,
        )

        assert any("ranging" in w.lower() for w in result["viability_warnings"])

    def test_current_exposure_filters_by_symbol(self):
        svc = SymbolSnapshotService(coach=TradingCoach())
        positions = [
            {
                "position_id": "1",
                "symbol": "XAUUSD",
                "side": "buy",
                "volume": 0.10,
                "entry_price": 2650.0,
                "sl": 2640.0,
                "tp": 2680.0,
                "unrealized_pnl": 50.0,
                "strategy_id": "scalp",
            },
            {
                "position_id": "2",
                "symbol": "EURUSD",
                "side": "sell",
                "volume": 0.50,
                "entry_price": 1.0800,
                "sl": 1.0820,
                "tp": 1.0760,
                "unrealized_pnl": -20.0,
                "strategy_id": "swing",
            },
        ]
        result = svc.build(
            symbol="XAUUSD",
            timeframe="H1",
            positions=positions,
        )

        assert result["current_exposure"]["position_count"] == 1
        assert result["current_exposure"]["total_volume_lots"] == 0.10

    def test_support_resistance_from_bars(self):
        svc = SymbolSnapshotService(coach=TradingCoach())
        bars = [
            {
                "open": 1.0800,
                "high": 1.0810 + i,
                "low": 1.0790 - i,
                "close": 1.0805,
                "volume": 10,
                "time": "2024-01-01",
            }
            for i in range(20)
        ]
        result = svc.build(
            symbol="EURUSD",
            timeframe="H1",
            bars_data=bars,
        )

        sr = result["support_resistance"]
        assert sr["method"] == "recent_highs_lows"
        assert len(sr["resistance"]) > 0
        assert len(sr["support"]) > 0


class TestMcpServerSnapshotEndpoint:
    """Test the MCP endpoint for market snapshot."""

    def test_snapshot_endpoint_error_on_batch_failure(self, monkeypatch):
        import apps.mcp_server.main as mcp_main

        monkeypatch.setattr(
            mcp_main,
            "_batch_enqueue_and_await",
            lambda *a, **kw: (_ for _ in ()).throw(Exception("bridge down")),
        )

        from mt5_mcp.schemas.tools import SnapshotRequest

        result = mcp_main.tool_market_snapshot(
            SnapshotRequest(symbol="XAUUSD", timeframe="H1")
        )
        assert "error" in result
        assert "Batch fetch failed" in result["error"]

    def test_snapshot_endpoint_success(self, monkeypatch):
        import apps.mcp_server.main as mcp_main

        def fake_batch(commands, timeout_s=30.0):
            results = []
            for cmd in commands:
                cmd_type = cmd["type"]
                if cmd_type == "get_bars":
                    results.append(
                        _make_batch_result(
                            status="completed", payload=_make_bars_payload(SAMPLE_BARS)
                        )
                    )
                elif cmd_type == "get_indicator":
                    indicator = cmd.get("indicator", "atr")
                    if indicator == "atr":
                        results.append(
                            _make_batch_result(
                                status="completed", payload=_make_indicator_payload(3.0)
                            )
                        )
                    elif indicator == "rsi":
                        results.append(
                            _make_batch_result(
                                status="completed",
                                payload=_make_indicator_payload(55.0),
                            )
                        )
                    elif indicator == "ema":
                        period = cmd.get("period", 20)
                        results.append(
                            _make_batch_result(
                                status="completed",
                                payload=_make_indicator_payload(2650.0 + period),
                            )
                        )
                    elif indicator == "macd":
                        results.append(
                            _make_batch_result(
                                status="completed",
                                payload=json.dumps(
                                    {"main": 0.5, "signal": 0.3, "histogram": 0.2}
                                ),
                            )
                        )
                    else:
                        results.append(
                            _make_batch_result(
                                status="completed", payload=_make_indicator_payload(0)
                            )
                        )
                elif cmd_type == "get_order_book":
                    results.append(
                        _make_batch_result(
                            status="completed", payload=json.dumps(SAMPLE_ORDER_BOOK)
                        )
                    )
                elif cmd_type == "get_symbol_info":
                    results.append(
                        _make_batch_result(
                            status="completed",
                            payload=json.dumps(
                                {"trade_mode": "Full", "digits": 2, "symbol": "XAUUSD"}
                            ),
                        )
                    )
                elif cmd_type == "get_positions":
                    results.append(
                        _make_batch_result(
                            status="completed",
                            payload=json.dumps({"positions": SAMPLE_POSITIONS}),
                        )
                    )
                else:
                    results.append(
                        _make_batch_result(status="completed", payload=json.dumps({}))
                    )
            return results

        monkeypatch.setattr(mcp_main, "_batch_enqueue_and_await", fake_batch)
        monkeypatch.setattr(mcp_main, "normalize_symbol", lambda s: s)

        from mt5_mcp.schemas.tools import SnapshotRequest

        result = mcp_main.tool_market_snapshot(
            SnapshotRequest(symbol="XAUUSD", timeframe="H1", bar_count=50)
        )

        assert "error" not in result
        assert result["symbol"] == "XAUUSD"
        assert result["price"]["bid"] == 2657.5
        assert result["price"]["ask"] == 2658.5
        assert result["indicators"]["atr"]["value"] == 3.0
        assert "coaching" in result
        assert "current_exposure" in result

    def test_snapshot_endpoint_without_coaching(self, monkeypatch):
        import apps.mcp_server.main as mcp_main

        def fake_batch(commands, timeout_s=30.0):
            results = []
            for cmd in commands:
                cmd_type = cmd["type"]
                if cmd_type == "get_bars":
                    results.append(
                        _make_batch_result(
                            status="completed", payload=_make_bars_payload(SAMPLE_BARS)
                        )
                    )
                elif cmd_type == "get_indicator":
                    indicator = cmd.get("indicator", "atr")
                    if indicator == "atr":
                        results.append(
                            _make_batch_result(
                                status="completed", payload=_make_indicator_payload(3.0)
                            )
                        )
                    elif indicator == "rsi":
                        results.append(
                            _make_batch_result(
                                status="completed",
                                payload=_make_indicator_payload(55.0),
                            )
                        )
                    elif indicator == "ema":
                        results.append(
                            _make_batch_result(
                                status="completed",
                                payload=_make_indicator_payload(2650.0),
                            )
                        )
                    elif indicator == "macd":
                        results.append(
                            _make_batch_result(
                                status="completed", payload=json.dumps({})
                            )
                        )
                    else:
                        results.append(
                            _make_batch_result(
                                status="completed", payload=_make_indicator_payload(0)
                            )
                        )
                elif cmd_type == "get_order_book":
                    results.append(
                        _make_batch_result(
                            status="completed", payload=json.dumps(SAMPLE_ORDER_BOOK)
                        )
                    )
                elif cmd_type == "get_symbol_info":
                    results.append(
                        _make_batch_result(
                            status="completed", payload=json.dumps({"symbol": "XAUUSD"})
                        )
                    )
                elif cmd_type == "get_positions":
                    results.append(
                        _make_batch_result(
                            status="completed", payload=json.dumps({"positions": []})
                        )
                    )
                else:
                    results.append(
                        _make_batch_result(status="completed", payload=json.dumps({}))
                    )
            return results

        monkeypatch.setattr(mcp_main, "_batch_enqueue_and_await", fake_batch)
        monkeypatch.setattr(mcp_main, "normalize_symbol", lambda s: s)

        from mt5_mcp.schemas.tools import SnapshotRequest

        result = mcp_main.tool_market_snapshot(
            SnapshotRequest(symbol="XAUUSD", timeframe="H1", include_coaching=False)
        )

        assert "coaching" not in result
