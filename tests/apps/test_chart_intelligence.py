from __future__ import annotations

import pytest

from mt5_mcp.services.chart_intelligence import ChartIntelligenceService
from mt5_mcp.schemas.tools import ChartIntelligenceRequest


def _make_bar(
    open_: float,
    high: float,
    low: float,
    close: float,
    time: str = "2025-01-01T00:00:00Z",
    volume: int = 1000,
) -> dict:
    return {
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "time": time,
        "volume": volume,
    }


def _make_uptrend_bars(count: int = 60, base: float = 2600.0) -> list[dict]:
    step = 0.5
    bars = []
    for i in range(count):
        o = base + i * step
        c = o + step * 0.6
        bars.append(_make_bar(o, c + 1.0, o - 0.5, c))
    return bars


def _make_downtrend_bars(count: int = 60, base: float = 2600.0) -> list[dict]:
    step = 0.5
    bars = []
    for i in range(count):
        o = base - i * step
        c = o - step * 0.6
        bars.append(_make_bar(o, o + 0.5, c - 1.0, c))
    return bars


def _make_sideways_bars(count: int = 60, base: float = 2600.0) -> list[dict]:
    bars = []
    for i in range(count):
        o = base + (i % 3 - 1) * 0.3
        c = o + (0.2 if i % 2 == 0 else -0.2)
        bars.append(_make_bar(o, o + 1.0, o - 1.0, c))
    return bars


class TestChartIntelligenceServiceBundle:
    """Full intelligence bundle tests."""

    def test_full_intelligence_bundle(self):
        svc = ChartIntelligenceService()
        bars = _make_uptrend_bars(60)

        result = svc.get_intelligence(
            symbol="XAUUSD",
            timeframe="H1",
            bars_data=bars,
            atr_value=3.5,
            atr_percentile=55.0,
            rsi=62.0,
            ema_fast=2610.0,
            ema_slow=2600.0,
            macd={"main": 0.5, "signal": 0.3, "histogram": 0.2},
            bbands={"upper": 2625.0, "middle": 2610.0, "lower": 2595.0},
            screenshot_data={"base64": "fake_png_data", "width": 1920, "height": 1080},
            include_screenshot_base64=True,
        )

        assert result["symbol"] == "XAUUSD"
        assert result["timeframe"] == "H1"
        assert result["bar_count"] == 60
        assert "timestamp" in result

        assert "screenshot" in result
        assert result["screenshot"]["available"] is True
        assert result["screenshot"]["image_base64"] == "fake_png_data"

        assert "support_resistance" in result
        assert "resistance" in result["support_resistance"]
        assert "support" in result["support_resistance"]

        assert "indicators" in result
        assert "rsi" in result["indicators"]
        assert result["indicators"]["rsi"]["state"] == "neutral"
        assert "macd" in result["indicators"]
        assert result["indicators"]["macd"]["crossover_direction"] == "bullish"
        assert "ema" in result["indicators"]
        assert result["indicators"]["ema"]["alignment"] == "bullish"
        assert "atr" in result["indicators"]

        assert "candlestick_patterns" in result
        assert "patterns" in result["candlestick_patterns"]

        assert "trend_analysis" in result
        assert "short_term" in result["trend_analysis"]
        assert "medium_term" in result["trend_analysis"]

        assert "volatility_bands" in result
        assert result["volatility_bands"]["position"] in (
            "near_upper",
            "upper_half",
            "middle",
            "lower_half",
            "near_lower",
        )

        assert "key_levels" in result
        assert "highest_in_range" in result["key_levels"]
        assert "lowest_in_range" in result["key_levels"]

        assert "meta" in result

    def test_no_screenshot_mode_metadata_only(self):
        svc = ChartIntelligenceService()
        bars = _make_uptrend_bars(30)

        result = svc.get_intelligence(
            symbol="XAUUSD",
            timeframe="H1",
            bars_data=bars,
            include_screenshot_base64=False,
        )

        assert result["screenshot"]["available"] is False
        assert result["screenshot"]["image_base64"] is None

    def test_screenshot_metadata_without_base64(self):
        svc = ChartIntelligenceService()
        bars = _make_uptrend_bars(30)

        result = svc.get_intelligence(
            symbol="XAUUSD",
            timeframe="H1",
            bars_data=bars,
            screenshot_data={"base64": "data", "width": 1920, "height": 1080},
            include_screenshot_base64=False,
        )

        assert result["screenshot"]["available"] is True
        assert result["screenshot"]["image_base64"] is None
        assert result["screenshot"]["width"] == 1920
        assert result["screenshot"]["height"] == 1080


