from __future__ import annotations

import json
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field
from mcp.types import ToolAnnotations

from . import mcp
from .shared import (
    get_gateway,
    get_http_client,
    get_settings_cached,
    _tcp_send_and_await,
    _batch_enqueue_and_await,
    _await_result,
    _parse_payload,
    _parse_payload_dict,
    _parse_indicator_value,
    _parse_indicator_value_from_data,
    _first_bid_ask,
    INDICATOR_DEFAULTS,
)
from mt5_mcp.adapters.common.symbol_utils import normalize_symbol, denormalize_symbol

_READ_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
)


@mcp.tool(name="mt5_get_bars", annotations=_READ_ANNOTATIONS)
def mt5_get_bars(symbol: str, timeframe: str = "H1", count: int = 100) -> dict:
    try:
        symbol_norm = normalize_symbol(symbol)
        result = _tcp_send_and_await(
            "get_bars", {"symbol": symbol_norm, "timeframe": timeframe, "count": count}
        )
        source = "bridge"
        if result is None:
            source = "tcp_bridge"
            client = get_http_client()
            settings = get_settings_cached()
            req = client.post(
                f"{settings.gateway_url}/bridge/commands/enqueue",
                params={
                    "type": "get_bars",
                    "symbol": symbol_norm,
                    "timeframe": timeframe,
                    "count": count,
                },
            )
            req.raise_for_status()
            req_id = req.json().get("id") or req.json().get("request_id")
            result = _await_result(req_id)

        data = _parse_payload_dict(result) if result else {}
        if "error" in data or "data" not in data:
            return {
                "symbol": symbol,
                "timeframe": timeframe,
                "data": [],
                "source": source,
            }

        bars_data = data.get("data", [])
        if "symbol" in data:
            data["symbol"] = denormalize_symbol(data["symbol"])

        return {
            "symbol": denormalize_symbol(symbol_norm),
            "timeframe": timeframe,
            "data": bars_data,
            "source": source,
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(name="mt5_get_indicator", annotations=_READ_ANNOTATIONS)
def mt5_get_indicator(
    symbol: str,
    timeframe: str = "H1",
    indicator: str = "rsi",
    period: Optional[int] = None,
    fast: Optional[int] = None,
    slow: Optional[int] = None,
    signal: Optional[int] = None,
) -> dict:
    try:
        symbol_norm = normalize_symbol(symbol)
        indicator_lower = indicator.lower()
        defaults = INDICATOR_DEFAULTS.get(indicator_lower, {})
        params: dict[str, Any] = {
            "type": "get_indicator",
            "symbol": symbol_norm,
            "timeframe": timeframe,
            "indicator": indicator_lower,
        }
        if period is not None:
            params["period"] = period
        elif "period" in defaults:
            params["period"] = defaults["period"]
        if fast is not None:
            params["fast"] = fast
        elif "fast" in defaults:
            params["fast"] = defaults["fast"]
        if slow is not None:
            params["slow"] = slow
        elif "slow" in defaults:
            params["slow"] = defaults["slow"]
        if signal is not None:
            params["signal"] = signal
        elif "signal" in defaults:
            params["signal"] = defaults["signal"]

        result = _tcp_send_and_await(
            params["type"], {k: v for k, v in params.items() if k != "type"}
        )
        if result is None:
            client = get_http_client()
            settings = get_settings_cached()
            req = client.post(
                f"{settings.gateway_url}/bridge/commands/enqueue",
                params=params,
            )
            req.raise_for_status()
            req_id = req.json().get("id") or req.json().get("request_id")
            result = _await_result(req_id)

        data = _parse_payload_dict(result) if result else {}
        if "symbol" in data:
            data["symbol"] = denormalize_symbol(data["symbol"])

        value = _parse_indicator_value_from_data(data, indicator_lower)

        return {
            "symbol": denormalize_symbol(symbol_norm),
            "indicator": indicator_lower,
            "timeframe": timeframe,
            "value": value,
            "status": result.get("status", "unknown") if result else "unknown",
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(name="mt5_get_ticks", annotations=_READ_ANNOTATIONS)
def mt5_get_ticks(symbol: str, count: int = 100) -> dict:
    try:
        symbol_norm = normalize_symbol(symbol)
        result = _tcp_send_and_await(
            "get_ticks", {"symbol": symbol_norm, "count": count}
        )
        if result is None:
            client = get_http_client()
            settings = get_settings_cached()
            req = client.post(
                f"{settings.gateway_url}/bridge/commands/enqueue",
                params={"type": "get_ticks", "symbol": symbol_norm, "count": count},
            )
            req.raise_for_status()
            req_id = req.json().get("id") or req.json().get("request_id")
            result = _await_result(req_id)

        data = _parse_payload_dict(result) if result else {}
        if "symbol" in data:
            data["symbol"] = denormalize_symbol(data["symbol"])

        return {
            "symbol": denormalize_symbol(symbol_norm),
            "count": count,
            "ticks": data.get("ticks", []),
            "source": "tcp" if result else "http",
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(name="mt5_get_order_book", annotations=_READ_ANNOTATIONS)
def mt5_get_order_book(symbol: str) -> dict:
    try:
        symbol_norm = normalize_symbol(symbol)
        result = _tcp_send_and_await("get_order_book", {"symbol": symbol_norm})
        if result is None:
            client = get_http_client()
            settings = get_settings_cached()
            req = client.post(
                f"{settings.gateway_url}/bridge/commands/enqueue",
                params={"type": "get_order_book", "symbol": symbol_norm},
            )
            req.raise_for_status()
            req_id = req.json().get("id") or req.json().get("request_id")
            result = _await_result(req_id)

        data = _parse_payload_dict(result) if result else {}
        if "symbol" in data:
            data["symbol"] = denormalize_symbol(data["symbol"])

        return {
            "symbol": denormalize_symbol(symbol_norm),
            "bid": data.get("bid"),
            "ask": data.get("ask"),
            "bids": data.get("bids", []),
            "asks": data.get("asks", []),
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(name="mt5_get_symbol_info", annotations=_READ_ANNOTATIONS)
def mt5_get_symbol_info(symbol: str) -> dict:
    try:
        symbol_norm = normalize_symbol(symbol)
        result = _tcp_send_and_await("get_symbol_info", {"symbol": symbol_norm})
        if result is None:
            client = get_http_client()
            settings = get_settings_cached()
            req = client.post(
                f"{settings.gateway_url}/bridge/commands/enqueue",
                params={"type": "get_symbol_info", "symbol": symbol_norm},
            )
            req.raise_for_status()
            req_id = req.json().get("id") or req.json().get("request_id")
            result = _await_result(req_id)

        data = _parse_payload_dict(result) if result else {}
        if "symbol" in data:
            data["symbol"] = denormalize_symbol(data["symbol"])

        return {"symbol": denormalize_symbol(symbol_norm), "info": data}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(name="mt5_get_deals_history", annotations=_READ_ANNOTATIONS)
def mt5_get_deals_history(
    limit: int = 100, symbol: Optional[str] = None, days: int = 30
) -> dict:
    try:
        params_dict: dict[str, Any] = {
            "type": "get_deals_history",
            "limit": limit,
            "days": days,
        }
        if symbol:
            params_dict["symbol"] = normalize_symbol(symbol)

        result = _tcp_send_and_await(
            params_dict["type"], {k: v for k, v in params_dict.items() if k != "type"}
        )
        if result is None:
            client = get_http_client()
            settings = get_settings_cached()
            req = client.post(
                f"{settings.gateway_url}/bridge/commands/enqueue",
                params=params_dict,
            )
            req.raise_for_status()
            req_id = req.json().get("id") or req.json().get("request_id")
            result = _await_result(req_id, timeout_s=15.0)

        data = _parse_payload_dict(result) if result else {}
        deals = data.get("deals", [])
        for deal in deals:
            if "symbol" in deal:
                deal["symbol"] = denormalize_symbol(deal["symbol"])

        return {"deals": deals, "count": len(deals)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(name="mt5_get_account_summary", annotations=_READ_ANNOTATIONS)
def mt5_get_account_summary() -> dict:
    try:
        result = _tcp_send_and_await("get_account", {})
        if result is None:
            client = get_http_client()
            settings = get_settings_cached()
            req = client.post(
                f"{settings.gateway_url}/bridge/commands/enqueue",
                params={"type": "get_account"},
            )
            req.raise_for_status()
            req_id = req.json().get("id") or req.json().get("request_id")
            result = _await_result(req_id)

        data = _parse_payload_dict(result) if result else {}
        return {
            "account": data,
            "status": result.get("status", "unknown") if result else "unknown",
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(name="mt5_get_positions", annotations=_READ_ANNOTATIONS)
def mt5_get_positions() -> dict:
    try:
        result = _tcp_send_and_await("get_positions", {})
        if result is None:
            client = get_http_client()
            settings = get_settings_cached()
            req = client.post(
                f"{settings.gateway_url}/bridge/commands/enqueue",
                params={"type": "get_positions"},
            )
            req.raise_for_status()
            req_id = req.json().get("id") or req.json().get("request_id")
            result = _await_result(req_id)

        data = _parse_payload_dict(result) if result else {}
        positions = data.get("positions", [])
        for pos in positions:
            if "symbol" in pos:
                pos["symbol"] = denormalize_symbol(pos["symbol"])

        return {"positions": positions, "count": len(positions)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(name="mt5_get_orders", annotations=_READ_ANNOTATIONS)
def mt5_get_orders() -> dict:
    try:
        result = _tcp_send_and_await("get_orders", {})
        if result is None:
            client = get_http_client()
            settings = get_settings_cached()
            req = client.post(
                f"{settings.gateway_url}/bridge/commands/enqueue",
                params={"type": "get_orders"},
            )
            req.raise_for_status()
            req_id = req.json().get("id") or req.json().get("request_id")
            result = _await_result(req_id)

        data = _parse_payload_dict(result) if result else {}
        orders = data.get("orders", [])
        for order in orders:
            if "symbol" in order:
                order["symbol"] = denormalize_symbol(order["symbol"])

        return {"orders": orders, "count": len(orders)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(name="mt5_get_chart_screenshot", annotations=_READ_ANNOTATIONS)
def mt5_get_chart_screenshot(
    symbol: str, timeframe: str = "H1", width: int = 1920, height: int = 1080
) -> dict:
    try:
        symbol_norm = normalize_symbol(symbol)
        result = _tcp_send_and_await(
            "get_chart_screenshot",
            {
                "symbol": symbol_norm,
                "timeframe": timeframe,
                "width": width,
                "height": height,
            },
        )
        if result is None:
            client = get_http_client()
            settings = get_settings_cached()
            req = client.post(
                f"{settings.gateway_url}/bridge/commands/enqueue",
                params={
                    "type": "get_chart_screenshot",
                    "symbol": symbol_norm,
                    "timeframe": timeframe,
                    "width": width,
                    "height": height,
                },
            )
            req.raise_for_status()
            req_id = req.json().get("id") or req.json().get("request_id")
            result = _await_result(req_id, timeout_s=15.0)

        data = _parse_payload_dict(result) if result else {}
        return {
            "symbol": denormalize_symbol(symbol_norm),
            "timeframe": timeframe,
            "width": width,
            "height": height,
            "image_base64": data.get("image_base64") or data.get("screenshot"),
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(name="mt5_market_snapshot", annotations=_READ_ANNOTATIONS)
def mt5_market_snapshot(
    symbol: str,
    timeframe: str = "H1",
    bar_count: int = 100,
    include_coaching: bool = True,
    session_id: Optional[str] = None,
    strategy_id: Optional[str] = None,
) -> dict:
    try:
        symbol_norm = normalize_symbol(symbol)
        commands = [
            {
                "type": "get_bars",
                "symbol": symbol_norm,
                "timeframe": timeframe,
                "count": bar_count,
            },
            {
                "type": "get_indicator",
                "symbol": symbol_norm,
                "timeframe": timeframe,
                "indicator": "atr",
                "period": 14,
            },
            {
                "type": "get_indicator",
                "symbol": symbol_norm,
                "timeframe": timeframe,
                "indicator": "rsi",
                "period": 14,
            },
            {
                "type": "get_indicator",
                "symbol": symbol_norm,
                "timeframe": timeframe,
                "indicator": "ema",
                "period": 20,
            },
            {
                "type": "get_indicator",
                "symbol": symbol_norm,
                "timeframe": timeframe,
                "indicator": "ema",
                "period": 50,
            },
            {
                "type": "get_indicator",
                "symbol": symbol_norm,
                "timeframe": timeframe,
                "indicator": "macd",
                "fast": 12,
                "slow": 26,
                "signal": 9,
            },
            {"type": "get_order_book", "symbol": symbol_norm},
            {"type": "get_symbol_info", "symbol": symbol_norm},
            {"type": "get_positions", "symbol": symbol_norm},
        ]

        results = _batch_enqueue_and_await(commands, timeout_s=30.0)

        bars_result = results[0] if len(results) > 0 else None
        atr_result = results[1] if len(results) > 1 else None
        rsi_result = results[2] if len(results) > 2 else None
        ema_fast_result = results[3] if len(results) > 3 else None
        ema_slow_result = results[4] if len(results) > 4 else None
        macd_result = results[5] if len(results) > 5 else None
        book_result = results[6] if len(results) > 6 else None
        symbol_info_result = results[7] if len(results) > 7 else None
        positions_result = results[8] if len(results) > 8 else None

        bars_data = (
            _parse_payload_dict(bars_result).get("data", []) if bars_result else []
        )
        atr_value = _parse_indicator_value(atr_result)
        rsi = _parse_indicator_value(rsi_result)
        ema_fast = _parse_indicator_value(ema_fast_result)
        ema_slow = _parse_indicator_value(ema_slow_result)

        macd_payload = _parse_payload_dict(macd_result) if macd_result else {}
        macd_data = macd_payload.get("value") or macd_payload

        book_data = _parse_payload_dict(book_result) if book_result else {}
        symbol_info_data = (
            _parse_payload_dict(symbol_info_result) if symbol_info_result else {}
        )
        positions_data = (
            _parse_payload_dict(positions_result).get("positions", [])
            if positions_result
            else []
        )

        bid, ask = _first_bid_ask(book_data)

        from mt5_mcp.services.snapshot_service import SymbolSnapshotService
        from mt5_mcp.services.trading_coach import TradingCoach

        snapshot_svc = SymbolSnapshotService(
            coach=TradingCoach(), reconciliation_service=None
        )
        return snapshot_svc.build(
            symbol=symbol,
            timeframe=timeframe,
            bars_data=bars_data,
            atr_value=atr_value,
            rsi=rsi,
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            macd=macd_data,
            order_book_data=book_data,
            bid=bid,
            ask=ask,
            symbol_info_data=symbol_info_data,
            positions=positions_data,
            include_coaching=include_coaching,
            session_id=session_id,
            strategy_id=strategy_id,
        )
    except Exception as e:
        return {"symbol": symbol, "error": f"Batch fetch failed: {e}"}


@mcp.tool(name="mt5_chart_intelligence", annotations=_READ_ANNOTATIONS)
def mt5_chart_intelligence(
    symbol: str,
    timeframe: str = "H1",
    bar_count: int = 100,
    include_screenshot: bool = False,
    width: int = 1920,
    height: int = 1080,
) -> dict:
    try:
        symbol_norm = normalize_symbol(symbol)
        commands = [
            {
                "type": "get_bars",
                "symbol": symbol_norm,
                "timeframe": timeframe,
                "count": bar_count,
            },
            {
                "type": "get_indicator",
                "symbol": symbol_norm,
                "timeframe": timeframe,
                "indicator": "atr",
                "period": 14,
            },
            {
                "type": "get_indicator",
                "symbol": symbol_norm,
                "timeframe": timeframe,
                "indicator": "rsi",
                "period": 14,
            },
            {
                "type": "get_indicator",
                "symbol": symbol_norm,
                "timeframe": timeframe,
                "indicator": "ema",
                "period": 20,
            },
            {
                "type": "get_indicator",
                "symbol": symbol_norm,
                "timeframe": timeframe,
                "indicator": "ema",
                "period": 50,
            },
            {
                "type": "get_indicator",
                "symbol": symbol_norm,
                "timeframe": timeframe,
                "indicator": "macd",
                "fast": 12,
                "slow": 26,
                "signal": 9,
            },
            {
                "type": "get_indicator",
                "symbol": symbol_norm,
                "timeframe": timeframe,
                "indicator": "bbands",
                "period": 20,
            },
        ]
        if include_screenshot:
            commands.append(
                {
                    "type": "get_chart_screenshot",
                    "symbol": symbol_norm,
                    "timeframe": timeframe,
                    "width": width,
                    "height": height,
                }
            )

        results = _batch_enqueue_and_await(commands, timeout_s=30.0)

        # Pad results to expected length if gateway returned fewer results
        expected = len(commands)
        if len(results) < expected:
            results.extend([None] * (expected - len(results)))

        bars_result = results[0]
        atr_result = results[1]
        rsi_result = results[2]
        ema_fast_result = results[3]
        ema_slow_result = results[4]
        macd_result = results[5]
        bbands_result = results[6]
        screenshot_result = (
            results[7] if len(results) > 7 and include_screenshot else None
        )

        bars_data = (
            _parse_payload_dict(bars_result).get("data", []) if bars_result else []
        )
        if not bars_data:
            return {
                "error": f"No bar data available for {symbol} on {timeframe}. The EA may not have data for this symbol.",
                "symbol": symbol,
                "timeframe": timeframe,
                "bars_received": 0,
            }
        atr_value = _parse_indicator_value(atr_result) if atr_result else None
        rsi = _parse_indicator_value(rsi_result) if rsi_result else None
        ema_fast = _parse_indicator_value(ema_fast_result) if ema_fast_result else None
        ema_slow = _parse_indicator_value(ema_slow_result) if ema_slow_result else None

        macd_payload = _parse_payload_dict(macd_result) if macd_result else {}
        macd_data = macd_payload.get("value") or macd_payload

        bbands_payload = _parse_payload_dict(bbands_result) if bbands_result else {}
        bbands_data = bbands_payload.get("value") or bbands_payload

        screenshot_base64 = None
        screenshot_data = None
        if include_screenshot and screenshot_result:
            screenshot_payload = _parse_payload_dict(screenshot_result)
            screenshot_base64 = screenshot_payload.get(
                "image_base64"
            ) or screenshot_payload.get("screenshot")
            if screenshot_base64:
                screenshot_data = {
                    "base64": screenshot_base64,
                    "width": width,
                    "height": height,
                }

        from mt5_mcp.services.chart_intelligence import ChartIntelligenceService

        svc = ChartIntelligenceService()
        return svc.get_intelligence(
            symbol=symbol,
            timeframe=timeframe,
            bars_data=bars_data,
            atr_value=atr_value,
            rsi=rsi,
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            macd=macd_data,
            bbands=bbands_data,
            screenshot_data=screenshot_data,
            include_screenshot_base64=include_screenshot
            and screenshot_base64 is not None,
            bar_count=bar_count,
        )
    except Exception as e:
        return {"symbol": symbol, "error": f"Batch fetch failed: {e}"}
