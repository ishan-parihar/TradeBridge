from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import httpx
import time
from uuid import uuid4

from mt5_mcp.observability.logging import setup_logging
from mt5_mcp.schemas.models import (
    AccountSummary,
    Bars,
    Deal,
    Order,
    PerformanceSummary,
    Position,
    ExecutionResult,
    HealthStatus,
    MarginEstimateRequest,
    SymbolInfo,
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
    CorrelationMatrixRequest,
    DealsHistoryRequest,
    ModifyPositionSLTPRequest,
    MultiTimeframeIndicatorRequest,
    ClosePositionRequest as ClosePosReq,
    PositionSizeRequest,
    SubmitPendingOrderRequest,
    CancelOrderRequest,
    SymbolInfoRequest,
    TicksRequest,
    TrailPositionRequest,
    OrderBookRequest,
    ValidateTradeSetupRequest,
    VolatilityProfileRequest,
    ModifyOrderRequest as ModOrderReq,
    CloseAllPositionsRequest,
    CancelAllOrdersRequest,
    BracketOrderRequest,
    BracketOrderResult,
    SetTrailingStopRequest,
    TrailingStopResult,
    PriceAlertRequest,
    PriceAlertResult,
    PositionMonitorRequest,
    PositionMonitorResult,
    MarketRegimeRequest,
    MarketScanRequest,
    TradeDecisionLogRequest,
    TradeJournalReflectionRequest,
    TradingContextRequest,
    TradingCoachRequest,
)
from mt5_mcp.policy.engine import validate_submit_order, get_policy
from mt5_mcp.services.agent_capabilities import (
    build_volatility_profile,
    calculate_position_size,
    compute_correlation_matrix,
    summarize_deals,
    validate_trade_setup,
)
from mt5_mcp.services.market_regime import detect_regime
from mt5_mcp.services.trade_journal_db import get_journal_db
from mt5_mcp.services.agent_prompt import (
    build_agent_system_prompt,
    get_trading_agent_prompt,
)
from mt5_mcp.services.market_context import build_context
from mt5_mcp.services.trading_coach import TradingCoach
from mt5_mcp.services.reconciliation import ReconciliationService
from mt5_mcp.services.session_service import (
    get_session_context as _get_session_context,
    get_session_for_pair as _get_session_for_pair,
)
from mt5_mcp.services.economic_calendar import (
    get_upcoming_events as _get_upcoming_events,
    is_blackout_now as _is_blackout_now,
    get_blackout_windows as _get_blackout_windows,
    get_daily_briefing as _get_daily_briefing,
)
from mt5_mcp.services.news_service import (
    fetch_news as _fetch_news,
    enrich_news as _enrich_news,
    get_available_pools as _get_available_pools,
    get_available_sources as _get_available_sources,
)
from mt5_mcp.schemas.tools import (
    BracketOrderRequest,
    BracketOrderResult,
    SetTrailingStopRequest,
    TrailingStopResult,
    PriceAlertRequest,
    PriceAlertResult,
    PositionMonitorRequest,
    PositionMonitorResult,
    MarketRegimeRequest,
    TradingPolicyStatusRequest,
    TradingPolicyConfigRequest,
    TradeJournalQueryRequest,
    MarketScanRequest,
    TradingDecisionSupportRequest,
    AgentSystemPromptRequest,
    NewsFetchRequest,
    EconomicCalendarRequest,
)


from mt5_mcp.services.cache import (
    cache_get,
    cache_set,
    cache_invalidate_symbol,
    get_all_cache_stats,
)


setup_logging()
app = FastAPI(title="MT5 MCP Server", version="0.1.0")

_gw = None
_settings = None
_http_client = None  # Persistent HTTP client for gateway communication (Keep-Alive)


def get_http_client() -> httpx.Client:
    """Get or create persistent HTTP client with Keep-Alive connection pooling.

    Eliminates ~50ms TCP handshake per request by reusing connections.
    """
    global _http_client
    if _http_client is None:
        _http_client = httpx.Client(
            timeout=10.0,
            http2=False,
            limits=httpx.Limits(
                max_keepalive_connections=10,
                max_connections=20,
                keepalive_expiry=30.0,
            ),
        )
    return _http_client


def get_gateway():
    global _gw
    if _gw is None:
        try:
            _gw = ExecutionGateway()
        except Exception as e:
            logger = __import__(
                "mt5_mcp.observability.logging", fromlist=["logger"]
            ).logger
            logger.warning(f"Gateway initialization failed: {e}")
            raise
    return _gw


def get_settings_cached():
    global _settings
    if _settings is None:
        _settings = get_settings()
    return _settings


# Resources (read-only)
@app.get("/resources/mt5/terminal/status", response_model=TerminalStatus)
def resource_terminal_status() -> TerminalStatus:
    return get_gateway().terminal_status()


@app.get("/resources/account/summary", response_model=AccountSummary)
def resource_account_summary() -> AccountSummary:
    summary = get_gateway().account_summary()
    if summary.account_id is not None:
        return summary
    try:
        return AccountSummary(**tool_get_account_summary())
    except Exception:
        return summary


@app.get("/resources/symbols/{symbol}/info", response_model=SymbolInfo)
def resource_symbol_info(symbol: str) -> SymbolInfo:
    adapter = getattr(get_gateway(), "adapter", None)
    if adapter is not None and hasattr(adapter, "get_symbol_info"):
        try:
            info = adapter.get_symbol_info(symbol)
        except Exception:
            info = None
        if info is not None:
            if info.symbol:
                info.symbol = denormalize_symbol(info.symbol)
            return info
    try:
        return SymbolInfo(**tool_get_symbol_info(symbol))
    except Exception:
        return SymbolInfo(symbol=symbol)


@app.get("/resources/deals/history", response_model=list[Deal])
def resource_deals_history(
    limit: int = 100, symbol: str | None = None, days: int = 30
) -> list[Deal]:
    adapter = getattr(get_gateway(), "adapter", None)
    if adapter is not None and hasattr(adapter, "get_deals_history"):
        try:
            deals = adapter.get_deals_history(limit=limit, symbol=symbol, days=days)
        except Exception:
            deals = []
        if deals:
            for deal in deals:
                if deal.symbol:
                    deal.symbol = denormalize_symbol(deal.symbol)
            return deals
    try:
        return [
            Deal(**item)
            for item in tool_get_deals_history(
                limit=limit, symbol=symbol, days=days
            ).get("deals", [])
        ]
    except Exception:
        return []


@app.get("/resources/performance/summary", response_model=PerformanceSummary)
def resource_performance_summary(
    limit: int = 100, symbol: str | None = None, days: int = 30
) -> PerformanceSummary:
    deals = resource_deals_history(limit=limit, symbol=symbol, days=days)
    summary = summarize_deals([deal.model_dump() for deal in deals])
    return PerformanceSummary(**summary)


@app.get("/resources/bars/{symbol}/{timeframe}", response_model=Bars)
def resource_bars(symbol: str, timeframe: str, count: int = 100) -> Bars:
    return get_gateway().get_bars(symbol, timeframe, count)


@app.get("/resources/positions/open", response_model=list[Position])
def resource_positions_open() -> list[Position]:
    positions = get_gateway().adapter.get_positions()
    if positions:
        return positions
    try:
        return [Position(**item) for item in tool_get_positions().get("positions", [])]
    except Exception:
        return positions


@app.get("/resources/orders/pending", response_model=list[Order])
def resource_orders_pending() -> list[Order]:
    orders = get_gateway().adapter.get_orders()
    if orders:
        return orders
    try:
        return [Order(**item) for item in tool_get_orders().get("orders", [])]
    except Exception:
        return orders


@app.get("/health", response_model=HealthStatus)
def health() -> HealthStatus:
    return get_gateway().health()


@app.get("/resources/mt5/bridge/status", response_model=TerminalStatus)
def resource_bridge_terminal_status() -> TerminalStatus:
    # Proxy bridge heartbeat status into MCP for unified visibility
    try:
        client = get_http_client()
        r = client.get(f"{get_settings_cached().gateway_url}/bridge/terminal/status")
        r.raise_for_status()
        data = r.json()
        return TerminalStatus(**data)
    except Exception:
        return TerminalStatus(connected=False, message="Bridge status unavailable")


# Bridge-backed tools (EA polling model)
# TCP Bridge: low-latency push communication (port 8025)
# HTTP Bridge: legacy polling fallback (port 8020)

import os as _os

_TCP_BRIDGE_ENABLED = _os.getenv("MT5_TCP_BRIDGE_ENABLED", "true").lower() == "true"


def _parse_payload(payload) -> dict:
    """Parse EA payload, handling both string and dict formats."""
    import json

    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except Exception:
            return {}
    elif isinstance(payload, dict):
        return payload
    return {}


def _await_result(req_id: str, timeout_s: float = 20.0, poll_s: float = 0.1) -> dict:
    """Wait for bridge command result."""
    import time as _t

    gw_url = get_settings_cached().gateway_url
    client = get_http_client()
    end = _t.time() + timeout_s
    while _t.time() < end:
        r = client.get(f"{gw_url}/bridge/results/{req_id}")
        if r.status_code == 200:
            data = r.json()
            if data.get("status") in {"completed", "error"}:
                return data
        _t.sleep(poll_s)
    return {"status": "timeout", "error": "timeout"}


def _tcp_send_and_await(
    type: str, payload: dict[str, Any], timeout_s: float = 20.0
) -> dict[str, Any]:
    """Send command via TCP bridge and await result.

    Falls back to HTTP if TCP bridge is unavailable.
    """
    if not _TCP_BRIDGE_ENABLED:
        return None

    try:
        from mt5_mcp.services.tcp_bridge_client import TCPBridgeClient
        import asyncio

        tcp_client = TCPBridgeClient()
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(tcp_client.connect())
            result = loop.run_until_complete(
                tcp_client.send_command(type, payload, timeout=timeout_s)
            )
            inner = result.get("payload", result)
            return {"status": "completed", "result": {"payload": inner}}
        except Exception:
            return None
        finally:
            loop.run_until_complete(tcp_client.close())
            loop.close()
    except Exception:
        return None


def _batch_enqueue_and_await(
    commands: list[dict[str, Any]], timeout_s: float = 20.0
) -> list[dict]:
    """Enqueue multiple bridge commands and await all results."""
    tcp_ok = False
    if _TCP_BRIDGE_ENABLED:
        try:
            from mt5_mcp.services.tcp_bridge_client import TCPBridgeClient
            import asyncio

            tcp_client = TCPBridgeClient()
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(tcp_client.connect())

                async def _run_all():
                    results = [None] * len(commands)
                    tasks = []
                    for i, cmd in enumerate(commands):
                        task = asyncio.create_task(
                            tcp_client.send_command(
                                type=cmd["type"],
                                payload={k: v for k, v in cmd.items() if k != "type"},
                                timeout=timeout_s,
                            )
                        )
                        tasks.append((i, task))
                    for i, task in tasks:
                        try:
                            result = await asyncio.wait_for(task, timeout=timeout_s)
                            inner = result.get("payload", result)
                            results[i] = {
                                "status": "completed",
                                "result": {"payload": inner},
                            }
                        except asyncio.TimeoutError:
                            results[i] = {"status": "timeout", "error": "timeout"}
                        except Exception as e:
                            results[i] = {"status": "error", "error": str(e)}
                    return results

                results = loop.run_until_complete(_run_all())
                tcp_ok = True
                return results
            finally:
                loop.run_until_complete(tcp_client.close())
                loop.close()
        except Exception:
            pass

    if tcp_ok:
        return []

    import time as _t

    gw_url = get_settings_cached().gateway_url
    client = get_http_client()

    # Enqueue all commands
    req_ids = []
    for cmd in commands:
        r = client.post(
            f"{gw_url}/bridge/commands/enqueue",
            params=cmd,
        )
        r.raise_for_status()
        req_ids.append(r.json()["id"])

    # Poll all results together
    end = _t.time() + timeout_s
    results = [None] * len(req_ids)
    while _t.time() < end and any(r is None for r in results):
        for i, req_id in enumerate(req_ids):
            if results[i] is not None:
                continue
            r = client.get(f"{gw_url}/bridge/results/{req_id}")
            if r.status_code == 200:
                data = r.json()
                if data.get("status") in {"completed", "error"}:
                    results[i] = data
        if any(r is None for r in results):
            _t.sleep(0.1)

    # Fill timeouts for any remaining
    for i, r in enumerate(results):
        if r is None:
            results[i] = {"status": "timeout", "error": "timeout"}

    return results


