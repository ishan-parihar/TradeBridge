"""Tests for MistakeCategory enum and its integration with trade journal."""

from __future__ import annotations

import os

import pytest
from mt5_mcp.services.mistake_categories import MistakeCategory
from mt5_mcp.services.trade_journal_db import TradeJournalDB


class TestMistakeCategoryEnum:
    """Unit tests for the MistakeCategory enum itself."""

    def test_all_values_are_unique(self):
        values = [cat.value for cat in MistakeCategory]
        assert len(values) == len(set(values))

    def test_all_values_are_non_empty_strings(self):
        for cat in MistakeCategory:
            assert isinstance(cat.value, str)
            assert len(cat.value) > 0

    def test_is_a_valid_str(self):
        """MistakeCategory members compare equal to their string values."""
        assert MistakeCategory.DUPLICATE_INTENT == "duplicate_intent"
        assert MistakeCategory.FOREIGN_PNL_CONFUSION == "foreign_pnl_confusion"
        assert MistakeCategory.BRIDGE_BLINDNESS == "bridge_blindness"
        assert MistakeCategory.LOST_SL_TP_ON_MODIFY == "lost_sl_tp_on_modify"

    def test_string_comparison_works_both_ways(self):
        assert "premature_exit" == MistakeCategory.PREMATURE_EXIT
        assert MistakeCategory.STALE_DATA == "stale_data"

    def test_all_expected_categories_exist(self):
        expected = {
            "duplicate_intent",
            "foreign_pnl_confusion",
            "bridge_blindness",
            "lost_sl_tp_on_modify",
            "invalid_stops_distance",
            "oversized_position",
            "premature_exit",
            "missing_entry_rationale",
            "wrong_regime_strategy",
            "calendar_blackout",
            "portfolio_overlap",
            "stale_data",
        }
        actual = {cat.value for cat in MistakeCategory}
        assert actual == expected

    def test_member_count(self):
        assert len(list(MistakeCategory)) == 12


class TestMistakeCategoryInJournal:
    """Integration tests for MistakeCategory with TradeJournalDB."""

    def setup_method(self):
        self.db = TradeJournalDB(db_path="/tmp/test_journal_mistake.db")

    def teardown_method(self):
        self.db.close()
        if os.path.exists("/tmp/test_journal_mistake.db"):
            os.remove("/tmp/test_journal_mistake.db")

    def test_log_decision_accepts_enum(self):
        did = self.db.log_decision(
            symbol="XAUUSD",
            side="buy",
            action="entry",
            mistake_category=MistakeCategory.PREMATURE_EXIT,
        )
        entry = self.db.get_decision(did)
        assert entry["mistake_category"] == "premature_exit"

    def test_log_decision_accepts_plain_string(self):
        did = self.db.log_decision(
            symbol="XAUUSD",
            side="buy",
            action="entry",
            mistake_category="custom_mistake",
        )
        entry = self.db.get_decision(did)
        assert entry["mistake_category"] == "custom_mistake"

    def test_log_decision_stores_string_not_enum(self):
        """Stored value is always a plain string in the DB."""
        did = self.db.log_decision(
            symbol="XAUUSD",
            side="buy",
            action="entry",
            mistake_category=MistakeCategory.BRIDGE_BLINDNESS,
        )
        entry = self.db.get_decision(did)
        assert isinstance(entry["mistake_category"], str)
        assert not isinstance(entry["mistake_category"], MistakeCategory)

    def test_log_decision_none_mistake_category(self):
        did = self.db.log_decision(
            symbol="XAUUSD",
            side="buy",
            action="entry",
        )
        entry = self.db.get_decision(did)
        assert entry["mistake_category"] is None

    def test_update_decision_outcome_accepts_enum(self):
        did = self.db.log_decision(
            symbol="XAUUSD", side="buy", action="entry", outcome="still_open"
        )
        self.db.update_decision_outcome(
            did,
            outcome="loss",
            mistake_category=MistakeCategory.LOST_SL_TP_ON_MODIFY,
        )
        entry = self.db.get_decision(did)
        assert entry["mistake_category"] == "lost_sl_tp_on_modify"

    def test_query_by_enum_mistake_category(self):
        self.db.log_decision(
            symbol="XAUUSD",
            side="buy",
            action="entry",
            mistake_category=MistakeCategory.DUPLICATE_INTENT,
        )
        self.db.log_decision(
            symbol="XAUUSD",
            side="buy",
            action="entry",
            mistake_category=MistakeCategory.PREMATURE_EXIT,
        )

        results = self.db.query(mistake_category=MistakeCategory.DUPLICATE_INTENT)
        assert len(results) == 1
        assert results[0]["mistake_category"] == "duplicate_intent"

    def test_query_by_string_mistake_category(self):
        self.db.log_decision(
            symbol="XAUUSD",
            side="buy",
            action="entry",
            mistake_category=MistakeCategory.OVERSIZED_POSITION,
        )

        results = self.db.query(mistake_category="oversized_position")
        assert len(results) == 1


class TestMistakeTaxonomy:
    """Tests for get_mistake_taxonomy() method."""

    def setup_method(self):
        self.db = TradeJournalDB(db_path="/tmp/test_journal_taxonomy.db")

    def teardown_method(self):
        self.db.close()
        if os.path.exists("/tmp/test_journal_taxonomy.db"):
            os.remove("/tmp/test_journal_taxonomy.db")

    def test_empty_taxonomy(self):
        taxonomy = self.db.get_mistake_taxonomy()
        assert taxonomy == {}

    def test_taxonomy_returns_correct_counts(self):
        self.db.log_decision(
            symbol="XAUUSD",
            side="buy",
            action="entry",
            mistake_category=MistakeCategory.PREMATURE_EXIT,
        )
        self.db.log_decision(
            symbol="EURUSD",
            side="sell",
            action="entry",
            mistake_category=MistakeCategory.PREMATURE_EXIT,
        )
        self.db.log_decision(
            symbol="XAUUSD",
            side="buy",
            action="entry",
            mistake_category=MistakeCategory.BRIDGE_BLINDNESS,
        )

        taxonomy = self.db.get_mistake_taxonomy()
        assert taxonomy == {
            "premature_exit": 2,
            "bridge_blindness": 1,
        }

    def test_taxonomy_ignores_none_categories(self):
        self.db.log_decision(
            symbol="XAUUSD",
            side="buy",
            action="entry",
            mistake_category=MistakeCategory.STALE_DATA,
        )
        self.db.log_decision(
            symbol="EURUSD",
            side="sell",
            action="entry",
        )

        taxonomy = self.db.get_mistake_taxonomy()
        assert len(taxonomy) == 1
        assert taxonomy["stale_data"] == 1

    def test_taxonomy_with_lookback_days(self):
        self.db.log_decision(
            symbol="XAUUSD",
            side="buy",
            action="entry",
            mistake_category=MistakeCategory.CALENDAR_BLACKOUT,
        )
        taxonomy = self.db.get_mistake_taxonomy(lookback_days=365)
        assert taxonomy == {"calendar_blackout": 1}

    def test_taxonomy_lookback_excludes_old_entries(self):
        """With lookback_days=0, all entries should be excluded (future timestamp trick)."""
        taxonomy = self.db.get_mistake_taxonomy(lookback_days=0)
        assert taxonomy == {}
