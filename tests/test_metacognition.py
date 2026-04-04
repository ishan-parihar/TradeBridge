"""Tests for metacognition: SQLite journal, trading context, and advisory coach."""

from __future__ import annotations

import os
import pytest
from mt5_mcp.services.trade_journal_db import TradeJournalDB, get_journal_db
from mt5_mcp.services.market_context import (
    build_context,
    SYMBOL_BASELINE,
    _price_to_pips,
    _price_to_points,
)
from mt5_mcp.services.trading_coach import TradingCoach, CoachingAdvice


class TestTradeJournalDB:
    """Tests for the SQLite-backed trade journal with reasoning capture."""

    def setup_method(self):
        self.db = TradeJournalDB(db_path="/tmp/test_journal_meta.db")

    def teardown_method(self):
        self.db.close()
        if os.path.exists("/tmp/test_journal_meta.db"):
            os.remove("/tmp/test_journal_meta.db")

    def test_log_decision_with_reasoning(self):
        did = self.db.log_decision(
            symbol="BTCUSD",
            side="buy",
            action="entry",
            entry_price=67000.0,
            sl=66635.0,
            tp=67730.0,
            volume_lots=0.01,
            model_justification="Breakout above H1 resistance with RSI > 50 and EMA alignment bullish",
            emotional_self_report="calm",
            confidence_level=0.75,
            risk_assessment="SL is 1x ATR, RR is 2:1, acceptable risk",
            regime="trending_up",
            atr_value=365.0,
            indicators_considered=["rsi", "ema_20", "ema_50", "atr"],
            alternatives_considered="Could have waited for pullback to EMA20, but breakout momentum was strong",
        )

        assert did.startswith("dec_")
        entry = self.db.get_decision(did)
        assert entry is not None
        assert entry["symbol"] == "BTCUSD"
        assert entry["model_justification"] is not None
        assert entry["emotional_self_report"] == "calm"
        assert entry["confidence_level"] == 0.75
        assert entry["outcome"] is None  # Not yet closed

    def test_update_decision_with_outcome(self):
        did = self.db.log_decision(
            symbol="BTCUSD",
            side="buy",
            action="entry",
            entry_price=67000.0,
        )

        updated = self.db.update_decision(
            did,
            exit_price=67730.0,
            pnl=0.73,
            outcome="win",
            lesson_learned="Breakout worked. Trust the setup when confluence is present.",
            quality_rating=4,
        )

        assert updated is True
        entry = self.db.get_decision(did)
        assert entry["outcome"] == "win"
        assert entry["pnl"] == 0.73
        assert entry["lesson_learned"] is not None

    def test_query_by_outcome(self):
        self.db.log_decision(
            symbol="BTCUSD", side="buy", action="entry", outcome="win", pnl=1.0
        )
        self.db.log_decision(
            symbol="BTCUSD", side="sell", action="entry", outcome="loss", pnl=-0.5
        )
        self.db.log_decision(
            symbol="XAUUSD", side="buy", action="entry", outcome="loss", pnl=-0.3
        )

        losses = self.db.query(outcome="loss")
        assert len(losses) == 2

        btc_entries = self.db.query(symbol="BTCUSD")
        assert len(btc_entries) == 2

    def test_query_by_emotional_state(self):
        self.db.log_decision(
            symbol="BTCUSD",
            side="buy",
            action="entry",
            emotional_self_report="anxious",
            outcome="loss",
        )
        self.db.log_decision(
            symbol="BTCUSD",
            side="buy",
            action="entry",
            emotional_self_report="calm",
            outcome="win",
        )
        self.db.log_decision(
            symbol="BTCUSD",
            side="sell",
            action="entry",
            emotional_self_report="anxious",
            outcome="loss",
        )

        anxious = self.db.query(emotional_self_report="anxious")
        assert len(anxious) == 2
        assert all(d["emotional_self_report"] == "anxious" for d in anxious)

    def test_reflection_insights(self):
        # Seed data for insights
        for i in range(5):
            self.db.log_decision(
                symbol="BTCUSD",
                side="buy",
                action="entry",
                emotional_self_report="calm",
                outcome="win" if i < 3 else "loss",
                pnl=0.5 if i < 3 else -0.3,
                regime="ranging",
            )
        for i in range(3):
            self.db.log_decision(
                symbol="BTCUSD",
                side="sell",
                action="entry",
                emotional_self_report="anxious",
                outcome="loss",
                pnl=-0.8,
                regime="ranging",
                mistake_category="premature_exit",
                confidence_level=0.3,
            )

        insights = self.db.get_reflection_insights(lookback_days=365)

        assert "win_rate_by_emotional_state" in insights
        assert "win_rate_by_regime" in insights
        assert "mistake_frequency" in insights
        assert "overall" in insights

        # Calm should have higher win rate than anxious
        calm = insights["win_rate_by_emotional_state"].get("calm", {})
        anxious = insights["win_rate_by_emotional_state"].get("anxious", {})
        if calm.get("win_rate") is not None and anxious.get("win_rate") is not None:
            assert calm["win_rate"] > anxious["win_rate"]

    def test_mistake_tracking(self):
        for _ in range(3):
            self.db.log_decision(
                symbol="BTCUSD",
                side="buy",
                action="entry",
                outcome="loss",
                mistake_category="premature_exit",
            )
        self.db.log_decision(
            symbol="BTCUSD",
            side="buy",
            action="entry",
            outcome="loss",
            mistake_category="wrong_regime",
        )

        insights = self.db.get_reflection_insights(lookback_days=365)
        assert insights.get("mistake_frequency") is not None
        top = insights["mistake_frequency"][0]
        assert top["category"] == "premature_exit"
        assert top["count"] == 3