def _map_trade_retcode(rc: int | str | None) -> str | None:
    try:
        ival = int(rc) if rc is not None else None
    except Exception:
        return None

    mapping = {
        10004: "REQUOTE",
        10006: "REJECTED",
        10008: "PLACED",
        10009: "DONE",
        10010: "DONE_PARTIAL",
        10012: "TIMEOUT",
        10013: "INVALID",
        10014: "INVALID_VOLUME",
        10015: "INVALID_PRICE",
        10016: "INVALID_STOPS",
        10017: "TRADE_DISABLED",
        10018: "MARKET_CLOSED",
        10019: "NO_MONEY",
        10020: "PRICE_CHANGED",
        10021: "PRICE_OFF",
        10022: "INVALID_EXPIRATION",
        10023: "ORDER_CHANGED",
        10024: "TOO_MANY_REQUESTS",
        10025: "NO_CHANGES",
        10026: "SERVER_DISABLES_AT",
        10027: "CLIENT_DISABLES_AT",
        10028: "LOCKED",
        10029: "FROZEN",
        10030: "INVALID_FILL",
        10031: "CONNECTION",
        10032: "ONLY_REAL",
        10033: "LIMIT_ORDERS",
        10034: "LIMIT_VOLUME",
        10035: "INVALID_ORDER",
        10036: "POSITION_CLOSED",
        10038: "INVALID_CLOSE_VOLUME",
        10039: "CLOSE_ORDER_EXIST",
        10040: "LIMIT_POSITIONS",
    }
    return mapping.get(ival, str(ival) if ival is not None else None)


def _build_trade_error_result(
    intent_id: str, payload: str | dict | None
) -> ExecutionResult:
    data = _parse_payload(payload)
    if not data:
        return ExecutionResult(
            intent_id=intent_id,
            status="error",
            message=str(payload or "timeout"),
        )

    retcode = data.get("retcode")
    retcode_label = _map_trade_retcode(retcode)
    try:
        retcode_int = int(retcode) if retcode is not None else None
    except Exception:
        retcode_int = None

    return ExecutionResult(
        intent_id=intent_id,
        status="error",
        adapter="EASocketAdapter",
        broker_order_id=str(data.get("order", "")) if data.get("order") else None,
        position_id=str(data.get("deal", "")) if data.get("deal") else None,
        retcode=retcode_label,
        message=f"Order failed: retcode={retcode_int} ({retcode_label})",
        raw=data,
    )


def _first_bid_ask(book: dict) -> tuple[float | None, float | None]:
    bids = book.get("bids") or []
    asks = book.get("asks") or []
    bid = bids[0].get("price") if bids else None
    ask = asks[0].get("price") if asks else None
    return (
        float(bid) if bid is not None else None,
        float(ask) if ask is not None else None,
    )


def _error_payload(error: object) -> object:
    if isinstance(error, str):
        parsed = _parse_payload(error)
        if parsed:
            return parsed
    return error


