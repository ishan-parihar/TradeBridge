"""Tests for new trading audit upgrades: policy engine, market regime, trade journal."""

from __future__ import annotations

import os
import time
import uuid
import pytest

from mt5_mcp.policy.engine import (
    TradingPolicy,
    TradingLimits,
    TradeRecord,
    PolicyDecision,
    get_policy,
    reset_policy,
)
from mt5_mcp.services.market_regime import detect_regime
from mt5_mcp.services.trade_journal_db import TradeJournalDB


# ============================================================
# Trading Policy Engine Tests
# ============================================================


class TestTradingPolicy:
    """Tests for the TradingPolicy guardrails."""

    def setup_method(self):
        self.policy = TradingPolicy(
            limits=TradingLimits(
                max_trades_per_day=3,
                max_loss_per_day_pct=2.0,
                min_rest_between_trades_sec=0,  # Disabled for fast tests
                cooldown_after_consecutive_losses=2,
                cooldown_duration_after_losses_sec=0,  # Disabled for fast tests
            )
        )

    def test_allows_first_trade_in_demo(self):
        decision = self.policy.validate_submit_order(environment="demo")
        assert decision.allowed is True

    def test_blocks_live_trading_by_default(self):
        decision = self.policy.validate_submit_order(environment="live")
        assert decision.allowed is False
        assert "live_trading_blocked" in decision.reason

    def test_blocks_after_daily_trade_limit(self):
        equity = 200.0
        now = time.time()

        # Fill up trades
        for i in range(3):
            d = self.policy.validate_submit_order(environment="demo", equity=equity)
            assert d.allowed is True, f"Trade {i + 1} should be allowed"
            self.policy.record_trade(
                TradeRecord(
                    timestamp=now - (3 - i),  # Staggered past timestamps
                    symbol="BTCUSD",
                    side="buy",
                    entry_price=67000.0,
                    pnl=-0.5,
                )
            )

        # Reset last trade time to avoid rest period check
        self.policy._last_trade_time = now - 10

        # 4th trade should be blocked
        decision = self.policy.validate_submit_order(environment="demo", equity=equity)
        assert decision.allowed is False
        assert "daily_trade_limit_reached" in decision.reason

    def test_blocks_on_daily_loss_circuit_breaker(self):
        equity = 200.0
        # 2% of $200 = $4.00
        # Record losses totaling more than $4
        now = time.time()
        self.policy.record_trade(
            TradeRecord(
                timestamp=now - 2,
                symbol="BTCUSD",
                side="buy",
                entry_price=67000.0,
                pnl=-2.5,
            )
        )
        self.policy.record_trade(
            TradeRecord(
                timestamp=now - 1,
                symbol="BTCUSD",
                side="buy",
                entry_price=67000.0,
                pnl=-2.0,
            )
        )
        # Reset last trade time to avoid rest period check
        self.policy._last_trade_time = now - 10

        decision = self.policy.validate_submit_order(environment="demo", equity=equity)
        assert decision.allowed is False
        assert "daily_loss_circuit_breaker" in decision.reason

    def test_blocks_on_consecutive_loss_cooldown(self):
        equity = 200.0
        now = time.time()

        # Create policy with active cooldown duration
        policy = TradingPolicy(
            limits=TradingLimits(
                max_trades_per_day=10,
                min_rest_between_trades_sec=0,
                cooldown_after_consecutive_losses=2,
                cooldown_duration_after_losses_sec=60,  # 60s cooldown
            )
        )

        # Two consecutive losses trigger cooldown
        policy.record_trade(
            TradeRecord(
                timestamp=now - 2,
                symbol="BTCUSD",
                side="buy",
                entry_price=67000.0,
                pnl=-0.5,
            )
        )
        policy.record_trade(
            TradeRecord(
                timestamp=now - 1,
                symbol="BTCUSD",
                side="buy",
                entry_price=67000.0,
                pnl=-0.5,
            )
        )
        # Reset last trade time to avoid rest period check
        policy._last_trade_time = now - 10

        decision = policy.validate_submit_order(environment="demo", equity=equity)
        assert decision.allowed is False
        assert "consecutive_loss_cooldown" in decision.reason

    def test_validates_breakeven_prevention(self):
        # Should block BE move before 1x ATR
        decision = self.policy.validate_breakeven_move(
            profit_points=50.0, atr_points=365.0
        )
        assert decision.allowed is False
        assert "premature_breakeven" in decision.reason

        # Should allow BE move after 1x ATR
        decision = self.policy.validate_breakeven_move(
            profit_points=400.0, atr_points=365.0
        )
        assert decision.allowed is True

    def test_resets_on_new_day(self):
        equity = 200.0

        # Fill up trades
        for i in range(3):
            self.policy.record_trade(
                TradeRecord(
                    timestamp=time.time(),
                    symbol="BTCUSD",
                    side="buy",
                    entry_price=67000.0,
                    pnl=-0.1,
                )
            )

        # Manually trigger day reset by setting session_date to a past UTC date
        from datetime import date, timedelta
        from datetime import datetime, timezone as tz

        yesterday_utc = datetime.now(tz.utc).date() - timedelta(days=7)
        self.policy._session_date = yesterday_utc
        self.policy._reset_if_new_day()

        # Should be allowed again
        decision = self.policy.validate_submit_order(environment="demo", equity=equity)
        assert decision.allowed is True

    def test_status_report(self):
        self.policy.record_trade(
            TradeRecord(
                timestamp=time.time(),
                symbol="BTCUSD",
                side="buy",
                entry_price=67000.0,
                pnl=0.5,
            )
        )

        status = self.policy.get_status(equity=200.0)
        assert status["trades_today"] == 1
        assert status["daily_pnl"] == 0.5
        assert "daily_loss_pct" in status


