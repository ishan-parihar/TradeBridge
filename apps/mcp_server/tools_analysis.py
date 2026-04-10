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
    _parse_payload,
    _parse_payload_dict,
    _parse_indicator_value,
    _first_bid_ask,
)
from mt5_mcp.adapters.common.symbol_utils import normalize_symbol, denormalize_symbol

_ANALYSIS_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)


def _get_bars_data(symbol_norm: str, timeframe: str, count: int) -> list[dict]:
    bars_result = _tcp_send_and_await(
        "get_bars", {"symbol": symbol_norm, "timeframe": timeframe, "count": count}
    )
    bars_data = []
    if bars_result and bars_result.get("status") == "completed":
        payload = bars_result.get("result", {}).get("payload", {})
        if isinstance(payload, str):
            try:
                bars_data = json.loads(payload).get("data", [])
            except Exception:
                pass
        elif isinstance(payload, dict):
            bars_data = payload.get("data", [])
    return bars_data


@mcp.tool(name="mt5_volatility_profile", annotations=_ANALYSIS_ANNOTATIONS)
def mt5_volatility_profile(
    symbol: str, timeframe: str = "H1", lookback: int = 100, atr_period: int = 14
) -> dict:
    try:
        symbol_norm = normalize_symbol(symbol)
        bars_data = _get_bars_data(symbol_norm, timeframe, lookback)

        atr_result = _tcp_send_and_await(
            "get_indicator",
            {
                "symbol": symbol_norm,
                "timeframe": timeframe,
                "indicator": "atr",
                "period": atr_period,
            },
        )
        atr_value = _parse_indicator_value(atr_result)

        from mt5_mcp.services.agent_capabilities import build_volatility_profile

        result = build_volatility_profile(
            symbol=symbol,
            timeframe=timeframe,
            bars=bars_data,
            atr_value=atr_value or 0.0,
        )
        if atr_value is None:
            result["warning"] = "ATR unavailable"
        return result
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


@mcp.tool(name="mt5_divergence", annotations=_ANALYSIS_ANNOTATIONS)
def mt5_divergence(
    symbol: str,
    timeframe: str = "H1",
    lookback: int = 100,
    macd_fast: int = 12,
    macd_slow: int = 26,
    macd_signal_period: int = 9,
    rsi_period: int = 14,
    swing_window: int = 5,
) -> dict:
    try:
        symbol_norm = normalize_symbol(symbol)
        bars_data = _get_bars_data(symbol_norm, timeframe, lookback)

        from mt5_mcp.services.divergence import detect_divergence

        result = detect_divergence(
            bars=bars_data,
            lookback=lookback,
            macd_fast=macd_fast,
            macd_slow=macd_slow,
            macd_signal_period=macd_signal_period,
            rsi_period=rsi_period,
            swing_window=swing_window,
        )
        result["symbol"] = symbol
        result["timeframe"] = timeframe
        return result
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


@mcp.tool(name="mt5_multi_bar_patterns", annotations=_ANALYSIS_ANNOTATIONS)
def mt5_multi_bar_patterns(
    symbol: str,
    timeframe: str = "H1",
    lookback: int = 100,
    period: int = 3,
    fib_lookback: int = 50,
) -> dict:
    try:
        symbol_norm = normalize_symbol(symbol)
        bars_data = _get_bars_data(symbol_norm, timeframe, lookback)

        from mt5_mcp.services.multi_bar_patterns import detect_multi_bar_patterns

        result = detect_multi_bar_patterns(
            bars=bars_data, period=period, fib_lookback=fib_lookback
        )
        result["symbol"] = symbol
        result["timeframe"] = timeframe
        return result
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


@mcp.tool(name="mt5_volume_profile", annotations=_ANALYSIS_ANNOTATIONS)
def mt5_volume_profile(symbol: str, timeframe: str = "H1", lookback: int = 100) -> dict:
    """Session volume profile with POC and value area. Volume type: tick_volume (CFD proxy)."""
    try:
        symbol_norm = normalize_symbol(symbol)
        bars_data = _get_bars_data(symbol_norm, timeframe, lookback)

        from mt5_mcp.services.volume_analysis import detect_volume_anomalies

        result = detect_volume_anomalies(
            bars=bars_data, lookback=lookback, symbol=symbol
        )
        result["timeframe"] = timeframe
        result["volume_type"] = "tick_volume"
        return result
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


