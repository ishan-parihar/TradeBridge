"""Market structure detection — BOS, ChoCh, HH/HL/LH/LL labeling.

Analyzes swing highs/lows to determine market structure shifts and trend health.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SwingPoint:
    index: int
    price: float
    kind: str  # "high" or "low"


@dataclass
class MarketStructureResult:
    structure: str  # "bullish", "bearish", "ranging", "transitioning"
    swing_points: list[dict] = field(default_factory=list)
    last_bos: dict | None = None
    last_choch: dict | None = None
    trend_health: str = "unknown"  # "strong", "weakening", "exhausted", "unknown"
    recent_structure: list[str] = field(default_factory=list)


def detect_market_structure(
    bars: list[dict],
    swing_lookback: int = 5,
    confirm_bos_pips: float = 0.0,
) -> MarketStructureResult:
    """Detect market structure from OHLC bars.

    Args:
        bars: List of bar dicts with 'high', 'low', 'close', 'open'.
        swing_lookback: Bars on each side to confirm a swing point.
        confirm_bos_pips: Minimum pips beyond swing to confirm BOS (0 = any breach).

    Returns:
        MarketStructureResult with structure, swings, BOS/ChoCh events.
    """
    if len(bars) < swing_lookback * 2 + 3:
        return MarketStructureResult(structure="ranging", trend_health="unknown")

    swings = _find_swing_points(bars, swing_lookback)
    if len(swings) < 4:
        return MarketStructureResult(
            structure="ranging",
            swing_points=[
                {"index": s.index, "price": s.price, "kind": s.kind} for s in swings
            ],
            trend_health="unknown",
        )

    events = _label_structure(swings, bars, confirm_bos_pips)
    structure = _determine_structure(events, swings)
    health = _assess_trend_health(swings, bars)

    return MarketStructureResult(
        structure=structure,
        swing_points=[
            {"index": s.index, "price": s.price, "kind": s.kind} for s in swings[-12:]
        ],
        last_bos=events.get("last_bos"),
        last_choch=events.get("last_choch"),
        trend_health=health,
        recent_structure=events.get("recent_structure", []),
    )


def _find_swing_points(bars: list[dict], lookback: int) -> list[SwingPoint]:
    """Find swing highs and lows using lookback windows."""
    swings: list[SwingPoint] = []
    n = len(bars)

    for i in range(lookback, n - lookback):
        bar = bars[i]
        high = float(bar.get("high", 0))
        low = float(bar.get("low", 0))

        is_high = all(
            float(bars[j].get("high", 0)) <= high
            for j in range(max(0, i - lookback), min(n, i + lookback + 1))
            if j != i
        )
        is_low = all(
            float(bars[j].get("low", 0)) >= low
            for j in range(max(0, i - lookback), min(n, i + lookback + 1))
            if j != i
        )

        if is_high:
            swings.append(SwingPoint(index=i, price=high, kind="high"))
        elif is_low:
            swings.append(SwingPoint(index=i, price=low, kind="low"))

    return swings


def _label_structure(
    swings: list[SwingPoint],
    bars: list[dict],
    confirm_pips: float,
) -> dict:
    """Label HH/HL/LH/LL sequence and detect BOS/ChoCh."""
    result: dict = {"recent_structure": []}
    if len(swings) < 4:
        return result

    point = float(bars[0].get("point", 0.00001)) if bars else 0.00001
    confirm_threshold = confirm_pips * point if confirm_pips > 0 else 0

    highs = [s for s in swings if s.kind == "high"]
    lows = [s for s in swings if s.kind == "low"]

    for i in range(1, len(highs)):
        if highs[i].price > highs[i - 1].price + confirm_threshold:
            result["recent_structure"].append("HH")
        elif highs[i].price < highs[i - 1].price - confirm_threshold:
            result["recent_structure"].append("LH")

    for i in range(1, len(lows)):
        if lows[i].price > lows[i - 1].price + confirm_threshold:
            result["recent_structure"].append("HL")
        elif lows[i].price < lows[i - 1].price - confirm_threshold:
            result["recent_structure"].append("LL")

    recent = result["recent_structure"][-8:]

    bos_pattern_bullish = ["HH", "HL"]
    bos_pattern_bearish = ["LL", "LH"]

    for i in range(len(recent) - 1):
        if recent[i : i + 2] == bos_pattern_bullish:
            result["last_bos"] = {"type": "bullish", "label": "HH+HL", "index": i}
        elif recent[i : i + 2] == bos_pattern_bearish:
            result["last_bos"] = {"type": "bearish", "label": "LL+LH", "index": i}

    if len(recent) >= 3:
        last_three = recent[-3:]
        if last_three == ["HH", "HL", "LL"]:
            result["last_choch"] = {"type": "bullish_to_bearish", "pattern": "HH-HL-LL"}
        elif last_three == ["LL", "LH", "HH"]:
            result["last_choch"] = {"type": "bearish_to_bullish", "pattern": "LL-LH-HH"}

    return result


def _determine_structure(events: dict, swings: list[SwingPoint]) -> str:
    """Determine overall structure from recent patterns."""
    recent = events.get("recent_structure", [])
    if not recent:
        return "ranging"

    last_4 = recent[-4:]
    hh_count = last_4.count("HH") + last_4.count("HL")
    ll_count = last_4.count("LL") + last_4.count("LH")

    choch = events.get("last_choch")
    if choch:
        return "transitioning"

    if hh_count >= 3:
        return "bullish"
    if ll_count >= 3:
        return "bearish"
    return "ranging"


def _assess_trend_health(swings: list[SwingPoint], bars: list[dict]) -> str:
    """Assess if the current trend is strong, weakening, or exhausted."""
    if len(swings) < 6:
        return "unknown"

    highs = [s for s in swings if s.kind == "high"]
    lows = [s for s in swings if s.kind == "low"]

    if len(highs) < 3 or len(lows) < 3:
        return "unknown"

    recent_highs = highs[-3:]
    recent_lows = lows[-3:]

    if len(recent_highs) >= 2:
        high_diffs = [
            recent_highs[i].price - recent_highs[i - 1].price
            for i in range(1, len(recent_highs))
        ]
        if len(high_diffs) >= 2 and high_diffs[-1] < high_diffs[0] * 0.3:
            return "weakening"
        if len(high_diffs) >= 2 and high_diffs[-1] <= 0:
            return "exhausted"

    if len(recent_lows) >= 2:
        low_diffs = [
            recent_lows[i].price - recent_lows[i - 1].price
            for i in range(1, len(recent_lows))
        ]
        if len(low_diffs) >= 2 and abs(low_diffs[-1]) < abs(low_diffs[0]) * 0.3:
            return "weakening"
        if len(low_diffs) >= 2 and low_diffs[-1] >= 0:
            return "exhausted"

    return "strong"
