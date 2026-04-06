"""Tests for the ReconciliationService — ownership filtering, foreign PnL, and state reconcile."""

from __future__ import annotations

import pytest
from mt5_mcp.schemas.models import Position, Deal
from mt5_mcp.services.reconciliation import ReconciliationService
from mt5_mcp.settings.config import Settings


class TestGetOwnedPositions:
    """Test filtering positions by strategy ownership."""

    def _make_settings(self, strategy_magic: dict[str, int] | None = None) -> Settings:
        return Settings(strategy_magic_numbers=strategy_magic or {})

    def _make_position(
        self,
        position_id: str = "pos_1",
        symbol: str = "XAUUSD",
        strategy_id: str | None = None,
        session_id: str | None = None,
    ) -> Position:
        return Position(
            position_id=position_id,
            symbol=symbol,
            side="buy",
            volume=0.1,
            entry_price=2000.0,
            strategy_id=strategy_id,
            session_id=session_id,
        )

    def test_returns_empty_when_no_positions(self):
        svc = ReconciliationService(self._make_settings({"scalp": 12345}))
        result = svc.get_owned_positions([])
        assert result == []

    def test_returns_empty_when_no_strategy_configured(self):
        """If settings has no strategy_magic_numbers, nothing is owned."""
        svc = ReconciliationService(self._make_settings({}))
        pos = self._make_position(strategy_id="scalp")
        result = svc.get_owned_positions([pos])
        assert result == []

    def test_filters_by_strategy_id_match(self):
        """Position with matching strategy_id is owned."""
        svc = ReconciliationService(self._make_settings({"scalp": 12345}))
        owned = self._make_position(position_id="p1", strategy_id="scalp")
        foreign = self._make_position(position_id="p2", strategy_id="other")
        result = svc.get_owned_positions([owned, foreign])
        assert len(result) == 1
        assert result[0].position_id == "p1"

    def test_filters_by_magic_number_match_when_no_strategy_id(self):
        """Position without strategy_id but with session_id that matches a known
        strategy's magic number should not be included — magic matching requires
        the position to have a source/magic context. Since Position model doesn't
        have a magic field, we match via strategy_id only."""
        svc = ReconciliationService(self._make_settings({"scalp": 12345}))
        pos = self._make_position(position_id="p1", strategy_id=None)
        result = svc.get_owned_positions([pos])
        assert result == []

    def test_returns_multiple_owned_positions(self):
        svc = ReconciliationService(self._make_settings({"scalp": 12345}))
        p1 = self._make_position(position_id="p1", strategy_id="scalp")
        p2 = self._make_position(position_id="p2", strategy_id="scalp")
        p3 = self._make_position(position_id="p3", strategy_id="other")
        result = svc.get_owned_positions([p1, p2, p3])
        assert len(result) == 2
        assert {p.position_id for p in result} == {"p1", "p2"}

    def test_multiple_strategies_in_settings(self):
        svc = ReconciliationService(
            self._make_settings({"scalp": 12345, "swing": 67890})
        )
        p1 = self._make_position(position_id="p1", strategy_id="scalp")
        p2 = self._make_position(position_id="p2", strategy_id="swing")
        p3 = self._make_position(position_id="p3", strategy_id="other")
        result = svc.get_owned_positions([p1, p2, p3])
        assert len(result) == 2
        assert {p.position_id for p in result} == {"p1", "p2"}