@mcp.tool(name="mt5_momentum_check", annotations=_ANALYSIS_ANNOTATIONS)
def mt5_momentum_check(
    symbol: str,
    timeframe: str = "H1",
    lookback: int = 100,
    rsi: bool = True,
    atr: bool = True,
) -> dict:
    try:
        symbol_norm = normalize_symbol(symbol)
        bars_data = _get_bars_data(symbol_norm, timeframe, lookback)

        rsi_value = None
        atr_value = None
        if rsi:
            rsi_result = _tcp_send_and_await(
                "get_indicator",
                {
                    "symbol": symbol_norm,
                    "timeframe": timeframe,
                    "indicator": "rsi",
                    "period": 14,
                },
            )
            rsi_value = _parse_indicator_value(rsi_result)
        if atr:
            atr_result = _tcp_send_and_await(
                "get_indicator",
                {
                    "symbol": symbol_norm,
                    "timeframe": timeframe,
                    "indicator": "atr",
                    "period": 14,
                },
            )
            atr_value = _parse_indicator_value(atr_result)

        from mt5_mcp.services.momentum import compute_momentum_penalty

        result = compute_momentum_penalty(
            bars=bars_data, rsi=rsi_value, atr=atr_value, lookback=lookback
        )
        result["symbol"] = symbol
        result["timeframe"] = timeframe
        return result
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


@mcp.tool(name="mt5_multi_timeframe_indicators", annotations=_ANALYSIS_ANNOTATIONS)
def mt5_multi_timeframe_indicators(
    symbol: str,
    indicator: str = "rsi",
    timeframes: list[str] = None,
    period: Optional[int] = None,
    fast: Optional[int] = None,
    slow: Optional[int] = None,
    signal: Optional[int] = None,
) -> dict:
    try:
        symbol_norm = normalize_symbol(symbol)
        timeframes = timeframes or ["M5", "M15", "H1", "H4", "D1"]
        readings: dict[str, Any] = {}

        for tf in timeframes:
            params: dict[str, Any] = {
                "symbol": symbol_norm,
                "timeframe": tf,
                "indicator": indicator,
            }
            if period is not None:
                params["period"] = period
            if fast is not None:
                params["fast"] = fast
            if slow is not None:
                params["slow"] = slow
            if signal is not None:
                params["signal"] = signal

            result = _tcp_send_and_await("get_indicator", params)
            payload = _parse_payload_dict(result) if result else {}
            value = payload.get("value")
            if value is None and "data" in payload:
                value = payload["data"]
            readings[tf] = {
                "value": value,
                "status": result.get("status", "unknown") if result else "unknown",
            }

        return {"symbol": symbol, "indicator": indicator, "readings": readings}
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


@mcp.tool(name="mt5_correlation_matrix", annotations=_ANALYSIS_ANNOTATIONS)
def mt5_correlation_matrix(
    symbols: list[str], timeframe: str = "H1", lookback: int = 100
) -> dict:
    try:
        close_series: dict[str, list[float]] = {}
        for sym in symbols:
            symbol_norm = normalize_symbol(sym)
            bars_data = _get_bars_data(symbol_norm, timeframe, lookback)
            closes = [float(b["close"]) for b in bars_data if "close" in b]
            if closes:
                close_series[sym] = closes

        from mt5_mcp.services.agent_capabilities import compute_correlation_matrix

        result = compute_correlation_matrix(close_series)
        return {"timeframe": timeframe, "lookback": lookback, "matrix": result}
    except Exception as e:
        return {"error": str(e), "symbols": symbols}


@mcp.tool(name="mt5_market_structure", annotations=_ANALYSIS_ANNOTATIONS)
def mt5_market_structure(
    symbol: str,
    timeframe: str = "H1",
    swing_lookback: int = 5,
    confirm_bos_pips: float = 0.0,
) -> dict:
    try:
        symbol_norm = normalize_symbol(symbol)
        bars_data = _get_bars_data(symbol_norm, timeframe, 100)

        from mt5_mcp.services.market_structure import detect_market_structure

        ms_result = detect_market_structure(
            bars=bars_data,
            swing_lookback=swing_lookback,
            confirm_bos_pips=confirm_bos_pips,
        )
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "structure": ms_result.structure,
            "trend_health": ms_result.trend_health,
            "swing_points": ms_result.swing_points,
            "last_bos": ms_result.last_bos,
            "last_choch": ms_result.last_choch,
            "recent_structure": ms_result.recent_structure,
        }
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


