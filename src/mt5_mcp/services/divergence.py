"""Divergence Detection Service — finds MACD-price and RSI-price divergences.

Detects bullish and bearish divergences between price action and momentum
indicators, computed entirely in Python from OHLCV bars (no EA calls needed).

Usage:
    result = detect_divergence(bars, lookback=50)
    # result["bullish"] → list of bullish divergence signals
    # result["bearish"] → list of bearish divergence signals
    # result["summary"] → aggregated summary with divergence score
"""

from __future__ import annotations

import math
from typing import Any


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_divergence(
    bars: list[dict],
    lookback: int = 50,
    macd_fast: int = 12,
    macd_slow: int = 26,
    macd_signal_period: int = 9,
    rsi_period: int = 14,
    swing_window: int = 5,
) -> dict:
    """Detect MACD-price and RSI-price divergences from OHLCV bars.

    Args:
        bars: List of OHLCV bar dicts ordered oldest → newest.
            Each: {"time": int, "open": float, "high": float, "low": float, "close": float}
        lookback: Number of most-recent bars to analyse.
        macd_fast: Fast EMA period for MACD.
        macd_slow: Slow EMA period for MACD.
        macd_signal_period: Signal line EMA period for MACD.
        rsi_period: Wilder's RSI period.
        swing_window: Sliding window size for finding local extrema.

    Returns:
        Structured dict with bullish/bearish divergence lists and a summary.
    """
    min_bars = max(macd_slow + macd_signal_period, rsi_period + 1) + swing_window * 2
    if not bars or len(bars) < min_bars:
        return _empty_result()

    # Use only the requested lookback (most recent bars)
    working = bars[-lookback:] if len(bars) > lookback else list(bars)
    n = len(working)

    # Extract price series
    closes = [float(b["close"]) for b in working]
    highs = [float(b["high"]) for b in working]
    lows = [float(b["low"]) for b in working]

    # Compute indicator series
    macd_histogram = _compute_macd_histogram(
        closes, macd_fast, macd_slow, macd_signal_period
    )
    rsi_series = _compute_rsi(closes, rsi_period)

    # Find swing extrema in price and indicators
    price_swing_lows = _find_swing_lows_price(lows, swing_window)
    price_swing_highs = _find_swing_highs_price(highs, swing_window)
    macd_swing_lows = _find_swing_lows_indicator(macd_histogram, swing_window)
    macd_swing_highs = _find_swing_highs_indicator(macd_histogram, swing_window)
    rsi_swing_lows = _find_swing_lows_indicator(rsi_series, swing_window)
    rsi_swing_highs = _find_swing_highs_indicator(rsi_series, swing_window)

    # Detect divergences
    bullish_macd = _detect_bullish_divergence(
        price_swing_lows, macd_swing_lows, "macd_price"
    )
    bearish_macd = _detect_bearish_divergence(
        price_swing_highs, macd_swing_highs, "macd_price"
    )
    bullish_rsi = _detect_bullish_divergence(
        price_swing_lows, rsi_swing_lows, "rsi_price"
    )
    bearish_rsi = _detect_bearish_divergence(
        price_swing_highs, rsi_swing_highs, "rsi_price"
    )

    bullish = bullish_macd + bullish_rsi
    bearish = bearish_macd + bearish_rsi

    # Hidden divergence (trend continuation)
    bullish_macd_hidden = _detect_hidden_bullish_divergence(
        price_swing_lows, macd_swing_lows, "macd_price"
    )
    bearish_macd_hidden = _detect_hidden_bearish_divergence(
        price_swing_highs, macd_swing_highs, "macd_price"
    )
    bullish_rsi_hidden = _detect_hidden_bullish_divergence(
        price_swing_lows, rsi_swing_lows, "rsi_price"
    )
    bearish_rsi_hidden = _detect_hidden_bearish_divergence(
        price_swing_highs, rsi_swing_highs, "rsi_price"
    )

    bullish_hidden = bullish_macd_hidden + bullish_rsi_hidden
    bearish_hidden = bearish_macd_hidden + bearish_rsi_hidden

    for sig in bullish_hidden + bearish_hidden:
        sig["hidden"] = True

    # Compute summary
    summary = _build_summary(bullish, bearish)
    summary["total_bullish_hidden"] = len(bullish_hidden)
    summary["total_bearish_hidden"] = len(bearish_hidden)

    return {
        "bullish": bullish,
        "bearish": bearish,
        "bullish_hidden": bullish_hidden,
        "bearish_hidden": bearish_hidden,
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# Indicator computation (pure Python, standard library only)
# ---------------------------------------------------------------------------


def _ema(data: list[float], period: int) -> list[float]:
    """Compute exponential moving average.

    multiplier = 2 / (period + 1)
    EMA[period-1] = SMA[0:period]
    EMA[i] = (data[i] - EMA[i-1]) * multiplier + EMA[i-1]

    Returns list of same length as input, with NaN for warm-up bars.
    """
    if period < 1 or len(data) < period:
        return [float("nan")] * len(data)

    multiplier = 2.0 / (period + 1)
    result: list[float] = [float("nan")] * (period - 1)

    # Seed with SMA of first `period` values
    seed = sum(data[:period]) / period
    result.append(seed)

    for i in range(period, len(data)):
        result.append((data[i] - result[-1]) * multiplier + result[-1])

    return result


def _compute_macd_histogram(
    closes: list[float],
    fast: int,
    slow: int,
    signal_period: int,
) -> list[float]:
    """Compute MACD histogram values."""
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)

    n = len(closes)
    macd_line: list[float] = []
    for i in range(n):
        if math.isnan(ema_fast[i]) or math.isnan(ema_slow[i]):
            macd_line.append(float("nan"))
        else:
            macd_line.append(ema_fast[i] - ema_slow[i])

    # Signal line = EMA of MACD line (handle NaN propagation)
    signal_line = _ema_safe_nan(macd_line, signal_period)

    histogram: list[float] = []
    for i in range(n):
        if math.isnan(macd_line[i]) or math.isnan(signal_line[i]):
            histogram.append(float("nan"))
        else:
            histogram.append(macd_line[i] - signal_line[i])

    return histogram


