"""Tests for indicator bug fixes: _parse_indicator_value, _compute_adx, EMA consistency."""

from __future__ import annotations

import json

import pytest


# ---------------------------------------------------------------------------
# _parse_indicator_value tests (Bug 1 + 8)
# ---------------------------------------------------------------------------


class TestParseIndicatorValue:
    def _make_result(self, payload_dict: dict) -> dict:
        return {
            "status": "completed",
            "result": {"payload": json.dumps(payload_dict)},
        }

    def test_rsi_returns_value(self):
        from apps.mcp_server.shared import _parse_indicator_value

        result = _parse_indicator_value(
            self._make_result({"indicator": "rsi", "value": 65.3})
        )
        assert result == 65.3

    def test_adx_returns_adx_key(self):
        from apps.mcp_server.shared import _parse_indicator_value

        result = _parse_indicator_value(
            self._make_result(
                {"indicator": "adx", "adx": 32.5, "plus_di": 28.1, "minus_di": 15.7}
            )
        )
        assert result == 32.5

    def test_macd_returns_main_key(self):
        from apps.mcp_server.shared import _parse_indicator_value

        result = _parse_indicator_value(
            self._make_result(
                {
                    "indicator": "macd",
                    "main": 0.0015,
                    "signal_val": 0.0012,
                    "hist": 0.0003,
                }
            )
        )
        assert result == 0.0015

    def test_stoch_returns_k_val_key(self):
        from apps.mcp_server.shared import _parse_indicator_value

        result = _parse_indicator_value(
            self._make_result({"indicator": "stoch", "k_val": 78.5, "d_val": 72.1})
        )
        assert result == 78.5

    def test_returns_none_for_timeout(self):
        from apps.mcp_server.shared import _parse_indicator_value

        assert _parse_indicator_value({"status": "timeout"}) is None

    def test_returns_none_for_empty_payload(self):
        from apps.mcp_server.shared import _parse_indicator_value

        assert _parse_indicator_value(self._make_result({})) is None


# ---------------------------------------------------------------------------
# _compute_adx tests (Bug 2)
# ---------------------------------------------------------------------------


class TestComputeAdx:
    def _bars(self, n, trend=0.0, noise=1.0, seed=42):
        import random

        random.seed(seed)
        bars, price = [], 100.0
        for i in range(n):
            d = 1 if random.random() > 0.5 else -1
            drift = d * trend
            bars.append(
                {
                    "time": i,
                    "open": price,
                    "high": price + abs(random.gauss(0, noise)) + drift,
                    "low": price - abs(random.gauss(0, noise)) - drift,
                    "close": price + drift + random.gauss(0, 0.3),
                }
            )
            price += drift
        return bars

    def test_flat_market_returns_low_adx(self):
        from mt5_mcp.services.market_regime import _compute_adx

        bars = [
            {"time": i, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0}
            for i in range(50)
        ]
        adx = _compute_adx(bars, 14)
        assert adx == 0.0

    def test_insufficient_bars_returns_zero(self):
        from mt5_mcp.services.market_regime import _compute_adx

        bars = [
            {"time": i, "open": 100.0, "high": 102.0, "low": 98.0, "close": 101.0}
            for i in range(27)
        ]
        assert _compute_adx(bars, 14) == 0.0

    def test_minimum_bars_does_not_crash(self):
        from mt5_mcp.services.market_regime import _compute_adx

        bars = [
            {"time": i, "open": 100.0, "high": 102.0, "low": 98.0, "close": 101.0}
            for i in range(28)
        ]
        adx = _compute_adx(bars, 14)
        assert isinstance(adx, float)

    def test_trending_market_returns_higher_than_choppy(self):
        from mt5_mcp.services.market_regime import _compute_adx

        trend_adx = _compute_adx(self._bars(100, trend=0.5, noise=0.5), 14)
        chop_adx = _compute_adx(self._bars(100, trend=0.0, noise=1.2), 14)
        assert trend_adx > chop_adx


