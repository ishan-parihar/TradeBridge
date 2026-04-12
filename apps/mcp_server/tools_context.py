from __future__ import annotations

import json
import asyncio
import time
from typing import Any, Optional, Literal
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
    _first_bid_ask,
)
from mt5_mcp.adapters.common.symbol_utils import normalize_symbol, denormalize_symbol

_CONTEXT_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)


def _extract_indicator_value(result: dict) -> float | None:
    if not result or result.get("status") != "completed":
        return None
    payload = result.get("result", {}).get("payload", {})
    if isinstance(payload, str):
        try:
            d = json.loads(payload)
            v = d.get("value")
            if v is not None:
                return float(v)
            data_list = d.get("data")
            if data_list and isinstance(data_list, list):
                return float(data_list[-1])
        except Exception:
            return None
    elif isinstance(payload, dict):
        v = payload.get("value")
        if v is not None:
            return float(v)
        data_list = payload.get("data")
        if data_list and isinstance(data_list, list):
            return float(data_list[-1])
    return None


@mcp.tool(name="mt5_trading_context", annotations=_CONTEXT_ANNOTATIONS)
def mt5_trading_context(symbol: str) -> dict:
    """Get live trading context for a symbol: ATR, RSI, EMAs, order book, and recent bars."""
    try:
        symbol_norm = normalize_symbol(symbol)
        commands = [
            {
                "type": "get_indicator",
                "symbol": symbol_norm,
                "timeframe": "H1",
                "indicator": "atr",
                "period": 14,
            },
            {"type": "get_order_book", "symbol": symbol_norm},
            {
                "type": "get_indicator",
                "symbol": symbol_norm,
                "timeframe": "H1",
                "indicator": "rsi",
                "period": 14,
            },
            {
                "type": "get_indicator",
                "symbol": symbol_norm,
                "timeframe": "H1",
                "indicator": "ema",
                "period": 20,
            },
            {
                "type": "get_indicator",
                "symbol": symbol_norm,
                "timeframe": "H1",
                "indicator": "ema",
                "period": 50,
            },
            {"type": "get_bars", "symbol": symbol_norm, "timeframe": "H1", "count": 3},
        ]
        results = _batch_enqueue_and_await(commands, timeout_s=15.0)

        atr_result = results[0] if len(results) > 0 else None
        book_result = results[1] if len(results) > 1 else None
        rsi_result = results[2] if len(results) > 2 else None
        ema20_result = results[3] if len(results) > 3 else None
        ema50_result = results[4] if len(results) > 4 else None
        bars_result = results[5] if len(results) > 5 else None

        current_atr = _extract_indicator_value(atr_result) if atr_result else None
        bid, ask = None, None
        spread_points = None
        if book_result:
            book_data = _parse_payload_dict(book_result)
            bid, ask = _first_bid_ask(book_data)
            if bid is not None and ask is not None:
                spread_points = (
                    ask - bid
                )  # raw price difference; build_context handles unit conversion
        rsi = _extract_indicator_value(rsi_result) if rsi_result else None
        ema_fast = _extract_indicator_value(ema20_result) if ema20_result else None
        ema_slow = _extract_indicator_value(ema50_result) if ema50_result else None

        current_price = (bid + ask) / 2 if bid is not None and ask is not None else None
        last_bar_range = None
        last_bar_direction = None
        if bars_result:
            bars_data = _parse_payload_dict(bars_result)
            bars = bars_data.get("data", [])
            if bars and len(bars) > 0:
                last_bar = bars[-1]
                high = float(last_bar.get("high", 0))
                low = float(last_bar.get("low", 0))
                open_p = float(last_bar.get("open", 0))
                close = float(last_bar.get("close", 0))
                last_bar_range = high - low if high > 0 and low > 0 else None
                if close > open_p:
                    last_bar_direction = "bullish"
                elif close < open_p:
                    last_bar_direction = "bearish"
                else:
                    last_bar_direction = "doji"

        from mt5_mcp.services.market_context import build_context

        return build_context(
            symbol=symbol,
            current_atr=current_atr,
            current_price=current_price,
            spread_points=spread_points,
            rsi=rsi,
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            last_bar_range=last_bar_range,
            last_bar_direction=last_bar_direction,
        )
    except Exception as e:
        from mt5_mcp.services.market_context import build_context

        return build_context(symbol=symbol)