class TestTradingContext:
    """Tests for the market-derived trading context system."""

    def test_btcusd_context_with_baseline(self):
        ctx = build_context(symbol="BTCUSD")
        assert ctx["symbol"] == "BTCUSD"
        assert "Bitcoin" in ctx["symbol_info"]["name"]
        assert "volatility_assessment" in ctx
        assert "market_state" in ctx
        assert "composure_notes" in ctx
        assert "baseline" in ctx

    def test_eurusd_context(self):
        ctx = build_context(symbol="EURUSD")
        assert "Euro" in ctx["symbol_info"]["name"]
        assert "typical_atr_h1_pips" in ctx["baseline"]

    def test_unknown_symbol_fallback(self):
        ctx = build_context(symbol="UNKNOWNXYZ")
        assert ctx["symbol"] == "UNKNOWNXYZ"
        assert ctx["symbol_info"]["name"] == "UNKNOWNXYZ"

    def test_live_atr_assessment(self):
        ctx = build_context(
            symbol="BTCUSD",
            current_atr=365.0,
            current_price=67000.0,
        )
        assert ctx["volatility_assessment"]["current_atr_price"] == 365.0
        # BTCUSD: ATR=365 pips, typical=400 pips → ratio=0.91x → normal
        assert ctx["volatility_assessment"]["status"] == "normal"
        # Should have composure notes
        assert len(ctx["composure_notes"]) > 0
        # Should answer "is 200 points a lot?"
        assert any("200 points" in n for n in ctx["composure_notes"])

    def test_atr_assessment_compressed(self):
        ctx = build_context(
            symbol="BTCUSD",
            current_atr=150.0,  # 150 pips vs typical 400 = 0.375x → compressed
            current_price=67000.0,
        )
        assert ctx["volatility_assessment"]["status"] == "compressed"
        assert any(
            "coil" in n.lower() or "breakout" in n.lower()
            for n in ctx["composure_notes"]
        )

    def test_atr_assessment_elevated(self):
        ctx = build_context(
            symbol="BTCUSD",
            current_atr=700.0,  # 700 pips vs typical 400 = 1.75x → elevated
            current_price=67000.0,
        )
        assert ctx["volatility_assessment"]["status"] == "elevated"

    def test_comparison_table(self):
        ctx = build_context(symbol="BTCUSD")
        assert "comparison" in ctx
        assert len(ctx["comparison"]) >= 3  # At least BTC, XAU, EUR

    def test_point_value_explained(self):
        ctx = build_context(symbol="BTCUSD")
        assert "point_context" in ctx
        assert "per_0_01_lot" in ctx["point_context"]
        assert "per_1_lot" in ctx["point_context"]

    def test_market_state_includes_indicators(self):
        ctx = build_context(
            symbol="BTCUSD",
            rsi=72.0,
            ema_fast=67500.0,
            ema_slow=66000.0,
            current_price=67000.0,
            last_bar_range=500.0,
            last_bar_direction="bullish",
            spread_points=14.0,  # raw price diff for BTCUSD
        )
        assert ctx["market_state"]["rsi"] == 72.0
        assert ctx["market_state"]["rsi_state"] == "overbought"
        assert ctx["market_state"]["ema_alignment"] == "bullish"
        # spread_points is now reported as pips and points
        assert "spread_pips" in ctx["market_state"]
        assert "spread_points" in ctx["market_state"]

    def test_unit_conversion_eurusd(self):
        """Test that EURUSD price differences convert correctly to pips and points."""
        # 5-digit forex: pip=0.0001, point=0.00001
        assert _price_to_pips(0.001, "EURUSD") == pytest.approx(10.0)
        assert _price_to_points(0.001, "EURUSD") == pytest.approx(100.0)
        assert _price_to_pips(0.00065, "EURUSD") == pytest.approx(6.5)
        assert _price_to_points(0.00065, "EURUSD") == pytest.approx(65.0)

    def test_unit_conversion_btcusd(self):
        """Test BTCUSD conversions."""
        assert _price_to_pips(1.0, "BTCUSD") == pytest.approx(1.0)
        assert _price_to_points(1.0, "BTCUSD") == pytest.approx(1.0)
        assert _price_to_pips(400.0, "BTCUSD") == pytest.approx(400.0)

    def test_unit_conversion_xauusd(self):
        """Test gold conversions: pip=$0.10, point=$0.01."""
        assert _price_to_pips(1.0, "XAUUSD") == pytest.approx(10.0)
        assert _price_to_points(1.0, "XAUUSD") == pytest.approx(100.0)
        assert _price_to_pips(0.30, "XAUUSD") == pytest.approx(3.0)

    def test_eurusd_atr_ratio_corrected(self):
        """Verify the ATR ratio is correctly computed for EURUSD.

        Before fix: current_atr(0.00096) / typical(10) = 0.0  ❌
        After fix:  current_atr_pips(9.59) / typical_pips(10) = 0.96 ✓
        """
        ctx = build_context(
            symbol="EURUSD",
            current_atr=0.000958571,  # ~9.59 pips from MT5
            current_price=1.15286,
        )
        va = ctx["volatility_assessment"]
        assert va["current_atr_pips"] == pytest.approx(9.59, abs=0.1)
        # Ratio should be ~0.96x, NOT 0.0
        assert va["atr_vs_typical"] == pytest.approx(0.96, abs=0.05)
        assert va["status"] == "normal"  # 0.96x is within 0.5-1.5 range

    def test_200_points_composure_corrected(self):
        """Verify '200 points' calculation is correct.

        Before fix: 200 / 0.00096 = 208,643x ATR  ❌
        After fix:  200 * 0.00001 / 0.00096 = 2.08x ATR  ✓
        """
        ctx = build_context(
            symbol="EURUSD",
            current_atr=0.000958571,
            current_price=1.15286,
        )
        note = [n for n in ctx["composure_notes"] if "200 points" in n][0]
        # Should say ~2.09x ATR, NOT 208,643x
        assert "208" not in note or "2.0" in note  # Reject the 208,643 bug
        assert "2.0" in note or "2.1" in note  # Accept ~2.09x
        # As percentage of price: 0.002 / 1.15286 = 0.17%, NOT 17,365%
        assert "173" not in note  # Reject 17,365% bug
        assert "0.17" in note or "0.18" in note  # Accept ~0.17%

    def test_spread_reported_in_pips_and_points(self):
        """Verify spread is reported in meaningful units, not raw price."""
        ctx = build_context(
            symbol="EURUSD",
            current_atr=0.001,
            spread_points=0.00065,  # raw price diff = 6.5 pips = 65 points
        )
        ms = ctx["market_state"]
        assert ms["spread_pips"] == 6.5
        assert ms["spread_points"] == 65.0
        assert ms["spread_price"] == 0.00065


