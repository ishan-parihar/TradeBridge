"""Tests for PortfolioRiskService — exposure aggregation and pre-trade gate."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from mt5_mcp.services.portfolio_risk import PortfolioRiskService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_position(symbol: str, side: str, volume: float, mark_price: float):
    """Create a mock Position-like object."""
    m = MagicMock()
    m.symbol = symbol
    m.side = side
    m.volume = volume
    m.mark_price = mark_price
    m.entry_price = mark_price * 0.99
    m.unrealized_pnl = 0.0
    return m


def _make_order(symbol: str, side: str, volume: float, price: float):
    """Create a mock Order-like object."""
    m = MagicMock()
    m.symbol = symbol
    m.side = side
    m.volume = volume
    m.price = price
    m.kind = "limit"
    return m


def _make_account(balance=10000.0, equity=10200.0, margin=500.0, free_margin=9700.0):
    """Create a mock AccountSummary-like object."""
    m = MagicMock()
    m.balance = balance
    m.equity = equity
    m.margin = margin
    m.free_margin = free_margin
    m.currency = "USD"
    return m


def _make_svc(positions=None, orders=None, account=None):
    """Create a PortfolioRiskService with mock callables."""
    return PortfolioRiskService(
        get_positions_fn=lambda: positions or [],
        get_orders_fn=lambda: orders or [],
        get_account_fn=lambda: account or _make_account(),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEmptyPortfolio:
    def test_empty_portfolio_returns_zero_exposure(self):
        svc = _make_svc()
        result = svc.get_exposure()
        assert result["total_exposure_usd"] == 0
        assert result["exposure_by_symbol"] == {}
        assert result["correlation_groups"] == []
        assert result["risk_score"] == 0

    def test_empty_portfolio_risk_score_zero(self):
        svc = _make_svc()
        result = svc.get_exposure()
        assert result["risk_score"] == 0


class TestSinglePosition:
    def test_single_eurusd_long_exposure(self):
        positions = [_make_position("EURUSD", "buy", 0.10, 1.0850)]
        svc = _make_svc(positions=positions)
        result = svc.get_exposure()
        # 0.10 * 100000 * 1.0850 = 10850
        assert result["total_exposure_usd"] > 0
        assert "EURUSD" in result["exposure_by_symbol"]
        eur = result["exposure_by_symbol"]["EURUSD"]
        assert eur["side"] == "buy"
        assert abs(eur["notional_usd"] - 10850.0) < 1.0

    def test_single_position_usd_direction_is_short(self):
        """Long EURUSD → USD-short exposure."""
        positions = [_make_position("EURUSD", "buy", 1.0, 1.0800)]
        svc = _make_svc(positions=positions)
        result = svc.get_exposure()
        assert result["exposure_by_symbol"]["EURUSD"]["usd_direction"] == "usd_short"

    def test_single_usdjpy_long_usd_direction(self):
        """Long USDJPY → USD-long exposure."""
        positions = [_make_position("USDJPY", "buy", 1.0, 150.0)]
        svc = _make_svc(positions=positions)
        result = svc.get_exposure()
        assert result["exposure_by_symbol"]["USDJPY"]["usd_direction"] == "usd_long"


class TestCorrelatedPositions:
    def test_eurusd_gbpusd_long_correlated(self):
        """Two correlated USD-short longs should show positive correlated exposure."""
        positions = [
            _make_position("EURUSD", "buy", 1.0, 1.0850),
            _make_position("GBPUSD", "buy", 1.0, 1.2650),
        ]
        svc = _make_svc(positions=positions)
        result = svc.get_exposure()
        # Both should be in usd_short group
        groups = result["correlation_groups"]
        usd_short = next((g for g in groups if g["group"] == "usd_short"), None)
        assert usd_short is not None
        assert len(usd_short["symbols"]) == 2

    def test_correlated_exposure_differs_from_raw_notional(self):
        """Correlation-adjusted exposure should differ from raw notional."""
        positions = [
            _make_position("EURUSD", "buy", 1.0, 1.0850),
            _make_position("GBPUSD", "buy", 1.0, 1.2650),
        ]
        svc = _make_svc(positions=positions)
        result = svc.get_exposure()
        raw_total = sum(
            d["notional_usd"] for d in result["exposure_by_symbol"].values()
        )
        correlated_total = result["total_exposure_usd"]
        # With 0.80 correlation, correlated should NOT equal raw sum
        assert abs(correlated_total - raw_total) > 1000

    def test_uncorrelated_positions_eurusd_xauusd(self):
        """EURUSD long + XAUUSD long have negative correlation (-0.40)."""
        positions = [
            _make_position("EURUSD", "buy", 1.0, 1.0850),
            _make_position("XAUUSD", "buy", 1.0, 2650.0),
        ]
        svc = _make_svc(positions=positions)
        result = svc.get_exposure()
        # XAUUSD has negative correlation with EURUSD, so effective should differ
        assert "EURUSD" in result["exposure_by_symbol"]
        assert "XAUUSD" in result["exposure_by_symbol"]


class TestMixedHedging:
    def test_mixed_long_short_hedging(self):
        """Long EURUSD + Short EURUSD should reduce net exposure."""
        positions = [
            _make_position("EURUSD", "buy", 1.0, 1.0850),
            _make_position("EURUSD", "sell", 1.0, 1.0840),
        ]
        svc = _make_svc(positions=positions)
        result = svc.get_exposure()
        # Net notional should be close to zero (same volume, opposite sides)
        # The aggregated notional adds both, but correlation with opposite directions
        # should produce near-zero correlated exposure
        assert result["total_exposure_usd"] < 5000  # Much lower than unhedged


class TestPendingOrders:
    def test_projection_includes_pending_orders(self):
        """Projection should have higher exposure when pending orders exist."""
        positions = [_make_position("EURUSD", "buy", 0.10, 1.0850)]
        orders = [_make_order("GBPUSD", "buy", 0.50, 1.2600)]
        svc = _make_svc(positions=positions, orders=orders)
        current = svc.get_exposure()
        projected = svc.get_projection_with_pending()
        assert projected["total_exposure_usd"] > current["total_exposure_usd"]

    def test_projection_adds_new_symbol_group(self):
        pending = [_make_order("USDJPY", "buy", 0.10, 150.0)]
        svc = _make_svc(orders=pending)
        projected = svc.get_projection_with_pending()
        assert "USDJPY" in projected["exposure_by_symbol"]


class TestPreTradeGate:
    def test_pre_trade_gate_approval_low_risk(self):
        svc = _make_svc()
        result = svc.pre_trade_gate("EURUSD", "buy", 0.01, 0.0050)
        assert result["allowed"] is True
        assert result["reason"] == "ok"

    def test_pre_trade_gate_rejection_correlated_exposure(self):
        """Huge volume should push correlated exposure > 2x equity."""
        # Equity = 10200, so correlated > 20400 should reject
        # 2.0 lots EURUSD ≈ 217000 notional — well over threshold
        positions = [_make_position("EURUSD", "buy", 2.0, 1.0850)]
        svc = _make_svc(positions=positions)
        result = svc.pre_trade_gate("GBPUSD", "buy", 2.0, 0.0050)
        assert result["allowed"] is False
        assert result["reason"] == "correlated_exposure_exceeds_threshold"

    def test_pre_trade_gate_rejection_margin_limit(self):
        """High margin usage should reject."""
        account = _make_account(equity=10000.0, margin=4000.0, free_margin=6000.0)
        svc = _make_svc(account=account)
        result = svc.pre_trade_gate("XAUUSD", "buy", 5.0, 10.0)
        # Margin usage is 4000/6000 = 66.7% → already exceeds 50%
        assert result["allowed"] is False
        assert result["reason"] == "margin_would_exceed_limit"

    def test_pre_trade_gate_returns_current_and_projected(self):
        svc = _make_svc()
        result = svc.pre_trade_gate("EURUSD", "buy", 0.01, 0.0050)
        assert "current_exposure" in result
        assert "projected_exposure" in result
        assert "risk_score" in result

    def test_pre_trade_gate_buy_and_sell_sides(self):
        """Gate should work for both buy and sell."""
        svc = _make_svc()
        buy_result = svc.pre_trade_gate("EURUSD", "buy", 0.01, 0.0050)
        sell_result = svc.pre_trade_gate("EURUSD", "sell", 0.01, 0.0050)
        assert buy_result["allowed"] is True
        assert sell_result["allowed"] is True


class TestRiskScore:
    def test_risk_score_scales_with_exposure(self):
        svc_empty = _make_svc()
        empty_score = svc_empty.get_exposure()["risk_score"]

        positions = [_make_position("EURUSD", "buy", 1.0, 1.0850)]
        svc_with_pos = _make_svc(positions=positions)
        with_pos_score = svc_with_pos.get_exposure()["risk_score"]

        assert with_pos_score > empty_score

    def test_risk_score_bounded_0_100(self):
        svc = _make_svc()
        result = svc.get_exposure()
        assert 0 <= result["risk_score"] <= 100


class TestEdgeCases:
    def test_unknown_symbol_not_in_correlation_matrix(self):
        """Symbols not in the correlation matrix should still work."""
        positions = [_make_position("EURTRY", "buy", 0.10, 35.0)]
        svc = _make_svc(positions=positions)
        result = svc.get_exposure()
        assert "EURTRY" in result["exposure_by_symbol"]
        assert result["total_exposure_usd"] > 0

    def test_very_small_volume(self):
        positions = [_make_position("EURUSD", "buy", 0.001, 1.0850)]
        svc = _make_svc(positions=positions)
        result = svc.get_exposure()
        assert result["total_exposure_usd"] > 0
        assert result["total_exposure_usd"] < 200  # 0.001 * 100000 * 1.085 ≈ 108.5

    def test_account_zero_equity(self):
        account = _make_account(equity=0.0, balance=0.0, margin=0.0, free_margin=0.0)
        positions = [_make_position("EURUSD", "buy", 0.10, 1.0850)]
        svc = _make_svc(positions=positions, account=account)
        result = svc.get_exposure()
        assert result["risk_score"] > 0
        # Pre-trade gate should reject
        gate = svc.pre_trade_gate("EURUSD", "buy", 0.01, 0.0050)
        assert gate["allowed"] is False

    def test_multiple_positions_same_symbol(self):
        """Two positions on the same symbol should aggregate correctly."""
        positions = [
            _make_position("EURUSD", "buy", 0.10, 1.0850),
            _make_position("EURUSD", "buy", 0.20, 1.0860),
        ]
        svc = _make_svc(positions=positions)
        result = svc.get_exposure()
        # Should have only one EURUSD entry
        assert "EURUSD" in result["exposure_by_symbol"]
        # Notional should be approximately (0.10 + 0.20) * 100000 * avg_price
        eur = result["exposure_by_symbol"]["EURUSD"]
        assert eur["notional_usd"] > 30000  # 0.30 * 100000 * ~1.0855
