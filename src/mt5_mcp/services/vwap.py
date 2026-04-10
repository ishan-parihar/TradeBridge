"""VWAP and Volume-at-Price approximation for forex markets.

Uses tick volume as a proxy for real volume to compute VWAP and
volume-at-price profiles — institutional reference prices for forex CFDs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class VWAPResult:
    current_vwap: float
    vwap_deviation_upper: float
    vwap_deviation_lower: float
    distance_from_vwap_pct: float
    price_position: str
    volume_type: str = "tick_volume"
    vwap_series: list[dict] = field(default_factory=list)


@dataclass
class VolumeAtPriceResult:
    poc: float
    value_area_high: float
    value_area_low: float
    value_area_width: float
    price_distribution: list[dict] = field(default_factory=list)
    current_price_position: str = "unknown"
    volume_type: str = "tick_volume"


def compute_vwap(
    bars: list[dict],
    std_dev_multiplier: float = 2.0,
) -> VWAPResult:
    """Compute VWAP from OHLCV bars using tick volume as proxy.

    VWAP = Sum(typical_price * volume) / Sum(volume)
    typical_price = (high + low + close) / 3
    """
    if not bars or len(bars) < 5:
        return VWAPResult(
            current_vwap=0,
            vwap_deviation_upper=0,
            vwap_deviation_lower=0,
            distance_from_vwap_pct=0,
            price_position="unknown",
        )

    cum_vol = 0.0
    cum_tp_vol = 0.0
    deviations: list[float] = []
    vwap_series: list[dict] = []

    for i, bar in enumerate(bars):
        high = float(bar.get("high", 0))
        low = float(bar.get("low", 0))
        close = float(bar.get("close", 0))
        volume = float(bar.get("volume", 1))
        if volume == 0:
            volume = 1

        tp = (high + low + close) / 3
        cum_vol += volume
        cum_tp_vol += tp * volume

        if cum_vol > 0:
            vwap = cum_tp_vol / cum_vol
            deviations.append(abs(close - vwap))
            vwap_series.append({"index": i, "vwap": round(vwap, 6)})
        else:
            vwap_series.append({"index": i, "vwap": None})

    if not cum_vol:
        return VWAPResult(
            current_vwap=0,
            vwap_deviation_upper=0,
            vwap_deviation_lower=0,
            distance_from_vwap_pct=0,
            price_position="unknown",
        )

    current_vwap = cum_tp_vol / cum_vol
    last_close = float(bars[-1]["close"])

    std_dev = (
        math.sqrt(sum(d * d for d in deviations) / len(deviations)) if deviations else 0
    )

    distance_pct = (
        ((last_close - current_vwap) / current_vwap * 100) if current_vwap else 0
    )

    if abs(distance_pct) < 0.05:
        position = "at_vwap"
    elif distance_pct > 0:
        position = "above_vwap"
    else:
        position = "below_vwap"

    return VWAPResult(
        current_vwap=round(current_vwap, 6),
        vwap_deviation_upper=round(current_vwap + std_dev_multiplier * std_dev, 6),
        vwap_deviation_lower=round(current_vwap - std_dev_multiplier * std_dev, 6),
        distance_from_vwap_pct=round(distance_pct, 4),
        price_position=position,
        vwap_series=vwap_series[-50:],
    )


def compute_volume_at_price(
    bars: list[dict],
    num_bins: int = 20,
) -> VolumeAtPriceResult:
    """Compute volume-at-price profile using tick volume.

    Buckets price levels into bins and aggregates volume per bin.
    Finds Point of Control (POC) and Value Area (70% of total volume).
    """
    if not bars or len(bars) < 10:
        return VolumeAtPriceResult(
            poc=0,
            value_area_high=0,
            value_area_low=0,
            value_area_width=0,
        )

    all_highs = [float(b.get("high", 0)) for b in bars]
    all_lows = [float(b.get("low", 0)) for b in bars]
    price_range = max(all_highs) - min(all_lows)
    if price_range == 0:
        price_range = 0.0001

    bin_size = price_range / num_bins
    min_price = min(all_lows)

    bins: list[float] = [0.0] * num_bins

    for bar in bars:
        high = float(bar.get("high", 0))
        low = float(bar.get("low", 0))
        volume = float(bar.get("volume", 1))
        if volume == 0:
            volume = 1

        bin_low = int((low - min_price) / bin_size)
        bin_high = int((high - min_price) / bin_size)
        bin_low = max(0, min(bin_low, num_bins - 1))
        bin_high = max(0, min(bin_high, num_bins - 1))

        if bin_low == bin_high:
            bins[bin_low] += volume
        else:
            vol_per_bin = volume / (bin_high - bin_low + 1)
            for b in range(bin_low, bin_high + 1):
                bins[b] += vol_per_bin

    total_volume = sum(bins)
    if total_volume == 0:
        return VolumeAtPriceResult(
            poc=0,
            value_area_high=0,
            value_area_low=0,
            value_area_width=0,
        )

    poc_index = bins.index(max(bins))
    poc = round(min_price + (poc_index + 0.5) * bin_size, 6)

    sorted_indices = sorted(range(num_bins), key=lambda i: bins[i], reverse=True)
    cumulative = 0.0
    value_area_bins: set[int] = set()
    target = total_volume * 0.7

    for idx in sorted_indices:
        cumulative += bins[idx]
        value_area_bins.add(idx)
        if cumulative >= target:
            break

    va_bins = sorted(value_area_bins)
    va_low = round(min_price + va_bins[0] * bin_size, 6)
    va_high = round(min_price + (va_bins[-1] + 1) * bin_size, 6)

    last_close = float(bars[-1]["close"])
    if last_close > va_high:
        pos = "above_value_area"
    elif last_close < va_low:
        pos = "below_value_area"
    elif abs(last_close - poc) < bin_size * 0.5:
        pos = "at_poc"
    else:
        pos = "inside_value_area"

    distribution = [
        {
            "price_low": round(min_price + i * bin_size, 6),
            "price_high": round(min_price + (i + 1) * bin_size, 6),
            "volume": round(bins[i], 2),
            "is_poc": i == poc_index,
            "is_value_area": i in value_area_bins,
        }
        for i in range(num_bins)
    ]

    return VolumeAtPriceResult(
        poc=poc,
        value_area_high=va_high,
        value_area_low=va_low,
        value_area_width=round(va_high - va_low, 6),
        price_distribution=distribution,
        current_price_position=pos,
    )
