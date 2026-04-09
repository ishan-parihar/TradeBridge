"""Multi-bar chart pattern detection service.

Detects 6 multi-bar patterns from OHLCV data using pure Python:
- W-Bottom / Double Bottom
- M-Top / Double Top
- Bollinger Squeeze
- Breakout Detection
- Gap Detection
- Fibonacci Retracement/Extension

No external dependencies — uses only ``math`` and stdlib.
"""

from __future__ import annotations

import math
from typing import Any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Convert a value to float, returning *default* on failure."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _sma(values: list[float], period: int) -> float:
    """Simple moving average over the last *period* values."""
    window = values[-period:]
    return sum(window) / len(window)


def _std(values: list[float], period: int) -> float:
    """Population standard deviation over the last *period* values."""
    window = values[-period:]
    mean = sum(window) / len(window)
    variance = sum((x - mean) ** 2 for x in window) / len(window)
    return math.sqrt(variance)


def _find_swing_low_indices(bars: list[dict], lookback: int = 5) -> list[int]:
    """Return indices where *low* is a local minimum among *lookback* neighbours."""
    indices: list[int] = []
    for i in range(lookback, len(bars) - lookback):
        current_low = _safe_float(bars[i].get("low"))
        if all(
            _safe_float(bars[j].get("low")) >= current_low
            for j in range(i - lookback, i + lookback + 1)
            if j != i
        ):
            indices.append(i)
    return indices


def _find_swing_high_indices(bars: list[dict], lookback: int = 5) -> list[int]:
    """Return indices where *high* is a local maximum among *lookback* neighbours."""
    indices: list[int] = []
    for i in range(lookback, len(bars) - lookback):
        current_high = _safe_float(bars[i].get("high"))
        if all(
            _safe_float(bars[j].get("high")) <= current_high
            for j in range(i - lookback, i + lookback + 1)
            if j != i
        ):
            indices.append(i)
    return indices


def _compute_bollinger_bands(bars: list[dict], period: int = 20) -> list[dict | None]:
    """Compute Bollinger Bands for every bar that has enough history.

    Returns a list of dicts with keys ``middle``, ``upper``, ``lower``
    aligned to the same indices as *bars* (None entries where insufficient).
    """
    closes = [_safe_float(b.get("close")) for b in bars]
    result: list[dict | None] = [None] * len(bars)
    for i in range(period - 1, len(bars)):
        window = closes[i - period + 1 : i + 1]
        middle = sum(window) / period
        variance = sum((x - middle) ** 2 for x in window) / period
        std_dev = math.sqrt(variance)
        result[i] = {
            "middle": middle,
            "upper": middle + 2 * std_dev,
            "lower": middle - 2 * std_dev,
        }
    return result


# ---------------------------------------------------------------------------
# W-Bottom / Double Bottom
# ---------------------------------------------------------------------------


def detect_w_bottom(bars: list[dict], tolerance: float = 0.03) -> dict:
    """Detect W-Bottom (Double Bottom) reversal pattern.

    Looks for two swing lows within *tolerance* of each other, then checks
    whether price has broken above the neckline (the high between the two
    bottoms).

    Returns a dict with ``status``, ``score``, ``bottom1_price``,
    ``bottom2_price``, ``neckline``, and ``bar_distance``.
    """
    default = {
        "status": "none",
        "score": 0,
        "bottom1_price": 0.0,
        "bottom2_price": 0.0,
        "neckline": 0.0,
        "bar_distance": 0,
    }

    swing_lows = _find_swing_low_indices(bars, lookback=5)
    if len(swing_lows) < 2:
        return default

    # Examine the two most recent swing lows
    idx2 = swing_lows[-1]
    idx1 = swing_lows[-2]

    low1 = _safe_float(bars[idx1].get("low"))
    low2 = _safe_float(bars[idx2].get("low"))

    # Check tolerance — second low should be within tolerance of first
    if low1 <= 0:
        return default
    if low2 < low1 * (1.0 - tolerance) or low2 > low1 * (1.0 + tolerance):
        return default

    # Neckline = highest high between the two bottoms
    between_highs = [_safe_float(bars[j].get("high")) for j in range(idx1, idx2 + 1)]
    neckline = max(between_highs) if between_highs else low1

    # Current price = close of most recent bar
    current_close = _safe_float(bars[-1].get("close"))
    bar_distance = idx2 - idx1

    result: dict[str, Any] = {
        "status": "forming",
        "score": 2,
        "bottom1_price": low1,
        "bottom2_price": low2,
        "neckline": neckline,
        "bar_distance": bar_distance,
    }

    # Confirmation: price broke above neckline
    if current_close > neckline:
        result["status"] = "confirmed"
        result["score"] = 5

    return result


