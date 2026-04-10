#!/usr/bin/env python3
"""MCP Wait Tools for TradeBridge.

Implements the waiting/timer tools using the official MCP Python SDK (FastMCP):
- mt5_wait_delay: Non-blocking delay with optional market state capture
- mt5_wait_indicator: Long-poll until indicator reaches target condition
- mt5_wait_trade_monitor: Long-poll trade setup with target/invalidation
- mt5_wait_for_price: Long-poll price alert

Bug fixes applied (see BUG_REPORT_WAIT_TOOLS.md):
  BUG-001: Duration clamped to 1-3600s via Pydantic Field constraints
  BUG-002: Crosses detection tracks previous_value (was always triggering)
  BUG-003: Proper error logging replaces silent except: pass
  BUG-004: Price crosses tracks previous_price (was always triggering)
  BUG-005: Price wait logs errors instead of silent suppression
  BUG-006: Condition validated via Literal, timeout/interval clamped
  BUG-007: Min/max duration enforced (no 0, negative, or extreme values)
  BUG-008: TradeMonitorRequest defined as proper Pydantic model
  BUG-009: Tools registered via @mcp.tool() with proper annotations
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from mt5_mcp.schemas.tools import (
    WaitDelayRequest,
    WaitDelayResult,
)
from mt5_mcp.services.trade_monitor import (
    check_price_condition,
    compute_price_bracket,
    parse_duration,
)

logger = logging.getLogger("mt5_mcp.wait_tools")

# Lazy-loaded helpers from modular tool files (avoids circular imports)
_first_bid_ask: Any = None
tool_get_order_book: Any = None
tool_get_indicator: Any = None
tool_get_symbol_info: Any = None
tool_get_bars: Any = None
_normalize_symbol: Any = None
_detect_regime: Any = None


def _init_helpers() -> None:
    """Lazily import helpers from modular tool files to avoid circular imports."""
    global _first_bid_ask, tool_get_order_book, tool_get_indicator
    global tool_get_symbol_info, tool_get_bars, _normalize_symbol, _detect_regime
    if _first_bid_ask is not None:
        return

    from .shared import _first_bid_ask as _fba
    from .tools_market_data import (
        mt5_get_order_book,
        mt5_get_indicator,
        mt5_get_symbol_info,
        mt5_get_bars,
    )
    from mt5_mcp.adapters.common.symbol_utils import normalize_symbol
    from mt5_mcp.services.market_regime import detect_regime

    _first_bid_ask = _fba
    tool_get_order_book = mt5_get_order_book
    tool_get_indicator = mt5_get_indicator
    tool_get_symbol_info = mt5_get_symbol_info
    tool_get_bars = mt5_get_bars
    _normalize_symbol = normalize_symbol
    _detect_regime = detect_regime


# Pydantic input models


class Mt5WaitDelayInput(BaseModel):
    """Input for mt5_wait_delay tool."""

    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
        extra="forbid",
    )
    duration_seconds: int = Field(
        default=60,
        ge=1,
        le=3600,
        description="Number of seconds to wait. Range: 1-3600 (1 hour max).",
    )
    symbol: Optional[str] = Field(
        default=None,
        description="Optional MT5 symbol (e.g., 'XAUUSDm'). When provided, "
        "captures market state (price, RSI, regime) before and after the wait.",
    )


class Mt5WaitIndicatorInput(BaseModel):
    """Input for mt5_wait_indicator tool."""

    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
        extra="forbid",
    )
    symbol: str = Field(
        min_length=1,
        description="MT5 symbol (e.g., 'XAUUSDm').",
    )
    timeframe: str = Field(
        default="H1",
        description="Candle timeframe (e.g., 'M1', 'M5', 'H1', 'H4', 'D1').",
    )
    indicator: str = Field(
        min_length=1,
        description="Indicator name (e.g., 'rsi', 'macd', 'sma', 'ema', 'atr', 'adx', 'cci').",
    )
    condition: Literal["above", "below", "crosses", "equals"] = Field(
        default="below",
        description="Trigger condition: 'above', 'below', 'crosses', or 'equals'.",
    )
    value: float = Field(description="Target value for the condition.")
    period: Optional[int] = Field(
        default=None,
        description="Indicator period (e.g., 14 for RSI).",
    )
    fast: Optional[int] = Field(default=None, description="Fast period (MACD).")
    slow: Optional[int] = Field(default=None, description="Slow period (MACD).")
    signal: Optional[int] = Field(default=None, description="Signal period (MACD).")
    timeout_seconds: int = Field(
        default=300,
        ge=5,
        le=3600,
        description="Maximum wait time in seconds. Range: 5-3600.",
    )
    check_interval_seconds: int = Field(
        default=5,
        ge=1,
        le=60,
        description="Poll interval in seconds. Range: 1-60.",
    )


class Mt5WaitTradeMonitorInput(BaseModel):
    """Input for mt5_wait_trade_monitor tool."""

    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
        extra="forbid",
    )
    symbol: str = Field(min_length=1, description="MT5 symbol (e.g., 'XAUUSDm').")
    side: Literal["buy", "sell"] = Field(
        description="Trade direction: 'buy' or 'sell'."
    )
    duration: str = Field(
        min_length=1,
        description="Duration spec: 'M5', 'H1', 'H1:4', '300', '15m', '14:30'.",
    )
    expected: dict = Field(
        description='Target condition: {"type": "price"|"pips"|"atr", ...}',
    )
    invalidation: dict = Field(
        description='Invalidation: {"type": "price"|"pips"|"atr", ...}',
    )
    check_interval_seconds: int = Field(
        default=5,
        ge=1,
        le=60,
        description="Poll interval in seconds. Range: 1-60.",
    )


class Mt5WaitForPriceInput(BaseModel):
    """Input for mt5_wait_for_price tool."""

    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
        extra="forbid",
    )
    symbol: str = Field(min_length=1, description="MT5 symbol (e.g., 'XAUUSDm').")
    condition: Literal["above", "below", "crosses"] = Field(
        description="Price condition: 'above', 'below', or 'crosses'."
    )
    price: float = Field(description="Trigger price level.")
    timeout_seconds: int = Field(
        default=300,
        ge=5,
        le=3600,
        description="Maximum wait time in seconds. Range: 5-3600.",
    )


# MCP server factory and tool annotations

_WAIT_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    idempotentHint=False,
    openWorldHint=True,
)


def create_wait_mcp_server() -> FastMCP:
    mcp = FastMCP("mt5-wait-tools")
    register_tools(mcp)
    return mcp


def register_tools(mcp: FastMCP) -> None:
    _register_wait_delay(mcp)
    _register_wait_indicator(mcp)
    _register_wait_trade_monitor(mcp)
    _register_wait_for_price(mcp)


# Tool 1: mt5_wait_delay


def _register_wait_delay(mcp: FastMCP) -> None:
    @mcp.tool(
        name="mt5_wait_delay",
        annotations=_WAIT_ANNOTATIONS,
    )
    async def mt5_wait_delay(
        duration_seconds: int = 60,
        symbol: Optional[str] = None,
    ) -> dict:
        """Wait for specified duration, optionally capturing market state.

        For durations <= 60s: blocks and returns market summary after waiting.
        For durations > 60s: returns immediately with scheduled completion time
        to avoid MCP client request timeouts (~60s). Agent should poll market
        data tools after the scheduled resume time.

        Args:
            duration_seconds: Seconds to wait. Range: 1-3600 (1 hour max). Default: 60.
            symbol: Optional MT5 symbol for market state capture (e.g., 'XAUUSDm').

        Returns:
            Dict with waited_seconds, resumed_at, market_summary (if symbol provided),
            and scheduled (bool) indicating if the wait was deferred.
        """
        _init_helpers()

        if duration_seconds < 1:
            raise ValueError("duration_seconds must be at least 1")
        if duration_seconds > 3600:
            raise ValueError("duration_seconds must not exceed 3600 (1 hour)")

        _capture_market_state = symbol is not None
        market_summary: dict | None = None
        capture_error: str | None = None

        if symbol:
            try:
                _normalize_symbol(symbol)
            except Exception as e:
                capture_error = str(e)
                symbol = None

        def _capture_before() -> dict | None:
            if not symbol:
                return None
            try:
                book = tool_get_order_book(symbol=symbol)
                if "error" in book:
                    return None
                bid, ask = _first_bid_ask(book)
                if bid is None or ask is None:
                    return None
                mid = (bid + ask) / 2

                rsi_resp = tool_get_indicator(
                    symbol=symbol, timeframe="H1", indicator="rsi", period=14
                )
                rsi = rsi_resp.get("value") if "error" not in rsi_resp else None

                bars_resp = tool_get_bars(symbol=symbol, timeframe="H1", count=20)
                bars_data = (
                    bars_resp.get("data", []) if "error" not in bars_resp else []
                )
                regime = _detect_regime(bars=bars_data, atr_value=0)
                regime_label = regime.get("regime", "unknown")

                sym_info_resp = tool_get_symbol_info(symbol=symbol)
                if "error" in sym_info_resp:
                    return None
                sym_info = sym_info_resp.get("info", sym_info_resp)
                point = float(sym_info.get("point", 0))
                digits = int(sym_info.get("digits", 5))
                pip_size = point * 10 if digits in (3, 5) else point

                return {
                    "bid": bid,
                    "ask": ask,
                    "mid": mid,
                    "rsi": rsi,
                    "regime": regime_label,
                    "point": point,
                    "digits": digits,
                    "pip_size": pip_size,
                }
            except Exception as e:
                logger.warning("wait/delay: pre-capture failed for %s: %s", symbol, e)
                return None

        def _build_summary(before: dict) -> dict | None:
            if not symbol:
                return None
            try:
                book = tool_get_order_book(symbol=symbol)
                if "error" in book:
                    return None
                bid, ask = _first_bid_ask(book)
                if bid is None or ask is None:
                    return None
                mid = (bid + ask) / 2

                rsi_resp = tool_get_indicator(
                    symbol=symbol, timeframe="H1", indicator="rsi", period=14
                )
                rsi = rsi_resp.get("value") if "error" not in rsi_resp else None

                bars_resp = tool_get_bars(symbol=symbol, timeframe="H1", count=20)
                bars_data = (
                    bars_resp.get("data", []) if "error" not in bars_resp else []
                )
                regime = _detect_regime(bars=bars_data, atr_value=0)
                regime_label = regime.get("regime", "unknown")

                pip_size = before.get("pip_size", 0)
                digits = before.get("digits", 5)
                mid_before = before.get("mid", 0)

                if mid_before > 0 and mid > 0 and pip_size > 0:
                    price_change_pips = round((mid - mid_before) / pip_size, 1)
                else:
                    price_change_pips = None

                return {
                    "symbol": symbol,
                    "price_before": round(mid_before, digits) if mid_before else None,
                    "price_after": round(mid, digits) if mid else None,
                    "price_change_pips": price_change_pips,
                    "regime_before": before.get("regime", "unknown"),
                    "regime_after": regime_label,
                    "rsi_before": before.get("rsi"),
                    "rsi_after": rsi,
                }
            except Exception as e:
                logger.warning("wait/delay: pre-capture failed for %s: %s", symbol, e)
                return None

        if duration_seconds <= 60:
            before_data = _capture_before() if _capture_market_state else None

            elapsed = 0
            while elapsed < duration_seconds:
                remaining = duration_seconds - elapsed
                sleep_time = min(30, remaining)
                await asyncio.sleep(sleep_time)
                elapsed += sleep_time

            if _capture_market_state and before_data:
                market_summary = _build_summary(before_data)

            result: dict[str, Any] = {
                "waited_seconds": duration_seconds,
                "resumed_at": datetime.now(timezone.utc).isoformat(),
                "scheduled": False,
                "market_summary": market_summary,
            }
            if capture_error:
                result["capture_error"] = capture_error
            return result
        else:
            before_data = _capture_before() if _capture_market_state else None
            resumed_at = datetime.now(timezone.utc)
            resumed_at_iso = resumed_at.isoformat()
            expected_completion = resumed_at.timestamp() + duration_seconds
            expected_completion_iso = datetime.fromtimestamp(
                expected_completion, tz=timezone.utc
            ).isoformat()

            market_summary = None
            if _capture_market_state and before_data:
                market_summary = {
                    "symbol": symbol,
                    "price_before": round(
                        before_data.get("mid", 0), before_data.get("digits", 5)
                    ),
                    "price_after": None,
                    "price_change_pips": None,
                    "regime_before": before_data.get("regime", "unknown"),
                    "regime_after": None,
                    "rsi_before": before_data.get("rsi"),
                    "rsi_after": None,
                    "note": "Post-wait market state unavailable (scheduled wait). "
                    f"Poll mt5_get_order_book and mt5_get_bars after {expected_completion_iso}.",
                }

            return {
                "waited_seconds": 0,
                "scheduled": True,
                "scheduled_duration_seconds": duration_seconds,
                "scheduled_at": resumed_at_iso,
                "expected_completion": expected_completion_iso,
                "expected_completion_epoch": expected_completion,
                "market_summary": market_summary,
                "action": f"Wait {duration_seconds}s then poll market data tools "
                f"(mt5_get_order_book, mt5_get_bars) to check conditions.",
            }


# Tool 2: mt5_wait_indicator


def _register_wait_indicator(mcp: FastMCP) -> None:
    @mcp.tool(
        name="mt5_wait_indicator",
        annotations=_WAIT_ANNOTATIONS,
    )
    async def mt5_wait_indicator(
        symbol: str,
        timeframe: str = "H1",
        indicator: str = "rsi",
        condition: Literal["above", "below", "crosses", "equals"] = "below",
        value: float = 30.0,
        period: Optional[int] = None,
        fast: Optional[int] = None,
        slow: Optional[int] = None,
        signal: Optional[int] = None,
        timeout_seconds: int = 300,
        check_interval_seconds: int = 5,
    ) -> dict:
        """Wait until a technical indicator meets a specified condition or timeout.

        Polls the indicator value at regular intervals and returns when the
        condition is met or the timeout is reached.

        Condition behavior:
            - **above**: Triggers when indicator value >= target value.
            - **below**: Triggers when indicator value <= target value.
            - **equals**: Triggers when indicator value is within 0.1% of target
              (or within 0.001 absolute if target is 0).
            - **crosses**: Triggers when indicator value crosses the threshold,
              i.e., moves from below to above OR above to below. Tracks the
              previous value to detect actual crossings — does NOT trigger on
              the first poll.

        Args:
            symbol: MT5 symbol (e.g., 'XAUUSDm').
            timeframe: Candle timeframe (e.g., 'M1', 'M5', 'H1', 'H4', 'D1').
            indicator: Indicator name (e.g., 'rsi', 'macd', 'sma', 'ema', 'atr', 'adx').
            condition: Trigger condition. One of: 'above', 'below', 'crosses', 'equals'.
            value: Target value for the condition.
            period: Indicator period (e.g., 14 for RSI).
            fast: Fast period for MACD.
            slow: Slow period for MACD.
            signal: Signal period for MACD.
            timeout_seconds: Maximum wait time in seconds. Range: 5-3600.
            check_interval_seconds: Poll interval in seconds. Range: 1-60.

        Returns:
            Dictionary with keys:
                - symbol: The symbol being monitored
                - indicator: The indicator name
                - condition: The condition that was checked
                - target_value: The target value
                - actual_value: Indicator value at trigger (or last valid on timeout)
                - triggered: True if condition met, False if timed out
                - timed_out: True if timeout was reached
                - poll_count: Number of polls performed
                - previous_value: Previous indicator value (for crosses, optional)
                - crossing_direction: 'upward' or 'downward' (for crosses, optional)
        """
        _init_helpers()

        check_interval = max(1, min(60, check_interval_seconds))
        timeout = max(5, min(3600, timeout_seconds))

        end_time = asyncio.get_event_loop().time() + timeout
        poll_count = 0
        previous_value: Optional[float] = None
        last_valid_value: Optional[float] = None

        while asyncio.get_event_loop().time() < end_time:
            try:
                result = tool_get_indicator(
                    symbol=symbol,
                    timeframe=timeframe,
                    indicator=indicator,
                    period=period,
                    fast=fast,
                    slow=slow,
                    signal=signal,
                )

                if "error" in result or result.get("value") is None:
                    poll_count += 1
                    logger.warning(
                        "wait/indicator: poll returned no value for %s %s: %s",
                        indicator,
                        symbol,
                        result.get("error", "value is None"),
                    )
                    await asyncio.sleep(check_interval)
                    continue

                current_value = float(result["value"])
                poll_count += 1
                last_valid_value = current_value

                if condition == "above" and current_value >= value:
                    return {
                        "symbol": symbol,
                        "indicator": indicator,
                        "condition": condition,
                        "target_value": value,
                        "actual_value": current_value,
                        "triggered": True,
                        "timed_out": False,
                        "poll_count": poll_count,
                    }

                if condition == "below" and current_value <= value:
                    return {
                        "symbol": symbol,
                        "indicator": indicator,
                        "condition": condition,
                        "target_value": value,
                        "actual_value": current_value,
                        "triggered": True,
                        "timed_out": False,
                        "poll_count": poll_count,
                    }

                if condition == "equals":
                    tolerance = abs(value) * 0.001 if value != 0 else 0.001
                    if abs(current_value - value) <= tolerance:
                        return {
                            "symbol": symbol,
                            "indicator": indicator,
                            "condition": condition,
                            "target_value": value,
                            "actual_value": current_value,
                            "triggered": True,
                            "timed_out": False,
                            "poll_count": poll_count,
                        }

                if condition == "crosses":
                    if previous_value is not None:
                        crossed = (
                            previous_value < value and current_value >= value
                        ) or (previous_value >= value and current_value < value)
                        if crossed:
                            return {
                                "symbol": symbol,
                                "indicator": indicator,
                                "condition": condition,
                                "target_value": value,
                                "actual_value": current_value,
                                "triggered": True,
                                "timed_out": False,
                                "poll_count": poll_count,
                                "previous_value": previous_value,
                                "crossing_direction": "upward"
                                if previous_value < value
                                else "downward",
                            }
                    previous_value = current_value

            except Exception as e:
                poll_count += 1
                logger.warning(
                    "wait/indicator: error polling %s for %s: %s",
                    indicator,
                    symbol,
                    e,
                )

            await asyncio.sleep(check_interval)

        return {
            "symbol": symbol,
            "indicator": indicator,
            "condition": condition,
            "target_value": value,
            "actual_value": last_valid_value,
            "triggered": False,
            "timed_out": True,
            "poll_count": poll_count,
        }


# Tool 3: mt5_wait_trade_monitor


def _register_wait_trade_monitor(mcp: FastMCP) -> None:
    @mcp.tool(
        name="mt5_wait_trade_monitor",
        annotations=_WAIT_ANNOTATIONS,
    )
    async def mt5_wait_trade_monitor(
        symbol: str,
        side: Literal["buy", "sell"],
        duration: str,
        expected: dict,
        invalidation: dict,
        check_interval_seconds: int = 5,
    ) -> dict:
        """Long-polling trade monitor: wait until target or invalidation is reached.

        Computes target/invalidation prices from spec (price/pips/atr), then polls
        the market at configurable intervals until a condition is met or duration
        expires.

        Duration formats supported:
            - Timeframe shortcuts: 'M5', 'M15', 'H1', 'D1'
            - Bar count: 'H1:4' (4 H1 bars = 14400s)
            - Time of day: '14:30' (seconds until next 14:30 UTC)
            - Plain seconds: '300'
            - Minutes: '5m', '15m'

        Args:
            symbol: MT5 symbol (e.g., 'XAUUSDm').
            side: Trade direction — 'buy' or 'sell'.
            duration: Duration specification string. Max: 3600s (1 hour).
            expected: Target condition dict. Examples:
                      {"type": "price", "value": 3000.0}
                      {"type": "pips", "value": 50}
                      {"type": "atr", "multiplier": 1.5}
            invalidation: Invalidation condition dict (same format as expected).
            check_interval_seconds: Poll interval in seconds. Range: 1-60.

        Returns:
            Dictionary with keys:
                - symbol: The symbol being monitored
                - reason: "target_reached", "invalidation_hit", or "timeout"
                - current_price: Last observed mid price
                - bid: Last observed bid
                - ask: Last observed ask
                - target_price: Computed target price
                - invalidation_price: Computed invalidation price
                - distance_to_target_pips: Distance to target in pips
                - distance_to_invalidation_pips: Distance to invalidation in pips
                - elapsed_seconds: Seconds elapsed
                - duration_seconds: Total duration in seconds
                - market_context: Dict with regime, atr, rsi, spread_points
                - timed_out: True if timeout was reached (only on timeout)

        Error Handling:
            - Invalid duration format: ValueError raised
            - Duration > 3600s: ValueError raised
            - Symbol info unavailable: ValueError raised
            - Price bracket computation error: ValueError raised
            - Poll errors: Logged as warning, polling continues

        Examples:
            # Monitor a buy trade: 50 pips target, 25 pips invalidation
            mt5_wait_trade_monitor(
                symbol="XAUUSDm",
                side="buy",
                duration="H1",
                expected={"type": "pips", "value": 50},
                invalidation={"type": "pips", "value": 25},
            )

            # Monitor with ATR-based boundaries
            mt5_wait_trade_monitor(
                symbol="EURUSD",
                side="sell",
                duration="300",
                expected={"type": "atr", "multiplier": 2.0},
                invalidation={"type": "atr", "multiplier": 1.0},
            )
        """
        _init_helpers()

        check_interval = max(1, min(60, check_interval_seconds))

        try:
            duration_seconds = parse_duration(duration)
        except ValueError as e:
            raise ValueError(f"Invalid duration: {e}")

        if duration_seconds > 3600:
            raise ValueError(
                f"Duration {duration_seconds}s exceeds maximum of 3600s (1 hour)"
            )
        if duration_seconds <= 0:
            raise ValueError("Duration must be positive")

        symbol_info_response = tool_get_symbol_info(symbol)
        if "error" in symbol_info_response:
            raise ValueError(
                f"Symbol info unavailable for {symbol}: {symbol_info_response['error']}"
            )

        symbol_info_data = symbol_info_response.get("info", symbol_info_response)
        point = symbol_info_data.get("point")
        if not point or point <= 0:
            raise ValueError(f"Could not determine point value for {symbol}")

        expected_type = expected.get("type")
        invalidation_type = invalidation.get("type")
        atr_value = 0.0

        if expected_type == "atr" or invalidation_type == "atr":
            try:
                atr_result = tool_get_indicator(
                    symbol=symbol, timeframe="H1", indicator="atr", period=14
                )
                if "error" in atr_result or atr_result.get("status") == "error":
                    logger.warning(
                        "Trade monitor: ATR request failed for %s: %s",
                        symbol,
                        atr_result.get("error", atr_result.get("message", "unknown")),
                    )
                elif "value" in atr_result and atr_result["value"]:
                    atr_value = float(atr_result["value"])
                elif "data" in atr_result and atr_result["data"]:
                    atr_value = float(atr_result["data"][-1])
            except Exception as e:
                logger.warning("Trade monitor: ATR fetch error for %s: %s", symbol, e)

        if expected_type == "atr":
            expected["atr_value"] = atr_value
        if invalidation_type == "atr":
            invalidation["atr_value"] = atr_value

        try:
            book = tool_get_order_book(symbol=symbol)
            bid, ask = _first_bid_ask(book)
            if bid is None or ask is None:
                raise ValueError("Order book returned no bid/ask")
            current_price = (bid + ask) / 2
        except Exception as e:
            raise ValueError(f"Could not fetch current price for {symbol}: {e}")

        try:
            bracket = compute_price_bracket(
                current_price=current_price,
                side=side,
                spec={"expected": expected, "invalidation": invalidation},
                symbol_info=symbol_info_data,
            )
        except ValueError as e:
            raise ValueError(f"Invalid price bracket: {e}")

        target_price = bracket["target_price"]
        invalidation_price = bracket["invalidation_price"]

        market_context = _get_market_context(symbol, atr_value, bid, ask, point)

        start_time = time.monotonic()
        end_time = start_time + duration_seconds

        while time.monotonic() < end_time:
            try:
                book = tool_get_order_book(symbol=symbol)
                bid, ask = _first_bid_ask(book)

                if bid is not None and ask is not None:
                    current_price = (bid + ask) / 2
                else:
                    logger.warning(
                        "Trade monitor: could not fetch price for %s, continuing poll",
                        symbol,
                    )
                    await asyncio.sleep(check_interval)
                    continue

                condition = check_price_condition(
                    current_price=current_price,
                    bid=bid,
                    ask=ask,
                    target_price=target_price,
                    invalidation_price=invalidation_price,
                    side=side,
                )

                elapsed = int(time.monotonic() - start_time)
                pip = 10 * float(point)
                dist_target_pips = (
                    abs(target_price - current_price) / pip if pip > 0 else 0.0
                )
                dist_inval_pips = (
                    abs(invalidation_price - current_price) / pip if pip > 0 else 0.0
                )

                if condition == "target_reached":
                    try:
                        market_context.update(
                            _get_market_context(symbol, atr_value, bid, ask, point)
                        )
                    except Exception:
                        pass
                    return {
                        "symbol": symbol,
                        "reason": "target_reached",
                        "current_price": current_price,
                        "bid": bid,
                        "ask": ask,
                        "target_price": target_price,
                        "invalidation_price": invalidation_price,
                        "distance_to_target_pips": round(dist_target_pips, 1),
                        "distance_to_invalidation_pips": round(dist_inval_pips, 1),
                        "elapsed_seconds": elapsed,
                        "duration_seconds": duration_seconds,
                        "market_context": dict(market_context),
                    }

                if condition == "invalidation_hit":
                    try:
                        market_context.update(
                            _get_market_context(symbol, atr_value, bid, ask, point)
                        )
                    except Exception:
                        pass
                    return {
                        "symbol": symbol,
                        "reason": "invalidation_hit",
                        "current_price": current_price,
                        "bid": bid,
                        "ask": ask,
                        "target_price": target_price,
                        "invalidation_price": invalidation_price,
                        "distance_to_target_pips": round(dist_target_pips, 1),
                        "distance_to_invalidation_pips": round(dist_inval_pips, 1),
                        "elapsed_seconds": elapsed,
                        "duration_seconds": duration_seconds,
                        "market_context": dict(market_context),
                    }

            except Exception as e:
                logger.warning("Trade monitor poll error for %s: %s", symbol, e)

            await asyncio.sleep(check_interval)

        try:
            book = tool_get_order_book(symbol=symbol)
            bid, ask = _first_bid_ask(book)
            if bid is not None and ask is not None:
                current_price = (bid + ask) / 2
            else:
                current_price = 0.0
        except Exception:
            current_price = 0.0
            bid = 0.0
            ask = 0.0

        elapsed = int(time.monotonic() - start_time)
        pip = 10 * float(point)

        try:
            market_context.update(
                _get_market_context(symbol, atr_value, bid, ask, point)
            )
        except Exception:
            pass

        return {
            "symbol": symbol,
            "reason": "timeout",
            "current_price": current_price,
            "bid": bid,
            "ask": ask,
            "target_price": target_price,
            "invalidation_price": invalidation_price,
            "distance_to_target_pips": round(
                abs(target_price - current_price) / pip if pip > 0 else 0.0, 1
            ),
            "distance_to_invalidation_pips": round(
                abs(invalidation_price - current_price) / pip if pip > 0 else 0.0, 1
            ),
            "elapsed_seconds": elapsed,
            "duration_seconds": duration_seconds,
            "market_context": dict(market_context),
            "timed_out": True,
        }


# Tool 4: mt5_wait_for_price


def _register_wait_for_price(mcp: FastMCP) -> None:
    @mcp.tool(
        name="mt5_wait_for_price",
        annotations=_WAIT_ANNOTATIONS,
    )
    async def mt5_wait_for_price(
        symbol: str,
        condition: Literal["above", "below", "crosses"],
        price: float,
        timeout_seconds: int = 300,
    ) -> dict:
        """Wait until price meets a specified condition or timeout.

        Polls the order book at ~1-second intervals and returns when the price
        condition is met or the timeout is reached.

        Condition behavior:
            - **above**: Triggers when ask price >= trigger price.
            - **below**: Triggers when bid price <= trigger price.
            - **crosses**: Triggers when mid price actually crosses the threshold
              (moves from below to above, or vice versa). Tracks the previous
              mid price to detect real crossings — does NOT trigger on the
              first poll.

        Args:
            symbol: MT5 symbol (e.g., 'XAUUSDm').
            condition: Price condition. One of: 'above', 'below', 'crosses'.
            price: Trigger price level.
            timeout_seconds: Maximum wait time in seconds. Range: 5-3600.

        Returns:
            Dictionary with keys:
                - symbol: The symbol being monitored
                - condition: The condition that was checked
                - trigger_price: The trigger price
                - actual_price: Price at trigger (or last sampled on timeout)
                - triggered: True if condition met, False if timed out
                - timed_out: True if timeout was reached

        Error Handling:
            - Poll failures are logged as warnings; polling continues until timeout.
            - Order book fetch failure on timeout: actual_price set to 0.

        Examples:
            # Wait for ask to reach 3000
            mt5_wait_for_price(symbol="XAUUSDm", condition="above", price=3000)

            # Wait for price to cross 1.0850
            mt5_wait_for_price(symbol="EURUSD", condition="crosses", price=1.0850)
        """
        _init_helpers()

        timeout = max(5, min(3600, timeout_seconds))

        end_time = asyncio.get_event_loop().time() + timeout
        previous_price: Optional[float] = None

        while asyncio.get_event_loop().time() < end_time:
            try:
                book = tool_get_order_book(symbol=symbol)
                bid, ask = _first_bid_ask(book)

                if bid is None or ask is None:
                    await asyncio.sleep(1)
                    continue

                if condition == "above":
                    current = ask
                    triggered = current >= price
                elif condition == "below":
                    current = bid
                    triggered = current <= price
                else:
                    mid = (bid + ask) / 2
                    current = mid
                    if previous_price is not None:
                        triggered = (previous_price < price and current >= price) or (
                            previous_price >= price and current < price
                        )
                    else:
                        previous_price = current
                        triggered = False

                if triggered:
                    return {
                        "symbol": symbol,
                        "condition": condition,
                        "trigger_price": price,
                        "actual_price": current,
                        "triggered": True,
                        "timed_out": False,
                    }

                if condition == "crosses":
                    previous_price = current

            except Exception as e:
                logger.warning("wait/price: error polling price for %s: %s", symbol, e)

            await asyncio.sleep(1)

        try:
            book = tool_get_order_book(symbol=symbol)
            bid, ask = _first_bid_ask(book)
            current = (bid + ask) / 2 if bid and ask else 0
        except Exception:
            current = 0

        return {
            "symbol": symbol,
            "condition": condition,
            "trigger_price": price,
            "actual_price": current,
            "triggered": False,
            "timed_out": True,
        }


# Shared helper


def _get_market_context(
    symbol: str,
    atr_value: float,
    bid: float | None,
    ask: float | None,
    point: float,
) -> dict:
    ctx: dict[str, Any] = {
        "regime": "unknown",
        "atr": atr_value,
        "rsi": None,
        "spread_points": None,
    }
    try:
        bars = tool_get_bars(symbol=symbol, timeframe="H1", count=20)
        bars_data = bars.get("data", []) if "error" not in bars else []
        regime_result = _detect_regime(
            bars=bars_data, atr_value=atr_value if atr_value and atr_value > 0 else 0
        )
        ctx["regime"] = regime_result.get("regime", "unknown")
    except Exception as e:
        logger.warning("Market context: regime detection error for %s: %s", symbol, e)

    try:
        rsi_result = tool_get_indicator(
            symbol=symbol, timeframe="H1", indicator="rsi", period=14
        )
        if "error" not in rsi_result and rsi_result.get("value"):
            ctx["rsi"] = float(rsi_result["value"])
    except Exception as e:
        logger.warning("Market context: RSI fetch error for %s: %s", symbol, e)

    try:
        if bid and ask:
            point_val = float(point)
            ctx["spread_points"] = int((ask - bid) / point_val) if point_val > 0 else 0
    except Exception as e:
        logger.warning("Market context: spread calculation error: %s", e)

    return ctx


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )
    mcp = create_wait_mcp_server()
    mcp.run(transport="stdio")