@app.post("/tools/get_bars", response_model=Bars)
def tool_get_bars(req: BarsRequest) -> Bars:
    symbol_normalized = normalize_symbol(req.symbol)

    tcp_result = _tcp_send_and_await(
        "get_bars",
        {"symbol": symbol_normalized, "timeframe": req.timeframe, "count": req.count},
    )
    if tcp_result and tcp_result.get("status") == "completed":
        payload = tcp_result.get("result", {}).get("payload", {})
        if isinstance(payload, str):
            import json

            data = json.loads(payload)
        elif isinstance(payload, dict):
            data = payload
        else:
            data = {"data": []}
        if "error" in data or "data" not in data:
            return Bars(
                symbol=req.symbol, timeframe=req.timeframe, data=[], source="tcp_bridge"
            )
        if "symbol" in data:
            data["symbol"] = denormalize_symbol(data["symbol"])
        return Bars(**data)

    gw_url = get_settings_cached().gateway_url
    client = get_http_client()
    r = client.post(
        f"{gw_url}/bridge/commands/enqueue",
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
    if isinstance(payload, str):
        try:
            import json

            data = json.loads(payload)
        except Exception:
            data = {"data": []}
    elif isinstance(payload, dict):
        data = payload
    else:
        data = {"data": []}
    if "error" in data or "data" not in data:
        return Bars(
            symbol=req.symbol, timeframe=req.timeframe, data=[], source="bridge"
        )
    if "symbol" in data:
        data["symbol"] = denormalize_symbol(data["symbol"])
    return Bars(**data)


# Sensible defaults for advanced indicators — AI agent should never get bad_args
# for standard indicator calls. These match MT5 standard defaults.
_INDICATOR_DEFAULTS: dict[str, dict[str, int]] = {
    "macd": {"fast": 12, "slow": 26, "signal": 9},
    "bbands": {"period": 20, "deviation": 2},
    "stoch": {"k_period": 5, "d_period": 3, "slowing": 3},
    "atr": {"period": 14},
    "adx": {"period": 14},
    "dmi": {"period": 14},
    "ichimoku": {"tenkan": 9, "kijun": 26, "senkou": 52},
    "cci": {"period": 14},
    "rsi": {"period": 14},
    "sma": {"period": 20},
    "ema": {"period": 20},
}


@app.post("/tools/get_indicator")
def tool_get_indicator(req: IndicatorRequest) -> dict:
    symbol_normalized = normalize_symbol(req.symbol)
    gw_url = get_settings_cached().gateway_url
    params = {
        "type": "get_indicator",
        "symbol": symbol_normalized,
        "timeframe": req.timeframe,
        "indicator": req.indicator,
    }

    # Inject sensible defaults for advanced indicators so AI agent never gets bad_args
    indicator_lower = req.indicator.lower() if req.indicator else ""
    defaults = _INDICATOR_DEFAULTS.get(indicator_lower, {})
    for key, default_val in defaults.items():
        current = getattr(req, key, None)
        if current is None:
            setattr(req, key, default_val)

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

    # TCP-first: try direct TCP command, fall back to HTTP polling
    tcp_result = _tcp_send_and_await("get_indicator", params)
    if tcp_result and tcp_result.get("status") == "completed":
        payload = tcp_result.get("result", {}).get("payload", {})
        if isinstance(payload, str):
            import json

            data = json.loads(payload)
        elif isinstance(payload, dict):
            data = payload
        else:
            data = {}
        if "symbol" in data:
            data["symbol"] = denormalize_symbol(data["symbol"])
        return data

    gw_url = get_settings_cached().gateway_url
    params = {
        "type": "get_indicator",
        "symbol": symbol_normalized,
        "timeframe": req.timeframe,
        "indicator": req.indicator,
    }

    # Inject sensible defaults for advanced indicators so AI agent never gets bad_args
    indicator_lower = req.indicator.lower() if req.indicator else ""
    defaults = _INDICATOR_DEFAULTS.get(indicator_lower, {})
    for key, default_val in defaults.items():
        current = getattr(req, key, None)
        if current is None:
            setattr(req, key, default_val)

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
    client = get_http_client()
    r = client.post(
        f"{gw_url}/bridge/commands/enqueue",
        params=params,
    )
    r.raise_for_status()
    req_id = r.json()["id"]
    res = _await_result(req_id, timeout_s=20.0)
    if res.get("status") != "completed":
        return {"status": "error", "message": res.get("error", "timeout")}
    payload = res.get("result", {}).get("payload", {})
    # Handle both string and dict payloads
    if isinstance(payload, str):
        try:
            data = _parse_payload(payload)
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

    tcp_result = _tcp_send_and_await(
        "get_chart_screenshot",
        {
            "symbol": symbol_normalized,
            "timeframe": req.timeframe,
            "width": req.width,
            "height": req.height,
        },
    )
    if tcp_result and tcp_result.get("status") == "completed":
        payload = tcp_result.get("result", {}).get("payload", "{}")
        if isinstance(payload, str):
            data = _parse_payload(payload)
        elif isinstance(payload, dict):
            data = payload
        else:
            data = {"image_base64": ""}
        return ChartScreenshotResult(**data)

    gw_url = get_settings_cached().gateway_url
    client = get_http_client()
    r = client.post(
        f"{gw_url}/bridge/commands/enqueue",
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
        data = _parse_payload(payload)
    except Exception:
        data = {"image_base64": ""}
    return ChartScreenshotResult(**data)


@app.post("/tools/get_ticks", response_model=dict)
def tool_get_ticks(req: TicksRequest) -> dict:
    symbol_normalized = normalize_symbol(req.symbol)

    tcp_result = _tcp_send_and_await(
        "get_ticks",
        {"symbol": symbol_normalized, "count": req.count},
    )
    if tcp_result and tcp_result.get("status") == "completed":
        payload = tcp_result.get("result", {}).get("payload", "{}")
        if isinstance(payload, str):
            try:
                data = _parse_payload(payload)
            except Exception:
                data = {}
        elif isinstance(payload, dict):
            data = payload
        else:
            data = {}
        if "symbol" in data:
            data["symbol"] = denormalize_symbol(data["symbol"])
        return data

    gw_url = get_settings_cached().gateway_url
    client = get_http_client()
    r = client.post(
        f"{gw_url}/bridge/commands/enqueue",
        params={
            "type": "get_ticks",
            "symbol": symbol_normalized,
            "count": req.count,
        },
    )
    r.raise_for_status()
    req_id = r.json()["id"]
    res = _await_result(req_id)
    if res.get("status") != "completed":
        return {"status": "error", "message": res.get("error", "timeout")}
    payload = res.get("result", {}).get("payload", "{}")
    try:
        data = _parse_payload(payload)
    except Exception:
        data = {}
    # Denormalize symbol in response
    if "symbol" in data:
        data["symbol"] = denormalize_symbol(data["symbol"])
    return data


@app.post("/tools/get_order_book", response_model=dict)
def tool_get_order_book(req: OrderBookRequest) -> dict:
    symbol_normalized = normalize_symbol(req.symbol)

    tcp_result = _tcp_send_and_await(
        "get_order_book",
        {"symbol": symbol_normalized},
    )
    if tcp_result and tcp_result.get("status") == "completed":
        payload = tcp_result.get("result", {}).get("payload", "{}")
        if isinstance(payload, str):
            try:
                data = _parse_payload(payload)
            except Exception:
                data = {}
        elif isinstance(payload, dict):
            data = payload
        else:
            data = {}
        if "symbol" in data:
            data["symbol"] = denormalize_symbol(data["symbol"])
        return data

    gw_url = get_settings_cached().gateway_url
    client = get_http_client()
    r = client.post(
        f"{gw_url}/bridge/commands/enqueue",
        params={"type": "get_order_book", "symbol": symbol_normalized},
    )
    r.raise_for_status()
    req_id = r.json()["id"]
    res = _await_result(req_id)
    if res.get("status") != "completed":
        return {"status": "error", "message": res.get("error", "timeout")}
    payload = res.get("result", {}).get("payload", "{}")
    try:
        data = _parse_payload(payload)
    except Exception:
        data = {}
    # Denormalize symbol in response
    if "symbol" in data:
        data["symbol"] = denormalize_symbol(data["symbol"])
    return data


@app.post("/tools/get_symbol_info", response_model=dict)
def post_tool_get_symbol_info(req: SymbolInfoRequest) -> dict:
    return tool_get_symbol_info(req.symbol)


def tool_get_symbol_info(symbol: str) -> dict:
    symbol_normalized = normalize_symbol(symbol)

    tcp_result = _tcp_send_and_await(
        "get_symbol_info",
        {"symbol": symbol_normalized},
    )
    if tcp_result and tcp_result.get("status") == "completed":
        payload = tcp_result.get("result", {}).get("payload", "{}")
        if isinstance(payload, str):
            data = _parse_payload(payload)
        elif isinstance(payload, dict):
            data = payload
        else:
            data = {}
        if "symbol" in data:
            data["symbol"] = denormalize_symbol(data["symbol"])
        return data

    gw_url = get_settings_cached().gateway_url
    client = get_http_client()
    r = client.post(
        f"{gw_url}/bridge/commands/enqueue",
        params={"type": "get_symbol_info", "symbol": symbol_normalized},
    )
    r.raise_for_status()
    req_id = r.json()["id"]
    res = _await_result(req_id)
    if res.get("status") != "completed":
        return {"symbol": symbol, "error": res.get("error", "timeout")}
    payload = res.get("result", {}).get("payload", "{}")
    data = _parse_payload(payload)
    if "symbol" in data:
        data["symbol"] = denormalize_symbol(data["symbol"])
    return data


@app.post("/tools/get_deals_history", response_model=dict)
def post_tool_get_deals_history(req: DealsHistoryRequest) -> dict:
    return tool_get_deals_history(limit=req.limit, symbol=req.symbol, days=req.days)


def tool_get_deals_history(
    limit: int = 100, symbol: str | None = None, days: int = 30
) -> dict:
    params: dict[str, object] = {
        "type": "get_deals_history",
        "limit": limit,
        "days": days,
    }
    if symbol:
        params["symbol"] = normalize_symbol(symbol)

    tcp_result = _tcp_send_and_await("get_deals_history", params)
    if tcp_result and tcp_result.get("status") == "completed":
        payload = tcp_result.get("result", {}).get("payload", "{}")
        data = _parse_payload(payload)
        for deal in data.get("deals", []):
            if "symbol" in deal:
                deal["symbol"] = denormalize_symbol(deal["symbol"])
        return data

    gw_url = get_settings_cached().gateway_url
    client = get_http_client()
    r = client.post(
        f"{gw_url}/bridge/commands/enqueue",
        params=params,
    )
    r.raise_for_status()
    req_id = r.json()["id"]
    res = _await_result(req_id, timeout_s=15.0)
    if res.get("status") != "completed":
        return {"deals": [], "error": res.get("error", "timeout")}
    payload = res.get("result", {}).get("payload", "{}")
    data = _parse_payload(payload)
    for deal in data.get("deals", []):
        if "symbol" in deal:
            deal["symbol"] = denormalize_symbol(deal["symbol"])
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

    # Add ownership fields when provided
    if req.session_id:
        params["session_id"] = req.session_id
    if req.strategy_id:
        params["strategy_id"] = req.strategy_id
    if req.intent_id:
        params["intent_id"] = req.intent_id
    if req.idempotency_key:
        params["idempotency_key"] = req.idempotency_key

    tcp_result = _tcp_send_and_await("modify_order", params)
    if tcp_result and tcp_result.get("status") == "completed":
        return tcp_result.get("result", {})

    gw_url = get_settings_cached().gateway_url
    client = get_http_client()
    r = client.post(
        f"{gw_url}/bridge/commands/enqueue",
        params=params,
    )
    r.raise_for_status()
    req_id = r.json()["id"]
    res = _await_result(req_id)
    if res.get("status") == "error":
        return {**res, "error": _error_payload(res.get("error"))}
    return res


@app.post("/tools/close_all_positions", response_model=dict)
def tool_close_all_positions(req: CloseAllPositionsRequest) -> dict:
    params: dict[str, object] = {"type": "close_all_positions", "side": req.side}
    if req.symbol is not None and req.symbol != "":
        params["symbol"] = normalize_symbol(req.symbol)

    # Add ownership fields when provided
    if req.session_id:
        params["session_id"] = req.session_id
    if req.strategy_id:
        params["strategy_id"] = req.strategy_id
    if req.intent_id:
        params["intent_id"] = req.intent_id
    if req.idempotency_key:
        params["idempotency_key"] = req.idempotency_key

    tcp_result = _tcp_send_and_await("close_all_positions", params)
    if tcp_result and tcp_result.get("status") == "completed":
        return tcp_result.get("result", {})

    gw_url = get_settings_cached().gateway_url
    client = get_http_client()
    r = client.post(
        f"{gw_url}/bridge/commands/enqueue",
        params=params,
    )
    r.raise_for_status()
    req_id = r.json()["id"]
    res = _await_result(req_id, timeout_s=60.0)
    if res.get("status") == "error":
        return {**res, "error": _error_payload(res.get("error"))}
    return res


@app.post("/tools/cancel_all_orders", response_model=dict)
def tool_cancel_all_orders(req: CancelAllOrdersRequest) -> dict:
    params: dict[str, object] = {"type": "cancel_all_orders", "side": req.side}
    if req.symbol is not None and req.symbol != "":
        params["symbol"] = normalize_symbol(req.symbol)

    # Add ownership fields when provided
    if req.session_id:
        params["session_id"] = req.session_id
    if req.strategy_id:
        params["strategy_id"] = req.strategy_id
    if req.intent_id:
        params["intent_id"] = req.intent_id
    if req.idempotency_key:
        params["idempotency_key"] = req.idempotency_key

    tcp_result = _tcp_send_and_await("cancel_all_orders", params)
    if tcp_result and tcp_result.get("status") == "completed":
        return tcp_result.get("result", {})

    gw_url = get_settings_cached().gateway_url
    client = get_http_client()
    r = client.post(
        f"{gw_url}/bridge/commands/enqueue",
        params=params,
    )
    r.raise_for_status()
    req_id = r.json()["id"]
    res = _await_result(req_id, timeout_s=60.0)
    if res.get("status") == "error":
        return {**res, "error": _error_payload(res.get("error"))}
    return res


@app.post("/tools/submit_market_order_via_bridge", response_model=ExecutionResult)
def tool_submit_market_order_via_bridge(req: TradeIntent) -> ExecutionResult:
    # Policy gate — enhanced with TradingPolicy engine
    from mt5_mcp.policy.engine import get_policy

    policy = get_policy()
    decision = policy.validate_submit_order(
        environment=get_settings_cached().environment,
    )
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.reason or "denied")

    if req.idempotency_key is None:
        req.idempotency_key = str(uuid4())

    symbol_normalized = normalize_symbol(req.symbol)

    tcp_result = _tcp_send_and_await(
        "submit_order",
        {
            "symbol": symbol_normalized,
            "side": req.side,
            "volume_lots": req.volume_lots,
            "sl": req.sl or 0,
            "tp": req.tp or 0,
            "deviation": req.deviation_points or 20,
            "session_id": req.session_id,
            "strategy_id": req.strategy_id,
            "intent_id": req.intent_id,
            "idempotency_key": req.idempotency_key,
        },
    )
    if tcp_result and tcp_result.get("status") == "completed":
        payload = tcp_result.get("result", {}).get("payload", "{}")
        try:
            data = _parse_payload(payload)
        except Exception:
            data = {}
        retcode = data.get("retcode")
        retcode_mapped = _map_trade_retcode(retcode)
        success_retcodes = {10009, 10008}
        try:
            retcode_int = int(retcode) if retcode else None
        except:
            retcode_int = None
        if retcode_int not in success_retcodes:
            return _build_trade_error_result(req.intent_id, data)
        return ExecutionResult(
            intent_id=req.intent_id,
            status="submitted",
            adapter="EASocketAdapter",
            broker_order_id=str(data.get("order", "")) if data else None,
            retcode=retcode_mapped,
            message=f"Order submitted successfully (retcode={retcode_int})",
            raw=data,
        )

    # Enqueue order submit and await result
    gw_url = get_settings_cached().gateway_url
    client = get_http_client()
    r = client.post(
        f"{gw_url}/bridge/commands/enqueue",
        params={
            "type": "submit_order",
            "symbol": symbol_normalized,
            "side": req.side,
            "volume_lots": req.volume_lots,
            "sl": req.sl or 0,
            "tp": req.tp or 0,
            "deviation": req.deviation_points or 20,
            "session_id": req.session_id,
            "strategy_id": req.strategy_id,
            "intent_id": req.intent_id,
            "idempotency_key": req.idempotency_key,
        },
    )
    r.raise_for_status()
    req_id = r.json()["id"]
    res = _await_result(req_id, timeout_s=30.0)
    if res.get("status") != "completed":
        return _build_trade_error_result(req.intent_id, res.get("error"))
    payload = res.get("result", {}).get("payload", "{}")
    try:
        data = _parse_payload(payload)
    except Exception:
        data = {}

    # Check retcode to determine success/failure
    retcode = data.get("retcode")
    retcode_mapped = _map_trade_retcode(retcode)

    # Success retcodes: 10009 (DONE), 10008 (PLACED)
    success_retcodes = {10009, 10008}
    try:
        retcode_int = int(retcode) if retcode else None
    except:
        retcode_int = None

    if retcode_int not in success_retcodes:
        return _build_trade_error_result(req.intent_id, data)

    return ExecutionResult(
        intent_id=req.intent_id,
        status="submitted",
        adapter="EASocketAdapter",
        broker_order_id=str(data.get("order", "")) if data else None,
        retcode=retcode_mapped,
        message=f"Order submitted successfully (retcode={retcode_int})",
        raw=data,
    )


@app.get("/tools/get_account_summary", response_model=dict)
def tool_get_account_summary() -> dict:
    tcp_result = _tcp_send_and_await("get_account", {})
    if tcp_result and tcp_result.get("status") == "completed":
        payload = tcp_result.get("result", {}).get("payload", {})
        if isinstance(payload, str):
            try:
                data = _parse_payload(payload)
            except Exception:
                data = {}
        elif isinstance(payload, dict):
            data = payload
        else:
            data = {}
        return data

    # via EA bridge command
    gw_url = get_settings_cached().gateway_url
    client = get_http_client()
    r = client.post(
        f"{gw_url}/bridge/commands/enqueue",
        params={"type": "get_account"},
    )
    r.raise_for_status()
    req_id = r.json()["id"]
    res = _await_result(req_id)
    payload = res.get("result", {}).get("payload", {})
    # Handle both string and dict payloads
    if isinstance(payload, str):
        try:
            data = _parse_payload(payload)
        except Exception:
            data = {}
    elif isinstance(payload, dict):
        data = payload
    else:
        data = {}
    return data


@app.get("/tools/get_positions", response_model=dict)
def tool_get_positions() -> dict:
    tcp_result = _tcp_send_and_await("get_positions", {})
    if tcp_result and tcp_result.get("status") == "completed":
        payload = tcp_result.get("result", {}).get("payload", "{}")
        try:
            data = _parse_payload(payload)
        except Exception:
            data = {}
        return data

    gw_url = get_settings_cached().gateway_url
    client = get_http_client()
    r = client.post(
        f"{gw_url}/bridge/commands/enqueue",
        params={"type": "get_positions"},
    )
    r.raise_for_status()
    req_id = r.json()["id"]
    res = _await_result(req_id)
    payload = res.get("result", {}).get("payload", "{}")
    try:
        data = _parse_payload(payload)
    except Exception:
        data = {}
    return data


@app.get("/tools/get_orders", response_model=dict)
def tool_get_orders() -> dict:
    tcp_result = _tcp_send_and_await("get_orders", {})
    if tcp_result and tcp_result.get("status") == "completed":
        payload = tcp_result.get("result", {}).get("payload", "{}")
        try:
            data = _parse_payload(payload)
        except Exception:
            data = {}
        return data

    gw_url = get_settings_cached().gateway_url
    client = get_http_client()
    r = client.post(
        f"{gw_url}/bridge/commands/enqueue",
        params={"type": "get_orders"},
    )
    r.raise_for_status()
    req_id = r.json()["id"]
    res = _await_result(req_id)
    payload = res.get("result", {}).get("payload", "{}")
    try:
        data = _parse_payload(payload)
    except Exception:
        data = {}
    return data


@app.post("/tools/modify_position_sl_tp", response_model=dict)
def tool_modify_position_sl_tp(req: ModifyPositionSLTPRequest) -> dict:
    tcp_result = _tcp_send_and_await(
        "modify_position_sl_tp",
        {
            "position_id": req.position_id,
            "sl": req.sl or 0,
            "tp": req.tp or 0,
            "session_id": req.session_id,
            "strategy_id": req.strategy_id,
            "intent_id": req.intent_id,
            "idempotency_key": req.idempotency_key,
        },
    )
    if tcp_result and tcp_result.get("status") == "completed":
        return tcp_result.get("result", {})

    gw_url = get_settings_cached().gateway_url
    client = get_http_client()
    r = client.post(
        f"{gw_url}/bridge/commands/enqueue",
        params={
            "type": "modify_position_sl_tp",
            "position_id": req.position_id,
            "sl": req.sl or 0,
            "tp": req.tp or 0,
            "session_id": req.session_id,
            "strategy_id": req.strategy_id,
            "intent_id": req.intent_id,
            "idempotency_key": req.idempotency_key,
        },
    )
    r.raise_for_status()
    req_id = r.json()["id"]
    res = _await_result(req_id)
    if res.get("status") == "error":
        return {**res, "error": _error_payload(res.get("error"))}
    return res


@app.post("/tools/close_position", response_model=dict)
def tool_close_position(req: ClosePosReq) -> dict:
    tcp_result = _tcp_send_and_await(
        "close_position",
        {
            "position_id": req.position_id,
            "volume": req.volume or 0,
            "session_id": req.session_id,
            "strategy_id": req.strategy_id,
            "intent_id": req.intent_id,
            "idempotency_key": req.idempotency_key,
        },
    )
    if tcp_result and tcp_result.get("status") == "completed":
        return tcp_result.get("result", {})

    gw_url = get_settings_cached().gateway_url
    client = get_http_client()
    r = client.post(
        f"{gw_url}/bridge/commands/enqueue",
        params={
            "type": "close_position",
            "position_id": req.position_id,
            "volume": req.volume or 0,
            "session_id": req.session_id,
            "strategy_id": req.strategy_id,
            "intent_id": req.intent_id,
            "idempotency_key": req.idempotency_key,
        },
    )
    r.raise_for_status()
    req_id = r.json()["id"]
    res = _await_result(req_id, timeout_s=20.0)
    if res.get("status") == "error":
        return {**res, "error": _error_payload(res.get("error"))}
    return res


@app.post("/tools/submit_pending_order", response_model=dict)
def tool_submit_pending_order(req: SubmitPendingOrderRequest) -> dict:
    from mt5_mcp.policy.engine import get_policy

    decision = get_policy().validate_submit_order(
        environment=get_settings_cached().environment,
    )
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.reason or "denied")
    symbol_normalized = normalize_symbol(req.symbol)

    tcp_result = _tcp_send_and_await(
        "submit_pending_order",
        {
            "symbol": symbol_normalized,
            "side": req.side,
            "kind": req.kind,
            "price": req.price,
            "volume_lots": req.volume_lots,
            "sl": req.sl or 0,
            "tp": req.tp or 0,
            "deviation": req.deviation,
            "session_id": req.session_id,
            "strategy_id": req.strategy_id,
            "intent_id": req.intent_id,
            "idempotency_key": req.idempotency_key,
        },
    )
    if tcp_result and tcp_result.get("status") == "completed":
        return tcp_result.get("result", {})

    gw_url = get_settings_cached().gateway_url
    client = get_http_client()
    r = client.post(
        f"{gw_url}/bridge/commands/enqueue",
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
            "session_id": req.session_id,
            "strategy_id": req.strategy_id,
            "intent_id": req.intent_id,
            "idempotency_key": req.idempotency_key,
        },
    )
    r.raise_for_status()
    req_id = r.json()["id"]
    res = _await_result(req_id, timeout_s=20.0)
    if res.get("status") == "error":
        return {**res, "error": _error_payload(res.get("error"))}
    return res