class TestCandlestickPatternDetection:
    """Candlestick pattern detection tests."""

    def test_doji_detection(self):
        svc = ChartIntelligenceService()
        bars = [
            _make_bar(2600, 2610, 2595, 2605),
            _make_bar(2605, 2615, 2600, 2610),
            _make_bar(2610, 2612, 2608, 2610.05),
        ]

        result = svc._detect_candlestick_patterns(bars)
        patterns = [p["pattern"] for p in result["patterns"]]
        assert "doji" in patterns

    def test_bullish_engulfing_detection(self):
        svc = ChartIntelligenceService()
        bars = [
            _make_bar(2610, 2611, 2605, 2606),
            _make_bar(2605, 2606, 2604, 2615),
        ]

        result = svc._detect_candlestick_patterns(bars)
        patterns = [p["pattern"] for p in result["patterns"]]
        assert "bullish_engulfing" in patterns

    def test_bearish_engulfing_detection(self):
        svc = ChartIntelligenceService()
        bars = [
            _make_bar(2600, 2605, 2599, 2604),
            _make_bar(2606, 2607, 2595, 2596),
        ]

        result = svc._detect_candlestick_patterns(bars)
        patterns = [p["pattern"] for p in result["patterns"]]
        assert "bearish_engulfing" in patterns

    def test_hammer_detection(self):
        svc = ChartIntelligenceService()
        bars = [
            _make_bar(2600, 2605, 2595, 2598),
            _make_bar(2600, 2601.2, 2590, 2601),
        ]

        result = svc._detect_candlestick_patterns(bars)
        patterns = [p["pattern"] for p in result["patterns"]]
        assert "hammer" in patterns

    def test_inside_bar_detection(self):
        svc = ChartIntelligenceService()
        bars = [
            _make_bar(2600, 2610, 2590, 2605),
            _make_bar(2601, 2606, 2595, 2603),
        ]

        result = svc._detect_candlestick_patterns(bars)
        patterns = [p["pattern"] for p in result["patterns"]]
        assert "inside_bar" in patterns

    def test_empty_bars_no_patterns(self):
        svc = ChartIntelligenceService()
        result = svc._detect_candlestick_patterns([])
        assert result["patterns"] == []
        assert result["lookback"] == 0


class TestTrendAnalysis:
    """Trend analysis tests."""

    def test_uptrend_detection(self):
        svc = ChartIntelligenceService()
        bars = _make_uptrend_bars(60)

        result = svc._analyze_trend(bars)
        assert result["short_term"]["direction"] in ("up", "sideways")
        assert result["medium_term"]["direction"] in ("up", "sideways")
        assert result["confidence"] > 0

    def test_downtrend_detection(self):
        svc = ChartIntelligenceService()
        bars = _make_downtrend_bars(60)

        result = svc._analyze_trend(bars)
        assert result["short_term"]["direction"] in ("down", "sideways")
        assert result["medium_term"]["direction"] in ("down", "sideways")

    def test_sideways_detection(self):
        svc = ChartIntelligenceService()
        bars = _make_sideways_bars(60)

        result = svc._analyze_trend(bars)
        assert result["short_term"]["direction"] == "sideways"
        assert result["medium_term"]["direction"] == "sideways"

    def test_insufficient_data(self):
        svc = ChartIntelligenceService()
        result = svc._analyze_trend([_make_bar(2600, 2601, 2599, 2600)])
        assert result["short_term"] == "unknown"
        assert result["medium_term"] == "unknown"


class TestBollingerBandPosition:
    """Bollinger Band position detection tests."""

    def test_near_upper_band(self):
        svc = ChartIntelligenceService()
        bars = [_make_bar(2623, 2625, 2622, 2624)]

        result = svc._analyze_volatility_bands(
            bars,
            {"upper": 2625.0, "middle": 2610.0, "lower": 2595.0},
        )
        assert result["position"] == "near_upper"
        assert result["interpretation"] == "overbought_zone"

    def test_near_lower_band(self):
        svc = ChartIntelligenceService()
        bars = [_make_bar(2596, 2598, 2595, 2596)]

        result = svc._analyze_volatility_bands(
            bars,
            {"upper": 2625.0, "middle": 2610.0, "lower": 2595.0},
        )
        assert result["position"] == "near_lower"
        assert result["interpretation"] == "oversold_zone"

    def test_middle_band(self):
        svc = ChartIntelligenceService()
        bars = [_make_bar(2609, 2612, 2608, 2610)]

        result = svc._analyze_volatility_bands(
            bars,
            {"upper": 2625.0, "middle": 2610.0, "lower": 2595.0},
        )
        assert result["position"] == "middle"

    def test_no_band_data(self):
        svc = ChartIntelligenceService()
        result = svc._analyze_volatility_bands([], None)
        assert result["position"] == "unknown"


class TestEmptyBarDataHandling:
    """Empty bar data edge case tests."""

    def test_empty_bars_intelligence(self):
        svc = ChartIntelligenceService()
        result = svc.get_intelligence(symbol="XAUUSD", timeframe="H1", bars_data=[])

        assert result["symbol"] == "XAUUSD"
        assert result["bar_count"] == 0
        assert result["support_resistance"]["method"] == "insufficient_data"
        assert result["candlestick_patterns"]["patterns"] == []
        assert result["trend_analysis"]["short_term"] == "unknown"
        assert result["key_levels"]["highest_in_range"] is None

    def test_none_bars_intelligence(self):
        svc = ChartIntelligenceService()
        result = svc.get_intelligence(symbol="XAUUSD", timeframe="H1")

        assert result["bar_count"] == 0
        assert result["screenshot"]["available"] is False


