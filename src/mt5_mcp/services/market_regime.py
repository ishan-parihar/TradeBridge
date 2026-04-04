"""Market Regime Detection — classifies market conditions for strategy selection.

Based on audit findings: the system repeatedly traded in choppy/ranging markets
without knowing it was choppy. This service detects:
- Ranging markets → use bracket orders, avoid directional entries
- Trending markets → use directional entries, wider stops
- Compressing markets → wait for breakout

Logic: If (current_range / ATR) < 0.7 → ranging. If > 1.2 → trending.
"""

from __future__ import annotations

from typing import Literal


MarketRegime = Literal[
    "ranging", "trending_up", "trending_down", "compressing", "unknown"
]


def detect_regime(
    bars: list[dict],
    atr_value: float,
    atr_period: int = 14,
    ema_fast: float | None = None,
    ema_slow: float | None = None,
) -> dict:
    """Detect current market regime from bar data and ATR.

    Args:
        bars: List of OHLCV bar dicts, ordered oldest → newest
        atr_value: Current ATR value (in price units)
        atr_period: Period used for ATR calculation
        ema_fast: Optional fast EMA value for trend direction
        ema_slow: Optional slow EMA value for trend direction

    Returns:
        Dict with regime classification and supporting metrics.
    """
    if not bars or atr_value <= 0:
        return {
            "regime": "unknown",
            "confidence": 0.0,
            "atr": atr_value,
            "recommendation": "wait_for_data",
        }

    # Calculate recent range statistics
    n = min(len(bars), 20)  # Look at last 20 bars
    recent_bars = bars[-n:]

    ranges = []
    for b in recent_bars:
        high = float(b.get("high", 0))
        low = float(b.get("low", 0))
        if high > low:
            ranges.append(high - low)

    if not ranges:
        return {
            "regime": "unknown",
            "confidence": 0.0,
            "atr": atr_value,
            "recommendation": "wait_for_data",
        }

    avg_range = sum(ranges) / len(ranges)
    max_range = max(ranges)
    min_range = min(ranges)
    last_close = float(recent_bars[-1].get("close", 0))

    # Range-to-ATR ratio: the key regime indicator
    range_atr_ratio = avg_range / atr_value if atr_value > 0 else 0

    # Volatility compression: are bars getting smaller?
    if len(ranges) >= 5:
        first_half_avg = sum(ranges[: len(ranges) // 2]) / (len(ranges) // 2)
        second_half_avg = sum(ranges[len(ranges) // 2 :]) / (
            len(ranges) - len(ranges) // 2
        )
        compression_ratio = (
            second_half_avg / first_half_avg if first_half_avg > 0 else 1.0
        )
    else:
        compression_ratio = 1.0

    # Price position within recent range (0-100%)
    recent_high = max(float(b.get("high", 0)) for b in recent_bars)
    recent_low = min(float(b.get("low", 0)) for b in recent_bars)
    total_range = recent_high - recent_low
    price_position = (
        ((last_close - recent_low) / total_range * 100) if total_range > 0 else 50
    )

    # Determine regime
    confidence = 0.5  # Default moderate confidence

    if range_atr_ratio < 0.7:
        regime: MarketRegime = "ranging"
        confidence = min(0.9, 0.5 + (0.7 - range_atr_ratio))
        recommendation = "use_bracket_orders"
    elif range_atr_ratio > 1.2:
        # Determine trend direction
        if ema_fast is not None and ema_slow is not None:
            if ema_fast > ema_slow:
                regime = "trending_up"
                confidence = min(0.9, 0.5 + (range_atr_ratio - 1.2) * 0.5)
            elif ema_fast < ema_slow:
                regime = "trending_down"
                confidence = min(0.9, 0.5 + (range_atr_ratio - 1.2) * 0.5)
            else:
                regime = "trending_up"  # Default to up if EMAs equal
                confidence = 0.6
        else:
            # Use price momentum as fallback
            if len(recent_bars) >= 3:
                momentum = float(recent_bars[-1].get("close", 0)) - float(
                    recent_bars[-3].get("close", 0)
                )
                regime = "trending_up" if momentum > 0 else "trending_down"
                confidence = 0.6
            else:
                regime = "trending_up"
                confidence = 0.5
        recommendation = "use_directional_entries"
    elif compression_ratio < 0.7:
        regime = "compressing"
        confidence = min(0.85, 0.5 + (0.7 - compression_ratio))
        recommendation = "wait_for_breakout"
    else:
        # Normal volatility, no clear regime
        regime = "ranging"
        confidence = 0.4
        recommendation = "cautious_entries"

    # Additional context
    result = {
        "regime": regime,
        "confidence": round(confidence, 2),
        "atr": round(atr_value, 2),
        "avg_range": round(avg_range, 2),
        "range_atr_ratio": round(range_atr_ratio, 2),
        "compression_ratio": round(compression_ratio, 2),
        "price_position_pct": round(price_position, 1),
        "recent_high": round(recent_high, 2) if total_range > 0 else None,
        "recent_low": round(recent_low, 2) if total_range > 0 else None,
        "recommendation": recommendation,
        "strategy_hints": _get_strategy_hints(regime),
    }

    return result


def _get_strategy_hints(regime: MarketRegime) -> dict:
    """Return strategy-specific hints based on regime."""
    hints = {
        "ranging": {
            "entry_style": "bracket_orders",
            "stop_strategy": "wide_stops_1x_atr",
            "avoid": "middle_of_range_entries",
            "preferred": "support_resistance_bounces",
            "max_trades": 2,
        },
        "trending_up": {
            "entry_style": "pullback_buy",
            "stop_strategy": "trail_with_atr",
            "avoid": "counter_trend_shorts",
            "preferred": "higher_low_entries",
            "max_trades": 3,
        },
        "trending_down": {
            "entry_style": "rally_sell",
            "stop_strategy": "trail_with_atr",
            "avoid": "counter_trend_longs",
            "preferred": "lower_high_entries",
            "max_trades": 3,
        },
        "compressing": {
            "entry_style": "bracket_breakout",
            "stop_strategy": "wide_stops_outside_range",
            "avoid": "entries_inside_consolidation",
            "preferred": "wait_for_range_break",
            "max_trades": 1,
        },
        "unknown": {
            "entry_style": "avoid",
            "stop_strategy": "n/a",
            "avoid": "all_entries",
            "preferred": "wait_for_clearer_data",
            "max_trades": 0,
        },
    }
    return hints.get(regime, hints["unknown"])