@app.post("/tools/cancel_order", response_model=dict)
def tool_cancel_order(req: CancelOrderRequest) -> dict:
    params: dict[str, object] = {"type": "cancel_order", "order_id": req.order_id}

    # Add ownership fields when provided
    if req.session_id:
        params["session_id"] = req.session_id
    if req.strategy_id:
        params["strategy_id"] = req.strategy_id
    if req.intent_id:
        params["intent_id"] = req.intent_id
    if req.idempotency_key:
        params["idempotency_key"] = req.idempotency_key

    tcp_result = _tcp_send_and_await("cancel_order", params)
    if tcp_result and tcp_result.get("status") == "completed":
        return tcp_result.get("result", {})

    gw_url = get_settings_cached().gateway_url
    client = get_http_client()
    r = client.post(
        f"{gw_url}/bridge/commands/enqueue",
        params=params,
    )
    r.raise_for_status()
    req_id = r.json()["id"]
    res = _await_result(req_id)
    if res.get("status") == "error":
        return {**res, "error": _error_payload(res.get("error"))}
    return res


@app.post("/tools/calculate_position_size", response_model=dict)
def tool_calculate_position_size(req: PositionSizeRequest) -> dict:
    symbol_info = resource_symbol_info(req.symbol)
    account = resource_account_summary()
    result = calculate_position_size(
        equity=req.equity
        if req.equity is not None
        else float(account.equity or account.balance or 0.0),
        risk_percent=req.risk_percent,
        entry_price=req.entry_price,
        stop_loss_price=req.stop_loss_price,
        tick_size=float(symbol_info.tick_size or 0.0),
        tick_value=float(symbol_info.tick_value or 0.0),
        volume_min=float(symbol_info.volume_min or 0.0),
        volume_max=float(symbol_info.volume_max or 0.0),
        volume_step=float(symbol_info.volume_step or 0.0),
    )
    return {"symbol": req.symbol, **result}


@app.post("/tools/validate_trade_setup", response_model=dict)
def tool_validate_trade_setup(req: ValidateTradeSetupRequest) -> dict:
    symbol_info = resource_symbol_info(req.symbol)
    account = resource_account_summary()
    book = tool_get_order_book(OrderBookRequest(symbol=req.symbol))
    bid, ask = _first_bid_ask(book)
    if bid is None or ask is None:
        return {"valid": False, "errors": ["market price unavailable"], "warnings": []}

    margin_estimate = get_gateway().estimate_margin(
        MarginEstimateRequest(
            symbol=req.symbol,
            side=req.side,
            volume_lots=req.volume_lots,
            price_hint=req.entry_price,
        )
    )
    result = validate_trade_setup(
        symbol_info=symbol_info.model_dump(),
        account_summary=account.model_dump(),
        side=req.side,
        order_kind=req.order_kind,
        volume_lots=req.volume_lots,
        current_bid=bid,
        current_ask=ask,
        entry_price=req.entry_price,
        sl=req.sl,
        tp=req.tp,
        required_margin=margin_estimate.required_margin,
    )
    return {
        "symbol": req.symbol,
        "bid": bid,
        "ask": ask,
        "required_margin": margin_estimate.required_margin,
        **result,
    }


@app.post("/tools/trail_position", response_model=dict)
def tool_trail_position(req: TrailPositionRequest) -> dict:
    positions = resource_positions_open()
    position = next(
        (item for item in positions if item.position_id == req.position_id), None
    )
    if position is None:
        return {"status": "error", "error": "position_not_found"}

    info = resource_symbol_info(denormalize_symbol(position.symbol))
    point = float(info.point or 0.0)
    if point <= 0 or position.mark_price is None:
        return {"status": "error", "error": "price_context_unavailable"}

    distance = req.distance_points * point
    lock_in = req.lock_in_points * point
    book = tool_get_order_book(
        OrderBookRequest(symbol=denormalize_symbol(position.symbol))
    )
    bid, ask = _first_bid_ask(book)
    if bid is None or ask is None:
        return {"status": "error", "error": "price_context_unavailable"}

    if position.side == "buy":
        new_sl = ask - distance
        if lock_in > 0:
            new_sl = max(new_sl, position.entry_price + lock_in)
        new_sl = min(new_sl, bid - point)
    else:
        new_sl = bid + distance
        if lock_in > 0:
            new_sl = min(new_sl, position.entry_price - lock_in)
        new_sl = max(new_sl, ask + point)

    result = tool_modify_position_sl_tp(
        ModifyPositionSLTPRequest(
            position_id=req.position_id,
            sl=new_sl,
            tp=position.tp,
        )
    )
    return {"computed_sl": new_sl, "result": result}


@app.post("/tools/volatility_profile", response_model=dict)
def tool_volatility_profile(req: VolatilityProfileRequest) -> dict:
    bars = tool_get_bars(
        BarsRequest(symbol=req.symbol, timeframe=req.timeframe, count=req.lookback)
    )
    atr_data = tool_get_indicator(
        IndicatorRequest(
            symbol=req.symbol,
            timeframe=req.timeframe,
            indicator="atr",
            period=req.atr_period,
        )
    )
    atr_value = float(atr_data.get("value", 0.0) or 0.0)
    return build_volatility_profile(
        symbol=req.symbol,
        timeframe=req.timeframe,
        bars=[bar.model_dump() for bar in bars.data],
        atr_value=atr_value,
    )


@app.post("/tools/multi_timeframe_indicators", response_model=dict)
def tool_multi_timeframe_indicators(req: MultiTimeframeIndicatorRequest) -> dict:
    readings: dict[str, dict] = {}
    for timeframe in req.timeframes:
        readings[timeframe] = tool_get_indicator(
            IndicatorRequest(
                symbol=req.symbol,
                timeframe=timeframe,
                indicator=req.indicator,
                period=req.period,
                fast=req.fast,
                slow=req.slow,
                signal=req.signal,
                deviation=req.deviation,
                shift=req.shift,
                k_period=req.k_period,
                d_period=req.d_period,
                slowing=req.slowing,
                tenkan=req.tenkan,
                kijun=req.kijun,
                senkou=req.senkou,
                window=req.window,
            )
        )
    return {"symbol": req.symbol, "indicator": req.indicator, "readings": readings}


@app.post("/tools/correlation_matrix", response_model=dict)
def tool_correlation_matrix(req: CorrelationMatrixRequest) -> dict:
    close_series: dict[str, list[float]] = {}
    for symbol in req.symbols:
        bars = tool_get_bars(
            BarsRequest(symbol=symbol, timeframe=req.timeframe, count=req.lookback)
        )
        close_series[symbol] = [bar.close for bar in bars.data]
    return {
        "timeframe": req.timeframe,
        "lookback": req.lookback,
        "matrix": compute_correlation_matrix(close_series),
    }


class SupportResistanceRequest(BaseModel):
    symbol: str
    timeframe: str = "H1"
    lookback: int = 100


@app.post("/tools/support_resistance", response_model=dict)
def tool_support_resistance(req: SupportResistanceRequest) -> dict:
    bars = tool_get_bars(
        BarsRequest(symbol=req.symbol, timeframe=req.timeframe, count=req.lookback)
    )
    if not bars.data:
        return {
            "symbol": req.symbol,
            "support_levels": [],
            "resistance_levels": [],
        }

    highs = [b.high for b in bars.data]
    lows = [b.low for b in bars.data]
    closes = [b.close for b in bars.data]

    support = sorted(lows)[:3]
    resistance = sorted(highs, reverse=True)[:3]

    return {
        "symbol": denormalize_symbol(req.symbol),
        "timeframe": req.timeframe,
        "lookback": req.lookback,
        "support_levels": support,
        "resistance_levels": resistance,
        "current_price": closes[-1] if closes else None,
    }


# Tools


@app.post("/tools/submit_market_order", response_model=ExecutionResult)
def tool_submit_market_order(req: TradeIntent) -> ExecutionResult:
    # In scaffold, writes are disabled
    raise HTTPException(status_code=501, detail="Execution disabled in scaffold")


# ============================================================
# Market Regime Detection
# ============================================================


@app.post("/tools/market/regime", response_model=dict)
def tool_market_regime(req: MarketRegimeRequest) -> dict:
    """Detect current market regime (ranging, trending, compressing).

    Returns regime classification with strategy hints.
    """
    # Fetch bars and ATR via existing tools
    bars_result = tool_get_bars(
        BarsRequest(symbol=req.symbol, timeframe=req.timeframe, count=req.lookback)
    )
    atr_result = tool_get_indicator(
        IndicatorRequest(
            symbol=req.symbol,
            timeframe=req.timeframe,
            indicator="atr",
            period=req.atr_period,
        )
    )

    atr_value = float(atr_result.get("value", 0.0) or 0.0)
    bars_data = [bar.model_dump() for bar in bars_result.data]

    # Try to get EMA for trend direction
    ema_result = tool_get_indicator(
        IndicatorRequest(
            symbol=req.symbol, timeframe=req.timeframe, indicator="ema", period=20
        )
    )
    ema_slow_result = tool_get_indicator(
        IndicatorRequest(
            symbol=req.symbol, timeframe=req.timeframe, indicator="ema", period=50
        )
    )

    ema_fast = ema_result.get("value")
    ema_slow = ema_slow_result.get("value")

    regime = detect_regime(
        bars=bars_data,
        atr_value=atr_value,
        atr_period=req.atr_period,
        ema_fast=ema_fast,
        ema_slow=ema_slow,
    )

    return {"symbol": req.symbol, **regime}


