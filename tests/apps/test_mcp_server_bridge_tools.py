from __future__ import annotations

from types import SimpleNamespace
import json

import apps.mcp_server.main as mcp_main
from mt5_mcp.schemas.models import TradeIntent
from mt5_mcp.schemas.tools import (
    ClosePositionRequest,
    ModifyPositionSLTPRequest,
    OrderBookRequest,
    SubmitPendingOrderRequest,
    TicksRequest,
    ModifyOrderRequest,
    CancelOrderRequest,
    CloseAllPositionsRequest,
    CancelAllOrdersRequest,
)


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
    monkeypatch.setattr(mcp_main, "_tcp_send_and_await", lambda *a, **kw: None)


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

    assert "positions" in result
    assert "sync_status" in result
    positions = result["positions"]
    assert len(positions) == 1
    assert positions[0]["position_id"] == "1"
    assert positions[0]["symbol"] == "BTCUSDm"
    assert "health" in positions[0]


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


# ---------------------------------------------------------------------------
# Task 1.1.7-1.1.10 — Ownership fields passthrough on write tools
# ---------------------------------------------------------------------------


def _make_enqueue_recorder():
    """Return an (EnqueueResponse class, HttpClient class, recorder) triple.

    The recorder captures every POST call made to the enqueue endpoint so
    tests can assert ownership fields are present in the wire payload.
    """
    calls: list[dict] = []

    class RecordingEnqueueResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"id": "req-rec-1"}

    class RecordingHttpClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self) -> "RecordingHttpClient":
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def post(self, *args, **kwargs) -> RecordingEnqueueResponse:
            # Capture the params dict — handle both `params=` and raw body
            params = kwargs.get("params", {})
            calls.append(params)
            return RecordingEnqueueResponse()

    return RecordingEnqueueResponse, RecordingHttpClient, calls


def _patch_http_client(monkeypatch, rec_client):
    """Replace the global HTTP client and patch the factory."""
    monkeypatch.setattr(mcp_main, "_http_client", None)
    monkeypatch.setattr(mcp_main.httpx, "Client", rec_client)


def test_submit_market_order_via_bridge_passes_ownership_fields(monkeypatch) -> None:
    """Task 1.1.7 — Ownership fields from TradeIntent survive the full path."""
    RecResp, RecClient, calls = _make_enqueue_recorder()
    _patch_http_client(monkeypatch, RecClient)
    monkeypatch.setattr(
        mcp_main,
        "get_settings_cached",
        lambda: SimpleNamespace(
            gateway_url="http://127.0.0.1:8020", environment="demo"
        ),
    )
    monkeypatch.setattr(mcp_main, "normalize_symbol", lambda symbol: symbol)
    monkeypatch.setattr(mcp_main, "_tcp_send_and_await", lambda *a, **kw: None)
    monkeypatch.setattr(
        mcp_main,
        "validate_submit_order",
        lambda *args, **kwargs: SimpleNamespace(allowed=True, reason=None),
    )
    monkeypatch.setattr(
        mcp_main,
        "_await_result",
        lambda *args, **kwargs: {
            "status": "completed",
            "result": {"payload": '{"retcode":10009,"order":123,"deal":456}'},
        },
    )

    mcp_main.tool_submit_market_order_via_bridge(
        TradeIntent(
            intent_id="intent-own-1",
            strategy_id="strat-alpha",
            session_id="sess-42",
            idempotency_key="idem-abc",
            account_id="270856971",
            symbol="XAUUSD",
            side="buy",
            volume_lots=0.01,
        )
    )

    assert len(calls) == 1, "Expected exactly one enqueue call"
    params = calls[0]
    assert params["session_id"] == "sess-42"
    assert params["strategy_id"] == "strat-alpha"
    assert params["intent_id"] == "intent-own-1"
    assert params["idempotency_key"] == "idem-abc"


