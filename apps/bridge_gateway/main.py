from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from datetime import datetime, timezone
from typing import Optional
import os
import asyncio

from mt5_mcp.observability.logging import setup_logging, logger
from mt5_mcp.schemas.models import (
    AccountSummary,
    Bars,
    Order,
    Position,
    ExecutionResult,
    MarginEstimate,
    MarginEstimateRequest,
    TerminalStatus,
    TradeIntent,
)
from mt5_mcp.services.execution_gateway.service import ExecutionGateway
from mt5_mcp.settings.config import get_settings
from mt5_mcp.services.gateway_queue import get_queue
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST, Gauge
from fastapi.responses import Response, PlainTextResponse


setup_logging()
app = FastAPI(title="MT5 Bridge Gateway", version="0.1.0")

# Lazy initialization to prevent blocking during import
_queue_cached = None
_gw_cached = None
_settings_cached = None


def get_queue_cached():
    global _queue_cached
    if _queue_cached is None:
        _queue_cached = get_queue()
    return _queue_cached


def get_gateway_cached():
    global _gw_cached
    if _gw_cached is None:
        _gw_cached = ExecutionGateway()
    return _gw_cached


def get_settings_cached():
    global _settings_cached
    if _settings_cached is None:
        _settings_cached = get_settings()
    return _settings_cached


# Use lazy getters instead of module-level instantiation
Q = None  # Will be initialized on first use via get_queue_cached()
gw = None  # Will be initialized on first use via get_gateway_cached()
settings = None  # Will be initialized on first use via get_settings_cached()

# In-memory heartbeat state for EA
_last_heartbeat: dict[str, Optional[str | int]] = {
    "server": None,
    "build": None,
    "account_id": None,
    "login": None,
    "timestamp": None,
}
_last_heartbeat_at: Optional[datetime] = None
_heartbeat_lock = asyncio.Lock()

# Basic Prometheus gauges
GAUGE_QUEUE_DEPTH = Gauge("bridge_queue_depth", "Current command queue depth")
GAUGE_HEARTBEAT_FRESH = Gauge("bridge_heartbeat_fresh", "1 if heartbeat fresh, else 0")


def _extract_quoted_field(raw_text: str, field_name: str) -> str | None:
    marker = f'"{field_name}":"'
    start = raw_text.find(marker)
    if start < 0:
        return None
    start += len(marker)
    end = raw_text.find('"', start)
    if end < 0:
        return None
    return raw_text[start:end]


def _recover_malformed_result_body(raw_text: str) -> dict[str, object]:
    """Recover request_id/status/error from EA error callbacks with unescaped JSON."""
    request_id = _extract_quoted_field(raw_text, "request_id")
    if not request_id:
        return {}

    data: dict[str, object] = {
        "request_id": request_id,
        "status": _extract_quoted_field(raw_text, "status") or "ok",
    }

    error_marker = '"error":"'
    error_start = raw_text.find(error_marker)
    if error_start >= 0:
        error_start += len(error_marker)
        error_end = raw_text.rfind('"}')
        if error_end < error_start:
            error_end = len(raw_text)
        data["error"] = raw_text[error_start:error_end]

    return data


def _secret_ok(request: Request, params: dict | None = None) -> bool:
    """Optional shared-secret enforcement.

    If MT5_GATEWAY_SECRET is set, require it via header 'X-Bridge-Secret'
    or query/form/body field 'secret'.
    """
    secret_cfg = os.getenv("MT5_GATEWAY_SECRET")
    if not secret_cfg:
        return True
    # Header wins
    hdr = request.headers.get("X-Bridge-Secret")
    if hdr and hdr == secret_cfg:
        return True
    # Fallback to provided params (already parsed) or query params
    data = params or {}
    qp = dict(request.query_params)
    provided = data.get("secret") or qp.get("secret")
    return provided == secret_cfg


@app.post("/bridge/health")
def bridge_health(request: Request) -> dict[str, str]:
    if not _secret_ok(request):
        raise HTTPException(status_code=401, detail="unauthorized")
    return {"state": get_gateway_cached().health().state}


@app.post("/bridge/account/summary", response_model=AccountSummary)
def bridge_account_summary() -> AccountSummary:
    return get_gateway_cached().account_summary()


