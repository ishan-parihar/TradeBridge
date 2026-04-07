"""Daily Volatility Breakout strategy.

Based on academic research showing 66.95% win rate on EURUSD D1 (edge ratio 1.75).
Works on D1 timeframe specifically — H4 results were far worse (49-51%).

Target symbols: GBPUSD D1, EURUSD D1, EURJPY D1, GBPJPY D1

Rules (FXAcademy research):
    Entry LONG:
        1. Close at 50-day high
        2. Close in top 25% of day's range
        3. Day's range >= 150% of ATR(15)

    Entry SHORT:
        1. Close at 50-day low
        2. Close in bottom 25% of day's range
        3. Day's range >= 150% of ATR(15)

    Exit:
        Time-based (2-day max hold) — fixed hold period

Quality metric:
    Combines how far above/below the breakout level + ATR ratio
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Compute Average True Range.

    Args:
        df: DataFrame with 'high', 'low', 'close' columns.
        period: ATR lookback period.

    Returns:
        Series of ATR values.
    """
    high = df["high"]
    low = df["low"]
    close = df["close"]

    prev_close = close.shift(1)

    # True range: max of (H-L, |H-prevC|, |L-prevC|)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    # ATR as EMA of true range
    return pd.Series(tr.ewm(span=period, adjust=False).mean())


def generate_vol_breakout_signals(
    df: pd.DataFrame,
    lookback: int = 50,
    atr_period: int = 14,
    atr_mult: float = 1.5,
    range_pct: float = 0.25,
    hold_period: int = 2,
) -> pd.DataFrame:
    """Generate volatility breakout signals.

    Rules:
        1. Price closes at N-day high/low
        2. Close in top/bottom X% of day's range
        3. Day's range >= Y * ATR
        4. Hold for fixed period then exit

    Args:
        df: OHLCV DataFrame with columns: timestamp (or DatetimeIndex),
            open, high, low, close, volume. Must have at least
            'high', 'low', 'close' columns.
        lookback: Lookback period for breakout high/low (default 50).
        atr_period: ATR computation period (default 14).
        atr_mult: Multiplier for ATR threshold (default 1.5 = 150%).
        range_pct: Fraction of day's range for close position
            (default 0.25 = top/bottom 25%).
        hold_period: Number of bars to hold position (default 2).

    Returns:
        DataFrame with columns:
            - timestamp: bar timestamp
            - signal: +1 (long), -1 (short), 0 (no signal/exit)
            - quality: 0-1 signal strength metric
            - template: strategy template name string
    """
    # Validate required columns
    required_cols = {"high", "low", "close"}
    if not required_cols.issubset(df.columns):
        missing = required_cols - set(df.columns)
        raise ValueError(f"Missing required columns: {missing}")

    # Work on a copy to avoid mutating input
    data = df.copy()

    # Ensure datetime index or timestamp column
    if isinstance(data.index, pd.DatetimeIndex):
        data["timestamp"] = data.index
    elif "timestamp" not in data.columns:
        data["timestamp"] = range(len(data))

    # Compute ATR if not already present
    if "atr" not in data.columns:
        data["atr"] = _compute_atr(data, period=atr_period)

    # Compute day's range
    data["day_range"] = data["high"] - data["low"]

    # Compute highest high and lowest low over previous lookback bars (shifted
    # by 1 so current bar is not included — checking breakout of prior range)
    data["highest_high"] = (
        data["high"].shift(1).rolling(window=lookback, min_periods=lookback).max()
    )
    data["lowest_low"] = (
        data["low"].shift(1).rolling(window=lookback, min_periods=lookback).min()
    )

    # Compute range position: where did close land within the day's range?
    # 0.0 = close at low, 1.0 = close at high
    # Protect against division by zero when day_range == 0
    data["range_position"] = np.where(
        data["day_range"] > 0,
        (data["close"] - data["low"]) / data["day_range"],
        0.5,  # neutral when no range
    )

    # Initialize signal columns
    n = len(data)
    signals = np.zeros(n, dtype=int)
    qualities = np.zeros(n, dtype=float)
    templates = np.full(n, "", dtype=object)

    # Track hold counter: 0 = no position, >0 = bars remaining in hold
    hold_counter = 0

    # Strategy requires at least `lookback` bars of data
    start_idx = lookback  # first bar with complete lookback window

    for i in range(start_idx, n):
        row = data.iloc[i]
        atr_val = row["atr"]
        day_range = row["day_range"]
        close = row["close"]
        range_pos = row["range_position"]
        highest = row["highest_high"]
        lowest = row["lowest_low"]

        # Skip if ATR is NaN or zero (edge case protection)
        if pd.isna(atr_val) or atr_val <= 0:
            if hold_counter > 0:
                hold_counter -= 1
                if hold_counter == 0:
                    signals[i] = 0  # exit signal
                    qualities[i] = 0.0
                    templates[i] = "vol_breakout"
            continue

        # Check if currently in a hold period
        if hold_counter > 0:
            hold_counter -= 1
            if hold_counter == 0:
                # Time-based exit
                signals[i] = 0
                qualities[i] = 0.0
                templates[i] = "vol_breakout"
            continue

        # --- Entry logic ---

        # Condition 3: Day's range >= atr_mult * ATR
        range_expanded = day_range >= (atr_mult * atr_val)

        if not range_expanded:
            continue

        # Condition 1 + 2 for LONG:
        #   Close at lookback high AND close in top range_pct of day's range
        is_long_breakout = (close >= highest) and (range_pos >= (1.0 - range_pct))

        # Condition 1 + 2 for SHORT:
        #   Close at lookback low AND close in bottom range_pct of day's range
        is_short_breakout = (close <= lowest) and (range_pos <= range_pct)

        if is_long_breakout:
            signals[i] = 1
            # Quality: how far above breakout level + ATR ratio
            breakout_distance = (close - highest) / atr_val if atr_val > 0 else 0
            atr_ratio = (
                min(day_range / (atr_val * atr_mult), 2.0) / 2.0
            )  # normalize 0-1
            quality = min(1.0, max(0.0, (breakout_distance + atr_ratio) / 2.0))
            qualities[i] = quality
            templates[i] = "vol_breakout"
            hold_counter = hold_period

        elif is_short_breakout:
            signals[i] = -1
            # Quality: how far below breakout level + ATR ratio
            breakout_distance = (lowest - close) / atr_val if atr_val > 0 else 0
            atr_ratio = min(day_range / (atr_val * atr_mult), 2.0) / 2.0
            quality = min(1.0, max(0.0, (breakout_distance + atr_ratio) / 2.0))
            qualities[i] = quality
            templates[i] = "vol_breakout"
            hold_counter = hold_period

    # Build result DataFrame
    result = pd.DataFrame(
        {
            "timestamp": data["timestamp"].values,
            "signal": signals,
            "quality": qualities,
            "template": templates,
        }
    )

    return result