@app.post("/tools/market/scan", response_model=dict)
def tool_market_scan(req: MarketScanRequest) -> dict:
    """Multi-symbol market scan — returns price, ATR, and regime for all symbols.

    OPTIMIZED: Uses batched bridge commands instead of sequential per-symbol calls.
    All symbols' data is fetched in parallel, then analyzed locally.
    """
    symbol_norms = [normalize_symbol(s) for s in req.symbols]

    # Build batch commands: for each symbol, get bars + ATR + order book
    commands = []
    for sym in symbol_norms:
        commands.append(
            {"type": "get_bars", "symbol": sym, "timeframe": req.timeframe, "count": 20}
        )
        commands.append(
            {
                "type": "get_indicator",
                "symbol": sym,
                "timeframe": req.timeframe,
                "indicator": "atr",
                "period": req.atr_period,
            }
        )
        commands.append({"type": "get_order_book", "symbol": sym})

    try:
        batch_results = _batch_enqueue_and_await(commands, timeout_s=30.0)
    except Exception as e:
        return {
            "symbols": {s: {"error": str(e)} for s in req.symbols},
            "timeframe": req.timeframe,
        }

    results = {}
    for i, sym in enumerate(req.symbols):
        try:
            bars_result = batch_results[i * 3]
            atr_result = batch_results[i * 3 + 1]
            book_result = batch_results[i * 3 + 2]

            # Parse ATR
            atr_value = 0.0
            if atr_result.get("status") == "completed":
                payload = atr_result.get("result", {}).get("payload", {})
                if isinstance(payload, str):
                    try:
                        import json

                        atr_value = float(json.loads(payload).get("value", 0) or 0)
                    except Exception:
                        pass
                elif isinstance(payload, dict):
                    atr_value = float(payload.get("value", 0) or 0)

            # Parse order book
            bid, ask = None, None
            if book_result.get("status") == "completed":
                payload = book_result.get("result", {}).get("payload", {})
                if isinstance(payload, str):
                    try:
                        import json

                        book_data = json.loads(payload)
                    except Exception:
                        book_data = {}
                else:
                    book_data = payload
                bid, ask = _first_bid_ask(book_data)

            # Parse bars for regime
            bars_data = []
            if bars_result.get("status") == "completed":
                payload = bars_result.get("result", {}).get("payload", {})
                if isinstance(payload, str):
                    try:
                        import json

                        bars_data = json.loads(payload).get("data", [])
                    except Exception:
                        pass
                elif isinstance(payload, dict):
                    bars_data = payload.get("data", [])

            # Regime detection (local, no bridge call)
            regime = detect_regime(bars=bars_data, atr_value=atr_value)

            results[sym] = {
                "bid": bid,
                "ask": ask,
                "atr": atr_value,
                "regime": regime.get("regime", "unknown"),
                "recommendation": regime.get("recommendation", "unknown"),
            }
        except Exception as e:
            results[sym] = {"error": str(e)}

    return {"symbols": results, "timeframe": req.timeframe}


# ============================================================
# Bracket Orders
# ============================================================


@app.post("/tools/place_bracket_order", response_model=BracketOrderResult)
def tool_place_bracket_order(req: BracketOrderRequest) -> BracketOrderResult:
    """Place paired BUY STOP + SELL STOP for breakout capture.

    When one order fills, the other is auto-cancelled.
    SL/TP are computed from ATR.
    """
    from mt5_mcp.policy.engine import get_policy
    import uuid

    # Policy gate
    decision = get_policy().validate_submit_order(
        environment=get_settings_cached().environment,
    )
    if not decision.allowed:
        return BracketOrderResult(
            status="error",
            message=f"Policy blocked: {decision.reason}",
        )

    # Get ATR for SL/TP calculation
    atr_result = tool_get_indicator(
        IndicatorRequest(symbol=req.symbol, timeframe="H1", indicator="atr", period=14)
    )
    atr_value = float(atr_result.get("value", 0.0) or 0.0)

    if atr_value <= 0:
        return BracketOrderResult(
            status="error",
            message="Could not determine ATR for SL/TP calculation",
        )

    # Compute SL/TP distances
    sl_distance = atr_value * req.sl_atr_multiplier
    tp_distance = atr_value * req.tp_atr_multiplier

    # BUY STOP order
    buy_sl = req.buy_trigger - sl_distance
    buy_tp = req.buy_trigger + tp_distance

    # SELL STOP order
    sell_sl = req.sell_trigger + sl_distance
    sell_tp = req.sell_trigger - tp_distance

    # Submit BUY STOP
    buy_req = SubmitPendingOrderRequest(
        symbol=req.symbol,
        side="buy",
        kind="stop",
        price=req.buy_trigger,
        volume_lots=req.volume_lots,
        sl=buy_sl,
        tp=buy_tp,
    )
    buy_result = tool_submit_pending_order(buy_req)

    # Submit SELL STOP
    sell_req = SubmitPendingOrderRequest(
        symbol=req.symbol,
        side="sell",
        kind="stop",
        price=req.sell_trigger,
        volume_lots=req.volume_lots,
        sl=sell_sl,
        tp=sell_tp,
    )
    sell_result = tool_submit_pending_order(sell_req)

    buy_order_id = (
        buy_result.get("payload", {}).get("order")
        if isinstance(buy_result, dict)
        else None
    )
    sell_order_id = (
        sell_result.get("payload", {}).get("order")
        if isinstance(sell_result, dict)
        else None
    )

    if isinstance(buy_result, dict) and buy_result.get("status") == "error":
        # Cancel sell if buy failed
        if sell_order_id:
            tool_cancel_order(CancelOrderRequest(order_id=str(sell_order_id)))
        return BracketOrderResult(
            status="error",
            message=f"BUY STOP failed: {buy_result.get('error', 'unknown')}",
            atr_used=atr_value,
        )

    if isinstance(sell_result, dict) and sell_result.get("status") == "error":
        # Cancel buy if sell failed
        if buy_order_id:
            tool_cancel_order(CancelOrderRequest(order_id=str(buy_order_id)))
        return BracketOrderResult(
            status="error",
            message=f"SELL STOP failed: {sell_result.get('error', 'unknown')}",
            atr_used=atr_value,
        )

    # Log to journal
    journal = get_journal_db()
    import uuid as _uuid

    journal.log_decision(
        symbol=req.symbol,
        side="bracket",
        action="entry",
        entry_price=0.0,  # Not filled yet
        volume_lots=req.volume_lots,
        model_justification=req.rationale
        or f"Bracket order: buy@{req.buy_trigger}, sell@{req.sell_trigger}",
        session_id=f"bracket_{_uuid.uuid4().hex[:8]}",
        indicators_considered=["atr", "bracket_breakout"],
    )

    return BracketOrderResult(
        buy_order_id=str(buy_order_id) if buy_order_id else None,
        sell_order_id=str(sell_order_id) if sell_order_id else None,
        status="placed",
        message=f"Bracket orders placed. BUY STOP @ {req.buy_trigger}, SELL STOP @ {req.sell_trigger}",
        atr_used=atr_value,
        computed_sl_buy=buy_sl,
        computed_tp_buy=buy_tp,
        computed_sl_sell=sell_sl,
        computed_tp_sell=sell_tp,
    )


# ============================================================
# Phase 3: Trailing Stop (Server-Side)
# ============================================================


# In-memory trailing stop trackers
_trailing_stops: dict[str, dict] = {}


@app.post("/tools/set_trailing_stop", response_model=TrailingStopResult)
def tool_set_trailing_stop(req: SetTrailingStopRequest) -> TrailingStopResult:
    """Start server-side trailing stop for a position.

    The server will check price every check_interval_seconds and
    automatically trail the stop loss.
    """
    positions = resource_positions_open()
    position = next((p for p in positions if p.position_id == req.position_id), None)
    if position is None:
        return TrailingStopResult(
            position_id=req.position_id,
            status="error",
            message="Position not found",
        )

    info = resource_symbol_info(position.symbol)
    point = float(info.point or 0.0)
    if point <= 0:
        return TrailingStopResult(
            position_id=req.position_id,
            status="error",
            message="Could not determine point value",
        )

    # Get ATR for distance calculation
    atr_result = tool_get_indicator(
        IndicatorRequest(
            symbol=position.symbol, timeframe="H1", indicator="atr", period=14
        )
    )
    atr_value = float(atr_result.get("value", 0.0) or 0.0)

    if atr_value <= 0:
        return TrailingStopResult(
            position_id=req.position_id,
            status="error",
            message="Could not determine ATR",
        )

    distance_points = int(atr_value * req.distance_atr_multiplier / point)
    lock_in_points = int(atr_value * req.lock_in_profit_after_atr / point)

    # Register trailing stop
    _trailing_stops[req.position_id] = {
        "position_id": req.position_id,
        "symbol": position.symbol,
        "side": position.side,
        "distance_points": distance_points,
        "lock_in_points": lock_in_points,
        "check_interval": req.check_interval_seconds,
        "initial_sl": position.sl,
        "active": True,
        "last_check": time.time(),
    }

    return TrailingStopResult(
        position_id=req.position_id,
        status="active",
        message=f"Trailing stop active: distance={distance_points}pts, lock_in={lock_in_points}pts",
        initial_sl=position.sl,
    )


@app.post("/tools/trailing_stop/cancel", response_model=dict)
def tool_trailing_stop_cancel(position_id: str) -> dict:
    """Cancel an active trailing stop."""
    if position_id in _trailing_stops:
        _trailing_stops[position_id]["active"] = False
        del _trailing_stops[position_id]
        return {"status": "cancelled", "position_id": position_id}
    return {"status": "not_found", "position_id": position_id}


@app.post("/tools/trailing_stop/list", response_model=dict)
def tool_trailing_stop_list() -> dict:
    """List all active trailing stops."""
    active = {
        pid: {k: v for k, v in ts.items() if k != "last_check"}
        for pid, ts in _trailing_stops.items()
        if ts.get("active")
    }
    return {"active_stops": active, "count": len(active)}


# ============================================================
# Phase 4: Price Alert (Long-Polling)
# ============================================================


@app.post("/resources/market/wait_for_price", response_model=PriceAlertResult)
def tool_wait_for_price(req: PriceAlertRequest) -> PriceAlertResult:
    """Long-polling price alert. Holds connection until price condition is met.

    Eliminates manual polling — the server checks and returns when triggered.
    """
    import time as _time

    end_time = _time.time() + req.timeout_seconds
    symbol_norm = normalize_symbol(req.symbol)

    while _time.time() < end_time:
        try:
            book = tool_get_order_book(OrderBookRequest(symbol=req.symbol))
            bid, ask = _first_bid_ask(book)

            if bid is None or ask is None:
                _time.sleep(1)
                continue

            # Use mid price for crosses, ask for above, bid for below
            if req.condition == "above":
                current = ask
                triggered = current >= req.price
            elif req.condition == "below":
                current = bid
                triggered = current <= req.price
            else:  # crosses
                mid = (bid + ask) / 2
                current = mid
                triggered = True  # Any update is a "cross" in this mode

            if triggered:
                return PriceAlertResult(
                    symbol=req.symbol,
                    condition=req.condition,
                    trigger_price=req.price,
                    actual_price=current,
                    triggered=True,
                )
        except Exception:
            pass

        _time.sleep(1)  # Check every second

    # Timeout
    try:
        book = tool_get_order_book(OrderBookRequest(symbol=req.symbol))
        bid, ask = _first_bid_ask(book)
        current = (bid + ask) / 2 if bid and ask else 0
    except Exception:
        current = 0

    return PriceAlertResult(
        symbol=req.symbol,
        condition=req.condition,
        trigger_price=req.price,
        actual_price=current,
        triggered=False,
        timed_out=True,
    )


# ============================================================
# Metacognition & AI Reasoning — Decision Journal
# ============================================================


