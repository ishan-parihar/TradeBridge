from __future__ import annotations

import pytest

from mt5_mcp.services.opportunity_rank import OpportunityRanker


def _make_snapshot(
    symbol: str = "XAUUSD",
    regime: str = "trending_up",
    regime_confidence: float = 0.8,
    spread_ratio_atr: float = 0.05,
    atr_value: float = 3.0,
    atr_percentile: float = 55.0,
    rsi_value: float = 62.0,
    ema_alignment: str = "bullish",
    macd_histogram: float = 0.2,
    is_market_open: bool = True,
    current_sessions: list[str] | None = None,
    active_overlaps: list[str] | None = None,
    calendar_is_blackout: bool = False,
    calendar_events: list[dict] | None = None,
    positions: list[dict] | None = None,
) -> dict:
    """Build a minimal snapshot dict for testing."""
    default_sessions = ["london", "new_york"]
    default_overlaps = ["london_ny_overlap"]
    return {
        "symbol": symbol,
        "price": {
            "spread_ratio_atr": spread_ratio_atr,
        },
        "indicators": {
            "atr": {"value": atr_value, "percentile": atr_percentile},
            "rsi": {"value": rsi_value, "state": "neutral"},
            "ema_alignment": ema_alignment,
            "macd": {"histogram": macd_histogram},
        },
        "regime": {
            "regime": regime,
            "confidence": regime_confidence,
        },
        "session_context": {
            "is_market_open": is_market_open,
            "current_sessions": current_sessions
            if current_sessions is not None
            else default_sessions,
            "active_overlaps": active_overlaps
            if active_overlaps is not None
            else default_overlaps,
        },
        "calendar": {
            "is_blackout": calendar_is_blackout,
            "upcoming_events": calendar_events or [],
        },
    }


class TestOpportunityRankerBasic:
    """Basic ranking functionality tests."""

    def test_empty_symbol_list_returns_empty(self):
        ranker = OpportunityRanker()
        result = ranker.rank(symbols=[], snapshots={})
        assert result == []

    def test_missing_snapshot_gets_neutral_scores(self):
        ranker = OpportunityRanker()
        result = ranker.rank(symbols=["XAUUSD"], snapshots={})
        assert len(result) == 1
        assert result[0]["symbol"] == "XAUUSD"
        # Empty snapshot → neutral factors → score ~50, which meets default threshold
        assert result[0]["score"] > 0
        assert "factors" in result[0]

    def test_ranking_with_mixed_quality_symbols(self):
        ranker = OpportunityRanker()
        snapshots = {
            "XAUUSD": _make_snapshot(
                regime="trending_up",
                spread_ratio_atr=0.03,
                atr_percentile=60.0,
                active_overlaps=["london_ny_overlap"],
                rsi_value=62.0,
                ema_alignment="bullish",
                macd_histogram=0.2,
            ),
            "EURUSD": _make_snapshot(
                regime="ranging",
                spread_ratio_atr=0.12,
                atr_percentile=25.0,
                current_sessions=["sydney"],
                active_overlaps=[],
                rsi_value=50.0,
                ema_alignment=None,
                macd_histogram=0.0,
            ),
        }
        result = ranker.rank(symbols=["XAUUSD", "EURUSD"], snapshots=snapshots)

        assert len(result) == 2
        # XAUUSD should rank higher
        assert result[0]["symbol"] == "XAUUSD"
        assert result[1]["symbol"] == "EURUSD"
        assert result[0]["score"] > result[1]["score"]
        assert result[0]["recommendation"] == "trade"


