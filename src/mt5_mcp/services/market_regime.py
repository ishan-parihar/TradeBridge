"""Market Regime Detection — classifies market conditions for strategy selection.

Based on audit findings: the system repeatedly traded in choppy/ranging markets
without knowing it was choppy. This service detects:
- Ranging markets → use bracket orders, avoid directional entries
- Trending markets → use directional entries, wider stops
- Compressing markets → wait for breakout

Logic: If (current_range / ATR) < 0.7 → ranging. If > 1.2 → trending.
"""

from __future__ import annotations

import math
from typing import Literal


MarketRegime = Literal[
    "ranging",
    "trending_up",
    "trending_down",
    "compressing",
    "momentum_push",
    "mean_reversion",
    "volatile_expansion",
    "low_volatility_consolidation",
    "unknown",
]


# ---------------------------------------------------------------------------
# Indicator helpers (standard-library only)
# ---------------------------------------------------------------------------


def _compute_ema(values: list[float], period: int) -> list[float]:
    """Compute EMA over a list of values."""
    if not values:
        return []
    multiplier = 2.0 / (period + 1)
    ema: list[float] = [values[0]]
    for v in values[1:]:
        ema.append((v - ema[-1]) * multiplier + ema[-1])
    return ema


def _compute_rsi(closes: list[float], period: int = 14) -> float | None:
    """Compute RSI from a list of closing prices. Returns None if insufficient data."""
    if len(closes) < period + 1:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _compute_bollinger_bands(
    closes: list[float], period: int = 20, num_std: float = 2.0
) -> tuple[float, float, float] | None:
    """Return (lower, middle, upper) of Bollinger Bands. None if insufficient data."""
    if len(closes) < period:
        return None
    recent = closes[-period:]
    middle = sum(recent) / period
    variance = sum((x - middle) ** 2 for x in recent) / period
    std = math.sqrt(variance)
    return (middle - num_std * std, middle, middle + num_std * std)


def _compute_atr_series(bars: list[dict], period: int = 14) -> list[float]:
    """Compute a series of ATR values from bar data using Wilder's smoothing."""
    if len(bars) < period + 1:
        return []

    tr_values: list[float] = []
    for i in range(len(bars)):
        b = bars[i]
        high = float(b.get("high", 0))
        low = float(b.get("low", 0))
        prev_close = float(bars[i - 1].get("close", 0)) if i > 0 else high
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr_values.append(tr)

    # Initial SMA for first ATR
    atr = sum(tr_values[:period]) / period
    atr_series = [atr]

    for i in range(period, len(tr_values)):
        atr = (atr * (period - 1) + tr_values[i]) / period
        atr_series.append(atr)

    return atr_series


def _compute_atr_percentile(atr_series: list[float]) -> float:
    """Compute the percentile rank of the most recent ATR in the series."""
    if len(atr_series) < 2:
        return 50.0
    current = atr_series[-1]
    sorted_values = sorted(atr_series[:-1])  # exclude current from reference
    rank = sum(1 for v in sorted_values if v < current)
    return (rank / len(sorted_values)) * 100.0