class TestCalculateForeignPnl:
    """Test PnL calculation for deals that don't belong to current strategy."""

    def _make_settings(self, strategy_magic: dict[str, int] | None = None) -> Settings:
        return Settings(strategy_magic_numbers=strategy_magic or {})

    def _make_deal(
        self,
        deal_id: str = "d1",
        symbol: str = "XAUUSD",
        profit: float = 0.0,
        commission: float = 0.0,
        swap: float = 0.0,
        magic: int | None = None,
        comment: str | None = None,
    ) -> Deal:
        return Deal(
            deal_id=deal_id,
            symbol=symbol,
            side="buy",
            volume=0.1,
            price=2000.0,
            profit=profit,
            commission=commission,
            swap=swap,
            fee=0.0,
            time="2025-01-01T00:00:00",
            magic=magic,
            comment=comment,
        )

    def _make_owned_position(self, strategy_id: str = "scalp") -> Position:
        return Position(
            position_id="pos_1",
            symbol="XAUUSD",
            side="buy",
            volume=0.1,
            entry_price=2000.0,
            strategy_id=strategy_id,
        )

    def test_zero_foreign_pnl_when_no_deals(self):
        svc = ReconciliationService(self._make_settings({"scalp": 12345}))
        owned = [self._make_owned_position()]
        result = svc.calculate_foreign_pnl(owned, [])
        assert result == 0.0

    def test_zero_foreign_pnl_when_all_deals_are_owned(self):
        """Deals matching our strategy magic are NOT foreign."""
        svc = ReconciliationService(self._make_settings({"scalp": 12345}))
        owned = [self._make_owned_position("scalp")]
        deal = self._make_deal(deal_id="d1", profit=50.0, magic=12345)
        result = svc.calculate_foreign_pnl(owned, [deal])
        assert result == 0.0

    def test_foreign_pnl_from_deals_with_different_magic(self):
        """Deals with different magic numbers contribute to foreign PnL."""
        svc = ReconciliationService(self._make_settings({"scalp": 12345}))
        owned = [self._make_owned_position("scalp")]
        d1 = self._make_deal(deal_id="d1", profit=100.0, magic=99999)
        d2 = self._make_deal(deal_id="d2", profit=-30.0, magic=99999)
        result = svc.calculate_foreign_pnl(owned, [d1, d2])
        assert result == pytest.approx(70.0)

    def test_foreign_pnl_from_deals_with_no_magic(self):
        """Deals without magic number (manual trades) are foreign."""
        svc = ReconciliationService(self._make_settings({"scalp": 12345}))
        owned = [self._make_owned_position("scalp")]
        deal = self._make_deal(deal_id="d1", profit=25.0, magic=None)
        result = svc.calculate_foreign_pnl(owned, [deal])
        assert result == pytest.approx(25.0)

    def test_foreign_pnl_includes_commission_and_swap(self):
        """Foreign PnL = profit + commission + swap."""
        svc = ReconciliationService(self._make_settings({"scalp": 12345}))
        owned = [self._make_owned_position("scalp")]
        deal = self._make_deal(
            deal_id="d1", profit=100.0, commission=-5.0, swap=-2.0, magic=99999
        )
        result = svc.calculate_foreign_pnl(owned, [deal])
        assert result == pytest.approx(93.0)

    def test_multiple_strategies_only_exclude_own(self):
        """With multiple strategies, only our strategy's deals are excluded."""
        svc = ReconciliationService(
            self._make_settings({"scalp": 12345, "swing": 67890})
        )
        owned = [self._make_owned_position("scalp")]
        d_own = self._make_deal(deal_id="d1", profit=50.0, magic=12345)
        d_other = self._make_deal(deal_id="d2", profit=30.0, magic=67890)
        d_unknown = self._make_deal(deal_id="d3", profit=20.0, magic=11111)
        result = svc.calculate_foreign_pnl(owned, [d_own, d_other, d_unknown])
        # d_other (swing) and d_unknown are foreign from scalp's perspective
        assert result == pytest.approx(50.0)

    def test_no_owned_positions_all_deals_foreign(self):
        """If we own nothing, all deals are foreign."""
        svc = ReconciliationService(self._make_settings({"scalp": 12345}))
        d1 = self._make_deal(deal_id="d1", profit=10.0, magic=12345)
        d2 = self._make_deal(deal_id="d2", profit=-5.0, magic=99999)
        result = svc.calculate_foreign_pnl([], [d1, d2])
        # Even our own deals are foreign if we have no positions (no active strategy context)
        assert result == pytest.approx(5.0)