class TestSkipReasons:
    """Tests for skip reason logic."""

    def test_calendar_blackout_excludes_symbols(self):
        ranker = OpportunityRanker()
        snapshot = _make_snapshot(
            calendar_is_blackout=True,
        )
        result = ranker.rank(symbols=["XAUUSD"], snapshots={"XAUUSD": snapshot})
        assert len(result) == 1
        assert result[0]["skip_reason"] == "calendar_blackout"
        assert result[0]["recommendation"] == "skip"

    def test_spread_too_wide_excludes_symbols(self):
        ranker = OpportunityRanker()
        snapshot = _make_snapshot(
            spread_ratio_atr=0.25,  # 25% of ATR — well above 20% threshold
        )
        result = ranker.rank(symbols=["XAUUSD"], snapshots={"XAUUSD": snapshot})
        assert len(result) == 1
        assert result[0]["skip_reason"] == "spread_too_wide"
        assert result[0]["recommendation"] == "skip"

    def test_market_closed_excludes_symbols(self):
        ranker = OpportunityRanker()
        snapshot = _make_snapshot(
            is_market_open=False,
            current_sessions=[],
            active_overlaps=[],
        )
        result = ranker.rank(symbols=["XAUUSD"], snapshots={"XAUUSD": snapshot})
        assert len(result) == 1
        assert result[0]["skip_reason"] == "market_closed"
        assert result[0]["recommendation"] == "skip"

    def test_low_volatility_excludes_symbols(self):
        ranker = OpportunityRanker()
        snapshot = _make_snapshot(
            atr_percentile=15.0,  # Below 30th percentile
        )
        result = ranker.rank(symbols=["XAUUSD"], snapshots={"XAUUSD": snapshot})
        assert len(result) == 1
        assert result[0]["skip_reason"] == "low_volatility"
        assert result[0]["recommendation"] == "skip"

    def test_ranging_market_excludes_symbols(self):
        ranker = OpportunityRanker()
        snapshot = _make_snapshot(
            regime="ranging",
        )
        result = ranker.rank(symbols=["XAUUSD"], snapshots={"XAUUSD": snapshot})
        assert len(result) == 1
        assert result[0]["skip_reason"] == "ranging_market"
        assert result[0]["recommendation"] == "skip"

    def test_all_symbols_below_threshold_returns_skip_reasons(self):
        ranker = OpportunityRanker()
        snapshots = {
            "XAUUSD": _make_snapshot(
                regime="ranging",
                spread_ratio_atr=0.15,
                atr_percentile=20.0,
                current_sessions=["sydney"],
                active_overlaps=[],
            ),
            "EURUSD": _make_snapshot(
                regime="unknown",
                spread_ratio_atr=0.18,
                atr_percentile=10.0,
                current_sessions=[],
                active_overlaps=[],
            ),
        }
        result = ranker.rank(
            symbols=["XAUUSD", "EURUSD"],
            snapshots=snapshots,
            min_score=50.0,
        )
        assert len(result) == 2
        for r in result:
            assert r["skip_reason"] is not None
            assert r["recommendation"] == "skip"


class TestPortfolioOverlap:
    """Tests for portfolio overlap detection."""

    def test_portfolio_overlap_detection(self):
        ranker = OpportunityRanker()
        snapshot = _make_snapshot()
        portfolio_positions = [
            {
                "symbol": "XAUUSD",
                "side": "buy",
                "volume": 0.10,
                "entry_price": 2650.0,
            }
        ]
        result = ranker.rank(
            symbols=["XAUUSD", "EURUSD"],
            snapshots={
                "XAUUSD": snapshot,
                "EURUSD": _make_snapshot(symbol="EURUSD"),
            },
            portfolio_positions=portfolio_positions,
        )
        xau = next(r for r in result if r["symbol"] == "XAUUSD")
        assert xau["skip_reason"] == "already_exposed"
        assert xau["recommendation"] == "skip"
        assert xau["score"] == 0.0

        # EURUSD should still be ranked normally
        eur = next(r for r in result if r["symbol"] == "EURUSD")
        assert eur["skip_reason"] is None or eur["skip_reason"] != "already_exposed"


class TestSessionQuality:
    """Tests for session quality impact on ranking."""

    def test_session_quality_affects_ranking(self):
        ranker = OpportunityRanker()

        london_ny = _make_snapshot(
            symbol="XAUUSD",
            active_overlaps=["london_ny_overlap"],
            current_sessions=["london", "new_york"],
        )
        sydney_only = _make_snapshot(
            symbol="EURUSD",
            active_overlaps=[],
            current_sessions=["sydney"],
        )
        closed = _make_snapshot(
            symbol="GBPUSD",
            is_market_open=False,
            current_sessions=[],
            active_overlaps=[],
        )

        snapshots = {
            "XAUUSD": london_ny,
            "EURUSD": sydney_only,
            "GBPUSD": closed,
        }
        result = ranker.rank(
            symbols=["XAUUSD", "EURUSD", "GBPUSD"],
            snapshots=snapshots,
        )

        scores = {r["symbol"]: r["score"] for r in result}
        # London/NY overlap should beat Sydney
        assert scores["XAUUSD"] > scores["EURUSD"]
        # Closed market should be lowest
        assert scores["GBPUSD"] == 0.0 or result[-1]["symbol"] == "GBPUSD"