def test_submit_pending_order_passes_ownership_fields(monkeypatch) -> None:
    """Task 1.1.8 — Ownership fields are forwarded in TCP and HTTP payloads."""
    RecResp, RecClient, calls = _make_enqueue_recorder()
    _patch_http_client(monkeypatch, RecClient)
    monkeypatch.setattr(
        mcp_main,
        "get_settings_cached",
        lambda: SimpleNamespace(
            gateway_url="http://127.0.0.1:8020", environment="demo"
        ),
    )
    monkeypatch.setattr(mcp_main, "normalize_symbol", lambda symbol: symbol)
    monkeypatch.setattr(mcp_main, "_tcp_send_and_await", lambda *a, **kw: None)
    monkeypatch.setattr(
        mcp_main,
        "validate_submit_order",
        lambda *args, **kwargs: SimpleNamespace(allowed=True, reason=None),
    )
    monkeypatch.setattr(
        mcp_main,
        "_await_result",
        lambda *args, **kwargs: {"status": "completed", "result": {"payload": "{}"}},
    )

    mcp_main.tool_submit_pending_order(
        SubmitPendingOrderRequest(
            session_id="sess-10",
            strategy_id="strat-beta",
            intent_id="intent-pending-1",
            idempotency_key="idem-pending",
            symbol="EURUSD",
            side="sell",
            kind="limit",
            price=1.0850,
            volume_lots=0.05,
        )
    )

    assert len(calls) == 1
    params = calls[0]
    assert params["session_id"] == "sess-10"
    assert params["strategy_id"] == "strat-beta"
    assert params["intent_id"] == "intent-pending-1"
    assert params["idempotency_key"] == "idem-pending"


def test_close_position_passes_ownership_fields(monkeypatch) -> None:
    """Task 1.1.9 — Ownership fields forwarded on close_position."""
    RecResp, RecClient, calls = _make_enqueue_recorder()
    _patch_http_client(monkeypatch, RecClient)
    monkeypatch.setattr(
        mcp_main,
        "get_settings_cached",
        lambda: SimpleNamespace(
            gateway_url="http://127.0.0.1:8020", environment="demo"
        ),
    )
    monkeypatch.setattr(mcp_main, "_tcp_send_and_await", lambda *a, **kw: None)
    monkeypatch.setattr(
        mcp_main,
        "_await_result",
        lambda *args, **kwargs: {"status": "completed", "result": {"payload": "{}"}},
    )

    mcp_main.tool_close_position(
        ClosePositionRequest(
            position_id="pos-99",
            volume=0.02,
            session_id="sess-20",
            strategy_id="strat-gamma",
            intent_id="intent-close-1",
            idempotency_key="idem-close",
        )
    )

    assert len(calls) == 1
    params = calls[0]
    assert params["session_id"] == "sess-20"
    assert params["strategy_id"] == "strat-gamma"
    assert params["intent_id"] == "intent-close-1"
    assert params["idempotency_key"] == "idem-close"


def test_modify_position_sl_tp_passes_ownership_fields(monkeypatch) -> None:
    """Task 1.1.10 — Ownership fields forwarded on modify_position_sl_tp."""
    RecResp, RecClient, calls = _make_enqueue_recorder()
    _patch_http_client(monkeypatch, RecClient)
    monkeypatch.setattr(
        mcp_main,
        "get_settings_cached",
        lambda: SimpleNamespace(
            gateway_url="http://127.0.0.1:8020", environment="demo"
        ),
    )
    monkeypatch.setattr(mcp_main, "_tcp_send_and_await", lambda *a, **kw: None)
    monkeypatch.setattr(
        mcp_main,
        "_await_result",
        lambda *args, **kwargs: {"status": "completed", "result": {"payload": "{}"}},
    )

    mcp_main.tool_modify_position_sl_tp(
        ModifyPositionSLTPRequest(
            position_id="pos-77",
            sl=1.0800,
            tp=1.0950,
            session_id="sess-30",
            strategy_id="strat-delta",
            intent_id="intent-modify-1",
            idempotency_key="idem-modify",
        )
    )

    assert len(calls) == 1
    params = calls[0]
    assert params["session_id"] == "sess-30"
    assert params["strategy_id"] == "strat-delta"
    assert params["intent_id"] == "intent-modify-1"
    assert params["idempotency_key"] == "idem-modify"


def test_modify_order_passes_ownership_fields(monkeypatch) -> None:
    """Task 1.1.11 — Ownership fields forwarded on modify_order."""
    RecResp, RecClient, calls = _make_enqueue_recorder()
    _patch_http_client(monkeypatch, RecClient)
    monkeypatch.setattr(
        mcp_main,
        "get_settings_cached",
        lambda: SimpleNamespace(
            gateway_url="http://127.0.0.1:8020", environment="demo"
        ),
    )
    monkeypatch.setattr(mcp_main, "normalize_symbol", lambda symbol: symbol)
    monkeypatch.setattr(mcp_main, "_tcp_send_and_await", lambda *a, **kw: None)
    monkeypatch.setattr(
        mcp_main,
        "_await_result",
        lambda *args, **kwargs: {"status": "completed", "result": {"payload": "{}"}},
    )

    mcp_main.tool_modify_order(
        ModifyOrderRequest(
            order_id="12345",
            new_price=1.0850,
            session_id="sess-40",
            strategy_id="strat-eps",
            intent_id="intent-modord-1",
            idempotency_key="idem-modord",
        )
    )

    assert len(calls) == 1
    params = calls[0]
    assert params["session_id"] == "sess-40"
    assert params["strategy_id"] == "strat-eps"
    assert params["intent_id"] == "intent-modord-1"
    assert params["idempotency_key"] == "idem-modord"


