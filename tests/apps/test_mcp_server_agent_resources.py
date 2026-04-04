from __future__ import annotations

from types import SimpleNamespace

import apps.mcp_server.main as mcp_main


def test_resource_account_summary_includes_agent_risk_fields(monkeypatch) -> None:
    monkeypatch.setattr(
        mcp_main,
        "get_gateway",
        lambda: SimpleNamespace(
            account_summary=lambda: mcp_main.AccountSummary(environment="demo")
        ),
    )
    monkeypatch.setattr(
        mcp_main,
        "tool_get_account_summary",
        lambda: {
            "account_id": "270856971",
            "balance": 205.88,
            "equity": 210.11,
            "margin": 25.0,
            "free_margin": 185.11,
            "margin_level": 840.44,
            "leverage": 2000,
            "profit": 4.23,
            "margin_call_level": 60.0,
            "margin_stop_out_level": 20.0,
            "currency": "USD",
            "server": "Exness-MT5Trial17",
            "environment": "demo",
        },
    )

    result = mcp_main.resource_account_summary()

    assert result.account_id == "270856971"
    assert result.leverage == 2000
    assert result.margin_level == 840.44
    assert result.margin_call_level == 60.0
    assert result.margin_stop_out_level == 20.0
    assert result.profit == 4.23


def test_resource_symbol_info_falls_back_to_bridge_data(monkeypatch) -> None:
    monkeypatch.setattr(
        mcp_main,
        "get_gateway",
        lambda: SimpleNamespace(
            adapter=SimpleNamespace(get_symbol_info=lambda symbol: None)
        ),
    )
    monkeypatch.setattr(
        mcp_main,
        "tool_get_symbol_info",
        lambda symbol: {
            "symbol": symbol,
            "digits": 2,
            "point": 0.01,
            "tick_size": 0.01,
            "tick_value": 1.0,
            "contract_size": 100.0,
            "volume_min": 0.01,
            "volume_max": 50.0,
            "volume_step": 0.01,
            "stops_level_points": 50,
            "freeze_level_points": 0,
            "currency_base": "XAU",
            "currency_profit": "USD",
            "currency_margin": "USD",
            "trade_mode": "full",
        },
    )

    result = mcp_main.resource_symbol_info("XAUUSD")

    assert result.symbol == "XAUUSD"
    assert result.tick_size == 0.01
    assert result.volume_step == 0.01
    assert result.trade_mode == "full"


def test_resource_deals_history_falls_back_to_bridge_data(monkeypatch) -> None:
    monkeypatch.setattr(
        mcp_main,
        "get_gateway",
        lambda: SimpleNamespace(
            adapter=SimpleNamespace(
                get_deals_history=lambda limit=100, symbol=None, days=30: []
            )
        ),
    )
    monkeypatch.setattr(
        mcp_main,
        "tool_get_deals_history",
        lambda limit=100, symbol=None, days=30: {
            "deals": [
                {
                    "deal_id": "1",
                    "order_id": "10",
                    "position_id": "10",
                    "symbol": symbol or "BTCUSDm",
                    "side": "buy",
                    "entry": "out",
                    "volume": 0.01,
                    "price": 66500.0,
                    "profit": 12.5,
                    "commission": -0.5,
                    "swap": 0.0,
                    "fee": 0.0,
                    "time": "1775184458",
                }
            ]
        },
    )

    result = mcp_main.resource_deals_history(limit=50, symbol="BTCUSD")

    assert len(result) == 1
    assert result[0].deal_id == "1"
    assert result[0].symbol == "BTCUSD"


def test_resource_performance_summary_uses_deal_history(monkeypatch) -> None:
    monkeypatch.setattr(
        mcp_main,
        "resource_deals_history",
        lambda limit=100, symbol=None, days=30: [
            mcp_main.Deal(
                deal_id="1",
                order_id="10",
                position_id="10",
                symbol="BTCUSD",
                side="buy",
                entry="out",
                volume=0.01,
                price=66500.0,
                profit=20.0,
                commission=-1.0,
                swap=0.0,
                fee=0.0,
                time="1",
            ),
            mcp_main.Deal(
                deal_id="2",
                order_id="11",
                position_id="11",
                symbol="BTCUSD",
                side="sell",
                entry="out",
                volume=0.01,
                price=66000.0,
                profit=-10.0,
                commission=-1.0,
                swap=0.0,
                fee=0.0,
                time="2",
            ),
        ],
    )

    result = mcp_main.resource_performance_summary(limit=100, symbol="BTCUSD")

    assert result.closed_trades == 2
    assert result.winning_trades == 1
    assert result.losing_trades == 1
    assert result.net_profit == 8.0
