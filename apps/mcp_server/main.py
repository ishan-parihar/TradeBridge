from __future__ import annotations

from fastapi import FastAPI, HTTPException
import httpx

from mt5_mcp.observability.logging import setup_logging
from mt5_mcp.schemas.models import (
    AccountSummary,
    Bars,
    Order,
    Position,
    ExecutionResult,
    HealthStatus,
    MarginEstimate,
    MarginEstimateRequest,
    SimulationResult,
    TerminalStatus,
    TradeIntent,
)
from mt5_mcp.services.execution_gateway.service import ExecutionGateway
from mt5_mcp.settings.config import get_settings
from mt5_mcp.adapters.common.symbol_utils import normalize_symbol, denormalize_symbol
from mt5_mcp.schemas.tools import (
    BarsRequest,
    IndicatorRequest,
    ChartScreenshotRequest,
    ChartScreenshotResult,
    ModifyPositionSLTPRequest,
    ClosePositionRequest as ClosePosReq,
    SubmitPendingOrderRequest,
    CancelOrderRequest,
    TicksRequest,
    OrderBookRequest,
    ModifyOrderRequest as ModOrderReq,
    CloseAllPositionsRequest,
    CancelAllOrdersRequest,
)
from mt5_mcp.policy.engine import validate_submit_order


setup_logging()
app = FastAPI(title="MT5 MCP Server", version="0.1.0")
gw = ExecutionGateway()
settings = get_settings()


# Resources (read-only)
@app.get("/resources/mt5/terminal/status", response_model=TerminalStatus)
def resource_terminal_status() -> TerminalStatus:
    return gw.terminal_status()


@app.get("/resources/account/summary", response_model=AccountSummary)
def resource_account_summary() -> AccountSummary:
    return gw.account_summary()


@app.get("/resources/bars/{symbol}/{timeframe}", response_model=Bars)
def resource_bars(symbol: str, timeframe: str, count: int = 100) -> Bars:
    return gw.get_bars(symbol, timeframe, count)


@app.get("/resources/positions/open", response_model=list[Position])
def resource_positions_open() -> list[Position]:
    return gw.adapter.get_positions()


@app.get("/resources/orders/pending", response_model=list[Order])
def resource_orders_pending() -> list[Order]:
    return gw.adapter.get_orders()


@app.get("/health", response_model=HealthStatus)
def health() -> HealthStatus:
    return gw.health()


@app.get("/resources/mt5/bridge/status", response_model=TerminalStatus)
def resource_bridge_terminal_status() -> TerminalStatus:
    # Proxy bridge heartbeat status into MCP for unified visibility
    try:
        with httpx.Client(timeout=2.0) as client:
            r = client.get(f"{settings.gateway_url}/bridge/terminal/status")
            r.raise_for_status()
            data = r.json()
            return TerminalStatus(**data)
    except Exception:
        return TerminalStatus(connected=False, message="Bridge status unavailable")


# Bridge-backed tools (EA polling model)
def _await_result(req_id: str, timeout_s: float = 10.0, poll_s: float = 0.5) -> dict:
    import time as _t

    with httpx.Client(timeout=2.0) as client:
        end = _t.time() + timeout_s
        while _t.time() < end:
            r = client.get(f"{settings.gateway_url}/bridge/results/{req_id}")
            if r.status_code == 200:
                data = r.json()
                if data.get("status") in {"completed", "error"}:
                    return data
            _t.sleep(poll_s)
    return {"status": "timeout", "error": "timeout"}


@app.post("/tools/get_bars", response_model=Bars)
def tool_get_bars(req: BarsRequest) -> Bars:
    symbol_normalized = normalize_symbol(req.symbol)
    with httpx.Client(timeout=2.0) as client:
        # enqueue
        r = client.post(
            f"{settings.gateway_url}/bridge/commands/enqueue",
            params={
                "type": "get_bars",
                "symbol": symbol_normalized,
                "timeframe": req.timeframe,
                "count": req.count,
            },
        )
        r.raise_for_status()
        req_id = r.json()["id"]
    res = _await_result(req_id)
    if res.get("status") != "completed":
        return Bars(
            symbol=req.symbol, timeframe=req.timeframe, data=[], source="bridge"
        )
    payload = res.get("result", {}).get("payload", {})
    # Handle both string and dict payloads
    if isinstance(payload, str):
        try:
            data = httpx.Response(200, content=payload).json()
        except Exception:
            data = {"data": []}
    elif isinstance(payload, dict):
        data = payload
    else:
        data = {"data": []}
    # Expect payload: {"symbol":"...","timeframe":"...","data":[{"time":...,"open":...}]}
    # Denormalize symbol in response for user-facing display
    if "symbol" in data:
        data["symbol"] = denormalize_symbol(data["symbol"])
    return Bars(**data)