@mcp.tool(name="mt5_strategy_selector", annotations=_ANALYSIS_ANNOTATIONS)
def mt5_strategy_selector(regime: Optional[str] = None) -> dict:
    try:
        from mt5_mcp.services.strategy_selector import list_strategies, select_strategy

        if regime:
            strategy = select_strategy(regime)
            return {
                "recommended": {
                    "name": strategy.name,
                    "regime": strategy.regime,
                    "entry_style": strategy.entry_style,
                    "stop_type": strategy.stop_type,
                    "take_profit_type": strategy.take_profit_type,
                    "max_positions": strategy.max_positions,
                    "risk_multiplier": strategy.risk_multiplier,
                    "trailing": strategy.trailing,
                    "description": strategy.description,
                },
                "all_strategies": list_strategies(),
            }
        else:
            return {"all_strategies": list_strategies()}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(name="mt5_vwap", annotations=_ANALYSIS_ANNOTATIONS)
def mt5_vwap(
    symbol: str,
    timeframe: str = "H1",
    bar_count: int = 100,
    std_dev_multiplier: float = 2.0,
) -> dict:
    """Session VWAP with deviation bands. Volume type: tick_volume (CFD proxy)."""
    try:
        symbol_norm = normalize_symbol(symbol)
        bars_data = _get_bars_data(symbol_norm, timeframe, bar_count)

        from mt5_mcp.services.vwap import compute_vwap

        vwap_result = compute_vwap(
            bars=bars_data, std_dev_multiplier=std_dev_multiplier
        )
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "volume_type": "tick_volume",
            "current_vwap": vwap_result.current_vwap,
            "vwap_deviation_upper": vwap_result.vwap_deviation_upper,
            "vwap_deviation_lower": vwap_result.vwap_deviation_lower,
            "distance_from_vwap_pct": vwap_result.distance_from_vwap_pct,
            "price_position": vwap_result.price_position,
            "vwap_series": vwap_result.vwap_series,
        }
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


@mcp.tool(name="mt5_volume_at_price", annotations=_ANALYSIS_ANNOTATIONS)
def mt5_volume_at_price(
    symbol: str, timeframe: str = "H1", bar_count: int = 100, num_bins: int = 20
) -> dict:
    """Volume-at-price distribution with POC and value area. Volume type: tick_volume (CFD proxy)."""
    try:
        symbol_norm = normalize_symbol(symbol)
        bars_data = _get_bars_data(symbol_norm, timeframe, bar_count)

        from mt5_mcp.services.vwap import compute_volume_at_price

        vap_result = compute_volume_at_price(bars=bars_data, num_bins=num_bins)
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "volume_type": "tick_volume",
            "poc": vap_result.poc,
            "value_area_high": vap_result.value_area_high,
            "value_area_low": vap_result.value_area_low,
            "value_area_width": vap_result.value_area_width,
            "price_distribution": vap_result.price_distribution,
            "current_price_position": vap_result.current_price_position,
        }
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


