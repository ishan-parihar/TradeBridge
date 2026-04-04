from __future__ import annotations

from math import isclose

from mt5_mcp.services.agent_capabilities import (
    build_volatility_profile,
    calculate_position_size,
    compute_correlation_matrix,
    summarize_deals,
    validate_trade_setup,
)


def test_summarize_deals_uses_realized_closing_pnl_and_costs() -> None:
    summary = summarize_deals(
        [
            {
                "deal_id": "1",
                "entry": "in",
                "side": "buy",
                "profit": 0.0,
                "commission": 0.0,
                "swap": 0.0,
                "fee": 0.0,
            },
            {
                "deal_id": "2",
                "entry": "out",
                "side": "buy",
                "profit": 120.0,
                "commission": -2.0,
                "swap": -1.0,
                "fee": 0.0,
            },
            {
                "deal_id": "3",
                "entry": "out",
                "side": "sell",
                "profit": -50.0,
                "commission": -2.0,
                "swap": 0.0,
                "fee": 0.0,
            },
        ]
    )

    assert summary["closed_trades"] == 2
    assert summary["winning_trades"] == 1
    assert summary["losing_trades"] == 1
    assert isclose(summary["win_rate"], 0.5)
    assert isclose(summary["gross_profit"], 117.0)
    assert isclose(summary["gross_loss"], 52.0)
    assert isclose(summary["net_profit"], 65.0)
    assert isclose(summary["profit_factor"], 2.25)


def test_calculate_position_size_respects_risk_budget_and_volume_step() -> None:
    result = calculate_position_size(
        equity=10_000.0,
        risk_percent=1.0,
        entry_price=1.2050,
        stop_loss_price=1.2000,
        tick_size=0.0001,
        tick_value=10.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
    )

    assert isclose(result["risk_amount"], 100.0)
    assert isclose(result["stop_distance_ticks"], 50.0)
    assert isclose(result["raw_volume_lots"], 0.2)
    assert isclose(result["volume_lots"], 0.2)
    assert isclose(result["estimated_loss_at_stop"], 100.0)


def test_validate_trade_setup_catches_pending_price_and_stop_distance_errors() -> None:
    result = validate_trade_setup(
        symbol_info={
            "digits": 5,
            "point": 0.00001,
            "volume_min": 0.01,
            "volume_max": 100.0,
            "volume_step": 0.01,
            "stops_level_points": 100,
        },
        account_summary={"free_margin": 500.0},
        side="buy",
        order_kind="stop",
        volume_lots=0.01,
        current_bid=1.20480,
        current_ask=1.20500,
        entry_price=1.20400,
        sl=1.20450,
        tp=1.21000,
        required_margin=50.0,
    )

    assert result["valid"] is False
    assert "buy stop entry must be above current ask" in result["errors"]
    assert "stop loss too close to market for broker minimum" in result["errors"]


def test_build_volatility_profile_reports_atr_and_average_range() -> None:
    profile = build_volatility_profile(
        symbol="EURUSD",
        timeframe="H1",
        bars=[
            {"high": 1.2060, "low": 1.2000, "close": 1.2050},
            {"high": 1.2080, "low": 1.2020, "close": 1.2040},
            {"high": 1.2090, "low": 1.2010, "close": 1.2030},
        ],
        atr_value=0.006,
    )

    assert profile["symbol"] == "EURUSD"
    assert profile["timeframe"] == "H1"
    assert isclose(profile["atr_value"], 0.006)
    assert profile["average_range"] > 0
    assert profile["average_range_percent"] > 0
    assert profile["atr_percent_of_price"] > 0


def test_compute_correlation_matrix_uses_close_to_close_returns() -> None:
    matrix = compute_correlation_matrix(
        {
            "AAA": [100.0, 101.0, 100.0, 101.0, 100.0],
            "BBB": [200.0, 202.0, 200.0, 202.0, 200.0],
            "CCC": [100.0, 99.0, 100.0, 99.0, 100.0],
        }
    )

    assert isclose(matrix["AAA"]["BBB"], 1.0, abs_tol=1e-6)
    assert matrix["AAA"]["CCC"] < 0