# ---------------------------------------------------------------------------
# M-Top / Double Top
# ---------------------------------------------------------------------------


def detect_m_top(bars: list[dict], tolerance: float = 0.03) -> dict:
    """Detect M-Top (Double Top) reversal pattern.

    Looks for two swing highs within *tolerance* of each other, then checks
    whether price has broken below the neckline (the low between the two
    tops).

    Returns a dict with ``status``, ``score``, ``top1_price``,
    ``top2_price``, ``neckline``, and ``bar_distance``.
    """
    default = {
        "status": "none",
        "score": 0,
        "top1_price": 0.0,
        "top2_price": 0.0,
        "neckline": 0.0,
        "bar_distance": 0,
    }

    swing_highs = _find_swing_high_indices(bars, lookback=5)
    if len(swing_highs) < 2:
        return default

    idx2 = swing_highs[-1]
    idx1 = swing_highs[-2]

    high1 = _safe_float(bars[idx1].get("high"))
    high2 = _safe_float(bars[idx2].get("high"))

    # Check tolerance — second high should be within tolerance of first
    if high1 <= 0:
        return default
    if high2 < high1 * (1.0 - tolerance) or high2 > high1 * (1.0 + tolerance):
        return default

    # Neckline = lowest low between the two tops
    between_lows = [_safe_float(bars[j].get("low")) for j in range(idx1, idx2 + 1)]
    neckline = min(between_lows) if between_lows else high1

    current_close = _safe_float(bars[-1].get("close"))
    bar_distance = idx2 - idx1

    result: dict[str, Any] = {
        "status": "forming",
        "score": -2,
        "top1_price": high1,
        "top2_price": high2,
        "neckline": neckline,
        "bar_distance": bar_distance,
    }

    # Confirmation: price broke below neckline
    if current_close < neckline:
        result["status"] = "confirmed"
        result["score"] = -5

    return result


# ---------------------------------------------------------------------------
# Bollinger Squeeze
# ---------------------------------------------------------------------------


def detect_bollinger_squeeze(bars: list[dict], period: int = 20) -> dict:
    """Detect Bollinger Band squeeze conditions.

    Computes bandwidth for each bar and compares the current bandwidth to
    the 20-bar average of bandwidths.

    Returns a dict with ``squeezing``, ``current_bandwidth``,
    ``avg_bandwidth``, ``bandwidth_percentile``, and ``score``.
    """
    default = {
        "squeezing": False,
        "current_bandwidth": 0.0,
        "avg_bandwidth": 0.0,
        "bandwidth_percentile": 0.0,
        "score": 0,
    }

    closes = [_safe_float(b.get("close")) for b in bars]
    if len(closes) < period:
        return default

    # Compute bandwidth for each bar with enough history
    bandwidths: list[float] = []
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1 : i + 1]
        middle = sum(window) / period
        variance = sum((x - middle) ** 2 for x in window) / period
        std_dev = math.sqrt(variance)
        upper = middle + 2 * std_dev
        lower = middle - 2 * std_dev
        if middle == 0:
            bandwidths.append(0.0)
        else:
            bandwidths.append((upper - lower) / middle)

    if len(bandwidths) < 2:
        return default

    current_bandwidth = bandwidths[-1]

    # 20-bar average of bandwidths
    avg_window = bandwidths[-min(20, len(bandwidths)) :]
    avg_bandwidth = sum(avg_window) / len(avg_window)

    # Percentile: where does current bandwidth rank among all bandwidths?
    sorted_bw = sorted(bandwidths)
    rank = sum(1 for bw in sorted_bw if bw <= current_bandwidth)
    bandwidth_percentile = rank / len(sorted_bw) if sorted_bw else 0.0

    squeezing = avg_bandwidth > 0 and current_bandwidth < 0.5 * avg_bandwidth

    return {
        "squeezing": squeezing,
        "current_bandwidth": round(current_bandwidth, 6),
        "avg_bandwidth": round(avg_bandwidth, 6),
        "bandwidth_percentile": round(bandwidth_percentile, 4),
        "score": 3 if squeezing else 0,
    }


# ---------------------------------------------------------------------------
# Breakout Detection
# ---------------------------------------------------------------------------


