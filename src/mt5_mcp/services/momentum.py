"""Momentum Penalty Service — computes chase/exhaustion risk from OHLCV bars.

Prevents AI agents from chasing exhaustion moves by scoring:
- ATR-based chase penalty (current bar range vs ATR)
- Percentile-based exhaustion (current bar range vs 95th percentile of lookback)
- RSI-based signal degradation (overbought/oversold adjustments)

Pure Python — no numpy/pandas.

Usage:
    result = compute_momentum_penalty(bars, rsi=55, atr=2.0, lookback=50)
    # result["chase_tier"] → "extreme_chase" | "strong_chase" | "moderate_chase" | "normal"
    # result["recommendation"] → "avoid_entry" | "reduce_size" | "caution" | "normal"
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_momentum_penalty(
    bars: list[dict],
    rsi: float | None = None,
    atr: float | None = None,
    lookback: int = 50,
) -> dict:
    """Compute a momentum penalty/bonus to prevent chasing exhaustion moves.

    Args:
        bars: List of OHLCV bar dicts ordered oldest → newest.
            Each: {"time": str, "open": float, "high": float,
                    "low": float, "close": float, "volume": float}
        rsi: Current RSI value (0-100 scale). None to skip RSI analysis.
        atr: Current ATR(14) value. None or zero to skip ATR-based chase penalty.
        lookback: Number of bars used for percentile-based exhaustion analysis.

    Returns:
        Structured dict with chase tier, exhaustion risk, RSI signal,
        individual penalties, total penalty, and entry recommendation.
    """
    if not bars:
        return _default_result(rsi, atr, note="insufficient_data")

    # Use only the requested lookback (most recent bars)
    working = bars[-lookback:] if len(bars) > lookback else list(bars)
    n = len(working)

    if n < 2:
        return _default_result(rsi, atr, note="insufficient_data")

    # Current bar is the most recent
    current_bar = working[-1]
    current_range = _bar_range(current_bar)

    # ATR-based chase penalty
    chase_tier, chase_penalty, range_atr_ratio = _compute_chase_penalty(
        current_range, atr
    )

    # Percentile-based exhaustion
    exhaustion_risk, exhaustion_penalty = _compute_exhaustion_penalty(
        working, current_range, n
    )

    # RSI-based signal degradation
    rsi_signal, rsi_penalty = _compute_rsi_signal(rsi)

    # Total penalty (sum of all components)
    total_penalty = chase_penalty + exhaustion_penalty + rsi_penalty

    # Overall recommendation
    recommendation = _recommendation(total_penalty)

    return {
        "chase_tier": chase_tier,
        "chase_penalty": chase_penalty,
        "range_atr_ratio": round(range_atr_ratio, 2),
        "exhaustion_risk": exhaustion_risk,
        "exhaustion_penalty": exhaustion_penalty,
        "rsi_signal": rsi_signal,
        "rsi_penalty": rsi_penalty,
        "total_penalty": total_penalty,
        "recommendation": recommendation,
        "rsi": rsi,
        "atr": atr,
    }


# ---------------------------------------------------------------------------
# Scoring components
# ---------------------------------------------------------------------------


def _compute_chase_penalty(
    current_range: float,
    atr: float | None,
) -> tuple[str, int, float]:
    """Compute ATR-based chase penalty.

    Returns (tier_name, penalty, range_atr_ratio).
    """
    if atr is None or atr <= 0:
        return "normal", 0, 0.0

    range_atr_ratio = current_range / atr

    if range_atr_ratio >= 2.0:
        return "extreme_chase", -12, range_atr_ratio
    if range_atr_ratio >= 1.5:
        return "strong_chase", -6, range_atr_ratio
    if range_atr_ratio >= 1.0:
        return "moderate_chase", -3, range_atr_ratio
    return "normal", 0, range_atr_ratio


def _compute_exhaustion_penalty(
    bars: list[dict],
    current_range: float,
    n: int,
) -> tuple[bool, int]:
    """Compute percentile-based exhaustion penalty.

    If current bar range exceeds the 95th percentile of all bar ranges
    in the lookback window, flag exhaustion risk.

    Returns (exhaustion_risk_bool, penalty).
    """
    if n < 5:
        # Too few bars for meaningful percentile — skip
        return False, 0

    # Compute range for every bar in the lookback
    ranges = [_bar_range(b) for b in bars]
    ranges_sorted = sorted(ranges)

    # 95th percentile via nearest-rank method
    percentile_index = int(0.95 * len(ranges_sorted))
    # Clamp to valid index
    percentile_index = min(percentile_index, len(ranges_sorted) - 1)
    p95 = ranges_sorted[percentile_index]

    if current_range > p95:
        return True, -4
    return False, 0


def _compute_rsi_signal(
    rsi: float | None,
) -> tuple[str, int]:
    """Compute RSI-based signal degradation.

    Returns (signal_name, penalty).
    Positive penalty = bonus (contrarian opportunity).
    """
    if rsi is None:
        return "neutral", 0

    if rsi > 80:
        return "overbought", -4
    if rsi > 70:
        return "elevated", -2
    if rsi < 20:
        return "oversold", 4
    if rsi < 30:
        return "approaching_oversold", 2
    return "neutral", 0


def _recommendation(total_penalty: int) -> str:
    """Map total penalty to an entry recommendation."""
    if total_penalty <= -10:
        return "avoid_entry"
    if total_penalty <= -6:
        return "reduce_size"
    if total_penalty <= -3:
        return "caution"
    return "normal"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bar_range(bar: dict) -> float:
    """Compute the high-low range of a bar."""
    high = float(bar.get("high", 0))
    low = float(bar.get("low", 0))
    return max(high - low, 0.0)


def _default_result(
    rsi: float | None,
    atr: float | None,
    note: str = "insufficient_data",
) -> dict:
    """Return a default result structure for cases with insufficient data."""
    return {
        "chase_tier": note,
        "chase_penalty": 0,
        "range_atr_ratio": 0.0,
        "exhaustion_risk": False,
        "exhaustion_penalty": 0,
        "rsi_signal": "neutral",
        "rsi_penalty": 0,
        "total_penalty": 0,
        "recommendation": "normal",
        "rsi": rsi,
        "atr": atr,
    }