@app.post("/tools/get_indicator")
def tool_get_indicator(req: IndicatorRequest) -> dict:
    symbol_normalized = normalize_symbol(req.symbol)
    params = {
        "type": "get_indicator",
        "symbol": symbol_normalized,
        "timeframe": req.timeframe,
        "indicator": req.indicator,
    }
    # Only include optional params when provided
    for key, val in (
        ("period", req.period),
        ("fast", req.fast),
        ("slow", req.slow),
        ("signal", req.signal),
        ("deviation", req.deviation),
        ("shift", req.shift),
        ("k_period", req.k_period),
        ("d_period", req.d_period),
        ("slowing", req.slowing),
        ("tenkan", req.tenkan),
        ("kijun", req.kijun),
        ("senkou", req.senkou),
        ("window", req.window),
    ):
        if val is not None:
            params[key] = val
    with httpx.Client(timeout=2.0) as client:
        r = client.post(
            f"{settings.gateway_url}/bridge/commands/enqueue",
            params=params,
        )
        r.raise_for_status()
        req_id = r.json()["id"]
    res = _await_result(req_id)
    if res.get("status") != "completed":
        return {"status": "error", "message": res.get("error", "timeout")}
    payload = res.get("result", {}).get("payload", {})
    # Handle both string and dict payloads
    if isinstance(payload, str):
        try:
            data = httpx.Response(200, content=payload).json()
        except Exception:
            data = {}
    elif isinstance(payload, dict):
        data = payload
    else:
        data = {}
    # Denormalize symbol in response
    if "symbol" in data:
        data["symbol"] = denormalize_symbol(data["symbol"])
    return data


@app.post("/tools/get_chart_screenshot", response_model=ChartScreenshotResult)
def tool_get_chart_screenshot(req: ChartScreenshotRequest) -> ChartScreenshotResult:
    symbol_normalized = normalize_symbol(req.symbol)
    with httpx.Client(timeout=2.0) as client:
        r = client.post(
            f"{settings.gateway_url}/bridge/commands/enqueue",
            params={
                "type": "get_chart_screenshot",
                "symbol": symbol_normalized,
                "timeframe": req.timeframe,
                "width": req.width,
                "height": req.height,
            },
        )
        r.raise_for_status()
        req_id = r.json()["id"]
    res = _await_result(req_id, timeout_s=15.0)
    payload = res.get("result", {}).get("payload", "{}")
    try:
        data = httpx.Response(200, content=payload).json()
    except Exception:
        data = {"image_base64": ""}
    return ChartScreenshotResult(**data)


@app.post("/tools/get_ticks", response_model=dict)
def tool_get_ticks(req: TicksRequest) -> dict:
    symbol_normalized = normalize_symbol(req.symbol)
    with httpx.Client(timeout=2.0) as client:
        r = client.post(
            f"{settings.gateway_url}/bridge/commands/enqueue",
            params={
                "type": "get_ticks",
                "symbol": symbol_normalized,
                "count": req.count,
            },
        )
        r.raise_for_status()
        req_id = r.json()["id"]
    res = _await_result(req_id)
    payload = res.get("result", {}).get("payload", "{}")
    try:
        data = httpx.Response(200, content=payload).json()
    except Exception:
        data = {}
    # Denormalize symbol in response
    if "symbol" in data:
        data["symbol"] = denormalize_symbol(data["symbol"])
    return data


@app.post("/tools/get_order_book", response_model=dict)
def tool_get_order_book(req: OrderBookRequest) -> dict:
    symbol_normalized = normalize_symbol(req.symbol)
    with httpx.Client(timeout=2.0) as client:
        r = client.post(
            f"{settings.gateway_url}/bridge/commands/enqueue",
            params={"type": "get_order_book", "symbol": symbol_normalized},
        )
        r.raise_for_status()
        req_id = r.json()["id"]
    res = _await_result(req_id)
    payload = res.get("result", {}).get("payload", "{}")
    try:
        data = httpx.Response(200, content=payload).json()
    except Exception:
        data = {}
    # Denormalize symbol in response
    if "symbol" in data:
        data["symbol"] = denormalize_symbol(data["symbol"])
    return data