def detect_breakout(bars: list[dict], period: int = 20) -> dict:
    """Detect breakouts above/below recent range with volume confirmation.

    Returns a dict with ``direction``, ``breakout_price``, ``threshold``,
    ``volume_confirmed``, ``strength``, and ``score``.
    """
    default = {
        "direction": "none",
        "breakout_price": 0.0,
        "threshold": 0.0,
        "volume_confirmed": False,
        "strength": "weak",
        "score": 0,
    }

    if len(bars) < period + 1:
        return default

    # Use bars up to (but not including) the current bar for the range
    lookback = bars[-(period + 1) : -1] if len(bars) > period else bars[:-1]
    if len(lookback) < period:
        lookback = bars[:period]

    highs = [_safe_float(b.get("high")) for b in lookback]
    lows = [_safe_float(b.get("low")) for b in lookback]
    twenty_high = max(highs) if highs else 0.0
    twenty_low = min(lows) if lows else 0.0

    current_close = _safe_float(bars[-1].get("close"))
    current_volume = _safe_float(bars[-1].get("volume"))

    # Bullish breakout
    if current_close > twenty_high and twenty_high > 0:
        direction = "bullish"
        breakout_price = current_close
        threshold = twenty_high
        score = 3
    # Bearish breakout
    elif current_close < twenty_low and twenty_low > 0:
        direction = "bearish"
        breakout_price = current_close
        threshold = twenty_low
        score = -3
    else:
        return default

    # Volume confirmation
    volumes = [_safe_float(b.get("volume")) for b in lookback]
    avg_volume = sum(v for v in volumes if v > 0) / max(
        1, sum(1 for v in volumes if v > 0)
    )
    volume_confirmed = current_volume > 1.5 * avg_volume if avg_volume > 0 else False
    strength = "strong" if volume_confirmed else "weak"

    return {
        "direction": direction,
        "breakout_price": breakout_price,
        "threshold": threshold,
        "volume_confirmed": volume_confirmed,
        "strength": strength,
        "score": score,
    }


# ---------------------------------------------------------------------------
# Gap Detection
# ---------------------------------------------------------------------------


def detect_gaps(bars: list[dict], threshold_pct: float = 0.01) -> dict:
    """Detect gaps between consecutive bars.

    Scans the last 20 bars (or fewer if insufficient).  A gap up occurs
    when the current open exceeds the previous high by more than
    *threshold_pct*.  A gap down is the inverse.

    Returns a dict with ``gaps`` (list), ``total_gaps``.
    """
    scan = bars[-20:] if len(bars) > 20 else bars
    gaps: list[dict[str, Any]] = []

    for i in range(1, len(scan)):
        prev_high = _safe_float(scan[i - 1].get("high"))
        prev_low = _safe_float(scan[i - 1].get("low"))
        prev_close = _safe_float(scan[i - 1].get("close"))
        curr_open = _safe_float(scan[i].get("open"))

        if prev_high <= 0 or prev_low <= 0:
            continue

        # Gap up
        if curr_open > prev_high:
            pct = (curr_open - prev_high) / prev_high
            if pct > threshold_pct:
                gaps.append(
                    {
                        "type": "gap_up",
                        "pct": round(pct, 6),
                        "bar_index": len(bars) - len(scan) + i,
                        "open_price": curr_open,
                        "prev_close": prev_close,
                    }
                )

        # Gap down
        if curr_open < prev_low:
            pct = (prev_low - curr_open) / prev_low
            if pct > threshold_pct:
                gaps.append(
                    {
                        "type": "gap_down",
                        "pct": round(pct, 6),
                        "bar_index": len(bars) - len(scan) + i,
                        "open_price": curr_open,
                        "prev_close": prev_close,
                    }
                )

    return {
        "gaps": gaps,
        "total_gaps": len(gaps),
    }


# ---------------------------------------------------------------------------
# Fibonacci Retracement / Extension
# ---------------------------------------------------------------------------