class TestReconcile:
    """Test reconciliation between expected (intent_ids) and actual positions."""

    def _make_settings(self, strategy_magic: dict[str, int] | None = None) -> Settings:
        return Settings(strategy_magic_numbers=strategy_magic or {})

    def _make_position(
        self,
        position_id: str = "pos_1",
        symbol: str = "XAUUSD",
        strategy_id: str | None = None,
        session_id: str | None = None,
    ) -> Position:
        return Position(
            position_id=position_id,
            symbol=symbol,
            side="buy",
            volume=0.1,
            entry_price=2000.0,
            strategy_id=strategy_id,
            session_id=session_id,
        )

    def test_empty_state(self):
        """No intents, no positions — clean."""
        svc = ReconciliationService(self._make_settings({"scalp": 12345}))
        result = svc.reconcile([], [])
        assert result["status"] == "clean"
        assert result["owned_positions"] == []
        assert result["foreign_positions"] == []
        assert result["missing_positions"] == []
        assert result["unexpected_positions"] == []

    def test_clean_state_matching(self):
        """Intent matches actual position — clean."""
        svc = ReconciliationService(self._make_settings({"scalp": 12345}))
        pos = self._make_position(position_id="pos_1", strategy_id="scalp")
        result = svc.reconcile(["pos_1"], [pos])
        assert result["status"] == "clean"
        assert len(result["owned_positions"]) == 1
        assert result["missing_positions"] == []
        assert result["unexpected_positions"] == []

    def test_missing_position(self):
        """Intent exists but position not found — missing."""
        svc = ReconciliationService(self._make_settings({"scalp": 12345}))
        result = svc.reconcile(["pos_missing"], [])
        assert result["status"] == "discrepancy"
        assert result["missing_positions"] == ["pos_missing"]

    def test_unexpected_position(self):
        """Position exists but not in intent list — unexpected."""
        svc = ReconciliationService(self._make_settings({"scalp": 12345}))
        pos = self._make_position(position_id="pos_extra", strategy_id="scalp")
        result = svc.reconcile([], [pos])
        assert result["status"] == "discrepancy"
        assert len(result["unexpected_positions"]) == 1
        assert result["unexpected_positions"][0]["position_id"] == "pos_extra"

    def test_both_missing_and_unexpected(self):
        svc = ReconciliationService(self._make_settings({"scalp": 12345}))
        pos = self._make_position(position_id="pos_extra", strategy_id="scalp")
        result = svc.reconcile(["pos_missing"], [pos])
        assert result["status"] == "discrepancy"
        assert result["missing_positions"] == ["pos_missing"]
        assert len(result["unexpected_positions"]) == 1

    def test_foreign_positions_identified(self):
        """Positions not owned by any configured strategy are foreign."""
        svc = ReconciliationService(self._make_settings({"scalp": 12345}))
        owned = self._make_position(position_id="p1", strategy_id="scalp")
        foreign = self._make_position(position_id="p2", strategy_id="unknown")
        result = svc.reconcile(["p1"], [owned, foreign])
        assert len(result["foreign_positions"]) == 1
        assert result["foreign_positions"][0]["position_id"] == "p2"

    def test_result_contains_summary(self):
        svc = ReconciliationService(self._make_settings({"scalp": 12345}))
        pos = self._make_position(position_id="p1", strategy_id="scalp")
        result = svc.reconcile(["p1"], [pos])
        assert "summary" in result
        summary = result["summary"]
        assert summary["expected"] == 1
        assert summary["actual"] == 1
        assert summary["owned"] == 1
        assert summary["foreign"] == 0
        assert summary["missing"] == 0
        assert summary["unexpected"] == 0

    def test_status_clean_when_only_owned_and_no_discrepancies(self):
        svc = ReconciliationService(self._make_settings({"scalp": 12345}))
        p1 = self._make_position(position_id="p1", strategy_id="scalp")
        p2 = self._make_position(position_id="p2", strategy_id="scalp")
        result = svc.reconcile(["p1", "p2"], [p1, p2])
        assert result["status"] == "clean"
