"""Tests for trade journal ownership metadata — strategy_id storage and querying."""

from __future__ import annotations

import os

import pytest
from mt5_mcp.services.trade_journal_db import TradeJournalDB


class TestTradeJournalOwnership:
    def setup_method(self):
        self.db = TradeJournalDB(db_path="/tmp/test_journal_ownership.db")

    def teardown_method(self):
        self.db.close()
        if os.path.exists("/tmp/test_journal_ownership.db"):
            os.remove("/tmp/test_journal_ownership.db")

    def test_log_decision_stores_strategy_id(self):
        did = self.db.log_decision(
            symbol="XAUUSD",
            side="buy",
            action="entry",
            session_id="sess_abc",
            strategy_id="scalp_v2",
        )
        entry = self.db.get_decision(did)
        assert entry is not None
        assert entry["session_id"] == "sess_abc"
        assert entry["strategy_id"] == "scalp_v2"

    def test_log_decision_strategy_id_defaults_none(self):
        did = self.db.log_decision(
            symbol="XAUUSD",
            side="buy",
            action="entry",
        )
        entry = self.db.get_decision(did)
        assert entry is not None
        assert entry["strategy_id"] is None

    def test_query_by_strategy_id(self):
        self.db.log_decision(
            symbol="XAUUSD", side="buy", action="entry", strategy_id="scalp"
        )
        self.db.log_decision(
            symbol="XAUUSD", side="buy", action="entry", strategy_id="swing"
        )
        self.db.log_decision(
            symbol="XAUUSD", side="buy", action="entry", strategy_id=None
        )

        results = self.db.query(strategy_id="scalp")
        assert len(results) == 1
        assert results[0]["strategy_id"] == "scalp"

    def test_query_by_session_id(self):
        self.db.log_decision(
            symbol="XAUUSD", side="buy", action="entry", session_id="sess_1"
        )
        self.db.log_decision(
            symbol="XAUUSD", side="buy", action="entry", session_id="sess_2"
        )

        results = self.db.query(session_id="sess_1")
        assert len(results) == 1
        assert results[0]["session_id"] == "sess_1"

    def test_query_by_session_and_strategy(self):
        self.db.log_decision(
            symbol="XAUUSD",
            side="buy",
            action="entry",
            session_id="sess_1",
            strategy_id="scalp",
        )
        self.db.log_decision(
            symbol="XAUUSD",
            side="buy",
            action="entry",
            session_id="sess_1",
            strategy_id="swing",
        )
        self.db.log_decision(
            symbol="XAUUSD",
            side="buy",
            action="entry",
            session_id="sess_2",
            strategy_id="scalp",
        )

        results = self.db.query(session_id="sess_1", strategy_id="scalp")
        assert len(results) == 1
        assert results[0]["session_id"] == "sess_1"
        assert results[0]["strategy_id"] == "scalp"

    def test_update_does_not_clear_strategy_id(self):
        did = self.db.log_decision(
            symbol="XAUUSD",
            side="buy",
            action="entry",
            strategy_id="scalp",
            session_id="sess_1",
        )
        self.db.update_decision(did, outcome="win", pnl=50.0)
        entry = self.db.get_decision(did)
        assert entry["strategy_id"] == "scalp"
        assert entry["session_id"] == "sess_1"
        assert entry["outcome"] == "win"

    def test_reflection_insights_include_strategy_grouping(self):
        self.db.log_decision(
            symbol="XAUUSD",
            side="buy",
            action="entry",
            strategy_id="scalp",
            outcome="win",
            pnl=10.0,
        )
        self.db.log_decision(
            symbol="XAUUSD",
            side="buy",
            action="entry",
            strategy_id="scalp",
            outcome="loss",
            pnl=-5.0,
        )
        self.db.log_decision(
            symbol="XAUUSD",
            side="buy",
            action="entry",
            strategy_id="swing",
            outcome="win",
            pnl=30.0,
        )

        insights = self.db.get_reflection_insights(lookback_days=365)
        assert "win_rate_by_strategy" in insights
        assert "scalp" in insights["win_rate_by_strategy"]
        assert "swing" in insights["win_rate_by_strategy"]
        assert insights["win_rate_by_strategy"]["scalp"]["win_rate"] == 50.0
        assert insights["win_rate_by_strategy"]["swing"]["win_rate"] == 100.0