def detect_fibonacci_levels(bars: list[dict], lookback: int = 50) -> dict:
    """Detect Fibonacci retracement and extension levels.

    Finds the highest high and lowest low in the *lookback* window, computes
    standard retracement and extension levels, and checks whether the
    current price sits near the 0.618 retracement.

    Returns a dict with ``trend``, ``swing_high``, ``swing_low``,
    ``retracements``, ``extensions``, ``current_level``, ``at_0618``, and
    ``score``.
    """
    default_retracements = {
        "0.236": 0.0,
        "0.382": 0.0,
        "0.500": 0.0,
        "0.618": 0.0,
        "0.786": 0.0,
    }
    default_extensions = {
        "1.272": 0.0,
        "1.618": 0.0,
        "2.000": 0.0,
    }
    default_result = {
        "trend": "none",
        "swing_high": 0.0,
        "swing_low": 0.0,
        "retracements": default_retracements,
        "extensions": default_extensions,
        "current_level": "none",
        "at_0618": False,
        "score": 0,
    }

    if len(bars) < lookback:
        return default_result

    window = bars[-lookback:]
    swing_high = max(_safe_float(b.get("high")) for b in window)
    swing_low = min(_safe_float(b.get("low")) for b in window)
    diff = swing_high - swing_low

    if diff == 0:
        return default_result

    # Determine trend: if most recent bar is in upper half, uptrend
    current_close = _safe_float(bars[-1].get("close"))
    mid = swing_low + diff / 2
    trend = "uptrend" if current_close >= mid else "downtrend"

    # Retracement levels
    retracements = {}
    for level in [0.236, 0.382, 0.500, 0.618, 0.786]:
        if trend == "uptrend":
            retracements[str(level)] = round(swing_high - diff * level, 6)
        else:
            retracements[str(level)] = round(swing_low + diff * level, 6)

    # Extension levels
    extensions = {}
    for level in [1.272, 1.618, 2.000]:
        if trend == "uptrend":
            extensions[str(level)] = round(swing_high + diff * (level - 1), 6)
        else:
            extensions[str(level)] = round(swing_low - diff * (level - 1), 6)

    # Check if current price is near 0.618 retracement (within 0.5%)
    level_0618 = retracements["0.618"]
    at_0618 = False
    current_level = "none"

    if level_0618 > 0:
        tolerance = level_0618 * 0.005
        if abs(current_close - level_0618) <= tolerance:
            at_0618 = True
            current_level = "0.618"

    # Determine which level current price is closest to
    all_levels = {**retracements, **extensions}
    min_dist = float("inf")
    for name, price in all_levels.items():
        if price > 0:
            dist = abs(current_close - price) / price
            if dist < min_dist:
                min_dist = dist
                current_level = name

    # Score: +6 if at 0.618 in the trend direction
    # Uptrend: 0.618 retracement is a buy zone (price pulled back to 0.618)
    # Downtrend: 0.618 retracement is a sell zone
    score = 6 if at_0618 else 0

    return {
        "trend": trend,
        "swing_high": round(swing_high, 6),
        "swing_low": round(swing_low, 6),
        "retracements": retracements,
        "extensions": extensions,
        "current_level": current_level,
        "at_0618": at_0618,
        "score": score,
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def detect_multi_bar_patterns(
    bars: list[dict],
    bbands: dict | None = None,
    period: int = 20,
    fib_lookback: int = 50,
    gap_threshold_pct: float = 0.01,
    breakout_period: int = 20,
) -> dict:
    """Detect all multi-bar patterns and return a unified result.

    Parameters
    ----------
    bars:
        List of OHLCV dicts with keys ``open``, ``high``, ``low``,
        ``close``, ``volume``.
    bbands:
        Optional pre-computed Bollinger Bands dict (not yet used — bands
        are computed internally if needed).
    period:
        Lookback period for Bollinger Bands and other period-based patterns.
    fib_lookback:
        Number of bars to scan for Fibonacci swing points.
    gap_threshold_pct:
        Minimum gap size as a fraction (default 1 %).
    breakout_period:
        Number of bars for breakout range computation.

    Returns
    -------
    dict with keys ``w_bottom``, ``m_top``, ``bollinger_squeeze``,
    ``breakout``, ``gaps``, ``fibonacci``, and ``summary``.
    """
    w_bottom = detect_w_bottom(bars)
    m_top = detect_m_top(bars)
    bollinger_squeeze = detect_bollinger_squeeze(bars, period=period)
    breakout = detect_breakout(bars, period=breakout_period)
    gaps = detect_gaps(bars, threshold_pct=gap_threshold_pct)
    fibonacci = detect_fibonacci_levels(bars, lookback=fib_lookback)

    # Aggregate summary
    scores = [
        w_bottom.get("score", 0),
        m_top.get("score", 0),
        bollinger_squeeze.get("score", 0),
        breakout.get("score", 0),
        fibonacci.get("score", 0),
    ]
    net_pattern_score = sum(scores)
    total_bullish_signals = sum(1 for s in scores if s > 0)
    total_bearish_signals = sum(1 for s in scores if s < 0)

    return {
        "w_bottom": w_bottom,
        "m_top": m_top,
        "bollinger_squeeze": bollinger_squeeze,
        "breakout": breakout,
        "gaps": gaps,
        "fibonacci": fibonacci,
        "summary": {
            "total_bullish_signals": total_bullish_signals,
            "total_bearish_signals": total_bearish_signals,
            "net_pattern_score": net_pattern_score,
        },
    }
