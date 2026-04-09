"""Volume Analysis Service — detects volume anomalies from OHLCV bar data.

Analyses tick volume from MT5 bars to identify:
- Volume ratio (current vs average)
- Price-volume relationship signals (accumulation, distribution, etc.)
- Volume trend (short vs long SMA)
- Anomalous volume spikes

Pure Python — no numpy/pandas.

Usage:
    result = detect_volume_anomalies(bars, lookback=20)
    # result["volume_tier"] → "extreme_surge" | "strong_surge" | ...
    # result["price_volume_signal"] → "accumulation" | "distribution" | ...
    # result["anomalies"] → list of bars with extreme volume
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_volume_anomalies(
    bars: list[dict],
    lookback: int = 20,
    symbol: str = "unknown",
) -> dict:
    """Detect volume anomalies from OHLCV bar data.

    Args:
        bars: List of OHLCV bar dicts ordered oldest -> newest.
            Each: {"time": str, "open": float, "high": float,
                    "low": float, "close": float, "volume": float}
        lookback: Number of most-recent bars to analyse.
        symbol: Trading symbol name for the result.

    Returns:
        Structured dict with volume analysis, tier, trend, signal, and anomalies.
    """
    if not bars:
        return _empty_result(symbol, lookback, note="insufficient_data")

    # Use only the requested lookback (most recent bars)
    working = bars[-lookback:] if len(bars) > lookback else list(bars)
    n = len(working)

    # Extract volume series (handle missing/None values)
    volumes = [_safe_volume(b) for b in working]
    valid_volumes = [v for v in volumes if v is not None]

    if not valid_volumes:
        return _empty_result(symbol, lookback, note="unknown")

    current_volume = valid_volumes[-1]
    avg_volume = sum(valid_volumes) / len(valid_volumes)

    # Volume ratio and tier
    if avg_volume == 0:
        volume_ratio = 0.0
    else:
        volume_ratio = current_volume / avg_volume

    volume_tier, volume_ratio_score = _classify_volume_tier(volume_ratio)

    # Volume trend (5-bar SMA vs 20-bar SMA)
    volume_trend = _compute_volume_trend(valid_volumes)

    # Price-volume signal
    price_volume_signal, pv_score = _compute_price_volume_signal(
        working, current_volume, avg_volume
    )

    # Anomaly detection
    anomalies = _detect_anomalies(working, valid_volumes, avg_volume)

    # Adjust lookback note
    actual_lookback = lookback
    note = None
    if n < lookback:
        note = f"used {n} of {lookback} bars requested"

    return {
        "symbol": symbol,
        "lookback": actual_lookback,
        "current_volume": current_volume,
        "avg_volume": round(avg_volume, 2),
        "volume_ratio": round(volume_ratio, 2),
        "volume_tier": volume_tier,
        "volume_trend": volume_trend,
        "price_volume_signal": price_volume_signal,
        "score": volume_ratio_score + pv_score,
        "anomalies": anomalies,
        "bars_analyzed": n,
        "note": note,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_volume(bar: dict) -> float | None:
    """Extract volume from a bar dict, returning None if missing or invalid."""
    v = bar.get("volume")
    if v is None:
        return None
    try:
        val = float(v)
        return val if val >= 0 else 0.0
    except (TypeError, ValueError):
        return None


def _classify_volume_tier(ratio: float) -> tuple[str, int]:
    """Classify volume ratio into tier and return (tier_name, score)."""
    if ratio > 3.0:
        return "extreme_surge", 6
    if ratio > 2.0:
        return "strong_surge", 4
    if ratio > 1.3:
        return "elevated", 2
    if ratio < 0.5:
        return "drying_up", -3
    return "normal", 0


def _compute_volume_trend(volumes: list[float]) -> str:
    """Determine volume trend by comparing 5-bar SMA to 20-bar SMA.

    Returns "increasing", "decreasing", or "stable".
    """
    if len(volumes) < 5:
        return "stable"

    short_avg = sum(volumes[-5:]) / 5
    long_avg = sum(volumes) / len(volumes)

    if short_avg > long_avg * 1.1:
        return "increasing"
    if short_avg < long_avg * 0.9:
        return "decreasing"
    return "stable"


def _compute_price_volume_signal(
    bars: list[dict],
    current_volume: float,
    avg_volume: float,
) -> tuple[str, int]:
    """Analyse the last 3 bars for price-volume relationship signals.

    Returns (signal_name, score).
    """
    if len(bars) < 3:
        return "none", 0

    last3 = bars[-3:]

    # Determine price direction of the current bar
    current_open = float(last3[-1].get("open", 0))
    current_close = float(last3[-1].get("close", 0))
    price_up = current_close > current_open
    price_down = current_close < current_open

    # Get volumes for last 3 bars (skip bars without volume)
    vol_list: list[float] = []
    for b in last3:
        v = _safe_volume(b)
        if v is not None:
            vol_list.append(v)

    if len(vol_list) < 3:
        return "none", 0

    v_oldest, v_middle, v_newest = vol_list[-3], vol_list[-2], vol_list[-1]
    volume_declining = v_newest < v_middle < v_oldest
    volume_surging_3x = v_newest > 3.0 * avg_volume
    volume_surging_2x = v_newest > 2.0 * avg_volume
    volume_drying = v_newest < 0.5 * avg_volume

    # Rule 1: price up + volume declining → weakness
    if price_up and volume_declining:
        return "weakness", -3

    # Rule 2: price down + volume surging 3× → distribution
    if price_down and volume_surging_3x:
        return "distribution", -4

    # Rule 3: price up + volume surging 2× → accumulation
    if price_up and volume_surging_2x:
        return "accumulation", 4

    # Rule 4: price down + volume drying → selling exhaustion
    if price_down and volume_drying:
        return "selling_exhaustion", 2

    return "none", 0


def _detect_anomalies(
    bars: list[dict],
    volumes: list[float],
    avg_volume: float,
) -> list[dict[str, Any]]:
    """Scan bars for volume anomalies (volume > 3× average).

    Returns list of anomaly records with bar index, volume details, and price change.
    """
    anomalies: list[dict[str, Any]] = []

    for i, vol in enumerate(volumes):
        if avg_volume == 0:
            continue

        ratio = vol / avg_volume
        if ratio > 3.0:
            bar = bars[i]
            o = float(bar.get("open", 0))
            c = float(bar.get("close", 0))
            if o != 0:
                price_change_pct = round((c - o) / o * 100, 2)
            else:
                price_change_pct = 0.0

            anomalies.append(
                {
                    "bar_index": i,
                    "volume": vol,
                    "avg_volume": round(avg_volume, 2),
                    "ratio": round(ratio, 2),
                    "price_change_pct": price_change_pct,
                }
            )

    return anomalies


def _empty_result(symbol: str, lookback: int, note: str = "insufficient_data") -> dict:
    """Return a default result structure for cases with insufficient data."""
    return {
        "symbol": symbol,
        "lookback": lookback,
        "current_volume": 0.0,
        "avg_volume": 0.0,
        "volume_ratio": 0.0,
        "volume_tier": note,
        "volume_trend": "stable",
        "price_volume_signal": "none",
        "score": 0,
        "anomalies": [],
        "bars_analyzed": 0,
        "note": note,
    }