def _ema_safe_nan(data: list[float], period: int) -> list[float]:
    """Compute EMA, propagating NaN for positions where data is NaN.

    The seed SMA only uses non-NaN values. Once started, subsequent NaN
    inputs propagate as NaN in the output, but valid inputs continue
    the EMA from the last valid value.
    """
    n = len(data)
    if period < 1 or n < period:
        return [float("nan")] * n

    multiplier = 2.0 / (period + 1)
    result: list[float] = [float("nan")] * n

    # Find first `period` non-NaN values for seed
    valid_indices = [i for i in range(n) if not math.isnan(data[i])]
    if len(valid_indices) < period:
        return result

    # Seed at the index of the period-th valid value
    seed_end_idx = valid_indices[period - 1]
    seed_start_idx = valid_indices[0]
    seed = sum(data[seed_start_idx : seed_end_idx + 1]) / period
    result[seed_end_idx] = seed

    # Iterate forward from the seed
    prev = seed
    for i in range(seed_end_idx + 1, n):
        if math.isnan(data[i]):
            # Keep NaN but remember last valid prev for continuation
            result[i] = float("nan")
        else:
            prev = (data[i] - prev) * multiplier + prev
            result[i] = prev

    return result


def _compute_rsi(closes: list[float], period: int = 14) -> list[float]:
    """Compute Wilder's RSI.

    gain[i] = max(close[i] - close[i-1], 0)
    loss[i] = max(close[i-1] - close[i], 0)
    avg_gain[0] = mean of first `period` gains
    avg_loss[0] = mean of first `period` losses
    avg_gain[i] = (avg_gain[i-1] * (period-1) + gain[i]) / period
    avg_loss[i] = (avg_loss[i-1] * (period-1) + loss[i]) / period
    RS = avg_gain / avg_loss
    RSI = 100 - 100 / (1 + RS)

    Returns list of same length as `closes`, with NaN for warm-up bars.
    """
    n = len(closes)
    result: list[float] = [float("nan")] * n

    if n < period + 1:
        return result

    # Calculate gains and losses
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, n):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))

    # Seed averages
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    # First RSI at index `period`
    if avg_loss == 0:
        result[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        result[period] = 100.0 - 100.0 / (1.0 + rs)

    prev_avg_gain = avg_gain
    prev_avg_loss = avg_loss

    # Smoothed averages
    for i in range(period, len(gains)):
        prev_avg_gain = (prev_avg_gain * (period - 1) + gains[i]) / period
        prev_avg_loss = (prev_avg_loss * (period - 1) + losses[i]) / period

        idx = i + 1  # +1 because gains/losses start at index 1 of closes
        if prev_avg_loss == 0:
            result[idx] = 100.0
        else:
            rs = prev_avg_gain / prev_avg_loss
            result[idx] = 100.0 - 100.0 / (1.0 + rs)

    return result


# ---------------------------------------------------------------------------
# Swing extrema detection
# ---------------------------------------------------------------------------


def _find_swing_lows_price(values: list[float], window: int) -> list[tuple[int, float]]:
    """Find swing lows (local minima) in a price series.

    Returns list of (index, value) tuples.
    """
    swings: list[tuple[int, float]] = []
    for i in range(window, len(values) - window):
        val = values[i]
        if all(values[j] >= val for j in range(i - window, i + window + 1) if j != i):
            swings.append((i, val))
    return swings


def _find_swing_highs_price(
    values: list[float], window: int
) -> list[tuple[int, float]]:
    """Find swing highs (local maxima) in a price series.

    Returns list of (index, value) tuples.
    """
    swings: list[tuple[int, float]] = []
    for i in range(window, len(values) - window):
        val = values[i]
        if all(values[j] <= val for j in range(i - window, i + window + 1) if j != i):
            swings.append((i, val))
    return swings


def _find_swing_lows_indicator(
    series: list[float], window: int
) -> list[tuple[int, float]]:
    """Find swing lows in an indicator series, skipping NaN values."""
    swings: list[tuple[int, float]] = []
    for i in range(window, len(series) - window):
        if math.isnan(series[i]):
            continue
        val = series[i]
        is_low = True
        for j in range(i - window, i + window + 1):
            if j == i:
                continue
            if math.isnan(series[j]):
                continue
            if series[j] < val:
                is_low = False
                break
        if is_low:
            swings.append((i, val))
    return swings


def _find_swing_highs_indicator(
    series: list[float], window: int
) -> list[tuple[int, float]]:
    """Find swing highs in an indicator series, skipping NaN values."""
    swings: list[tuple[int, float]] = []
    for i in range(window, len(series) - window):
        if math.isnan(series[i]):
            continue
        val = series[i]
        is_high = True
        for j in range(i - window, i + window + 1):
            if j == i:
                continue
            if math.isnan(series[j]):
                continue
            if series[j] > val:
                is_high = False
                break
        if is_high:
            swings.append((i, val))
    return swings


# ---------------------------------------------------------------------------
# Divergence detection
# ---------------------------------------------------------------------------


def _detect_bullish_divergence(
    price_lows: list[tuple[int, float]],
    indicator_lows: list[tuple[int, float]],
    div_type: str,
) -> list[dict]:
    """Detect bullish divergence: price makes lower low, indicator makes higher low.

    Scans consecutive pairs of swing lows to find divergence patterns.
    """
    if len(price_lows) < 2 or len(indicator_lows) < 2:
        return []

    divergences: list[dict[str, Any]] = []

    for i in range(len(price_lows) - 1):
        idx1_p, price_low_1 = price_lows[i]
        idx2_p, price_low_2 = price_lows[i + 1]

        # Price must make a lower low
        if price_low_2 >= price_low_1:
            continue

        # Find indicator lows near the price low indices
        ind_low_1 = _find_nearest_low(indicator_lows, idx1_p, tolerance=10)
        ind_low_2 = _find_nearest_low(indicator_lows, idx2_p, tolerance=10)

        if ind_low_1 is None or ind_low_2 is None:
            continue

        ind_idx_1, ind_val_1 = ind_low_1
        ind_idx_2, ind_val_2 = ind_low_2

        # Indicator must make a higher low
        if ind_val_2 <= ind_val_1:
            continue

        # Ensure temporal order
        if ind_idx_2 < ind_idx_1:
            continue

        strength = _score_bullish_strength(
            price_low_1, price_low_2, ind_val_1, ind_val_2
        )

        divergences.append(
            {
                "type": div_type,
                "price_low_1": round(price_low_1, 6),
                "price_low_2": round(price_low_2, 6),
                "indicator_low_1": round(ind_val_1, 6),
                "indicator_low_2": round(ind_val_2, 6),
                "bar_index_1": ind_idx_1,
                "bar_index_2": ind_idx_2,
                "strength": round(strength, 2),
            }
        )

    return divergences


def _detect_bearish_divergence(
    price_highs: list[tuple[int, float]],
    indicator_highs: list[tuple[int, float]],
    div_type: str,
) -> list[dict]:
    """Detect bearish divergence: price makes higher high, indicator makes lower high."""
    if len(price_highs) < 2 or len(indicator_highs) < 2:
        return []

    divergences: list[dict[str, Any]] = []

    for i in range(len(price_highs) - 1):
        idx1_p, price_high_1 = price_highs[i]
        idx2_p, price_high_2 = price_highs[i + 1]

        # Price must make a higher high
        if price_high_2 <= price_high_1:
            continue

        # Find indicator highs near the price high indices
        ind_high_1 = _find_nearest_high(indicator_highs, idx1_p, tolerance=10)
        ind_high_2 = _find_nearest_high(indicator_highs, idx2_p, tolerance=10)

        if ind_high_1 is None or ind_high_2 is None:
            continue

        ind_idx_1, ind_val_1 = ind_high_1
        ind_idx_2, ind_val_2 = ind_high_2

        # Indicator must make a lower high
        if ind_val_2 >= ind_val_1:
            continue

        # Ensure temporal order
        if ind_idx_2 < ind_idx_1:
            continue

        strength = _score_bearish_strength(
            price_high_1, price_high_2, ind_val_1, ind_val_2
        )

        divergences.append(
            {
                "type": div_type,
                "price_high_1": round(price_high_1, 6),
                "price_high_2": round(price_high_2, 6),
                "indicator_high_1": round(ind_val_1, 6),
                "indicator_high_2": round(ind_val_2, 6),
                "bar_index_1": ind_idx_1,
                "bar_index_2": ind_idx_2,
                "strength": round(strength, 2),
            }
        )

    return divergences


def _find_nearest_low(
    swing_lows: list[tuple[int, float]],
    target_idx: int,
    tolerance: int = 10,
) -> tuple[int, float] | None:
    """Find the nearest indicator swing low to a target price index."""
    best: tuple[int, float] | None = None
    best_dist = tolerance + 1

    for ind_idx, ind_val in swing_lows:
        dist = abs(ind_idx - target_idx)
        if dist <= tolerance and dist < best_dist:
            best = (ind_idx, ind_val)
            best_dist = dist

    return best


def _find_nearest_high(
    swing_highs: list[tuple[int, float]],
    target_idx: int,
    tolerance: int = 10,
) -> tuple[int, float] | None:
    """Find the nearest indicator swing high to a target price index."""
    best: tuple[int, float] | None = None
    best_dist = tolerance + 1

    for ind_idx, ind_val in swing_highs:
        dist = abs(ind_idx - target_idx)
        if dist <= tolerance and dist < best_dist:
            best = (ind_idx, ind_val)
            best_dist = dist

    return best


# ---------------------------------------------------------------------------
# Strength scoring
# ---------------------------------------------------------------------------


def _score_bullish_strength(
    price_low_1: float,
    price_low_2: float,
    ind_low_1: float,
    ind_low_2: float,
) -> float:
    """Score bullish divergence strength from 0.3 to 1.0.

    Higher score = more pronounced divergence.
    """
    if price_low_1 == 0:
        return 0.3

    # Price decline (how much lower is the second low)
    price_change = (price_low_1 - price_low_2) / abs(price_low_1)
    # Indicator rise (how much higher is the second low)
    ind_change = (ind_low_2 - ind_low_1) / (abs(ind_low_1) + 1e-10)

    # Normalize to 0-1 range
    price_score = min(1.0, price_change * 500)
    ind_score = min(1.0, ind_change * 10)

    # Geometric mean
    raw = math.sqrt(price_score * ind_score)

    # Scale to 0.3-1.0
    return 0.3 + raw * 0.7


def _score_bearish_strength(
    price_high_1: float,
    price_high_2: float,
    ind_high_1: float,
    ind_high_2: float,
) -> float:
    """Score bearish divergence strength from 0.3 to 1.0."""
    if price_high_1 == 0:
        return 0.3

    price_change = (price_high_2 - price_high_1) / abs(price_high_1)
    ind_change = (ind_high_1 - ind_high_2) / (abs(ind_high_1) + 1e-10)

    price_score = min(1.0, price_change * 500)
    ind_score = min(1.0, ind_change * 10)

    raw = math.sqrt(price_score * ind_score)

    return 0.3 + raw * 0.7


def _detect_hidden_bullish_divergence(
    price_lows: list[tuple[int, float]],
    indicator_lows: list[tuple[int, float]],
    div_type: str,
) -> list[dict]:
    """Hidden bullish divergence: price makes higher low, indicator makes lower low.

    Signals trend continuation — buyers are absorbing despite lower momentum.
    """
    if len(price_lows) < 2 or len(indicator_lows) < 2:
        return []

    divergences: list[dict[str, Any]] = []

    for i in range(len(price_lows) - 1):
        idx1_p, price_low_1 = price_lows[i]
        idx2_p, price_low_2 = price_lows[i + 1]

        if price_low_2 <= price_low_1:
            continue

        ind_low_1 = _find_nearest_low(indicator_lows, idx1_p, tolerance=10)
        ind_low_2 = _find_nearest_low(indicator_lows, idx2_p, tolerance=10)

        if ind_low_1 is None or ind_low_2 is None:
            continue

        ind_idx_1, ind_val_1 = ind_low_1
        ind_idx_2, ind_val_2 = ind_low_2

        if ind_val_2 >= ind_val_1:
            continue

        if ind_idx_2 < ind_idx_1:
            continue

        strength = _score_bullish_strength(
            price_low_1, price_low_2, ind_val_1, ind_val_2
        )

        divergences.append(
            {
                "type": div_type,
                "price_low_1": round(price_low_1, 6),
                "price_low_2": round(price_low_2, 6),
                "indicator_low_1": round(ind_val_1, 6),
                "indicator_low_2": round(ind_val_2, 6),
                "bar_index_1": ind_idx_1,
                "bar_index_2": ind_idx_2,
                "strength": round(strength, 2),
                "hidden": True,
            }
        )

    return divergences


def _detect_hidden_bearish_divergence(
    price_highs: list[tuple[int, float]],
    indicator_highs: list[tuple[int, float]],
    div_type: str,
) -> list[dict]:
    """Hidden bearish divergence: price makes lower high, indicator makes higher high.

    Signals trend continuation — sellers are absorbing despite lower momentum.
    """
    if len(price_highs) < 2 or len(indicator_highs) < 2:
        return []

    divergences: list[dict[str, Any]] = []

    for i in range(len(price_highs) - 1):
        idx1_p, price_high_1 = price_highs[i]
        idx2_p, price_high_2 = price_highs[i + 1]

        if price_high_2 >= price_high_1:
            continue

        ind_high_1 = _find_nearest_high(indicator_highs, idx1_p, tolerance=10)
        ind_high_2 = _find_nearest_high(indicator_highs, idx2_p, tolerance=10)

        if ind_high_1 is None or ind_high_2 is None:
            continue

        ind_idx_1, ind_val_1 = ind_high_1
        ind_idx_2, ind_val_2 = ind_high_2

        if ind_val_2 <= ind_val_1:
            continue

        if ind_idx_2 < ind_idx_1:
            continue

        strength = _score_bearish_strength(
            price_high_1, price_high_2, ind_val_1, ind_val_2
        )

        divergences.append(
            {
                "type": div_type,
                "price_high_1": round(price_high_1, 6),
                "price_high_2": round(price_high_2, 6),
                "indicator_high_1": round(ind_val_1, 6),
                "indicator_high_2": round(ind_val_2, 6),
                "bar_index_1": ind_idx_1,
                "bar_index_2": ind_idx_2,
                "strength": round(strength, 2),
                "hidden": True,
            }
        )

    return divergences


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def _build_summary(
    bullish: list[dict],
    bearish: list[dict],
) -> dict:
    """Build summary with aggregated divergence information.

    divergence_score: -10 to +10 scale.
    - Negative = bearish pressure
    - Positive = bullish pressure
    """
    total_bullish = len(bullish)
    total_bearish = len(bearish)

    # Compute divergence score from weighted strengths
    bull_score = sum(sig.get("strength", 0) for sig in bullish)
    bear_score = sum(sig.get("strength", 0) for sig in bearish)

    # Scale: each signal contributes up to 1.0, total range -10 to +10
    raw_score = (bull_score - bear_score) * 2.0
    divergence_score = max(-10.0, min(10.0, raw_score))

    if divergence_score > 0.5:
        strongest = "bullish"
    elif divergence_score < -0.5:
        strongest = "bearish"
    else:
        strongest = "none"

    return {
        "total_bullish": total_bullish,
        "total_bearish": total_bearish,
        "strongest_signal": strongest,
        "divergence_score": round(divergence_score, 2),
    }


def _empty_result() -> dict:
    """Return an empty result structure for insufficient data."""
    return {
        "bullish": [],
        "bearish": [],
        "summary": {
            "total_bullish": 0,
            "total_bearish": 0,
            "strongest_signal": "none",
            "divergence_score": 0.0,
        },
    }