class TestFactorScoring:
    """Service-level: individual factor scoring tests."""

    def setup_method(self):
        self.ranker = OpportunityRanker()

    def test_regime_scoring_trending(self):
        snapshot = _make_snapshot(regime="trending_up")
        score = self.ranker._score_regime(snapshot)
        assert score == 100.0

    def test_regime_scoring_compressing(self):
        snapshot = _make_snapshot(regime="compressing")
        score = self.ranker._score_regime(snapshot)
        assert score == 60.0

    def test_regime_scoring_ranging(self):
        snapshot = _make_snapshot(regime="ranging")
        score = self.ranker._score_regime(snapshot)
        assert score == 20.0

    def test_regime_scoring_unknown(self):
        snapshot = {"regime": {}}
        score = self.ranker._score_regime(snapshot)
        assert score == 0.0

    def test_spread_atr_scoring_tight(self):
        snapshot = _make_snapshot(spread_ratio_atr=0.03)  # 3%
        score = self.ranker._score_spread_atr(snapshot)
        assert score == 100.0

    def test_spread_atr_scoring_wide(self):
        snapshot = _make_snapshot(spread_ratio_atr=0.25)  # 25%
        score = self.ranker._score_spread_atr(snapshot)
        assert score == 0.0

    def test_spread_atr_scoring_mid(self):
        snapshot = _make_snapshot(spread_ratio_atr=0.125)  # 12.5%
        score = self.ranker._score_spread_atr(snapshot)
        assert 0 < score < 100

    def test_spread_atr_scoring_unknown(self):
        snapshot = {"price": {}}
        score = self.ranker._score_spread_atr(snapshot)
        assert score == 50.0

    def test_volatility_scoring_normal(self):
        snapshot = _make_snapshot(atr_percentile=60.0)
        score = self.ranker._score_volatility(snapshot)
        assert score == 100.0

    def test_volatility_scoring_compressed(self):
        snapshot = _make_snapshot(atr_percentile=15.0)
        score = self.ranker._score_volatility(snapshot)
        assert score == 30.0

    def test_volatility_scoring_extreme(self):
        snapshot = _make_snapshot(atr_percentile=98.0)
        score = self.ranker._score_volatility(snapshot)
        assert 0 < score < 100
        assert score < 100  # Extreme is penalized

    def test_volatility_scoring_transition(self):
        snapshot = _make_snapshot(atr_percentile=40.0)
        score = self.ranker._score_volatility(snapshot)
        assert 30.0 < score < 100.0

    def test_confluence_scoring_agreement(self):
        snapshot = _make_snapshot(
            rsi_value=65.0,
            ema_alignment="bullish",
            macd_histogram=0.2,
        )
        score = self.ranker._score_confluence(snapshot)
        assert score == 100.0

    def test_confluence_scoring_contradictory(self):
        snapshot = _make_snapshot(
            rsi_value=35.0,  # bearish (< 40)
            ema_alignment="bullish",
            macd_histogram=-0.2,  # bearish
        )
        score = self.ranker._score_confluence(snapshot)
        assert score == 10.0

    def test_confluence_scoring_mixed(self):
        snapshot = _make_snapshot(
            rsi_value=50.0,  # neutral
            ema_alignment="bullish",
            macd_histogram=0.2,  # bullish
        )
        score = self.ranker._score_confluence(snapshot)
        assert 50.0 <= score < 100.0

    def test_session_scoring_london_ny_overlap(self):
        snapshot = _make_snapshot(
            active_overlaps=["london_ny_overlap"],
            current_sessions=["london", "new_york"],
        )
        score = self.ranker._score_session(snapshot)
        assert score == 100.0

    def test_session_scoring_london_only(self):
        snapshot = _make_snapshot(
            active_overlaps=[],
            current_sessions=["london"],
        )
        score = self.ranker._score_session(snapshot)
        assert score == 70.0

    def test_session_scoring_sydney_only(self):
        snapshot = _make_snapshot(
            active_overlaps=[],
            current_sessions=["sydney"],
        )
        score = self.ranker._score_session(snapshot)
        assert score == 40.0

    def test_session_scoring_closed(self):
        snapshot = _make_snapshot(
            is_market_open=False,
            current_sessions=[],
            active_overlaps=[],
        )
        score = self.ranker._score_session(snapshot)
        assert score == 0.0

    def test_calendar_scoring_no_events(self):
        snapshot = _make_snapshot(
            calendar_is_blackout=False,
            calendar_events=[],
        )
        score = self.ranker._score_calendar(snapshot)
        assert score == 100.0

    def test_calendar_scoring_blackout(self):
        snapshot = _make_snapshot(calendar_is_blackout=True)
        score = self.ranker._score_calendar(snapshot)
        assert score == 0.0

    def test_weighted_score_calculation(self):
        factors = {
            "regime_clarity": 100.0,
            "spread_atr_ratio": 100.0,
            "volatility_usability": 100.0,
            "session_quality": 100.0,
            "confluence": 100.0,
            "portfolio_overlap": 100.0,
            "calendar": 100.0,
        }
        score = self.ranker._weighted_score(factors)
        assert score == 100.0

    def test_weighted_score_mixed(self):
        factors = {
            "regime_clarity": 100.0,
            "spread_atr_ratio": 50.0,
            "volatility_usability": 80.0,
            "session_quality": 70.0,
            "confluence": 60.0,
            "portfolio_overlap": 100.0,
            "calendar": 100.0,
        }
        score = self.ranker._weighted_score(factors)
        # Should be between 0 and 100
        assert 0 < score < 100

    def test_results_sorted_by_score_descending(self):
        ranker = OpportunityRanker()
        snapshots = {
            "SYM_A": _make_snapshot(
                symbol="SYM_A",
                regime="trending_up",
                spread_ratio_atr=0.03,
                atr_percentile=60.0,
            ),
            "SYM_B": _make_snapshot(
                symbol="SYM_B",
                regime="ranging",
                spread_ratio_atr=0.08,
                atr_percentile=45.0,
            ),
            "SYM_C": _make_snapshot(
                symbol="SYM_C",
                regime="compressing",
                spread_ratio_atr=0.05,
                atr_percentile=50.0,
            ),
        }
        result = ranker.rank(
            symbols=["SYM_A", "SYM_B", "SYM_C"],
            snapshots=snapshots,
        )
        scores = [r["score"] for r in result]
        assert scores == sorted(scores, reverse=True)

    def test_reasons_are_populated(self):
        ranker = OpportunityRanker()
        snapshot = _make_snapshot()
        result = ranker.rank(symbols=["XAUUSD"], snapshots={"XAUUSD": snapshot})
        assert len(result) == 1
        assert isinstance(result[0]["reasons"], list)
        assert len(result[0]["reasons"]) > 0

    def test_factors_dict_is_present(self):
        ranker = OpportunityRanker()
        snapshot = _make_snapshot()
        result = ranker.rank(symbols=["XAUUSD"], snapshots={"XAUUSD": snapshot})
        assert "factors" in result[0]
        assert "regime_clarity" in result[0]["factors"]
        assert "spread_atr_ratio" in result[0]["factors"]

    def test_min_score_threshold(self):
        ranker = OpportunityRanker()
        # Create a mediocre snapshot
        snapshot = _make_snapshot(
            regime="compressing",
            spread_ratio_atr=0.10,
            atr_percentile=40.0,
            current_sessions=["tokyo"],
            active_overlaps=[],
        )
        result = ranker.rank(
            symbols=["XAUUSD"],
            snapshots={"XAUUSD": snapshot},
            min_score=90.0,
        )
        assert len(result) == 1
        # With min_score=90, mediocre symbol should be below threshold
        assert (
            result[0]["skip_reason"] is not None
            or result[0]["recommendation"] != "trade"
        )
