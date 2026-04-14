"""Tests for Vibe-Trading signal translator."""

import json

from apps.vibe_bridge.signal_translator import (
    SignalAction,
    SignalStrength,
    TradeSignal,
    extract_signal_from_backtest,
    extract_signal_from_swarm_report,
)


class TestTradeSignal:
    def test_symbol_mapping_xau(self):
        signal = TradeSignal(action=SignalAction.BUY, symbol="XAU/USD")
        params = signal.to_order_params()
        assert params["symbol"] == "XAUUSD"

    def test_symbol_mapping_btc(self):
        signal = TradeSignal(action=SignalAction.BUY, symbol="BTC-USDT")
        params = signal.to_order_params()
        assert params["symbol"] == "BTCUSD"

    def test_symbol_mapping_already_mt5(self):
        signal = TradeSignal(action=SignalAction.SELL, symbol="EURUSD")
        params = signal.to_order_params()
        assert params["symbol"] == "EURUSD"

    def test_to_order_params_includes_levels(self):
        signal = TradeSignal(
            action=SignalAction.BUY,
            symbol="XAUUSD",
            entry_price=2350.0,
            stop_loss=2340.0,
            take_profit=2380.0,
        )
        params = signal.to_order_params()
        assert params["price"] == 2350.0
        assert params["sl"] == 2340.0
        assert params["tp"] == 2380.0

    def test_risk_reward_calculation(self):
        signal = TradeSignal(
            action=SignalAction.BUY,
            symbol="EURUSD",
            entry_price=1.0850,
            stop_loss=1.0830,
            take_profit=1.0910,
        )
        assert signal.risk_reward == 3.0

    def test_comment_truncated(self):
        signal = TradeSignal(
            action=SignalAction.BUY,
            symbol="XAUUSD",
            reasoning="A" * 200,
        )
        params = signal.to_order_params()
        assert params["comment"] == "Vibe: " + "A" * 100


class TestExtractSignalFromSwarm:
    def test_buy_signal_extraction(self):
        report = """
        Investment Committee Decision:
        BUY XAUUSD at $2350.00
        Entry at 2350.00, stop loss at 2340.00
        Take profit at 2380.00
        Confidence: 75%
        The gold market shows strong bullish momentum due to geopolitical uncertainty.
        """
        signal = extract_signal_from_swarm_report(report, "investment_committee")
        assert signal is not None
        assert signal.action == SignalAction.BUY
        assert signal.entry_price == 2350.0
        assert signal.stop_loss == 2340.0
        assert signal.take_profit == 2380.0
        assert signal.confidence == 0.75
        assert signal.strength == SignalStrength.STRONG
        assert "investment_committee" in signal.source

    def test_sell_signal_extraction(self):
        report = "Recommendation: SELL EUR/USD. Target: 1.0800. SL: 1.0900."
        signal = extract_signal_from_swarm_report(report)
        assert signal is not None
        assert signal.action == SignalAction.SELL
        assert "EURUSD" in signal.symbol

    def test_no_signal_in_report(self):
        report = "The market is consolidating. No clear direction at this time."
        signal = extract_signal_from_swarm_report(report)
        assert signal is None

    def test_risk_reward_on_swarm_signal(self):
        report = """
        BUY EURUSD
        Entry at 1.0850
        Stop loss at 1.0830
        Take profit at 1.0910
        """
        signal = extract_signal_from_swarm_report(report)
        assert signal is not None
        assert signal.risk_reward == 3.0


class TestExtractSignalFromBacktest:
    def test_good_backtest_generates_buy(self):
        result = json.dumps(
            {
                "sharpe_ratio": 1.5,
                "max_drawdown": 0.12,
                "win_rate": 0.62,
                "total_return": 0.25,
            }
        )
        signal = extract_signal_from_backtest(result, "XAUUSD")
        assert signal is not None
        assert signal.action == SignalAction.BUY
        assert signal.confidence > 0.3
        assert "Backtest" in signal.reasoning

    def test_bad_backtest_generates_hold(self):
        result = json.dumps(
            {
                "sharpe_ratio": -0.3,
                "max_drawdown": 0.45,
                "total_return": -0.15,
            }
        )
        signal = extract_signal_from_backtest(result, "EURUSD")
        assert signal is not None
        assert signal.action == SignalAction.HOLD

    def test_invalid_json_returns_none(self):
        assert extract_signal_from_backtest("not json") is None

    def test_backtest_with_dict_input(self):
        result = {
            "sharpe_ratio": 2.0,
            "max_drawdown": 0.08,
            "total_return": 0.35,
        }
        signal = extract_signal_from_backtest(result, "GBPUSD")
        assert signal is not None
        assert signal.action == SignalAction.BUY
        assert signal.confidence > 0.5