@app.post("/tools/trading/log_decision", response_model=dict)
def tool_log_trade_decision(req: TradeDecisionLogRequest) -> dict:
    """Log a trading decision with full AI reasoning.

    EVERY trading decision should be logged here. This enables:
    - Post-trade reflection ("What did I do wrong?")
    - Pattern recognition ("I always lose when anxious")
    - Agentic metacognition (learning from own history)

    The model_justification field is CRITICAL — it captures WHY
    the AI made the decision, not just what it did.
    """
    journal = get_journal_db()
    decision_id = req.decision_id

    if decision_id:
        # Update existing decision (e.g., adding exit info to an entry)
        updated = journal.update_decision(
            decision_id,
            exit_price=req.exit_price,
            pnl=req.pnl,
            outcome=req.outcome,
            lesson_learned=req.lesson_learned,
            would_do_differently=req.would_do_differently,
            mistake_category=req.mistake_category,
            quality_rating=req.quality_rating,
            emotional_self_report=req.emotional_self_report,
            model_justification=req.model_justification,
        )
        return {
            "status": "updated" if updated else "not_found",
            "decision_id": decision_id,
        }

    # New decision
    decision_id = journal.log_decision(
        symbol=req.symbol,
        side=req.side,
        action=req.action,
        entry_price=req.entry_price,
        exit_price=req.exit_price,
        sl=req.sl,
        tp=req.tp,
        volume_lots=req.volume_lots,
        pnl=req.pnl,
        session_id=req.session_id,
        regime=req.regime,
        atr_value=req.atr_value,
        atr_percent_of_price=req.atr_percent_of_price,
        rsi_value=req.rsi_value,
        indicator_snapshot=req.indicator_snapshot,
        model_justification=req.model_justification,
        indicators_considered=req.indicators_considered,
        confidence_level=req.confidence_level,
        risk_assessment=req.risk_assessment,
        emotional_self_report=req.emotional_self_report,
        alternatives_considered=req.alternatives_considered,
        expected_duration=req.expected_duration,
        expected_move_points=req.expected_move_points,
        outcome=req.outcome,
        lesson_learned=req.lesson_learned,
        would_do_differently=req.would_do_differently,
        mistake_category=req.mistake_category,
        quality_rating=req.quality_rating,
    )

    return {
        "status": "logged",
        "decision_id": decision_id,
        "message": "Decision logged. Use this ID to update with outcome later.",
    }


@app.post("/tools/trading/reflect", response_model=dict)
def tool_reflect_on_trades(req: TradeJournalReflectionRequest) -> dict:
    """Query past decisions for metacognitive reflection.

    The AI agent uses this to understand its own patterns:
    - "Show me my last 5 losing trades"
    - "What regime was I in when I won?"
    - "What happens when I'm anxious?"
    """
    journal = get_journal_db()
    decisions = journal.query(
        symbol=req.symbol,
        outcome=req.outcome,
        regime=req.regime,
        emotional_self_report=req.emotional_self_report,
        mistake_category=req.mistake_category,
        action=req.action,
        limit=req.limit,
    )

    # Clean up for response (parse JSON fields)
    for d in decisions:
        if d.get("indicator_snapshot"):
            try:
                d["indicator_snapshot"] = json.loads(d["indicator_snapshot"])
            except Exception:
                pass
        if d.get("indicators_considered"):
            try:
                d["indicators_considered"] = json.loads(d["indicators_considered"])
            except Exception:
                pass

    return {
        "count": len(decisions),
        "decisions": decisions,
    }


@app.post("/tools/trading/insights", response_model=dict)
def tool_trading_insights(lookback_days: int = 7) -> dict:
    """Get metacognitive insights from recent trading history.

    Returns patterns like:
    - Win rate by emotional state ("When anxious: 20% win rate")
    - Win rate by regime ("Ranging: 60%, Trending: 35%")
    - Most common mistakes
    - Confidence on wins vs losses
    - Recent lessons learned

    The AI should call this at the start of each session to
    orient itself and avoid repeating past mistakes.
    """
    journal = get_journal_db()
    insights = journal.get_reflection_insights(lookback_days=lookback_days)

    # Add AI-actionable guidance
    guidance = []
    if insights.get("win_rate_by_emotional_state"):
        for state, data in insights["win_rate_by_emotional_state"].items():
            if data["win_rate"] < 30:
                guidance.append(
                    f"⚠️ When you feel '{state}', your win rate is only {data['win_rate']}%. "
                    f"Consider stepping back or reducing position size."
                )

    if insights.get("mistake_frequency"):
        top_mistake = insights["mistake_frequency"][0]
        guidance.append(
            f"📌 Your most common mistake: {top_mistake['category']} "
            f"({top_mistake['count']} times). Be conscious of this today."
        )

    if insights.get("overall"):
        overall = insights["overall"]
        if overall["win_rate"] < 40:
            guidance.append(
                f"📊 Your recent win rate is {overall['win_rate']}%. "
                "Consider reducing trade frequency and focusing on higher-conviction setups."
            )
        elif overall["win_rate"] > 60:
            guidance.append(
                f"📊 Your recent win rate is {overall['win_rate']}%. "
                "You're in good form. Stick to your process."
            )

    insights["ai_guidance"] = guidance
    return insights


# ============================================================
# Trading Context — Symbol Education
# ============================================================
# Trading Context — Live Market-Derived Composure Report
# ============================================================


@app.post("/tools/trading/context", response_model=dict)
def tool_trading_context(req: TradingContextRequest) -> dict:
    """Get LIVE market context for a symbol.

    OPTIMIZED: Uses batched bridge commands instead of 6 sequential calls.
    All data fetched in single round-trip, then assembled locally.

    Combines baseline knowledge (point values, typical ranges) with
    REAL-TIME market data (current ATR, order book, indicators, bar patterns).

    Answers: "Is 200 points a lot RIGHT NOW?" "How does current volatility compare?"
    """
    symbol = req.symbol
    symbol_norm = normalize_symbol(symbol)

    # Batch all bridge commands into a single round-trip
    commands = [
        {
            "type": "get_indicator",
            "symbol": symbol_norm,
            "timeframe": "H1",
            "indicator": "atr",
            "period": 14,
        },
        {"type": "get_order_book", "symbol": symbol_norm},
        {
            "type": "get_indicator",
            "symbol": symbol_norm,
            "timeframe": "H1",
            "indicator": "rsi",
            "period": 14,
        },
        {
            "type": "get_indicator",
            "symbol": symbol_norm,
            "timeframe": "H1",
            "indicator": "ema",
            "period": 20,
        },
        {
            "type": "get_indicator",
            "symbol": symbol_norm,
            "timeframe": "H1",
            "indicator": "ema",
            "period": 50,
        },
        {"type": "get_bars", "symbol": symbol_norm, "timeframe": "H1", "count": 3},
    ]

    try:
        batch_results = _batch_enqueue_and_await(commands, timeout_s=15.0)
    except Exception:
        # Fallback: build context with no live data
        return build_context(symbol=symbol)

    # Parse results
    atr_result, book_result, rsi_result, ema20_result, ema50_result, bars_result = (
        batch_results
    )

    current_atr = None
    current_price = None
    spread_points = None
    rsi = None
    ema_fast = None
    ema_slow = None
    last_bar_range = None
    last_bar_direction = None

    # Parse ATR
    if atr_result.get("status") == "completed":
        payload = atr_result.get("result", {}).get("payload", {})
        if isinstance(payload, str):
            try:
                import json

                current_atr = float(json.loads(payload).get("value", 0) or 0)
            except Exception:
                pass
        elif isinstance(payload, dict):
            current_atr = float(payload.get("value", 0) or 0)

    # Parse order book
    if book_result.get("status") == "completed":
        payload = book_result.get("result", {}).get("payload", {})
        if isinstance(payload, str):
            try:
                import json

                book_data = json.loads(payload)
            except Exception:
                book_data = {}
        else:
            book_data = payload
        bid, ask = _first_bid_ask(book_data)
        if bid and ask:
            current_price = (bid + ask) / 2
            spread_points = ask - bid

    # Parse RSI
    if rsi_result.get("status") == "completed":
        payload = rsi_result.get("result", {}).get("payload", {})
        if isinstance(payload, str):
            try:
                import json

                rsi = float(json.loads(payload).get("value", 0) or 0)
            except Exception:
                pass
        elif isinstance(payload, dict):
            rsi = float(payload.get("value", 0) or 0)

    # Parse EMAs
    if ema20_result.get("status") == "completed":
        payload = ema20_result.get("result", {}).get("payload", {})
        if isinstance(payload, str):
            try:
                import json

                ema_fast = json.loads(payload).get("value")
            except Exception:
                pass
        elif isinstance(payload, dict):
            ema_fast = payload.get("value")
    if ema50_result.get("status") == "completed":
        payload = ema50_result.get("result", {}).get("payload", {})
        if isinstance(payload, str):
            try:
                import json

                ema_slow = json.loads(payload).get("value")
            except Exception:
                pass
        elif isinstance(payload, dict):
            ema_slow = payload.get("value")

    # Parse bars
    if bars_result.get("status") == "completed":
        payload = bars_result.get("result", {}).get("payload", {})
        if isinstance(payload, str):
            try:
                import json

                bars_data = json.loads(payload).get("data", [])
            except Exception:
                bars_data = []
        elif isinstance(payload, dict):
            bars_data = payload.get("data", [])
        else:
            bars_data = []
        if bars_data:
            last = bars_data[-1]
            last_bar_range = last.get("high", 0) - last.get("low", 0)
            body = last.get("close", 0) - last.get("open", 0)
            if body > 0:
                last_bar_direction = "bullish"
            elif body < 0:
                last_bar_direction = "bearish"
            else:
                last_bar_direction = "doji"

    context = build_context(
        symbol=symbol,
        current_atr=current_atr,
        current_price=current_price,
        spread_points=spread_points,
        rsi=rsi,
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        last_bar_range=last_bar_range,
        last_bar_direction=last_bar_direction,
    )

    return context


# ============================================================
# Trading Coach — Data-Driven Advisory Feedback
# ============================================================


