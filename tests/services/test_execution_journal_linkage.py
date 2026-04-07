"""Tests for intent_id schema addition and execution-to-journal linkage."""

from __future__ import annotations

import os

import pytest
from mt5_mcp.services.trade_journal_db import TradeJournalDB


class TestIntentIdInLogDecision:
    def setup_method(self):
        self.db = TradeJournalDB(db_path="/tmp/test_intent_log.db")

    def teardown_method(self):
        self.db.close()
        if os.path.exists("/tmp/test_intent_log.db"):
            os.remove("/tmp/test_intent_log.db")

    def test_log_decision_accepts_and_stores_intent_id(self):
        did = self.db.log_decision(
            symbol="XAUUSD",
            side="buy",
            action="entry",
            intent_id="intent_001",
        )
        entry = self.db.get_decision(did)
        assert entry is not None
        assert entry["intent_id"] == "intent_001"

    def test_log_decision_intent_id_defaults_none(self):
        did = self.db.log_decision(
            symbol="XAUUSD",
            side="buy",
            action="entry",
        )
        entry = self.db.get_decision(did)
        assert entry is not None
        assert entry["intent_id"] is None

    def test_log_decision_with_all_linkage_ids(self):
        did = self.db.log_decision(
            symbol="XAUUSD",
            side="buy",
            action="entry",
            session_id="sess_abc",
            strategy_id="scalp_v2",
            intent_id="intent_001",
        )
        entry = self.db.get_decision(did)
        assert entry["session_id"] == "sess_abc"
        assert entry["strategy_id"] == "scalp_v2"
        assert entry["intent_id"] == "intent_001"


class TestUpdateDecisionOutcome:
    def setup_method(self):
        self.db = TradeJournalDB(db_path="/tmp/test_update_outcome.db")

    def teardown_method(self):
        self.db.close()
        if os.path.exists("/tmp/test_update_outcome.db"):
            os.remove("/tmp/test_update_outcome.db")

    def test_update_decision_outcome_updates_existing(self):
        did = self.db.log_decision(
            symbol="XAUUSD",
            side="buy",
            action="entry",
            outcome="still_open",
        )
        result = self.db.update_decision_outcome(
            did,
            outcome="win",
            exit_price=2660.0,
            pnl=50.0,
            duration_seconds=300.0,
            mistake_category=None,
        )
        assert result is True
        entry = self.db.get_decision(did)
        assert entry["outcome"] == "win"
        assert entry["exit_price"] == 2660.0
        assert entry["pnl"] == 50.0
        assert entry["duration_seconds"] == 300.0

    def test_update_decision_outcome_returns_false_for_nonexistent(self):
        result = self.db.update_decision_outcome(
            "nonexistent_decision_id",
            outcome="win",
        )
        assert result is False

    def test_update_decision_outcome_partial_update(self):
        did = self.db.log_decision(
            symbol="XAUUSD",
            side="buy",
            action="entry",
            outcome="still_open",
            exit_price=2650.0,
        )
        self.db.update_decision_outcome(did, outcome="loss", pnl=-25.0)
        entry = self.db.get_decision(did)
        assert entry["outcome"] == "loss"
        assert entry["pnl"] == -25.0
        assert entry["exit_price"] == 2650.0  # unchanged