# ---------------------------------------------------------------------------
# EMA consistency tests (Bug 3)
# ---------------------------------------------------------------------------


class TestEmaConsistency:
    def test_regime_ema_matches_divergence_ema(self):
        from mt5_mcp.services.market_regime import _compute_ema as regime_ema

        data = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0, 108.0, 109.0]
        ema = regime_ema(data, 5)
        # divergence.py style (SMA seed)
        mult = 2.0 / (5 + 1)
        seed = sum(data[:5]) / 5
        expected = [seed]
        for v in data[5:]:
            expected.append((v - expected[-1]) * mult + expected[-1])
        assert ema == pytest.approx(expected)

    def test_ema_returns_empty_for_insufficient_data(self):
        from mt5_mcp.services.market_regime import _compute_ema

        assert _compute_ema([1.0, 2.0], 5) == []
        assert _compute_ema([], 5) == []


# ---------------------------------------------------------------------------
# momentum_push direction test (Bug 4)
# ---------------------------------------------------------------------------


class TestMomentumPushDirection:
    def test_momentum_push_includes_direction(self):
        from mt5_mcp.services.market_regime import detect_regime

        bars = []
        price = 100.0
        for i in range(100):
            bars.append(
                {
                    "time": i,
                    "open": price,
                    "high": price + 3.0,
                    "low": price - 0.3,
                    "close": price + 2.5,
                }
            )
            price += 2.0

        result = detect_regime(bars, atr_value=1.0, ema_fast=120.0, ema_slow=110.0)
        if result["regime"] == "momentum_push":
            assert result["momentum_direction"] == "up"


# ---------------------------------------------------------------------------
# _parse_indicator_value_from_data tests (Bug 9 - mt5_get_indicator)
# ---------------------------------------------------------------------------


class TestParseIndicatorValueFromData:
    def test_adx_returns_adx_key(self):
        from apps.mcp_server.shared import _parse_indicator_value_from_data

        data = {
            "indicator": "adx",
            "period": 14,
            "adx": 34.3,
            "plus_di": 25.1,
            "minus_di": 18.2,
        }
        assert _parse_indicator_value_from_data(data, "adx") == 34.3

    def test_stoch_returns_k_val(self):
        from apps.mcp_server.shared import _parse_indicator_value_from_data

        data = {
            "indicator": "stoch",
            "k": 14,
            "d": 3,
            "slowing": 3,
            "k_val": 72.1,
            "d_val": 68.5,
        }
        assert _parse_indicator_value_from_data(data, "stoch") == 72.1

    def test_macd_returns_main(self):
        from apps.mcp_server.shared import _parse_indicator_value_from_data

        data = {
            "indicator": "macd",
            "main": 0.0012,
            "signal_val": 0.0010,
            "hist": 0.0002,
        }
        assert _parse_indicator_value_from_data(data, "macd") == 0.0012

    def test_uses_ea_indicator_over_caller_name(self):
        """Caller passes 'stochastic' but EA returns 'stoch' — should still work."""
        from apps.mcp_server.shared import _parse_indicator_value_from_data

        data = {"indicator": "stoch", "k_val": 55.5}
        assert _parse_indicator_value_from_data(data, "stochastic") == 55.5

    def test_empty_data_returns_none(self):
        from apps.mcp_server.shared import _parse_indicator_value_from_data

        assert _parse_indicator_value_from_data({}, "adx") is None

    def test_missing_value_returns_none(self):
        from apps.mcp_server.shared import _parse_indicator_value_from_data

        assert (
            _parse_indicator_value_from_data({"indicator": "adx", "period": 14}, "adx")
            is None
        )

    def test_bbands_returns_upper(self):
        from apps.mcp_server.shared import _parse_indicator_value_from_data

        data = {"indicator": "bbands", "upper": 1.172, "middle": 1.169, "lower": 1.166}
        assert _parse_indicator_value_from_data(data, "bbands") == 1.172