@app.post("/tools/trading/coach", response_model=dict)
def tool_trading_coach(req: TradingCoachRequest) -> dict:
    """Get advisory coaching derived from LIVE market data.

    OPTIMIZED: Uses batched bridge commands instead of 8+ sequential calls.
    All data fetched in single round-trip, then analyzed locally.

    This does NOT block. It fetches real-time indicators, bar patterns,
    order book depth, and journal history to generate context-specific advice.

    Every warning and insight is computed from actual market conditions.
    """
    symbol = req.symbol
    side = req.side
    symbol_norm = normalize_symbol(symbol)

    # Batch all bridge commands into a single round-trip
    commands = [
        {
            "type": "get_indicator",
            "symbol": symbol_norm,
            "timeframe": "H1",
            "indicator": "atr",
            "period": 14,
        },
        {
            "type": "get_indicator",
            "symbol": symbol_norm,
            "timeframe": "H1",
            "indicator": "rsi",
            "period": 14,
        },
        {
            "type": "get_indicator",
            "symbol": symbol_norm,
            "timeframe": "H1",
            "indicator": "ema",
            "period": 20,
        },
        {
            "type": "get_indicator",
            "symbol": symbol_norm,
            "timeframe": "H1",
            "indicator": "ema",
            "period": 50,
        },
        {"type": "get_order_book", "symbol": symbol_norm},
        {"type": "get_bars", "symbol": symbol_norm, "timeframe": "H1", "count": 5},
    ]

    try:
        batch_results = _batch_enqueue_and_await(commands, timeout_s=15.0)
    except Exception:
        # Fallback: coach with provided params only
        coach = TradingCoach()
        advice = coach.evaluate(symbol=symbol, side=side)
        return {
            "recommendation": advice.recommendation,
            "warnings": advice.warnings,
            "insights": advice.insights,
            "confidence_factors": advice.confidence_factors,
            "raw_metrics": advice.raw_metrics,
        }

    atr_result, rsi_result, ema20_result, ema50_result, book_result, bars_result = (
        batch_results
    )

    # Parse all results
    current_atr = None
    rsi = None
    ema_fast = None
    ema_slow = None
    current_price = None
    spread_points = None
    last_bar_range = None
    last_bar_body = None
    last_bar_direction = None
    recent_compression = None
    regime = req.regime
    position_in_range = None

    # Parse ATR
    if atr_result.get("status") == "completed":
        payload = atr_result.get("result", {}).get("payload", {})
        if isinstance(payload, str):
            try:
                import json

                current_atr = float(json.loads(payload).get("value", 0) or 0)
            except Exception:
                pass
        elif isinstance(payload, dict):
            current_atr = float(payload.get("value", 0) or 0)

    # Parse RSI
    if rsi_result.get("status") == "completed":
        payload = rsi_result.get("result", {}).get("payload", {})
        if isinstance(payload, str):
            try:
                import json

                rsi = float(json.loads(payload).get("value", 0) or 0)
            except Exception:
                pass
        elif isinstance(payload, dict):
            rsi = float(payload.get("value", 0) or 0)

    # Parse EMAs
    if ema20_result.get("status") == "completed":
        payload = ema20_result.get("result", {}).get("payload", {})
        if isinstance(payload, str):
            try:
                import json

                ema_fast = json.loads(payload).get("value")
            except Exception:
                pass
        elif isinstance(payload, dict):
            ema_fast = payload.get("value")
    if ema50_result.get("status") == "completed":
        payload = ema50_result.get("result", {}).get("payload", {})
        if isinstance(payload, str):
            try:
                import json

                ema_slow = json.loads(payload).get("value")
            except Exception:
                pass
        elif isinstance(payload, dict):
            ema_slow = payload.get("value")

    # Parse order book
    if book_result.get("status") == "completed":
        payload = book_result.get("result", {}).get("payload", {})
        if isinstance(payload, str):
            try:
                import json

                book_data = json.loads(payload)
            except Exception:
                book_data = {}
        else:
            book_data = payload
        bid, ask = _first_bid_ask(book_data)
        if bid and ask:
            current_price = (bid + ask) / 2
            spread_points = ask - bid

    # Parse bars
    if bars_result.get("status") == "completed":
        payload = bars_result.get("result", {}).get("payload", {})
        if isinstance(payload, str):
            try:
                import json

                bars_data = json.loads(payload).get("data", [])
            except Exception:
                bars_data = []
        elif isinstance(payload, dict):
            bars_data = payload.get("data", [])
        else:
            bars_data = []

        if len(bars_data) >= 2:
            last = bars_data[-1]
            prev = bars_data[-2]
            last_bar_range = last.get("high", 0) - last.get("low", 0)
            last_bar_body = last.get("close", 0) - last.get("open", 0)
            if last_bar_body > 0:
                last_bar_direction = "bullish"
            elif last_bar_body < 0:
                last_bar_direction = "bearish"
            else:
                last_bar_direction = "doji"

            # Compression: average of last 3 bars vs ATR
            if len(bars_data) >= 3 and current_atr and current_atr > 0:
                recent_ranges = [
                    b.get("high", 0) - b.get("low", 0) for b in bars_data[-3:]
                ]
                avg_recent = sum(recent_ranges) / len(recent_ranges)
                recent_compression = avg_recent / current_atr

    # Detect regime if not provided (local, no bridge call)
    if not regime and bars_data:
        regime_result = detect_regime(
            bars=bars_data,
            atr_value=current_atr or 0,
            ema_fast=ema_fast,
            ema_slow=ema_slow,
        )
        regime = regime_result.get("regime")
        if "price_position_pct" in regime_result:
            position_in_range = regime_result["price_position_pct"]

    # Get journal context
    journal = get_journal_db()
    recent_decisions = journal.query(symbol=symbol, limit=10)
    recent_with_outcome = [
        d for d in recent_decisions if d.get("outcome") in ("win", "loss")
    ]

    consecutive_losses = 0
    for d in reversed(recent_with_outcome):
        if d["outcome"] == "loss":
            consecutive_losses += 1
        else:
            break

    win_rate_10 = None
    if recent_with_outcome:
        wins = sum(1 for d in recent_with_outcome if d["outcome"] == "win")
        win_rate_10 = wins / len(recent_with_outcome) * 100

    # Compute SL/TP distances if provided
    sl_points = req.sl_distance_points
    tp_points = req.tp_distance_points

    # Run the data-driven coach
    coach = TradingCoach()
    advice = coach.evaluate(
        symbol=symbol,
        side=side,
        atr_value=current_atr,
        rsi=rsi,
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        current_price=current_price,
        spread_points=spread_points,
        last_bar_range=last_bar_range,
        last_bar_body=last_bar_body,
        last_bar_direction=last_bar_direction,
        recent_bars_compression=recent_compression,
        proposed_sl_points=sl_points,
        proposed_tp_points=tp_points,
        indicator_agreements=req.indicator_agreements,
        total_indicators_checked=None,
        trades_today=req.trades_today,
        daily_pnl=req.daily_pnl,
        recent_consecutive_losses=consecutive_losses,
        win_rate_last_10=win_rate_10,
        position_in_range=position_in_range,
        regime=regime,
    )

    return {
        "recommendation": advice.recommendation,
        "warnings": advice.warnings,
        "insights": advice.insights,
        "confidence_factors": advice.confidence_factors,
        "raw_metrics": advice.raw_metrics,
    }


# ============================================================
# Quick Context — One-Call Decision Support
# ============================================================


@app.post("/tools/trading/decision_support", response_model=dict)
def tool_decision_support(req: TradingDecisionSupportRequest) -> dict:
    """One-call decision support: fetches ALL market data in a SINGLE
    batched bridge round-trip, then runs analysis locally.

    BEFORE: 6+ sequential bridge calls = 3-5 seconds
    AFTER:  1 batched enqueue + 1 poll cycle = ~200-400ms

    Combines: regime detection + ATR + RSI + EMAs + coaching advice.
    """
    symbol = req.symbol
    side = req.side
    symbol_norm = normalize_symbol(symbol)
    result: dict = {"symbol": symbol, "side": side}

    # Batch all bridge commands into a single round-trip
    commands = [
        {"type": "get_bars", "symbol": symbol_norm, "timeframe": "H1", "count": 20},
        {
            "type": "get_indicator",
            "symbol": symbol_norm,
            "timeframe": "H1",
            "indicator": "atr",
            "period": 14,
        },
        {
            "type": "get_indicator",
            "symbol": symbol_norm,
            "timeframe": "H1",
            "indicator": "rsi",
            "period": 14,
        },
        {
            "type": "get_indicator",
            "symbol": symbol_norm,
            "timeframe": "H1",
            "indicator": "ema",
            "period": 20,
        },
        {
            "type": "get_indicator",
            "symbol": symbol_norm,
            "timeframe": "H1",
            "indicator": "ema",
            "period": 50,
        },
        {"type": "get_order_book", "symbol": symbol_norm},
    ]

    try:
        batch_results = _batch_enqueue_and_await(commands, timeout_s=15.0)
    except Exception as e:
        return {"symbol": symbol, "side": side, "error": f"Batch fetch failed: {e}"}

    # Parse results
    bars_result = batch_results[0]
    atr_result = batch_results[1]
    rsi_result = batch_results[2]
    ema20_result = batch_results[3]
    ema50_result = batch_results[4]
    book_result = batch_results[5]

    # Parse bars
    bars_data = []
    if bars_result.get("status") == "completed":
        payload = bars_result.get("result", {}).get("payload", {})
        if isinstance(payload, str):
            try:
                import json

                bars_data = json.loads(payload).get("data", [])
            except Exception:
                pass
        elif isinstance(payload, dict):
            bars_data = payload.get("data", [])

    # Parse ATR
    atr_value = 0.0
    if atr_result.get("status") == "completed":
        payload = atr_result.get("result", {}).get("payload", {})
        if isinstance(payload, str):
            try:
                import json

                atr_value = float(json.loads(payload).get("value", 0) or 0)
            except Exception:
                pass
        elif isinstance(payload, dict):
            atr_value = float(payload.get("value", 0) or 0)
    result["atr"] = {"value": atr_value}

    # Parse RSI
    rsi = None
    if rsi_result.get("status") == "completed":
        payload = rsi_result.get("result", {}).get("payload", {})
        if isinstance(payload, str):
            try:
                import json

                rsi = float(json.loads(payload).get("value", 0) or 0)
            except Exception:
                pass
        elif isinstance(payload, dict):
            rsi = float(payload.get("value", 0) or 0)
    result["rsi"] = rsi

    # Parse EMAs
    ema_fast = None
    ema_slow = None
    if ema20_result.get("status") == "completed":
        payload = ema20_result.get("result", {}).get("payload", {})
        if isinstance(payload, str):
            try:
                import json

                ema_fast = json.loads(payload).get("value")
            except Exception:
                pass
        elif isinstance(payload, dict):
            ema_fast = payload.get("value")
    if ema50_result.get("status") == "completed":
        payload = ema50_result.get("result", {}).get("payload", {})
        if isinstance(payload, str):
            try:
                import json

                ema_slow = json.loads(payload).get("value")
            except Exception:
                pass
        elif isinstance(payload, dict):
            ema_slow = payload.get("value")
    result["ema_20"] = ema_fast
    result["ema_50"] = ema_slow

    # Parse order book
    bid, ask = None, None
    if book_result.get("status") == "completed":
        payload = book_result.get("result", {}).get("payload", {})
        if isinstance(payload, str):
            try:
                import json

                book_data = json.loads(payload)
            except Exception:
                book_data = {}
        else:
            book_data = payload
        bid, ask = _first_bid_ask(book_data)
    result["bid"] = bid
    result["ask"] = ask

    # Run regime detection locally (no bridge call needed - we have bars + ATR + EMAs)
    regime = detect_regime(
        bars=bars_data,
        atr_value=atr_value,
        ema_fast=ema_fast,
        ema_slow=ema_slow,
    )
    result["regime"] = regime

    # Run coaching locally
    coach = TradingCoach()
    advice = coach.evaluate(
        symbol=symbol,
        side=side,
        regime=regime.get("regime"),
        atr_value=atr_value,
        rsi=rsi,
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        current_price=(bid + ask) / 2 if bid and ask else None,
        proposed_sl_points=req.sl_distance_points,
        proposed_tp_points=req.tp_distance_points,
    )
    result["coaching"] = {
        "recommendation": advice.recommendation,
        "warnings": advice.warnings,
        "insights": advice.insights,
        "raw_metrics": advice.raw_metrics,
    }

    # ====== SESSION CONTEXT ======
    try:
        session_ctx = _get_session_context()
        pair_ctx = _get_session_for_pair(symbol)
        result["session_context"] = {
            "current_sessions": session_ctx.current_sessions,
            "active_overlaps": session_ctx.active_overlaps,
            "is_market_open": session_ctx.is_market_open,
            "volatility_regime": session_ctx.volatility_regime,
            "spread_quality": session_ctx.spread_quality,
            "volume_concentration_pct": round(
                session_ctx.volume_concentration * 100, 1
            ),
            "day_of_week": session_ctx.day_of_week,
            "day_of_week_factor": session_ctx.day_of_week_factor,
            "pair_quality_score": pair_ctx.get("quality_score", 0),
            "pair_is_optimal": pair_ctx.get("is_optimal", False),
            "pair_warnings": pair_ctx.get("warnings", []),
        }
    except Exception:
        result["session_context"] = {"error": "unavailable"}

    # ====== ECONOMIC CALENDAR / NEWS BLACKOUT ======
    # Try MT5 Terminal Calendar API first, fall back to schedule-based
    try:
        sym_clean = symbol.upper().replace("/", "")
        currencies_to_check = set()
        if len(sym_clean) >= 6:
            currencies_to_check.add(sym_clean[:3])
            currencies_to_check.add(sym_clean[3:6])

        mt5_calendar_ok = False

        # PRIMARY: Try MT5 Terminal Calendar via bridge
        try:
            cal_cmd = {
                "type": "get_calendar",
                "currency": ",".join(currencies_to_check)
                if currencies_to_check
                else "",
                "hours_ahead": "2",
                "min_impact": "HIGH",
            }
            cal_results = _batch_enqueue_and_await([cal_cmd], timeout_s=10.0)
            cal_result = cal_results[0]

            if cal_result.get("status") == "completed":
                cal_payload = cal_result.get("result", {}).get("payload", {})
                if isinstance(cal_payload, str):
                    import json as _json

                    cal_payload = _json.loads(cal_payload)

                if "events" in cal_payload and "error" not in cal_payload:
                    mt5_calendar_ok = True
                    result["calendar_context"] = {
                        "is_blackout": cal_payload.get("event_count", 0) > 0,
                        "source": "mt5_terminal",
                        "upcoming_high_impact_events": cal_payload.get("events", [])[
                            :5
                        ],
                        "event_count_next_2h": cal_payload.get("event_count", 0),
                    }
        except Exception:
            pass  # Fall through to schedule-based

        # FALLBACK: Schedule-based calendar
        if not mt5_calendar_ok:
            blackouts = []
            for cur in currencies_to_check:
                blackout = _is_blackout_now(currency=cur, minutes_ahead=30)
                if blackout.get("is_blackout"):
                    blackouts.append(blackout)

            upcoming = _get_upcoming_events(hours_ahead=2, min_impact="HIGH")
            relevant_events = [
                e.to_dict()
                for e in upcoming
                if not currencies_to_check or e.currency in currencies_to_check
            ]

            result["calendar_context"] = {
                "is_blackout": len(blackouts) > 0,
                "source": "schedule_based_fallback",
                "blackout_details": blackouts,
                "upcoming_high_impact_events": relevant_events[:5],
                "event_count_next_2h": len(relevant_events),
            }
    except Exception:
        result["calendar_context"] = {"error": "unavailable"}

    return result


