"""Chart Intelligence Service — unified agent-friendly chart analysis bundle.

Combines screenshot metadata, support/resistance levels, indicator summary,
candlestick pattern detection, trend analysis, and volatility bands into
a single response — replacing 3+ separate calls.

Usage:
    service = ChartIntelligenceService()
    intel = service.get_intelligence(
        symbol="XAUUSD",
        timeframe="H1",
        bars_data=[...],
        atr_value=1.5,
        rsi=55.0,
        macd={"main": 0.5, "signal": 0.3, "histogram": 0.2},
        ema_fast=2650.0,
        ema_slow=2640.0,
        bbands={"upper": 2660.0, "middle": 2650.0, "lower": 2640.0},
        screenshot_data={"base64": "...", "width": 1920, "height": 1080},
        include_screenshot_base64=False,
    )
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

try:
    from mt5_mcp.services.multi_bar_patterns import detect_multi_bar_patterns
except ImportError:
    detect_multi_bar_patterns = None


class ChartIntelligenceService:
    """Aggregates chart analysis into a single agent-friendly payload.

    The service does NOT call the EA directly — it orchestrates analysis
    on raw data provided by the caller (typically an MCP endpoint that
    handles the bridge communication).
    """

    def __init__(self):
        pass

    def get_intelligence(
        self,
        *,
        symbol: str,
        timeframe: str,
        bars_data: list[dict] | None = None,
        # Indicator values
        atr_value: float | None = None,
        atr_percentile: float | None = None,
        rsi: float | None = None,
        ema_fast: float | None = None,
        ema_slow: float | None = None,
        macd: dict | None = None,
        bbands: dict | None = None,
        # Screenshot
        screenshot_data: dict | None = None,
        include_screenshot_base64: bool = False,
        # Options
        bar_count: int = 100,
        session_id: str | None = None,
        strategy_id: str | None = None,
    ) -> dict:
        """Build unified chart intelligence payload.

        Returns a dict with:
        - Screenshot (metadata or base64)
        - Support/resistance levels
        - Indicator summary
        - Candlestick patterns
        - Trend analysis
        - Volatility bands
        - Recent key levels
        """
        symbol_upper = symbol.upper()
        bars = bars_data or []

        result: dict[str, Any] = {
            "symbol": symbol_upper,
            "timeframe": timeframe,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "bar_count": len(bars),
        }

        # ====== 1. SCREENSHOT ======
        result["screenshot"] = self._build_screenshot(
            screenshot_data, include_screenshot_base64, symbol_upper, timeframe
        )

        # ====== 2. SUPPORT / RESISTANCE ======
        result["support_resistance"] = self._extract_support_resistance(bars)

        # ====== 3. INDICATOR SUMMARY ======
        result["indicators"] = self._summarize_indicators(
            atr_value=atr_value,
            atr_percentile=atr_percentile,
            rsi=rsi,
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            macd=macd,
        )

        # ====== 4. CANDLESTICK PATTERNS ======
        result["candlestick_patterns"] = self._detect_candlestick_patterns(bars)

        # ====== 5. TREND ANALYSIS ======
        result["trend_analysis"] = self._analyze_trend(bars, ema_fast, ema_slow)

        # ====== 6. VOLATILITY BANDS ======
        result["volatility_bands"] = self._analyze_volatility_bands(bars, bbands)

        # ====== 7. RECENT KEY LEVELS ======
        result["key_levels"] = self._extract_key_levels(bars)

        # ====== 8. MULTI-BAR PATTERNS ======
        result["multi_bar_patterns"] = self._detect_multi_bar_patterns(bars, bbands)

        # ====== Meta ======
        result["meta"] = {
            "session_id": session_id,
            "strategy_id": strategy_id,
        }

        return result

    # ----------------------------------------------------------------
    # Private helpers
    # ----------------------------------------------------------------

    def _build_screenshot(
        self,
        screenshot_data: dict | None,
        include_base64: bool,
        symbol: str,
        timeframe: str,
    ) -> dict:
        """Build screenshot section — metadata only or full base64."""
        if not screenshot_data:
            return {"available": False, "image_base64": None}

        width = screenshot_data.get("width", 1920)
        height = screenshot_data.get("height", 1080)
        result: dict[str, Any] = {
            "available": True,
            "width": width,
            "height": height,
            "symbol": symbol,
            "timeframe": timeframe,
        }

        if include_base64 and screenshot_data.get("base64"):
            result["image_base64"] = screenshot_data["base64"]
            result["content_type"] = "image/png"
        else:
            result["image_base64"] = None

        return result

    def _extract_support_resistance(self, bars: list[dict]) -> dict:
        """Extract support/resistance levels from bar data.

        Uses swing highs/lows with strength scoring based on
        how many times price touched/rejected at that level.
        """
        if len(bars) < 5:
            return {
                "support": [],
                "resistance": [],
                "method": "insufficient_data",
            }

        highs = []
        lows = []
        for b in bars:
            h = b.get("high")
            l = b.get("low")
            if h is not None:
                highs.append(h)
            if l is not None:
                lows.append(l)

        if not highs or not lows:
            return {"support": [], "resistance": [], "method": "no_data"}

        # Find swing highs/lows using local extrema
        swing_highs = self._find_swing_highs(bars)
        swing_lows = self._find_swing_lows(bars)

        # Cluster nearby levels
        resistance = self._cluster_levels(swing_highs, tolerance_pct=0.002)
        support = self._cluster_levels(swing_lows, tolerance_pct=0.002)

        # Sort: resistance descending (strongest first), support ascending
        resistance.sort(key=lambda x: x["level"], reverse=True)
        support.sort(key=lambda x: x["level"])

        return {
            "resistance": resistance[:5],  # Top 5
            "support": support[:5],
            "method": "swing_extrema_clustering",
        }

    def _find_swing_highs(self, bars: list[dict], lookback: int = 5) -> list[float]:
        """Find swing highs — local maxima where high is higher than lookback neighbors."""
        swing_highs = []
        for i in range(lookback, len(bars) - lookback):
            current_high = float(bars[i].get("high", 0))
            is_swing = all(
                float(bars[j].get("high", 0)) <= current_high
                for j in range(i - lookback, i + lookback + 1)
                if j != i
            )
            if is_swing:
                swing_highs.append(current_high)
        return swing_highs

    def _find_swing_lows(self, bars: list[dict], lookback: int = 5) -> list[float]:
        """Find swing lows — local minima where low is lower than lookback neighbors."""
        swing_lows = []
        for i in range(lookback, len(bars) - lookback):
            current_low = float(bars[i].get("low", 0))
            is_swing = all(
                float(bars[j].get("low", 0)) >= current_low
                for j in range(i - lookback, i + lookback + 1)
                if j != i
            )
            if is_swing:
                swing_lows.append(current_low)
        return swing_lows

    def _cluster_levels(
        self, levels: list[float], tolerance_pct: float = 0.002
    ) -> list[dict]:
        """Cluster nearby levels and assign strength scores."""
        if not levels:
            return []

        sorted_levels = sorted(levels)
        clusters: list[list[float]] = []

        for level in sorted_levels:
            if not clusters:
                clusters.append([level])
            else:
                last_cluster = clusters[-1]
                cluster_center = sum(last_cluster) / len(last_cluster)
                if abs(level - cluster_center) / cluster_center <= tolerance_pct:
                    last_cluster.append(level)
                else:
                    clusters.append([level])

        result = []
        for cluster in clusters:
            avg_level = sum(cluster) / len(cluster)
            # Strength: more touches = stronger (capped at 1.0)
            strength = min(1.0, len(cluster) / 5.0)
            result.append(
                {
                    "level": round(avg_level, 6),
                    "strength": round(strength, 2),
                    "touches": len(cluster),
                }
            )

        return result

    def _summarize_indicators(
        self,
        *,
        atr_value: float | None = None,
        atr_percentile: float | None = None,
        rsi: float | None = None,
        ema_fast: float | None = None,
        ema_slow: float | None = None,
        macd: dict | None = None,
    ) -> dict:
        """Summarize all indicator values with context."""
        indicators: dict[str, Any] = {}

        # RSI
        if rsi is not None:
            rsi_rounded = round(rsi, 1)
            if rsi > 70:
                rsi_state = "overbought"
            elif rsi < 30:
                rsi_state = "oversold"
            else:
                rsi_state = "neutral"
            indicators["rsi"] = {
                "value": rsi_rounded,
                "state": rsi_state,
            }

        # MACD
        if macd is not None:
            main = macd.get("main") or macd.get("value")
            signal = macd.get("signal")
            histogram = macd.get("histogram")

            if histogram is None and main is not None and signal is not None:
                histogram = main - signal

            crossover_direction = None
            if histogram is not None:
                if histogram > 0:
                    crossover_direction = "bullish"
                elif histogram < 0:
                    crossover_direction = "bearish"
                else:
                    crossover_direction = "neutral"

            indicators["macd"] = {
                "main": main,
                "signal": signal,
                "histogram": round(histogram, 6) if histogram is not None else None,
                "crossover_direction": crossover_direction,
            }

        # EMAs
        if ema_fast is not None or ema_slow is not None:
            ema_info: dict[str, Any] = {}
            if ema_fast is not None:
                ema_info["fast"] = ema_fast
            if ema_slow is not None:
                ema_info["slow"] = ema_slow

            if ema_fast is not None and ema_slow is not None:
                diff_pct = abs(ema_fast - ema_slow) / ema_slow * 100
                if ema_fast > ema_slow:
                    alignment = "bullish"
                elif ema_fast < ema_slow:
                    alignment = "bearish"
                else:
                    alignment = "flat"

                # Flat if very close (< 0.05% difference)
                if diff_pct < 0.05:
                    alignment = "flat"

                ema_info["alignment"] = alignment
                ema_info["diff_pct"] = round(diff_pct, 3)

            indicators["ema"] = ema_info

        # ATR
        if atr_value is not None:
            atr_info: dict[str, Any] = {"value": round(atr_value, 6)}
            if atr_percentile is not None:
                atr_info["percentile"] = round(atr_percentile, 1)
                if atr_percentile > 80:
                    atr_info["state"] = "high"
                elif atr_percentile < 20:
                    atr_info["state"] = "low"
                else:
                    atr_info["state"] = "normal"
            else:
                atr_info["state"] = "unknown"

            indicators["atr"] = atr_info

        return indicators

    def _detect_candlestick_patterns(self, bars: list[dict]) -> dict:
        """Detect recent candlestick patterns.

        Detects: doji, hammer, shooting star, bullish/bearish engulfing,
        inside bar.
        """
        if len(bars) < 2:
            return {"patterns": [], "lookback": min(len(bars), 20)}

        patterns: list[dict] = []
        lookback = min(len(bars), 20)

        for i in range(len(bars) - lookback, len(bars)):
            bar = bars[i]
            open_ = float(bar.get("open", 0))
            high = float(bar.get("high", 0))
            low = float(bar.get("low", 0))
            close = float(bar.get("close", 0))

            body = abs(close - open_)
            range_ = high - low if high > low else 0.0001
            upper_shadow = high - max(open_, close)
            lower_shadow = min(open_, close) - low

            # Doji: very small body relative to range
            if range_ > 0 and body / range_ < 0.1:
                patterns.append(
                    {
                        "pattern": "doji",
                        "bar_index": i,
                        "time": bar.get("time"),
                        "strength": "moderate",
                        "signal": "indecision",
                    }
                )

            # Hammer: small body at top, long lower shadow
            if lower_shadow > body * 2 and upper_shadow < body * 0.5 and close > open_:
                patterns.append(
                    {
                        "pattern": "hammer",
                        "bar_index": i,
                        "time": bar.get("time"),
                        "strength": "strong" if lower_shadow > body * 3 else "moderate",
                        "signal": "bullish_reversal",
                    }
                )

            # Shooting star: small body at bottom, long upper shadow
            if upper_shadow > body * 2 and lower_shadow < body * 0.5 and close < open_:
                patterns.append(
                    {
                        "pattern": "shooting_star",
                        "bar_index": i,
                        "time": bar.get("time"),
                        "strength": "strong" if upper_shadow > body * 3 else "moderate",
                        "signal": "bearish_reversal",
                    }
                )

            # Engulfing (needs previous bar)
            if i > 0:
                prev = bars[i - 1]
                prev_open = float(prev.get("open", 0))
                prev_close = float(prev.get("close", 0))
                prev_body = abs(prev_close - prev_open)

                if prev_body > 0 and body > prev_body * 1.2:
                    # Bullish engulfing
                    if (
                        prev_close < prev_open
                        and close > open_
                        and close > prev_open
                        and open_ < prev_close
                    ):
                        patterns.append(
                            {
                                "pattern": "bullish_engulfing",
                                "bar_index": i,
                                "time": bar.get("time"),
                                "strength": "strong",
                                "signal": "bullish_reversal",
                            }
                        )

                    # Bearish engulfing
                    elif (
                        prev_close > prev_open
                        and close < open_
                        and close < prev_open
                        and open_ > prev_close
                    ):
                        patterns.append(
                            {
                                "pattern": "bearish_engulfing",
                                "bar_index": i,
                                "time": bar.get("time"),
                                "strength": "strong",
                                "signal": "bearish_reversal",
                            }
                        )

            # Inside bar
            if i > 0:
                prev = bars[i - 1]
                prev_high = float(prev.get("high", 0))
                prev_low = float(prev.get("low", 0))

                if high < prev_high and low > prev_low:
                    patterns.append(
                        {
                            "pattern": "inside_bar",
                            "bar_index": i,
                            "time": bar.get("time"),
                            "strength": "moderate",
                            "signal": "consolidation",
                        }
                    )

        # Return most recent patterns (last 5)
        return {
            "patterns": patterns[-5:],
            "lookback": lookback,
            "total_detected": len(patterns),
        }

    def _analyze_trend(
        self,
        bars: list[dict],
        ema_fast: float | None = None,
        ema_slow: float | None = None,
    ) -> dict:
        """Analyze short-term and medium-term trend direction."""
        if len(bars) < 10:
            return {
                "short_term": "unknown",
                "medium_term": "unknown",
                "confidence": 0.0,
                "note": "insufficient_data",
            }

        result: dict[str, Any] = {}

        # Short-term trend (last 10 bars)
        short_term = self._calculate_trend_direction(bars, lookback=10)
        result["short_term"] = short_term

        # Medium-term trend (last 50 bars, or as many as available)
        medium_lookback = min(50, len(bars))
        medium_term = self._calculate_trend_direction(bars, lookback=medium_lookback)
        result["medium_term"] = medium_term

        # EMA confirmation
        if ema_fast is not None and ema_slow is not None:
            if ema_fast > ema_slow:
                ema_alignment = "bullish"
            elif ema_fast < ema_slow:
                ema_alignment = "bearish"
            else:
                ema_alignment = "flat"
            result["ema_alignment"] = ema_alignment

        # Overall confidence
        directions = [short_term["direction"], medium_term["direction"]]
        if len(set(directions)) == 1 and directions[0] != "sideways":
            confidence = 0.8
        elif directions[0] == directions[1]:
            confidence = 0.7 if directions[0] != "sideways" else 0.3
        else:
            confidence = 0.4  # Conflicting signals

        # EMA boosts confidence if aligned
        if result.get("ema_alignment") and result["ema_alignment"] != "flat":
            ema_bullish = result["ema_alignment"] == "bullish"
            if (ema_bullish and "up" in directions) or (
                not ema_bullish and "down" in directions
            ):
                confidence = min(1.0, confidence + 0.1)

        result["confidence"] = confidence

        return result

    def _calculate_trend_direction(self, bars: list[dict], lookback: int) -> dict:
        """Calculate trend direction over a given lookback period.

        Uses linear regression slope normalized by price.
        """
        n = min(lookback, len(bars))
        if n < 5:
            return {"direction": "unknown", "slope": 0.0, "strength": 0.0}

        closes = [
            float(bars[i].get("close", 0)) for i in range(len(bars) - n, len(bars))
        ]

        # Simple linear regression
        x_vals = list(range(n))
        x_mean = sum(x_vals) / n
        y_mean = sum(closes) / n

        numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_vals, closes))
        denominator = sum((x - x_mean) ** 2 for x in x_vals)

        if denominator == 0:
            return {"direction": "flat", "slope": 0.0, "strength": 0.0}

        slope = numerator / denominator
        slope_pct = (slope / y_mean) * 100 if y_mean != 0 else 0

        # Threshold for "sideways"
        if abs(slope_pct) < 0.01:
            direction = "sideways"
        elif slope_pct > 0:
            direction = "up"
        else:
            direction = "down"

        # Strength: 0 to 1 based on slope magnitude
        strength = min(1.0, abs(slope_pct) * 50)

        return {
            "direction": direction,
            "slope": round(slope, 6),
            "slope_pct": round(slope_pct, 4),
            "strength": round(strength, 2),
            "bars_analyzed": n,
        }

    def _analyze_volatility_bands(self, bars: list[dict], bbands: dict | None) -> dict:
        """Analyze price position relative to Bollinger Bands."""
        if not bbands or not bars:
            return {"position": "unknown", "note": "insufficient_data"}

        upper = bbands.get("upper") or bbands.get("upper_band")
        middle = bbands.get("middle") or bbands.get("middle_band")
        lower = bbands.get("lower") or bbands.get("lower_band")

        if upper is None or middle is None or lower is None:
            return {"position": "unknown", "note": "missing_band_data"}

        last_close = float(bars[-1].get("close", 0))
        band_width = upper - lower if upper > lower else 0.0001
        position_pct = ((last_close - lower) / band_width) * 100

        # Determine position
        if position_pct > 90:
            position = "near_upper"
            interpretation = "overbought_zone"
        elif position_pct > 60:
            position = "upper_half"
            interpretation = "bullish_territory"
        elif position_pct > 40:
            position = "middle"
            interpretation = "neutral_zone"
        elif position_pct > 10:
            position = "lower_half"
            interpretation = "bearish_territory"
        else:
            position = "near_lower"
            interpretation = "oversold_zone"

        # Band width as volatility measure
        band_width_pct = (band_width / middle) * 100 if middle > 0 else 0

        return {
            "position": position,
            "position_pct": round(position_pct, 1),
            "interpretation": interpretation,
            "upper": round(upper, 6),
            "middle": round(middle, 6),
            "lower": round(lower, 6),
            "band_width_pct": round(band_width_pct, 2),
            "current_price": round(last_close, 6),
        }

    def _extract_key_levels(self, bars: list[dict]) -> dict:
        """Extract recent key levels: highest/lowest, swing highs/lows."""
        if not bars:
            return {
                "highest_in_range": None,
                "lowest_in_range": None,
                "last_swing_high": None,
                "last_swing_low": None,
                "range_points": None,
            }

        n = len(bars)
        # Highest/lowest in last N bars
        highs = [float(b.get("high", 0)) for b in bars if b.get("high") is not None]
        lows = [float(b.get("low", 0)) for b in bars if b.get("low") is not None]

        highest = max(highs) if highs else None
        lowest = min(lows) if lows else None

        # Swing high/low (most recent)
        swing_high = None
        swing_low = None

        if n >= 5:
            # Find most recent swing high
            for i in range(n - 2, 2, -1):
                h = float(bars[i].get("high", 0))
                if (
                    h > float(bars[i - 1].get("high", 0))
                    and h > float(bars[i - 2].get("high", 0))
                    and h > float(bars[i + 1].get("high", 0))
                    and h > float(bars[i + 2].get("high", 0))
                ):
                    swing_high = h
                    break

            # Find most recent swing low
            for i in range(n - 2, 2, -1):
                l = float(bars[i].get("low", 0))
                if (
                    l < float(bars[i - 1].get("low", 0))
                    and l < float(bars[i - 2].get("low", 0))
                    and l < float(bars[i + 1].get("low", 0))
                    and l < float(bars[i + 2].get("low", 0))
                ):
                    swing_low = l
                    break

        return {
            "highest_in_range": round(highest, 6) if highest else None,
            "lowest_in_range": round(lowest, 6) if lowest else None,
            "last_swing_high": round(swing_high, 6) if swing_high else None,
            "last_swing_low": round(swing_low, 6) if swing_low else None,
            "range_points": round(highest - lowest, 6) if highest and lowest else None,
        }

    def _detect_multi_bar_patterns(self, bars: list[dict], bbands: dict | None) -> dict:
        """Detect multi-bar chart patterns (W-Bottom, M-Top, Squeeze, Breakout, Gap, Fibonacci)."""
        if detect_multi_bar_patterns is None:
            return {
                "available": False,
                "note": "multi_bar_patterns module not available",
            }
        if len(bars) < 20:
            return {
                "available": False,
                "note": "insufficient_data",
                "min_bars": 20,
                "available_bars": len(bars),
            }
        try:
            return detect_multi_bar_patterns(
                bars, bbands=bbands, period=20, fib_lookback=50
            )
        except Exception as e:
            return {"available": False, "error": str(e)}