class TestIndicatorSummary:
    """Individual indicator summary tests."""

    def test_rsi_overbought(self):
        svc = ChartIntelligenceService()
        result = svc._summarize_indicators(rsi=75.0)
        assert result["rsi"]["state"] == "overbought"

    def test_rsi_oversold(self):
        svc = ChartIntelligenceService()
        result = svc._summarize_indicators(rsi=25.0)
        assert result["rsi"]["state"] == "oversold"

    def test_rsi_neutral(self):
        svc = ChartIntelligenceService()
        result = svc._summarize_indicators(rsi=55.0)
        assert result["rsi"]["state"] == "neutral"

    def test_macd_bullish(self):
        svc = ChartIntelligenceService()
        result = svc._summarize_indicators(
            macd={"main": 0.5, "signal": 0.3, "histogram": 0.2}
        )
        assert result["macd"]["crossover_direction"] == "bullish"

    def test_macd_bearish(self):
        svc = ChartIntelligenceService()
        result = svc._summarize_indicators(
            macd={"main": -0.3, "signal": 0.1, "histogram": -0.4}
        )
        assert result["macd"]["crossover_direction"] == "bearish"

    def test_ema_bullish_alignment(self):
        svc = ChartIntelligenceService()
        result = svc._summarize_indicators(ema_fast=2610.0, ema_slow=2600.0)
        assert result["ema"]["alignment"] == "bullish"

    def test_ema_bearish_alignment(self):
        svc = ChartIntelligenceService()
        result = svc._summarize_indicators(ema_fast=2590.0, ema_slow=2600.0)
        assert result["ema"]["alignment"] == "bearish"

    def test_ema_flat_alignment(self):
        svc = ChartIntelligenceService()
        result = svc._summarize_indicators(ema_fast=2600.001, ema_slow=2600.0)
        assert result["ema"]["alignment"] == "flat"

    def test_atr_high_percentile(self):
        svc = ChartIntelligenceService()
        result = svc._summarize_indicators(atr_value=5.0, atr_percentile=85.0)
        assert result["atr"]["state"] == "high"

    def test_atr_low_percentile(self):
        svc = ChartIntelligenceService()
        result = svc._summarize_indicators(atr_value=1.0, atr_percentile=15.0)
        assert result["atr"]["state"] == "low"


class TestChartIntelligenceRequest:
    """Schema validation tests."""

    def test_default_values(self):
        req = ChartIntelligenceRequest(symbol="XAUUSD")
        assert req.timeframe == "H1"
        assert req.width == 1920
        assert req.height == 1080
        assert req.include_screenshot is True
        assert req.include_screenshot_base64 is False
        assert req.bar_count == 100
        assert req.session_id is None
        assert req.strategy_id is None

    def test_custom_values(self):
        req = ChartIntelligenceRequest(
            symbol="EURUSD",
            timeframe="M15",
            width=1280,
            height=720,
            include_screenshot=False,
            bar_count=50,
            session_id="test-session",
            strategy_id="scalp",
        )
        assert req.symbol == "EURUSD"
        assert req.timeframe == "M15"
        assert req.width == 1280
        assert req.height == 720
        assert req.include_screenshot is False
        assert req.bar_count == 50
        assert req.session_id == "test-session"
        assert req.strategy_id == "scalp"


class TestKeyLevels:
    """Key level extraction tests."""

    def test_key_levels_from_trend(self):
        svc = ChartIntelligenceService()
        bars = _make_uptrend_bars(60)

        result = svc._extract_key_levels(bars)
        assert result["highest_in_range"] is not None
        assert result["lowest_in_range"] is not None
        assert result["range_points"] is not None
        assert result["highest_in_range"] > result["lowest_in_range"]

    def test_key_levels_empty(self):
        svc = ChartIntelligenceService()
        result = svc._extract_key_levels([])
        assert result["highest_in_range"] is None
        assert result["lowest_in_range"] is None
        assert result["last_swing_high"] is None
        assert result["last_swing_low"] is None


class TestSupportResistance:
    """Support/resistance extraction tests."""

    def test_sr_with_swing_extrema(self):
        svc = ChartIntelligenceService()
        bars = _make_uptrend_bars(60)
        result = svc._extract_support_resistance(bars)

        assert result["method"] == "swing_extrema_clustering"
        assert len(result["resistance"]) <= 5
        assert len(result["support"]) <= 5

        levels_have_strength = all(
            "strength" in level for level in result["resistance"] + result["support"]
        )
        assert levels_have_strength

    def test_sr_insufficient_data(self):
        svc = ChartIntelligenceService()
        result = svc._extract_support_resistance([_make_bar(2600, 2601, 2599, 2600)])
        assert result["method"] == "insufficient_data"