def test_cancel_order_passes_ownership_fields(monkeypatch) -> None:
    """Task 1.1.12 — Ownership fields forwarded on cancel_order."""
    RecResp, RecClient, calls = _make_enqueue_recorder()
    _patch_http_client(monkeypatch, RecClient)
    monkeypatch.setattr(
        mcp_main,
        "get_settings_cached",
        lambda: SimpleNamespace(
            gateway_url="http://127.0.0.1:8020", environment="demo"
        ),
    )
    monkeypatch.setattr(mcp_main, "_tcp_send_and_await", lambda *a, **kw: None)
    monkeypatch.setattr(
        mcp_main,
        "_await_result",
        lambda *args, **kwargs: {"status": "completed", "result": {"payload": "{}"}},
    )

    mcp_main.tool_cancel_order(
        CancelOrderRequest(
            order_id="67890",
            session_id="sess-50",
            strategy_id="strat-zeta",
            intent_id="intent-cancel-1",
            idempotency_key="idem-cancel",
        )
    )

    assert len(calls) == 1
    params = calls[0]
    assert params["session_id"] == "sess-50"
    assert params["strategy_id"] == "strat-zeta"
    assert params["intent_id"] == "intent-cancel-1"
    assert params["idempotency_key"] == "idem-cancel"


def test_close_all_positions_passes_ownership_fields(monkeypatch) -> None:
    """Task 1.1.13 — Ownership fields forwarded on close_all_positions."""
    RecResp, RecClient, calls = _make_enqueue_recorder()
    _patch_http_client(monkeypatch, RecClient)
    monkeypatch.setattr(
        mcp_main,
        "get_settings_cached",
        lambda: SimpleNamespace(
            gateway_url="http://127.0.0.1:8020", environment="demo"
        ),
    )
    monkeypatch.setattr(mcp_main, "normalize_symbol", lambda symbol: symbol)
    monkeypatch.setattr(mcp_main, "_tcp_send_and_await", lambda *a, **kw: None)
    monkeypatch.setattr(
        mcp_main,
        "_await_result",
        lambda *args, **kwargs: {"status": "completed", "result": {"payload": "{}"}},
    )

    mcp_main.tool_close_all_positions(
        CloseAllPositionsRequest(
            side="both",
            symbol="EURUSD",
            session_id="sess-60",
            strategy_id="strat-eta",
            intent_id="intent-closeall-1",
            idempotency_key="idem-closeall",
        )
    )

    assert len(calls) == 1
    params = calls[0]
    assert params["session_id"] == "sess-60"
    assert params["strategy_id"] == "strat-eta"
    assert params["intent_id"] == "intent-closeall-1"
    assert params["idempotency_key"] == "idem-closeall"


def test_cancel_all_orders_passes_ownership_fields(monkeypatch) -> None:
    """Task 1.1.14 — Ownership fields forwarded on cancel_all_orders."""
    RecResp, RecClient, calls = _make_enqueue_recorder()
    _patch_http_client(monkeypatch, RecClient)
    monkeypatch.setattr(
        mcp_main,
        "get_settings_cached",
        lambda: SimpleNamespace(
            gateway_url="http://127.0.0.1:8020", environment="demo"
        ),
    )
    monkeypatch.setattr(mcp_main, "normalize_symbol", lambda symbol: symbol)
    monkeypatch.setattr(mcp_main, "_tcp_send_and_await", lambda *a, **kw: None)
    monkeypatch.setattr(
        mcp_main,
        "_await_result",
        lambda *args, **kwargs: {"status": "completed", "result": {"payload": "{}"}},
    )

    mcp_main.tool_cancel_all_orders(
        CancelAllOrdersRequest(
            side="both",
            symbol="XAUUSD",
            session_id="sess-70",
            strategy_id="strat-theta",
            intent_id="intent-cancelall-1",
            idempotency_key="idem-cancelall",
        )
    )

    assert len(calls) == 1
    params = calls[0]
    assert params["session_id"] == "sess-70"
    assert params["strategy_id"] == "strat-theta"
    assert params["intent_id"] == "intent-cancelall-1"
    assert params["idempotency_key"] == "idem-cancelall"