# ============================================================
# Market Regime Detection Tests
# ============================================================


class TestMarketRegime:
    """Tests for regime detection."""

    def test_ranging_market(self):
        # Low range-to-ATR ratio indicates ranging
        bars = [
            {
                "time": i,
                "open": 100.0,
                "high": 101.0,
                "low": 99.5,
                "close": 100.2,
                "tick_volume": 100,
            }
            for i in range(20)
        ]
        result = detect_regime(bars=bars, atr_value=10.0)

        assert result["regime"] == "ranging"
        assert result["recommendation"] == "use_bracket_orders"

    def test_trending_market(self):
        # High range-to-ATR ratio indicates trending
        bars = [
            {
                "time": i,
                "open": 100.0 + i * 2,
                "high": 105.0 + i * 2,
                "low": 98.0 + i * 2,
                "close": 103.0 + i * 2,
                "tick_volume": 100,
            }
            for i in range(20)
        ]
        result = detect_regime(bars=bars, atr_value=2.0)

        assert result["regime"] in ("trending_up", "trending_down")
        assert result["recommendation"] == "use_directional_entries"

    def test_unknown_regime_with_no_data(self):
        result = detect_regime(bars=[], atr_value=0.0)
        assert result["regime"] == "unknown"
        assert result["recommendation"] == "wait_for_data"

    def test_strategy_hints_included(self):
        bars = [
            {
                "time": i,
                "open": 100.0,
                "high": 101.0,
                "low": 99.5,
                "close": 100.2,
                "tick_volume": 100,
            }
            for i in range(20)
        ]
        result = detect_regime(bars=bars, atr_value=10.0)

        assert "strategy_hints" in result
        hints = result["strategy_hints"]
        assert "entry_style" in hints
        assert "avoid" in hints
        assert "max_trades" in hints


# ============================================================
# Trade Journal Tests
# ============================================================


class TestTradeJournal:
    """Tests for the SQLite-backed trade journal."""

    def setup_method(self):
        self._db_path = f"/tmp/test_tj_upgrade_{uuid.uuid4().hex[:8]}.db"
        self.journal = TradeJournalDB(db_path=self._db_path)

    def teardown_method(self):
        self.journal.close()
        if os.path.exists(self._db_path):
            os.remove(self._db_path)

    def test_log_and_find_entry(self):
        entry_id = self.journal.log_decision(
            symbol="BTCUSD",
            side="buy",
            action="entry",
            entry_price=67000.0,
            volume_lots=0.01,
            model_justification="Breakout test",
        )

        found = self.journal.get_decision(entry_id)
        assert found is not None
        assert found["symbol"] == "BTCUSD"
        assert found["side"] == "buy"

    def test_log_exit(self):
        entry_id = self.journal.log_decision(
            symbol="BTCUSD",
            side="buy",
            action="entry",
            entry_price=67000.0,
            volume_lots=0.01,
        )

        updated = self.journal.update_decision(
            entry_id,
            exit_price=67100.0,
            outcome="win",
            pnl=1.0,
        )

        assert updated is True
        entry = self.journal.get_decision(entry_id)
        assert entry["exit_price"] == 67100.0
        assert entry["pnl"] == 1.0
        assert entry["outcome"] == "win"

    def test_query_by_symbol(self):
        self.journal.log_decision(
            symbol="BTCUSD", side="buy", action="entry", entry_price=67000.0
        )
        self.journal.log_decision(
            symbol="XAUUSD", side="buy", action="entry", entry_price=2000.0
        )

        btc_entries = self.journal.query(symbol="BTCUSD")
        assert len(btc_entries) == 1
        assert btc_entries[0]["symbol"] == "BTCUSD"

    def test_session_summary(self):
        id1 = self.journal.log_decision(
            symbol="BTCUSD",
            side="buy",
            action="entry",
            entry_price=67000.0,
            session_id="session_1",
        )
        id2 = self.journal.log_decision(
            symbol="BTCUSD",
            side="sell",
            action="entry",
            entry_price=67100.0,
            session_id="session_1",
        )
        self.journal.update_decision(id1, exit_price=67050.0, outcome="loss", pnl=-0.5)
        self.journal.update_decision(id2, exit_price=67000.0, outcome="win", pnl=1.0)

        insights = self.journal.get_reflection_insights(lookback_days=365)
        assert insights["overall"]["total_decisions"] == 2
        assert insights["overall"]["wins"] == 1
        assert insights["overall"]["losses"] == 1
        assert insights["overall"]["total_pnl"] == 0.5

    def test_summary_by_regime(self):
        entry_id = self.journal.log_decision(
            symbol="BTCUSD",
            side="buy",
            action="entry",
            entry_price=67000.0,
            regime="ranging",
        )
        self.journal.update_decision(
            entry_id, exit_price=67050.0, outcome="win", pnl=0.5
        )

        insights = self.journal.get_reflection_insights(lookback_days=365)
        assert "win_rate_by_regime" in insights
        assert "ranging" in insights["win_rate_by_regime"]
        assert insights["win_rate_by_regime"]["ranging"]["wins"] == 1