@app.post("/tools/modify_order", response_model=dict)
def tool_modify_order(req: ModOrderReq) -> dict:
    params: dict[str, object] = {"type": "modify_order", "order_id": req.order_id}
    # Only include fields when provided to avoid unintended zeroing in EA
    if req.new_price is not None:
        params["new_price"] = req.new_price
    if req.new_sl is not None:
        params["new_sl"] = req.new_sl
    if req.new_tp is not None:
        params["new_tp"] = req.new_tp
    with httpx.Client(timeout=2.0) as client:
        r = client.post(
            f"{settings.gateway_url}/bridge/commands/enqueue",
            params=params,
        )
        r.raise_for_status()
        req_id = r.json()["id"]
    res = _await_result(req_id)
    return res


@app.post("/tools/close_all_positions", response_model=dict)
def tool_close_all_positions(req: CloseAllPositionsRequest) -> dict:
    params: dict[str, object] = {"type": "close_all_positions", "side": req.side}
    if req.symbol is not None and req.symbol != "":
        params["symbol"] = normalize_symbol(req.symbol)
    with httpx.Client(timeout=2.0) as client:
        r = client.post(
            f"{settings.gateway_url}/bridge/commands/enqueue",
            params=params,
        )
        r.raise_for_status()
        req_id = r.json()["id"]
    res = _await_result(req_id, timeout_s=60.0)
    return res


@app.post("/tools/cancel_all_orders", response_model=dict)
def tool_cancel_all_orders(req: CancelAllOrdersRequest) -> dict:
    params: dict[str, object] = {"type": "cancel_all_orders", "side": req.side}
    if req.symbol is not None and req.symbol != "":
        params["symbol"] = normalize_symbol(req.symbol)
    with httpx.Client(timeout=2.0) as client:
        r = client.post(
            f"{settings.gateway_url}/bridge/commands/enqueue",
            params=params,
        )
        r.raise_for_status()
        req_id = r.json()["id"]
    res = _await_result(req_id, timeout_s=60.0)
    return res


@app.post("/tools/submit_market_order_via_bridge", response_model=ExecutionResult)
def tool_submit_market_order_via_bridge(req: TradeIntent) -> ExecutionResult:
    # Policy gate
    decision = validate_submit_order(settings.environment, None)
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.reason or "denied")
    # Normalize symbol for EA
    symbol_normalized = normalize_symbol(req.symbol)
    # Enqueue order submit and await result
    with httpx.Client(timeout=2.0) as client:
        r = client.post(
            f"{settings.gateway_url}/bridge/commands/enqueue",
            params={
                "type": "submit_order",
                "symbol": symbol_normalized,
                "side": req.side,
                "volume_lots": req.volume_lots,
                "sl": req.sl or 0,
                "tp": req.tp or 0,
                "deviation": req.deviation_points or 20,
            },
        )
        r.raise_for_status()
        req_id = r.json()["id"]
    res = _await_result(req_id, timeout_s=20.0)
    if res.get("status") != "completed":
        return ExecutionResult(
            intent_id=req.intent_id, status="error", message=res.get("error", "timeout")
        )
    payload = res.get("result", {}).get("payload", "{}")
    try:
        data = httpx.Response(200, content=payload).json()
    except Exception:
        data = {}

    # Map basic fields with retcode interpretation
    def _map_retcode(rc: int | str | None) -> str | None:
        try:
            ival = int(rc) if rc is not None else None
        except Exception:
            return None
        mapping = {
            10009: "DONE",
            10008: "PLACED",
            10004: "REQUOTE",
            10006: "REJECTED",
            10021: "INVALID_PRICE",
            10030: "MARKET_CLOSED",
            10032: "NO_MONEY",
        }
        return mapping.get(ival, str(ival) if ival is not None else None)

    return ExecutionResult(
        intent_id=req.intent_id,
        status="submitted",
        adapter="EASocketAdapter",
        broker_order_id=str(data.get("order", "")) if data else None,
        retcode=_map_retcode(data.get("retcode")) if data else None,
        message=None,
    )