class TestLogExecutionResult:
    def setup_method(self):
        self.db = TradeJournalDB(db_path="/tmp/test_exec_result.db")

    def teardown_method(self):
        self.db.close()
        if os.path.exists("/tmp/test_exec_result.db"):
            os.remove("/tmp/test_exec_result.db")

    def test_log_execution_result_creates_complete_entry(self):
        did = self.db.log_execution_result(
            symbol="XAUUSD",
            side="buy",
            action="entry",
            intent_id="intent_042",
            session_id="sess_1",
            strategy_id="momentum",
            entry_price=2650.0,
            sl=2640.0,
            tp=2680.0,
            volume_lots=0.1,
            outcome="success",
            message="Order filled",
        )
        assert did.startswith("dec_")
        entry = self.db.get_decision(did)
        assert entry is not None
        assert entry["symbol"] == "XAUUSD"
        assert entry["side"] == "buy"
        assert entry["action"] == "entry"
        assert entry["intent_id"] == "intent_042"
        assert entry["session_id"] == "sess_1"
        assert entry["strategy_id"] == "momentum"
        assert entry["entry_price"] == 2650.0
        assert entry["sl"] == 2640.0
        assert entry["tp"] == 2680.0
        assert entry["volume_lots"] == 0.1
        assert entry["outcome"] == "success"
        assert entry["model_justification"] == "Order filled"

    def test_log_execution_result_minimal(self):
        did = self.db.log_execution_result(
            symbol="XAUUSD",
            side="sell",
            action="exit",
        )
        entry = self.db.get_decision(did)
        assert entry is not None
        assert entry["symbol"] == "XAUUSD"
        assert entry["side"] == "sell"
        assert entry["action"] == "exit"
        assert entry["intent_id"] is None
        assert entry["outcome"] is None

    def test_log_execution_result_returns_custom_decision_id(self):
        custom_id = "dec_custom123"
        did = self.db.log_execution_result(
            symbol="XAUUSD",
            side="buy",
            action="entry",
            decision_id=custom_id,
        )
        assert did == custom_id
        entry = self.db.get_decision(custom_id)
        assert entry is not None


class TestIntentBasedQuerying:
    def setup_method(self):
        self.db = TradeJournalDB(db_path="/tmp/test_intent_query.db")

    def teardown_method(self):
        self.db.close()
        if os.path.exists("/tmp/test_intent_query.db"):
            os.remove("/tmp/test_intent_query.db")

    def test_query_by_intent_id(self):
        self.db.log_decision(
            symbol="XAUUSD", side="buy", action="entry", intent_id="intent_A"
        )
        self.db.log_decision(
            symbol="XAUUSD", side="buy", action="entry", intent_id="intent_B"
        )
        self.db.log_decision(
            symbol="XAUUSD", side="buy", action="entry", intent_id=None
        )

        results = self.db.query(intent_id="intent_A")
        assert len(results) == 1
        assert results[0]["intent_id"] == "intent_A"

    def test_query_by_intent_and_strategy(self):
        self.db.log_decision(
            symbol="XAUUSD",
            side="buy",
            action="entry",
            intent_id="intent_A",
            strategy_id="scalp",
        )
        self.db.log_decision(
            symbol="XAUUSD",
            side="buy",
            action="entry",
            intent_id="intent_A",
            strategy_id="swing",
        )

        results = self.db.query(intent_id="intent_A", strategy_id="scalp")
        assert len(results) == 1
        assert results[0]["strategy_id"] == "scalp"

    def test_find_execution_entries_by_intent_id(self):
        self.db.log_execution_result(
            symbol="XAUUSD",
            side="buy",
            action="entry",
            intent_id="intent_exec_1",
            outcome="success",
        )
        results = self.db.query(intent_id="intent_exec_1")
        assert len(results) == 1
        assert results[0]["outcome"] == "success"
        assert results[0]["intent_id"] == "intent_exec_1"


class TestMigrationIdempotency:
    def test_opening_same_db_twice_does_not_error(self):
        db_path = "/tmp/test_migration_idempotent.db"
        try:
            db1 = TradeJournalDB(db_path=db_path)
            db1.log_decision(symbol="XAUUSD", side="buy", action="entry")
            db1.close()

            db2 = TradeJournalDB(db_path=db_path)
            db2.log_decision(symbol="XAUUSD", side="sell", action="entry")
            results = db2.query()
            assert len(results) == 2
            db2.close()
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    def test_new_db_has_intent_id_column(self):
        db_path = "/tmp/test_new_db_intent.db"
        try:
            db = TradeJournalDB(db_path=db_path)
            did = db.log_decision(
                symbol="XAUUSD", side="buy", action="entry", intent_id="test_intent"
            )
            entry = db.get_decision(did)
            assert entry["intent_id"] == "test_intent"
            db.close()
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)