@mcp.tool(name="mt5_setup_probability", annotations=_ANALYSIS_ANNOTATIONS)
def mt5_setup_probability(
    symbol: str, regime: str, session: str, min_samples: int = 10
) -> dict:
    try:
        from mt5_mcp.services.trade_journal_db import get_journal_db

        journal = get_journal_db()
        rows = journal._conn.execute(
            "SELECT symbol, regime, session_id, outcome, pnl, mistake_category "
            "FROM trade_decisions WHERE outcome IN ('win', 'loss')"
        ).fetchall()
        trades = [
            {
                "symbol": row[0],
                "regime": row[1],
                "session_id": row[2],
                "outcome": row[3],
                "pnl": row[4],
                "mistake_category": row[5],
            }
            for row in rows
        ]

        from mt5_mcp.services.setup_probability import estimate_setup_probability

        result = estimate_setup_probability(
            trades=trades,
            current_regime=regime,
            current_session=session,
            current_symbol=symbol,
            min_samples=min_samples,
        )
        return {
            "estimated_win_rate": result.estimated_win_rate,
            "sample_size": result.sample_size,
            "confidence": result.confidence,
            "recommendation": result.recommendation,
            "similar_trades": result.similar_trades,
            "win_rate_by_regime": result.win_rate_by_regime,
            "win_rate_by_session": result.win_rate_by_session,
            "common_mistakes": result.common_mistakes,
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(name="mt5_support_resistance", annotations=_ANALYSIS_ANNOTATIONS)
def mt5_support_resistance(
    symbol: str, timeframe: str = "H1", lookback: int = 100
) -> dict:
    try:
        symbol_norm = normalize_symbol(symbol)
        bars_data = _get_bars_data(symbol_norm, timeframe, lookback)

        if not bars_data:
            return {
                "symbol": symbol,
                "timeframe": timeframe,
                "support": [],
                "resistance": [],
            }

        swing_window = 5
        swing_lows = []
        swing_highs = []
        for i in range(swing_window, len(bars_data) - swing_window):
            low = float(bars_data[i].get("low", 0))
            high = float(bars_data[i].get("high", 0))
            if all(
                float(bars_data[j].get("low", 0)) >= low
                for j in range(i - swing_window, i + swing_window + 1)
                if j != i
            ):
                swing_lows.append({"price": low, "bar_index": i})
            if all(
                float(bars_data[j].get("high", 0)) <= high
                for j in range(i - swing_window, i + swing_window + 1)
                if j != i
            ):
                swing_highs.append({"price": high, "bar_index": i})

        support = sorted(swing_lows, key=lambda x: x["price"], reverse=True)[:5]
        resistance = sorted(swing_highs, key=lambda x: x["price"])[:5]

        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "support": support,
            "resistance": resistance,
            "current_price": float(bars_data[-1].get("close", 0))
            if bars_data
            else None,
        }
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


@mcp.tool(name="mt5_market_regime", annotations=_ANALYSIS_ANNOTATIONS)
def mt5_market_regime(
    symbol: str, timeframe: str = "H1", lookback: int = 100, atr_period: int = 14
) -> dict:
    try:
        symbol_norm = normalize_symbol(symbol)
        bars_data = _get_bars_data(symbol_norm, timeframe, lookback)

        atr_result = _tcp_send_and_await(
            "get_indicator",
            {
                "symbol": symbol_norm,
                "timeframe": timeframe,
                "indicator": "atr",
                "period": atr_period,
            },
        )
        atr_value = _parse_indicator_value(atr_result)

        ema_fast_result = _tcp_send_and_await(
            "get_indicator",
            {
                "symbol": symbol_norm,
                "timeframe": timeframe,
                "indicator": "ema",
                "period": 20,
            },
        )
        ema_fast = _parse_indicator_value(ema_fast_result)

        ema_slow_result = _tcp_send_and_await(
            "get_indicator",
            {
                "symbol": symbol_norm,
                "timeframe": timeframe,
                "indicator": "ema",
                "period": 50,
            },
        )
        ema_slow = _parse_indicator_value(ema_slow_result)

        from mt5_mcp.services.market_regime import detect_regime

        regime = detect_regime(
            bars=bars_data,
            atr_value=atr_value or 0.0,
            atr_period=atr_period,
            ema_fast=ema_fast,
            ema_slow=ema_slow,
        )
        return {"symbol": symbol, **regime}
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


@mcp.tool(name="mt5_market_scan", annotations=_ANALYSIS_ANNOTATIONS)
def mt5_market_scan(
    symbols: list[str], timeframe: str = "H1", atr_period: int = 14
) -> dict:
    try:
        commands = []
        for sym in symbols:
            symbol_norm = normalize_symbol(sym)
            commands.append(
                {
                    "type": "get_bars",
                    "symbol": symbol_norm,
                    "timeframe": timeframe,
                    "count": 20,
                }
            )
            commands.append(
                {
                    "type": "get_indicator",
                    "symbol": symbol_norm,
                    "timeframe": timeframe,
                    "indicator": "atr",
                    "period": atr_period,
                }
            )
            commands.append({"type": "get_order_book", "symbol": symbol_norm})

        results = _batch_enqueue_and_await(commands, timeout_s=30.0)

        from mt5_mcp.services.market_regime import detect_regime

        symbol_results: dict[str, Any] = {}
        idx = 0
        for sym in symbols:
            bars_result = results[idx] if idx < len(results) else None
            atr_result = results[idx + 1] if idx + 1 < len(results) else None
            book_result = results[idx + 2] if idx + 2 < len(results) else None
            idx += 3

            bars_data = []
            if bars_result and bars_result.get("status") == "completed":
                payload = bars_result.get("result", {}).get("payload", {})
                if isinstance(payload, str):
                    try:
                        bars_data = json.loads(payload).get("data", [])
                    except Exception:
                        pass
                elif isinstance(payload, dict):
                    bars_data = payload.get("data", [])

            atr_value = _parse_indicator_value(atr_result) if atr_result else None
            book_data = _parse_payload_dict(book_result) if book_result else {}
            bid, ask = _first_bid_ask(book_data)

            regime = detect_regime(
                bars=bars_data,
                atr_value=atr_value or 0.0,
                atr_period=atr_period,
            )

            symbol_results[sym] = {
                "regime": regime,
                "bid": bid,
                "ask": ask,
                "atr": atr_value,
                "bars_count": len(bars_data),
            }

        return {"symbols": symbol_results, "timeframe": timeframe}
    except Exception as e:
        return {"error": str(e), "symbols": symbols}


