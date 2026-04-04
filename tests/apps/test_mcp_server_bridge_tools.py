from __future__ import annotations

from types import SimpleNamespace

import apps.mcp_server.main as mcp_main
from mt5_mcp.schemas.models import TradeIntent
from mt5_mcp.schemas.tools import OrderBookRequest, TicksRequest


class DummyEnqueueResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, str]:
        return {"id": "req-1"}


class DummyHttpClient:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def __enter__(self) -> "DummyHttpClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def post(self, *args, **kwargs) -> DummyEnqueueResponse:
        return DummyEnqueueResponse()


def _patch_bridge_dependencies(monkeypatch) -> None:
    monkeypatch.setattr(mcp_main.httpx, "Client", DummyHttpClient)
    monkeypatch.setattr(
        mcp_main,
        "get_settings_cached",
        lambda: SimpleNamespace(
            gateway_url="http://127.0.0.1:8020", environment="demo"
        ),
    )
    monkeypatch.setattr(mcp_main, "normalize_symbol", lambda symbol: symbol)


def test_tool_get_ticks_returns_error_payload_on_timeout(monkeypatch) -> None:
    _patch_bridge_dependencies(monkeypatch)
    monkeypatch.setattr(
        mcp_main,
        "_await_result",
        lambda *args, **kwargs: {"status": "timeout", "error": "timeout"},
    )

    result = mcp_main.tool_get_ticks(TicksRequest(symbol="XAUUSD", count=5))

    assert result == {"status": "error", "message": "timeout"}


def test_tool_get_order_book_returns_error_payload_on_timeout(monkeypatch) -> None:
    _patch_bridge_dependencies(monkeypatch)
    monkeypatch.setattr(
        mcp_main,
        "_await_result",
        lambda *args, **kwargs: {"status": "timeout", "error": "timeout"},
    )

    result = mcp_main.tool_get_order_book(OrderBookRequest(symbol="XAUUSD"))

    assert result == {"status": "error", "message": "timeout"}


def test_submit_market_order_parses_error_payload(monkeypatch) -> None:
    _patch_bridge_dependencies(monkeypatch)
    monkeypatch.setattr(
        mcp_main,
        "_await_result",
        lambda *args, **kwargs: {
            "status": "error",
            "error": '{"retcode":10021,"order":0,"deal":0,"bid":4675.25,"ask":4675.64}',
        },
    )
    monkeypatch.setattr(
        mcp_main,
        "validate_submit_order",
        lambda *args, **kwargs: SimpleNamespace(allowed=True, reason=None),
    )

    result = mcp_main.tool_submit_market_order_via_bridge(
        TradeIntent(
            intent_id="intent-1",
            strategy_id="manual",
            account_id="270856971",
            symbol="XAUUSD",
            side="sell",
            volume_lots=0.01,
        )
    )

    assert result.status == "error"
    assert result.raw == {
        "retcode": 10021,
        "order": 0,
        "deal": 0,
        "bid": 4675.25,
        "ask": 4675.64,
    }
    assert result.message == "Order failed: retcode=10021 (PRICE_OFF)"


def test_resource_account_summary_falls_back_to_bridge_data(monkeypatch) -> None:
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
            "equity": 205.88,
            "margin": 0.0,
            "free_margin": 205.88,
            "currency": "USD",
            "environment": "demo",
        },
    )

    result = mcp_main.resource_account_summary()

    assert result.account_id == "270856971"
    assert result.balance == 205.88



def test_resource_positions_open_falls_back_to_bridge_data(monkeypatch) -> None:
    monkeypatch.setattr(
        mcp_main,
        "get_gateway",
        lambda: SimpleNamespace(adapter=SimpleNamespace(get_positions=lambda: [])),
    )
    monkeypatch.setattr(
        mcp_main,
        "tool_get_positions",
        lambda: {
            "positions": [
                {
                    "position_id": "1",
                    "symbol": "BTCUSDm",
                    "side": "buy",
                    "volume": 0.01,
                    "entry_price": 66700.0,
                }
            ]
        },
    )

    result = mcp_main.resource_positions_open()

    assert len(result) == 1
    assert result[0].position_id == "1"
    assert result[0].symbol == "BTCUSDm"



def test_resource_orders_pending_falls_back_to_bridge_data(monkeypatch) -> None:
    monkeypatch.setattr(
        mcp_main,
        "get_gateway",
        lambda: SimpleNamespace(adapter=SimpleNamespace(get_orders=lambda: [])),
    )
    monkeypatch.setattr(
        mcp_main,
        "tool_get_orders",
        lambda: {
            "orders": [
                {
                    "order_id": "2",
                    "symbol": "BTCUSDm",
                    "side": "buy",
                    "kind": "limit",
                    "volume": 0.01,
                    "price": 65000.0,
                }
            ]
        },
    )

    result = mcp_main.resource_orders_pending()

    assert len(result) == 1
    assert result[0].order_id == "2"
    assert result[0].symbol == "BTCUSDm"