@app.post("/bridge/market/bars", response_model=Bars)
async def bridge_bars(symbol: str, timeframe: str, count: int = 100) -> Bars:
    """Fetch bars via EA bridge with fallback to pymt5"""
    import asyncio
    import json

    # Check if EA is connected
    if _last_heartbeat_at:
        from datetime import datetime, timezone

        delta = datetime.now(timezone.utc) - _last_heartbeat_at
        ea_connected = delta.total_seconds() <= 15
    else:
        ea_connected = False

    if ea_connected:
        # Use EA bridge
        cmd_id = Q.enqueue(
            "get_bars", {"symbol": symbol, "timeframe": timeframe, "count": count}
        )

        # Poll for result (max 10 seconds)
        for _ in range(20):
            cmd = Q.get(cmd_id)
            if cmd and cmd.status in ("completed", "error"):
                if cmd.status == "completed" and cmd.result:
                    bars_data = cmd.result.get("bars", [])
                    if isinstance(bars_data, str):
                        bars_data = json.loads(bars_data)
                    return Bars(
                        symbol=symbol,
                        timeframe=timeframe,
                        data=bars_data,
                        source="bridge",
                    )
                break
            await asyncio.sleep(0.5)

    # Fallback to pymt5
    return get_gateway_cached().get_bars(symbol, timeframe, count)


@app.post("/bridge/positions/open", response_model=list[Position])
def bridge_positions_open() -> list[Position]:
    return get_gateway_cached().adapter.get_positions()


@app.post("/bridge/orders/pending", response_model=list[Order])
def bridge_orders_pending() -> list[Order]:
    return get_gateway_cached().adapter.get_orders()


@app.post("/bridge/orders/submit", response_model=ExecutionResult)
def bridge_submit(req: TradeIntent) -> ExecutionResult:
    # Disabled in scaffold until policy/approval wired
    raise HTTPException(status_code=501, detail="Write path disabled in scaffold")