@mcp.tool(name="mt5_trading_coach", annotations=_CONTEXT_ANNOTATIONS)
def mt5_trading_coach(symbol: str) -> dict:
    """Get data-driven coaching advice for a potential trade on the given symbol."""
    try:
        from mt5_mcp.services.trading_coach import TradingCoach

        coach = TradingCoach()
        symbol_norm = normalize_symbol(symbol)

        commands = [
            {
                "type": "get_indicator",
                "symbol": symbol_norm,
                "timeframe": "H1",
                "indicator": "atr",
                "period": 14,
            },
            {"type": "get_order_book", "symbol": symbol_norm},
            {
                "type": "get_indicator",
                "symbol": symbol_norm,
                "timeframe": "H1",
                "indicator": "rsi",
                "period": 14,
            },
            {
                "type": "get_indicator",
                "symbol": symbol_norm,
                "timeframe": "H1",
                "indicator": "ema",
                "period": 20,
            },
            {
                "type": "get_indicator",
                "symbol": symbol_norm,
                "timeframe": "H1",
                "indicator": "ema",
                "period": 50,
            },
            {"type": "get_bars", "symbol": symbol_norm, "timeframe": "H1", "count": 5},
        ]
        results = _batch_enqueue_and_await(commands, timeout_s=15.0)

        atr_result = results[0] if len(results) > 0 else None
        book_result = results[1] if len(results) > 1 else None
        rsi_result = results[2] if len(results) > 2 else None
        ema20_result = results[3] if len(results) > 3 else None
        ema50_result = results[4] if len(results) > 4 else None
        bars_result = results[5] if len(results) > 5 else None

        atr_value = _extract_indicator_value(atr_result) if atr_result else None
        bid, ask = None, None
        spread_price = None
        if book_result:
            book_data = _parse_payload_dict(book_result)
            bid, ask = _first_bid_ask(book_data)
            if bid is not None and ask is not None:
                spread_price = ask - bid
        current_price = (bid + ask) / 2 if bid is not None and ask is not None else None
        rsi = _extract_indicator_value(rsi_result) if rsi_result else None
        ema_fast = _extract_indicator_value(ema20_result) if ema20_result else None
        ema_slow = _extract_indicator_value(ema50_result) if ema50_result else None

        last_bar_range = None
        last_bar_body = None
        last_bar_direction = None
        if bars_result:
            bars_data = _parse_payload_dict(bars_result)
            bars = bars_data.get("data", [])
            if bars and len(bars) > 0:
                last_bar = bars[-1]
                high = float(last_bar.get("high", 0))
                low = float(last_bar.get("low", 0))
                open_p = float(last_bar.get("open", 0))
                close = float(last_bar.get("close", 0))
                last_bar_range = high - low if high > 0 and low > 0 else None
                last_bar_body = close - open_p
                if close > open_p:
                    last_bar_direction = "bullish"
                elif close < open_p:
                    last_bar_direction = "bearish"
                else:
                    last_bar_direction = "doji"

        advice = coach.evaluate(
            symbol=symbol,
            side="buy",
            atr_value=atr_value,
            rsi=rsi,
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            current_price=current_price,
            spread_points=spread_price,
            last_bar_range=last_bar_range,
            last_bar_body=last_bar_body,
            last_bar_direction=last_bar_direction,
            point=0.00001,
        )

        return {
            "symbol": symbol,
            "recommendation": advice.recommendation,
            "warnings": advice.warnings,
            "insights": advice.insights,
            "confidence_factors": advice.confidence_factors,
            "blocking": advice.blocking,
            "raw_metrics": advice.raw_metrics,
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(name="mt5_decision_support", annotations=_CONTEXT_ANNOTATIONS)
def mt5_decision_support(symbol: str, side: Literal["buy", "sell"] = "buy") -> dict:
    """Get decision support combining trading context, coaching advice, and regime analysis."""
    try:
        from mt5_mcp.services.market_regime import detect_regime
        from mt5_mcp.services.trading_coach import TradingCoach

        symbol_norm = normalize_symbol(symbol)
        commands = [
            {
                "type": "get_indicator",
                "symbol": symbol_norm,
                "timeframe": "H1",
                "indicator": "atr",
                "period": 14,
            },
            {"type": "get_order_book", "symbol": symbol_norm},
            {
                "type": "get_indicator",
                "symbol": symbol_norm,
                "timeframe": "H1",
                "indicator": "rsi",
                "period": 14,
            },
            {
                "type": "get_indicator",
                "symbol": symbol_norm,
                "timeframe": "H1",
                "indicator": "ema",
                "period": 20,
            },
            {
                "type": "get_indicator",
                "symbol": symbol_norm,
                "timeframe": "H1",
                "indicator": "ema",
                "period": 50,
            },
            {
                "type": "get_bars",
                "symbol": symbol_norm,
                "timeframe": "H1",
                "count": 100,
            },
        ]
        results = _batch_enqueue_and_await(commands, timeout_s=15.0)

        atr_result = results[0] if len(results) > 0 else None
        book_result = results[1] if len(results) > 1 else None
        rsi_result = results[2] if len(results) > 2 else None
        ema20_result = results[3] if len(results) > 3 else None
        ema50_result = results[4] if len(results) > 4 else None
        bars_result = results[5] if len(results) > 5 else None

        atr_value = _extract_indicator_value(atr_result) if atr_result else None
        bid, ask = None, None
        if book_result:
            book_data = _parse_payload_dict(book_result)
            bid, ask = _first_bid_ask(book_data)
        current_price = (bid + ask) / 2 if bid is not None and ask is not None else None
        rsi = _extract_indicator_value(rsi_result) if rsi_result else None
        ema_fast = _extract_indicator_value(ema20_result) if ema20_result else None
        ema_slow = _extract_indicator_value(ema50_result) if ema50_result else None

        bars_data = (
            _parse_payload_dict(bars_result).get("data", []) if bars_result else []
        )
        regime_result = detect_regime(bars=bars_data, atr_value=atr_value)

        coach = TradingCoach()
        advice = coach.evaluate(
            symbol=symbol,
            side=side,
            atr_value=atr_value,
            rsi=rsi,
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            current_price=current_price,
        )

        from mt5_mcp.services.market_context import build_context

        context = build_context(
            symbol=symbol,
            current_atr=atr_value,
            current_price=current_price,
            rsi=rsi,
            ema_fast=ema_fast,
            ema_slow=ema_slow,
        )

        return {
            "symbol": symbol,
            "side": side,
            "regime": regime_result,
            "coaching": {
                "recommendation": advice.recommendation,
                "warnings": advice.warnings,
                "insights": advice.insights,
                "raw_metrics": advice.raw_metrics,
            },
            "market_context": context,
        }
    except Exception as e:
        return {"error": str(e)}