@mcp.tool(name="mt5_opportunity_rank", annotations=_ANALYSIS_ANNOTATIONS)
def mt5_opportunity_rank(
    symbols: list[str],
    timeframe: str = "H1",
    min_score: float = 50.0,
    session_id: Optional[str] = None,
    strategy_id: Optional[str] = None,
) -> dict:
    try:
        commands = []
        for sym in symbols:
            symbol_norm = normalize_symbol(sym)
            commands.append(
                {
                    "type": "get_bars",
                    "symbol": symbol_norm,
                    "timeframe": timeframe,
                    "count": 20,
                }
            )
            commands.append(
                {
                    "type": "get_indicator",
                    "symbol": symbol_norm,
                    "timeframe": timeframe,
                    "indicator": "atr",
                    "period": 14,
                }
            )
            commands.append(
                {
                    "type": "get_indicator",
                    "symbol": symbol_norm,
                    "timeframe": timeframe,
                    "indicator": "rsi",
                    "period": 14,
                }
            )
            commands.append({"type": "get_order_book", "symbol": symbol_norm})

        results = _batch_enqueue_and_await(commands, timeout_s=60.0)

        snapshots: dict[str, Any] = {}
        idx = 0
        for sym in symbols:
            bars_result = results[idx] if idx < len(results) else None
            atr_result = results[idx + 1] if idx + 1 < len(results) else None
            rsi_result = results[idx + 2] if idx + 2 < len(results) else None
            book_result = results[idx + 3] if idx + 3 < len(results) else None
            idx += 4

            bars_data = []
            if bars_result and bars_result.get("status") == "completed":
                payload = bars_result.get("result", {}).get("payload", {})
                if isinstance(payload, str):
                    try:
                        bars_data = json.loads(payload).get("data", [])
                    except Exception:
                        pass
                elif isinstance(payload, dict):
                    bars_data = payload.get("data", [])

            atr_value = _parse_indicator_value(atr_result) if atr_result else None
            rsi_value = _parse_indicator_value(rsi_result) if rsi_result else None
            book_data = _parse_payload_dict(book_result) if book_result else {}
            bid, ask = _first_bid_ask(book_data)

            current_price = (
                float(bars_data[-1]["close"])
                if bars_data
                else (bid + ask) / 2
                if bid and ask
                else 0
            )

            from mt5_mcp.services.market_regime import detect_regime

            regime = detect_regime(
                bars=bars_data,
                atr_value=atr_value or 0.0,
            )

            snapshots[sym.upper()] = {
                "price": {
                    "current": current_price,
                    "bid": bid,
                    "ask": ask,
                    "spread_ratio_atr": (ask - bid) / atr_value
                    if atr_value and atr_value > 0 and ask and bid
                    else None,
                },
                "indicators": {
                    "atr": {
                        "value": atr_value,
                        "percentile": regime.get("atr_percentile"),
                    },
                    "rsi": {"value": rsi_value},
                },
                "regime": regime,
                "session_context": {},
                "calendar": {},
            }

        from mt5_mcp.services.opportunity_rank import OpportunityRanker

        ranker = OpportunityRanker()
        rankings = ranker.rank(
            symbols=symbols,
            snapshots=snapshots,
            min_score=min_score,
        )

        return {
            "rankings": rankings,
            "timeframe": timeframe,
            "symbols_count": len(symbols),
        }
    except Exception as e:
        return {"error": str(e), "symbols": symbols}