@app.api_route("/bridge/terminal/heartbeat", methods=["POST", "GET"])
async def bridge_terminal_heartbeat(request: Request) -> dict[str, str]:
    """Flexible heartbeat endpoint: accepts JSON body, form, or query params.

    This avoids strict 422 errors from EA WebRequest header quirks.
    """
    global _last_heartbeat, _last_heartbeat_at
    data: dict[str, object] = {}
    raw_body = (await request.body()).split(b"\x00")[0].strip()
    logger.info(f"HEARTBEAT RAW BODY: {raw_body}")

    # Parse JSON directly from stripped body (avoid request.json() which re-reads)
    import json

    try:
        if not raw_body:
            data = {}
        else:
            data = json.loads(raw_body.decode("utf-8"))
        if not isinstance(data, dict):
            data = {}
    except Exception as e:
        logger.info(f"HEARTBEAT JSON parse failed: {e}")
        data = {}
    # Fallback: form
    if not data:
        try:
            form = await request.form()
            data = dict(form)
        except Exception:
            data = {}
    # Fallback: query params
    if not data:
        qp = dict(request.query_params)
        data = qp

    logger.info(f"HEARTBEAT PARSED DATA: {data}")

    # Normalize types
    server = data.get("server") if isinstance(data.get("server"), (str,)) else None

    def _to_int(v):
        try:
            return int(v) if v is not None and str(v) != "" else None
        except Exception:
            return None

    build = _to_int(data.get("build"))
    login = _to_int(data.get("login"))
    account_id = (
        data.get("account_id") if isinstance(data.get("account_id"), (str,)) else None
    )
    timestamp = (
        data.get("timestamp") if isinstance(data.get("timestamp"), (str,)) else None
    )

    async with _heartbeat_lock:
        _last_heartbeat = {
            "server": server,
            "build": build,
            "account_id": account_id,
            "login": login,
            "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        }
        if not _secret_ok(request, _last_heartbeat):
            raise HTTPException(status_code=401, detail="unauthorized")
        _last_heartbeat_at = datetime.now(timezone.utc)
    logger.info(
        "heartbeat",
        extra={
            "server": server,
            "build": build,
            "account_id": account_id,
            "login": login,
        },
    )
    return {"status": "ok"}


@app.get("/bridge/terminal/status", response_model=TerminalStatus)
def bridge_terminal_status() -> TerminalStatus:
    # Consider heartbeat fresh if within the last 15 seconds
    fresh = False
    if _last_heartbeat_at:
        delta = datetime.now(timezone.utc) - _last_heartbeat_at
        fresh = delta.total_seconds() <= 15
    st = TerminalStatus(
        connected=fresh,
        login=int(_last_heartbeat["login"]) if _last_heartbeat["login"] else None,
        server=_last_heartbeat["server"] if _last_heartbeat["server"] else None,
        build=int(_last_heartbeat["build"]) if _last_heartbeat["build"] else None,
        path=None,
        message=None if fresh else "No recent heartbeat",
    )
    try:
        GAUGE_HEARTBEAT_FRESH.set(1 if fresh else 0)
    except Exception:
        pass
    return st


# Simple command queue APIs for EA polling model
@app.post("/bridge/commands/enqueue")
def bridge_commands_enqueue(
    request: Request,
    type: str,
    symbol: str | None = None,
    timeframe: str | None = None,
    count: int | None = None,
    indicator: str | None = None,
    period: int | None = None,
    fast: int | None = None,
    slow: int | None = None,
    signal: int | None = None,
    width: int | None = None,
    height: int | None = None,
    side: str | None = None,
    volume_lots: float | None = None,
    volume: float | None = None,
    sl: float | None = None,
    tp: float | None = None,
    deviation: int | None = None,
    shift: int | None = None,
    k_period: int | None = None,
    d_period: int | None = None,
    slowing: int | None = None,
    tenkan: int | None = None,
    kijun: int | None = None,
    senkou: int | None = None,
    window: int | None = None,
    position_id: str | None = None,
    order_id: str | None = None,
    new_price: float | None = None,
    new_sl: float | None = None,
    new_tp: float | None = None,
    price: float | None = None,
    kind: str | None = None,
    limit: int | None = None,
    days: int | None = None,
    # Ownership / tracing params
    session_id: str | None = None,
    strategy_id: str | None = None,
    intent_id: str | None = None,
    idempotency_key: str | None = None,
    magic_number: int | None = None,
    comment: str | None = None,
) -> dict[str, str]:
    if not _secret_ok(request):
        raise HTTPException(status_code=401, detail="unauthorized")
    payload: dict[str, object] = {}
    if symbol is not None:
        payload["symbol"] = symbol
    if timeframe is not None:
        payload["timeframe"] = timeframe
    if count is not None:
        payload["count"] = count
    if indicator is not None:
        payload["indicator"] = indicator
    if period is not None:
        payload["period"] = period
    if fast is not None:
        payload["fast"] = fast
    if slow is not None:
        payload["slow"] = slow
    if signal is not None:
        payload["signal"] = signal
    if width is not None:
        payload["width"] = width
    if height is not None:
        payload["height"] = height
    if side is not None:
        payload["side"] = side
    if volume_lots is not None:
        payload["volume_lots"] = volume_lots
    if volume is not None:
        payload["volume"] = volume
    if sl is not None:
        payload["sl"] = sl
    if tp is not None:
        payload["tp"] = tp
    if deviation is not None:
        payload["deviation"] = deviation
    if shift is not None:
        payload["shift"] = shift
    if k_period is not None:
        payload["k_period"] = k_period
    if d_period is not None:
        payload["d_period"] = d_period
    if slowing is not None:
        payload["slowing"] = slowing
    if tenkan is not None:
        payload["tenkan"] = tenkan
    if kijun is not None:
        payload["kijun"] = kijun
    if senkou is not None:
        payload["senkou"] = senkou
    if window is not None:
        payload["window"] = window
    if position_id is not None:
        payload["position_id"] = position_id
    if order_id is not None:
        payload["order_id"] = order_id
    if new_price is not None:
        payload["new_price"] = new_price
    if new_sl is not None:
        payload["new_sl"] = new_sl
    if new_tp is not None:
        payload["new_tp"] = new_tp
    if price is not None:
        payload["price"] = price
    if kind is not None:
        payload["kind"] = kind
    if limit is not None:
        payload["limit"] = limit
    if days is not None:
        payload["days"] = days
    if session_id is not None:
        payload["session_id"] = session_id
    if strategy_id is not None:
        payload["strategy_id"] = strategy_id
    if intent_id is not None:
        payload["intent_id"] = intent_id
    if magic_number is not None:
        payload["magic_number"] = magic_number
    if comment is not None:
        payload["comment"] = comment
    queue = get_queue_cached()
    cmd_id = queue.enqueue(type, payload, idempotency_key=idempotency_key)
    try:
        depth = getattr(queue, "depth", lambda: 0)()
        GAUGE_QUEUE_DEPTH.set(depth)
    except Exception:
        pass
    return {"id": cmd_id}


@app.get("/bridge/commands/next")
def bridge_commands_next(request: Request) -> PlainTextResponse:
    if not _secret_ok(request):
        raise HTTPException(status_code=401, detail="unauthorized")
    queue = get_queue_cached()
    cmd = queue.next()
    if not cmd:
        return PlainTextResponse("NONE")
    # Return simple key=value list for easy parsing in MQL5
    parts = [f"type={cmd.type}", f"request_id={cmd.id}"]
    for k, v in cmd.payload.items():
        parts.append(f"{k}={v}")
    return PlainTextResponse("&".join(parts))


@app.post("/bridge/results")
async def bridge_results(request: Request) -> dict[str, str]:
    """Accept EA results via JSON, form, or query params.

    Keeps backwards compatibility with prior query-param only behavior.
    """
    # Debug: log raw body
    raw_body = await request.body()
    raw_text = raw_body.split(b"\x00")[0].decode("utf-8", errors="ignore").strip()
    logger.info(f"RESULTS_RAW: body_bytes={raw_body[:300] if raw_body else 'EMPTY'}")
    logger.info(f"RESULTS_RAW: content_type={request.headers.get('content-type')}")

    # Parse JSON
    data: dict[str, object] = {}
    import json

    try:
        body_json = json.loads(raw_text) if raw_text else {}
        logger.info(f"RESULTS_JSON: parsed={body_json}")
        if isinstance(body_json, dict):
            data = body_json
    except Exception as e:
        logger.info(f"RESULTS_JSON: parse_error={e}")
        data = {}
    if not data and raw_text:
        data = _recover_malformed_result_body(raw_text)
        if data:
            logger.info(f"RESULTS_RECOVERED: {data}")
    # Fallback: form
    if not data:
        try:
            form = await request.form()
            logger.info(f"RESULTS_FORM: {dict(form)}")
            data = dict(form)
        except Exception as e:
            logger.info(f"RESULTS_FORM: error={e}")
            data = {}
    # Fallback: query
    if not data:
        logger.info(f"RESULTS_QUERY: {dict(request.query_params)}")
        data = dict(request.query_params)

    # Optional secret enforcement
    if not _secret_ok(request, data):
        raise HTTPException(status_code=401, detail="unauthorized")

    request_id = str(data.get("request_id") or "")
    status = str(data.get("status") or "ok")
    payload = data.get("payload")
    error = data.get("error")

    logger.info(
        f"RESULTS_PARSED: request_id={request_id!r} status={status!r} error={error!r}"
    )

    if not request_id:
        raise HTTPException(status_code=422, detail="missing request_id")

    # Maintain existing storage layout: result is a dict with 'payload' string
    queue = get_queue_cached()
    if status == "ok":
        result_data = {"payload": payload or ""}
        completed = queue.complete(request_id, result_data)
        logger.info(
            f"RESULTS: request_id={request_id} completed={completed} payload_len={len(str(payload)) if payload else 0}"
        )
    else:
        failed = queue.fail(request_id, str(error or "unknown"))
        logger.info(f"RESULTS: request_id={request_id} failed={failed} error={error}")
    return {"status": "ok"}


@app.get("/bridge/results/{request_id}")
def bridge_results_get(request_id: str) -> dict[str, object]:
    queue = get_queue_cached()
    cmd = queue.get(request_id)
    if not cmd:
        raise HTTPException(status_code=404, detail="not found")
    return {
        "status": cmd.status,
        "result": cmd.result or {},
        "error": cmd.error,
    }


@app.get("/metrics")
def metrics() -> Response:
    try:
        queue = get_queue_cached()
        depth = getattr(queue, "depth", lambda: 0)()
        GAUGE_QUEUE_DEPTH.set(depth)
    except Exception:
        pass
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8020)