@app.get("/tools/get_account_summary", response_model=dict)
def tool_get_account_summary() -> dict:
    # via EA bridge command
    with httpx.Client(timeout=2.0) as client:
        r = client.post(
            f"{settings.gateway_url}/bridge/commands/enqueue",
            params={"type": "get_account"},
        )
        r.raise_for_status()
        req_id = r.json()["id"]
    res = _await_result(req_id)
    payload = res.get("result", {}).get("payload", "{}")
    try:
        data = httpx.Response(200, content=payload).json()
    except Exception:
        data = {}
    return data


@app.get("/tools/get_positions", response_model=dict)
def tool_get_positions() -> dict:
    with httpx.Client(timeout=2.0) as client:
        r = client.post(
            f"{settings.gateway_url}/bridge/commands/enqueue",
            params={"type": "get_positions"},
        )
        r.raise_for_status()
        req_id = r.json()["id"]
    res = _await_result(req_id)
    payload = res.get("result", {}).get("payload", "{}")
    try:
        data = httpx.Response(200, content=payload).json()
    except Exception:
        data = {}
    return data


@app.get("/tools/get_orders", response_model=dict)
def tool_get_orders() -> dict:
    with httpx.Client(timeout=2.0) as client:
        r = client.post(
            f"{settings.gateway_url}/bridge/commands/enqueue",
            params={"type": "get_orders"},
        )
        r.raise_for_status()
        req_id = r.json()["id"]
    res = _await_result(req_id)
    payload = res.get("result", {}).get("payload", "{}")
    try:
        data = httpx.Response(200, content=payload).json()
    except Exception:
        data = {}
    return data


@app.post("/tools/modify_position_sl_tp", response_model=dict)
def tool_modify_position_sl_tp(req: ModifyPositionSLTPRequest) -> dict:
    with httpx.Client(timeout=2.0) as client:
        r = client.post(
            f"{settings.gateway_url}/bridge/commands/enqueue",
            params={
                "type": "modify_position_sl_tp",
                "position_id": req.position_id,
                "sl": req.sl or 0,
                "tp": req.tp or 0,
            },
        )
        r.raise_for_status()
        req_id = r.json()["id"]
    res = _await_result(req_id)
    return res


@app.post("/tools/close_position", response_model=dict)
def tool_close_position(req: ClosePosReq) -> dict:
    with httpx.Client(timeout=2.0) as client:
        r = client.post(
            f"{settings.gateway_url}/bridge/commands/enqueue",
            params={
                "type": "close_position",
                "position_id": req.position_id,
                "volume": req.volume or 0,
            },
        )
        r.raise_for_status()
        req_id = r.json()["id"]
    res = _await_result(req_id, timeout_s=20.0)
    return res


@app.post("/tools/submit_pending_order", response_model=dict)
def tool_submit_pending_order(req: SubmitPendingOrderRequest) -> dict:
    decision = validate_submit_order(settings.environment, None)
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.reason or "denied")
    symbol_normalized = normalize_symbol(req.symbol)
    with httpx.Client(timeout=2.0) as client:
        r = client.post(
            f"{settings.gateway_url}/bridge/commands/enqueue",
            params={
                "type": "submit_pending_order",
                "symbol": symbol_normalized,
                "side": req.side,
                "kind": req.kind,
                "price": req.price,
                "volume_lots": req.volume_lots,
                "sl": req.sl or 0,
                "tp": req.tp or 0,
                "deviation": req.deviation,
            },
        )
        r.raise_for_status()
        req_id = r.json()["id"]
    res = _await_result(req_id, timeout_s=20.0)
    return res


@app.post("/tools/cancel_order", response_model=dict)
def tool_cancel_order(req: CancelOrderRequest) -> dict:
    with httpx.Client(timeout=2.0) as client:
        r = client.post(
            f"{settings.gateway_url}/bridge/commands/enqueue",
            params={"type": "cancel_order", "order_id": req.order_id},
        )
        r.raise_for_status()
        req_id = r.json()["id"]
    res = _await_result(req_id)
    return res


# Tools
@app.post("/tools/simulate_order", response_model=SimulationResult)
def tool_simulate_order(req: TradeIntent) -> SimulationResult:
    return gw.simulate_order(req)


@app.post("/tools/submit_market_order", response_model=ExecutionResult)
def tool_submit_market_order(req: TradeIntent) -> ExecutionResult:
    # In scaffold, writes are disabled
    raise HTTPException(status_code=501, detail="Execution disabled in scaffold")


@app.post("/tools/estimate_margin", response_model=MarginEstimate)
def tool_estimate_margin(req: MarginEstimateRequest) -> MarginEstimate:
    return gw.estimate_margin(req)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8010)