# ============================================================
# Agent System Prompt Injection
# ============================================================


@app.post("/tools/trading/agent_prompt", response_model=dict)
def tool_agent_prompt(req: AgentSystemPromptRequest) -> dict:
    """Generate the complete system prompt that orients a new trading agent.

    Call this at session start to get a context-rich prompt including:
    - Available tools and when to use them
    - Complete trading workflow
    - Market context with LIVE data (optional)
    - News integration guide
    - Metacognition loop instructions
    """
    account_data = None
    symbol_contexts = None

    if req.live_account_context:
        try:
            acct = resource_account_summary()
            account_data = {
                "equity": float(acct.equity or 0),
                "balance": float(acct.balance or 0),
                "free_margin": float(acct.free_margin or 0),
                "account_id": acct.account_id or "N/A",
                "environment": get_settings_cached().environment,
            }
        except Exception:
            pass

    if req.live_symbol_context:
        symbol_contexts = {}
        for sym in req.live_symbol_context:
            try:
                ctx = tool_trading_context(TradingContextRequest(symbol=sym))
                symbol_contexts[sym] = ctx
            except Exception:
                symbol_contexts[sym] = {"error": "context unavailable"}

    prompt = build_agent_system_prompt(
        include_market_context=req.include_market_context,
        include_news_context=req.include_news_context,
        include_workflow=req.include_workflow,
        include_trading_rules=req.include_trading_rules,
        include_tool_guide=req.include_tool_guide,
        include_metacognition=req.include_metacognition,
        live_account_context=account_data,
        live_symbol_context=symbol_contexts,
    )

    return {
        "prompt": prompt,
        "sections_included": {
            "market_context": req.include_market_context,
            "news_context": req.include_news_context,
            "workflow": req.include_workflow,
            "trading_rules": req.include_trading_rules,
            "tool_guide": req.include_tool_guide,
            "metacognition": req.include_metacognition,
        },
        "live_context_injected": bool(account_data or symbol_contexts),
    }


# ============================================================
# Phase 4: Position Monitor (Long-Polling)
# ============================================================


@app.post("/resources/positions/monitor", response_model=PositionMonitorResult)
def tool_monitor_position(req: PositionMonitorRequest) -> PositionMonitorResult:
    """Long-polling position monitor. Holds connection until alert triggers."""
    import time as _time

    end_time = _time.time() + req.timeout_seconds

    while _time.time() < end_time:
        try:
            positions = resource_positions_open()
            position = next(
                (p for p in positions if p.position_id == req.position_id), None
            )

            if position is None:
                return PositionMonitorResult(
                    position_id=req.position_id,
                    alert_type="closed",
                    triggered_value=0,
                )

            current_pnl = position.pnl or 0

            for pnl_level in req.alert_at_pnl:
                if current_pnl >= pnl_level:
                    return PositionMonitorResult(
                        position_id=req.position_id,
                        alert_type="pnl",
                        current_pnl=current_pnl,
                        triggered_value=pnl_level,
                    )

            book = tool_get_order_book(OrderBookRequest(symbol=position.symbol))
            bid, ask = _first_bid_ask(book)
            if bid and ask:
                current_price = (bid + ask) / 2
                for price_level in req.alert_at_price:
                    if position.side == "buy" and current_price >= price_level:
                        return PositionMonitorResult(
                            position_id=req.position_id,
                            alert_type="price",
                            current_pnl=current_pnl,
                            current_price=current_price,
                            triggered_value=price_level,
                        )
                    elif position.side == "sell" and current_price <= price_level:
                        return PositionMonitorResult(
                            position_id=req.position_id,
                            alert_type="price",
                            current_pnl=current_pnl,
                            current_price=current_price,
                            triggered_value=price_level,
                        )

        except Exception:
            pass

        _time.sleep(2)

    try:
        positions = resource_positions_open()
        position = next(
            (p for p in positions if p.position_id == req.position_id), None
        )
        current_pnl = position.pnl if position else 0
        current_price = position.mark_price if position else 0
    except Exception:
        current_pnl = 0
        current_price = 0

    return PositionMonitorResult(
        position_id=req.position_id,
        alert_type="timeout",
        current_pnl=current_pnl,
        current_price=current_price,
        timed_out=True,
    )


# ============================================================
# News & Session Awareness Tools
# ============================================================


@app.post("/tools/news/fetch", response_model=dict)
def tool_news_fetch(req: NewsFetchRequest) -> dict:
    """Fetch forex-relevant news from RSS feeds.

    Default: FOREX_MAJOR pool, last 6 hours. Custom: specify pools/currencies/keywords.
    Set enrich=true to add sentiment, topics, and entity extraction.
    """
    result = _fetch_news(
        pools=req.pools,
        currencies=req.currencies,
        keywords=req.keywords,
        exclude_keywords=req.exclude_keywords,
        limit=req.limit,
        hours_back=req.hours_back,
        match_all=req.match_all,
        source_ids=req.source_ids,
    )

    # Optionally enrich
    if req.enrich and result.get("items"):
        enriched = _enrich_news(result["items"])
        result["items"] = enriched["items"]

    return result


@app.post("/tools/news/enrich", response_model=dict)
def tool_news_enrich(req: dict) -> dict:
    """Add sentiment analysis, topics, entities, and currency relevance to news items.

    Pass items from news.fetch in the request body.
    """
    items = req.get("items", [])
    extract = req.get(
        "extract", ["sentiment", "topics", "entities", "currency_relevance"]
    )
    return _enrich_news(items, extract=extract)


@app.post("/tools/news/pools", response_model=dict)
def tool_news_pools() -> dict:
    """Get all available news pools and sources."""
    return {
        "pools": _get_available_pools(),
        "sources": _get_available_sources(),
    }


@app.post("/tools/trading/economic_calendar", response_model=dict)
def tool_economic_calendar(req: EconomicCalendarRequest) -> dict:
    """Get upcoming high-impact economic events.

    PRIMARY: Fetches from MT5 Terminal's native Economic Calendar API
    via the EA bridge (CalendarEventByCurrency + CalendarValueHistory).

    FALLBACK: If MT5 calendar is unavailable (disconnected, broker doesn't
    provide calendar data), falls back to schedule-based recurring events.

    The MT5 terminal provides REAL data — actual/forecast/previous values
    for 600+ indicators across 18 major economies. This is the ground truth.
    """
    mt5_data = None
    mt5_error = None

    # PRIMARY: Try MT5 Terminal Calendar API via EA bridge
    try:
        calendar_cmd = {
            "type": "get_calendar",
            "currency": req.currency or "",
            "hours_ahead": str(req.hours_ahead),
            "min_impact": req.min_impact,
        }
        batch_results = _batch_enqueue_and_await([calendar_cmd], timeout_s=15.0)
        result = batch_results[0]

        if result.get("status") == "completed":
            payload = result.get("result", {}).get("payload", {})
            if isinstance(payload, str):
                import json

                payload = json.loads(payload)

            if "error" not in payload:
                mt5_data = payload
            else:
                mt5_error = payload.get("error", "unknown")
        else:
            mt5_error = result.get("error", "bridge_timeout")
    except Exception as e:
        mt5_error = str(e)

    # FALLBACK: Use hardcoded schedule-based events
    fallback_events = _get_upcoming_events(
        hours_ahead=req.hours_ahead,
        currency=req.currency,
        min_impact=req.min_impact,
    )
    fallback_blackouts = _get_blackout_windows(hours_ahead=req.hours_ahead)
    fallback_current = _is_blackout_now(currency=req.currency, minutes_ahead=30)

    if mt5_data and "events" in mt5_data:
        # MT5 calendar succeeded — use real terminal data
        events = mt5_data["events"]

        # Compute blackout windows from MT5 events
        from datetime import datetime as _dt, timedelta as _td

        blackouts = []
        current_blackout = False
        now = _dt.utcnow()

        for evt in events:
            impact = evt.get("importance", "LOW")
            if impact in ("HIGH",):
                # Parse timestamp and compute blackout window
                ts = evt.get("timestamp", 0)
                if ts > 0:
                    evt_time = _dt.utcfromtimestamp(ts)
                    # 60 min blackout for HIGH, 120 min for CRITICAL-level events
                    blackout_minutes = (
                        120
                        if "rate" in evt.get("name", "").lower()
                        or "decision" in evt.get("name", "").lower()
                        else 60
                    )
                    blackouts.append(
                        {
                            "event_name": evt.get("name"),
                            "start_utc": (
                                evt_time - _td(minutes=blackout_minutes)
                            ).isoformat(),
                            "end_utc": (
                                evt_time + _td(minutes=blackout_minutes)
                            ).isoformat(),
                            "event_time_utc": evt_time.isoformat(),
                            "currency": evt.get("currency"),
                            "impact": impact,
                            "blackout_minutes": blackout_minutes,
                            "source": "mt5_terminal",
                        }
                    )
                    if (
                        evt_time - _td(minutes=blackout_minutes)
                        <= now
                        <= evt_time + _td(minutes=blackout_minutes)
                    ):
                        current_blackout = True

        return {
            "events": events,
            "event_count": len(events),
            "source": "mt5_terminal_calendar",
            "blackout_windows": blackouts,
            "current_blackout": {
                "is_blackout": current_blackout,
                "events_causing_blackout": [
                    b
                    for b in blackouts
                    if _dt.fromisoformat(b["start_utc"])
                    <= now
                    <= _dt.fromisoformat(b["end_utc"])
                ],
                "source": "mt5_terminal",
            },
            "mt5_calendar_status": "connected",
        }
    else:
        # MT5 unavailable — fall back to schedule-based events
        return {
            "events": [e.to_dict() for e in fallback_events],
            "event_count": len(fallback_events),
            "source": "schedule_based_fallback",
            "blackout_windows": fallback_blackouts,
            "current_blackout": fallback_current,
            "daily_briefing": _get_daily_briefing(),
            "mt5_calendar_status": f"unavailable ({mt5_error}) — using schedule-based fallback",
            "warning": "MT5 Terminal calendar data unavailable. Events are estimated from recurring schedules, not real-time terminal data.",
        }


class ReconcileRequest(BaseModel):
    intent_ids: list[str] = []


def _get_reconcile_context() -> dict:
    adapter = getattr(get_gateway(), "adapter", None)
    positions: list[Position] = []
    deals: list[Deal] = []

    if adapter is not None:
        try:
            positions = adapter.get_positions() or []
        except Exception:
            pass
        if hasattr(adapter, "get_deals_history"):
            try:
                raw = adapter.get_deals_history(limit=500, symbol=None, days=7)
                deals = raw if isinstance(raw, list) else raw.get("deals", [])
            except Exception:
                pass

    if not positions:
        try:
            raw_positions = tool_get_positions().get("positions", [])
            positions = [Position(**p) for p in raw_positions]
        except Exception:
            pass

    if not deals:
        try:
            raw_deals = tool_get_deals_history(limit=500, days=7).get("deals", [])
            deals = [Deal(**d) for d in raw_deals]
        except Exception:
            pass

    return {"positions": positions, "deals": deals}


@app.post("/tools/reconcile", response_model=dict)
def tool_reconcile(req: ReconcileRequest) -> dict:
    settings = get_settings_cached()
    svc = ReconciliationService(settings)
    ctx = _get_reconcile_context()

    positions = ctx["positions"]
    deals = ctx["deals"]

    positions = [p if isinstance(p, Position) else Position(**p) for p in positions]
    deals = [d if isinstance(d, Deal) else Deal(**d) for d in deals]

    reconcile_result = svc.reconcile(req.intent_ids, positions)
    owned = svc.get_owned_positions(positions)
    foreign_pnl = svc.calculate_foreign_pnl(owned, deals)

    reconcile_result["foreign_pnl"] = foreign_pnl
    return reconcile_result


# ============================================================
# Server entry point — MUST be at the end so all routes register first
# ============================================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8010)