class TestTradingCoach:
    """Tests for the data-driven advisory trading coach."""

    def setup_method(self):
        self.coach = TradingCoach()

    def test_never_blocks(self):
        """Coach should never block — only advise."""
        advice = self.coach.evaluate(
            symbol="BTCUSD",
            side="buy",
            regime="trending_down",
            atr_value=365,
            proposed_sl_points=50,
            proposed_tp_points=100,
            trades_today=10,
            recent_consecutive_losses=5,
        )
        assert advice.blocking is False

    def test_warns_about_tight_sl_vs_atr(self):
        advice = self.coach.evaluate(
            symbol="BTCUSD",
            side="buy",
            atr_value=365.0,
            proposed_sl_points=50.0,
            proposed_tp_points=600.0,
        )
        assert any(
            "0.1x ATR" in w or "normal volatility" in w.lower() for w in advice.warnings
        )

    def test_warns_about_tight_sl_vs_bar(self):
        advice = self.coach.evaluate(
            symbol="BTCUSD",
            side="buy",
            atr_value=365.0,
            proposed_sl_points=50.0,
            proposed_tp_points=600.0,
            last_bar_range=200.0,
        )
        assert any(
            "smaller than" in w.lower() or "single" in w.lower()
            for w in advice.warnings
        )

    def test_warns_about_bad_rr(self):
        advice = self.coach.evaluate(
            symbol="BTCUSD",
            side="buy",
            proposed_sl_points=500.0,
            proposed_tp_points=200.0,
        )
        assert any("Risk:Reward" in w for w in advice.warnings)

    def test_rr_required_win_rate(self):
        advice = self.coach.evaluate(
            symbol="BTCUSD",
            side="buy",
            proposed_sl_points=500.0,
            proposed_tp_points=1000.0,
        )
        assert advice.raw_metrics.get("rr_ratio") == 2.0
        assert advice.raw_metrics.get("required_win_rate") is not None

    def test_recommends_cautious_wait_in_range_middle(self):
        advice = self.coach.evaluate(
            symbol="BTCUSD",
            side="buy",
            regime="ranging",
            position_in_range=50.0,
            atr_value=365,
            proposed_sl_points=400,
            proposed_tp_points=800,
        )
        # With multiple conditions, recommendation should be cautious_wait or neutral
        # The key assertion is that there's a warning about middle-of-range
        assert any(
            "middle" in w.lower() or "no edge" in w.lower() for w in advice.warnings
        )

    def test_warns_counter_trend(self):
        advice = self.coach.evaluate(
            symbol="BTCUSD",
            side="sell",
            current_price=67000.0,
            ema_fast=66500.0,
            ema_slow=66000.0,
        )
        assert any(
            "against the trend" in w.lower() or "bearish-aligned" in w.lower()
            for w in advice.warnings
        )

    def test_ema_alignment_supports_trade(self):
        advice = self.coach.evaluate(
            symbol="BTCUSD",
            side="buy",
            current_price=67000.0,
            ema_fast=67500.0,
            ema_slow=66000.0,
        )
        assert any(
            "structure supports" in i.lower() or "bullish" in i.lower()
            for i in advice.insights
        )

    def test_consecutive_loss_warning(self):
        advice = self.coach.evaluate(
            symbol="BTCUSD",
            side="buy",
            recent_consecutive_losses=3,
            atr_value=365,
            proposed_sl_points=400,
            proposed_tp_points=800,
        )
        assert any(
            "revenge" in w.lower() or "consecutive" in w.lower()
            for w in advice.warnings
        )

    def test_good_sl_and_rr(self):
        advice = self.coach.evaluate(
            symbol="BTCUSD",
            side="buy",
            atr_value=365,
            proposed_sl_points=400,
            proposed_tp_points=800,
        )
        # SL is 1.1x ATR → should be in insights, not warnings
        assert any("volatility-appropriate" in i.lower() for i in advice.insights)
        # RR is 2:1 → favorable
        assert advice.raw_metrics.get("rr_ratio") == 2.0

    def test_compression_detection(self):
        advice = self.coach.evaluate(
            symbol="BTCUSD",
            side="buy",
            atr_value=400,
            recent_bars_compression=0.3,
        )
        assert any(
            "coil" in i.lower() or "bracket" in i.lower() for i in advice.insights
        )

    def test_rsi_momentum_shift(self):
        advice = self.coach.evaluate(
            symbol="BTCUSD",
            side="buy",
            rsi=45,
            rsi_1h_ago=60,
        )
        assert any(
            "momentum" in w.lower() and "shifting" in w.lower() for w in advice.warnings
        )

    def test_raw_metrics_included(self):
        advice = self.coach.evaluate(
            symbol="BTCUSD",
            side="buy",
            atr_value=365,
            proposed_sl_points=400,
            proposed_tp_points=800,
            rsi=55,
        )
        assert "sl_atr_ratio" in advice.raw_metrics
        assert "rr_ratio" in advice.raw_metrics
        assert "rsi" in advice.raw_metrics

    def test_bar_pattern_analysis(self):
        advice = self.coach.evaluate(
            symbol="BTCUSD",
            side="buy",
            last_bar_range=500,
            last_bar_body=10,
            last_bar_direction="doji",
        )
        assert any(
            "doji" in i.lower() or "indecision" in i.lower() for i in advice.insights
        )

    def test_spread_atr_warning(self):
        advice = self.coach.evaluate(
            symbol="BTCUSD",
            side="buy",
            atr_value=100,
            current_price=67000.0,
            spread_points=15,
        )
        assert any("spread" in w.lower() for w in advice.warnings)