def _compute_adx(bars: list[dict], period: int = 14) -> float:
    """Simplified ADX calculation from bar data."""
    if len(bars) < period * 2:
        return 0.0

    plus_dm: list[float] = []
    minus_dm: list[float] = []
    tr_values: list[float] = []

    for i in range(1, len(bars)):
        curr = bars[i]
        prev = bars[i - 1]
        high = float(curr.get("high", 0))
        low = float(curr.get("low", 0))
        prev_high = float(prev.get("high", 0))
        prev_low = float(prev.get("low", 0))
        prev_close = float(prev.get("close", 0))

        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr_values.append(tr)

        up_move = high - prev_high
        down_move = prev_low - low

        if up_move > down_move and up_move > 0:
            plus_dm.append(up_move)
        else:
            plus_dm.append(0.0)

        if down_move > up_move and down_move > 0:
            minus_dm.append(down_move)
        else:
            minus_dm.append(0.0)

    if len(tr_values) < period:
        return 0.0

    # Smoothed values using Wilder's method
    smooth_tr = sum(tr_values[:period])
    smooth_plus = sum(plus_dm[:period])
    smooth_minus = sum(minus_dm[:period])

    dx_values: list[float] = []

    for i in range(period, len(tr_values)):
        smooth_tr = smooth_tr - (smooth_tr / period) + tr_values[i]
        smooth_plus = smooth_plus - (smooth_plus / period) + plus_dm[i]
        smooth_minus = smooth_minus - (smooth_minus / period) + minus_dm[i]

        if smooth_tr == 0:
            dx_values.append(0.0)
            continue

        plus_di = (smooth_plus / smooth_tr) * 100.0
        minus_di = (smooth_minus / smooth_tr) * 100.0

        di_sum = plus_di + minus_di
        if di_sum == 0:
            dx_values.append(0.0)
        else:
            dx_values.append(abs(plus_di - minus_di) / di_sum * 100.0)

    if not dx_values:
        return 0.0

    return sum(dx_values) / len(dx_values)


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

    # Compute extended indicators for new regime detection
    closes = [float(b.get("close", 0)) for b in bars]
    atr_series = _compute_atr_series(bars, atr_period)
    atr_percentile = _compute_atr_percentile(atr_series) if atr_series else 50.0
    rsi = _compute_rsi(closes)
    bb = _compute_bollinger_bands(closes)
    ema20 = _compute_ema(closes, 20)
    adx = _compute_adx(bars)

    # Price deviation from EMA20 (in ATR units)
    ema20_deviation_atr = 0.0
    if ema20 and len(ema20) > 0 and atr_value > 0:
        ema20_current = ema20[-1]
        ema20_deviation_atr = abs(last_close - ema20_current) / atr_value

    # Bar range trend: are ranges expanding or contracting over last N bars
    n_lookback = min(len(ranges), 10)
    if n_lookback >= 6:
        early_ranges = ranges[: n_lookback // 2]
        late_ranges = ranges[n_lookback // 2 :]
        early_avg = sum(early_ranges) / len(early_ranges)
        late_avg = sum(late_ranges) / len(late_ranges)
        range_expansion_ratio = late_avg / early_avg if early_avg > 0 else 1.0
    else:
        range_expansion_ratio = 1.0

    # Bollinger Band proximity: is price touching a band?
    bb_touched = False
    if bb is not None:
        lower, middle, upper = bb
        bb_width = upper - lower
        if bb_width > 0:
            bb_position = (last_close - lower) / bb_width
            bb_touched = bb_position <= 0.05 or bb_position >= 0.95

    # -----------------------------------------------------------------------
    # Priority-ordered regime detection (new regimes first, then existing)
    # -----------------------------------------------------------------------

    # 1. Volatile expansion: ATR percentile > 80, bar ranges expanding
    if atr_percentile > 80 and range_expansion_ratio > 1.15:
        regime: MarketRegime = "volatile_expansion"
        confidence = min(
            0.9,
            0.5 + (atr_percentile - 80) / 40.0 + (range_expansion_ratio - 1.15) * 0.5,
        )
        recommendation = "use_wide_brackets"

    # 2. Low volatility consolidation: ATR percentile < 20, ranges contracting
    elif atr_percentile < 20 and range_expansion_ratio < 0.85:
        regime = "low_volatility_consolidation"
        confidence = min(
            0.9,
            0.5 + (20 - atr_percentile) / 40.0 + (0.85 - range_expansion_ratio) * 0.5,
        )
        recommendation = "wait_for_expansion"

    # 3. Momentum push: ADX > 30, ATR expanding, price far from EMA20
    elif adx > 30 and range_expansion_ratio > 1.1 and ema20_deviation_atr > 1.5:
        if ema_fast is not None and ema_slow is not None:
            regime = "trending_up" if ema_fast > ema_slow else "trending_down"
        else:
            momentum = (
                last_close - float(recent_bars[-3].get("close", 0))
                if len(recent_bars) >= 3
                else 0
            )
            regime = "trending_up" if momentum > 0 else "trending_down"
        # Override to momentum_push — stronger than plain trend
        regime = "momentum_push"
        confidence = min(
            0.9, 0.5 + (adx - 30) / 60.0 + (ema20_deviation_atr - 1.5) * 0.1
        )
        recommendation = "momentum_continuation"

    # 4. Mean reversion: RSI extreme in ranging context + Bollinger Band touch
    elif (
        rsi is not None
        and (rsi > 75 or rsi < 25)
        and bb_touched
        and range_atr_ratio < 1.0
    ):
        regime = "mean_reversion"
        rsi_extremity = max(rsi - 75, 25 - rsi) if (rsi > 75 or rsi < 25) else 0
        confidence = min(0.9, 0.5 + rsi_extremity / 50.0)
        recommendation = "fade_extremes"

    # 5. Existing: ranging
    elif range_atr_ratio < 0.7:
        regime = "ranging"
        confidence = min(0.9, 0.5 + (0.7 - range_atr_ratio))
        recommendation = "use_bracket_orders"

    # 6. Existing: trending
    elif range_atr_ratio > 1.2:
        if ema_fast is not None and ema_slow is not None:
            if ema_fast > ema_slow:
                regime = "trending_up"
                confidence = min(0.9, 0.5 + (range_atr_ratio - 1.2) * 0.5)
            elif ema_fast < ema_slow:
                regime = "trending_down"
                confidence = min(0.9, 0.5 + (range_atr_ratio - 1.2) * 0.5)
            else:
                regime = "trending_up"
                confidence = 0.6
        else:
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

    # 7. Existing: compressing
    elif compression_ratio < 0.7:
        regime = "compressing"
        confidence = min(0.85, 0.5 + (0.7 - compression_ratio))
        recommendation = "wait_for_breakout"

    # 8. Fallback
    else:
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
        "rsi": round(rsi, 1) if rsi is not None else None,
        "adx": round(adx, 1),
        "atr_percentile": round(atr_percentile, 1),
        "ema20_deviation_atr": round(ema20_deviation_atr, 2),
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
        "momentum_push": {
            "entry_style": "momentum_continuation",
            "stop_strategy": "trail_with_atr",
            "avoid": "counter_trend",
            "preferred": "breakout_pullback",
            "max_trades": 2,
        },
        "mean_reversion": {
            "entry_style": "fade_extremes",
            "stop_strategy": "tight_stops",
            "avoid": "chasing",
            "preferred": "reversal_at_bands",
            "max_trades": 2,
        },
        "volatile_expansion": {
            "entry_style": "wide_brackets",
            "stop_strategy": "very_wide_stops",
            "avoid": "tight_stops",
            "preferred": "breakout_follow",
            "max_trades": 1,
        },
        "low_volatility_consolidation": {
            "entry_style": "patience_breakout",
            "stop_strategy": "normal_stops",
            "avoid": "ranging_chop",
            "preferred": "wait_for_expansion",
            "max_trades": 0,
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
