from __future__ import annotations

import os
import json
import asyncio
import subprocess
import socket
from typing import Any
from pathlib import Path

import httpx
from mcp import types
from mcp.server import Server
import mcp.server.stdio


BASE_URL = os.environ.get("MCP_HTTP_URL", "http://127.0.0.1:8010")
GATEWAY_URL = os.environ.get("MT5_GATEWAY_URL", "http://127.0.0.1:8020")
IGS_URL = os.environ.get("IGS_MCP_URL", "http://127.0.0.1:8030")

# Persistent HTTP clients with Keep-Alive connection pooling
# Eliminates ~50ms TCP handshake per request by reusing connections
_http_client: httpx.AsyncClient | None = None
_igs_client: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    """Get or create persistent async HTTP client with Keep-Alive."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=45.0,
            http2=False,
            limits=httpx.Limits(
                max_keepalive_connections=10,
                max_connections=20,
                keepalive_expiry=30.0,
            ),
        )
    return _http_client


def _get_igs_client() -> httpx.AsyncClient:
    """Get or create persistent async HTTP client for IGS news service."""
    global _igs_client
    if _igs_client is None or _igs_client.is_closed:
        _igs_client = httpx.AsyncClient(
            timeout=45.0,
            http2=False,
            limits=httpx.Limits(
                max_keepalive_connections=10,
                max_connections=20,
                keepalive_expiry=30.0,
            ),
        )
    return _igs_client


def _is_port_in_use(port: int) -> bool:
    """Check if a port is already in use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _get_project_root() -> Path:
    """Get the project root directory."""
    return Path(__file__).parent.parent


def _start_server(name: str, port: int, script: str) -> subprocess.Popen | None:
    """Start a backend server if not already running."""
    if _is_port_in_use(port):
        return None

    project_root = _get_project_root()
    script_path = project_root / script

    if not script_path.exists():
        print(
            f"Warning: {script} not found at {script_path}",
            file=__import__("sys").stderr,
        )
        return None

    env = os.environ.copy()
    env["PYTHONPATH"] = str(project_root / "src")

    try:
        proc = subprocess.Popen(
            ["python", str(script_path)],
            cwd=str(project_root),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        print(f"Started {name} (PID: {proc.pid})", file=__import__("sys").stderr)
        return proc
    except Exception as e:
        print(f"Failed to start {name}: {e}", file=__import__("sys").stderr)
        return None


async def ensure_servers_running_async() -> None:
    """Ensure backend servers are running (non-blocking async version)."""
    started = []

    # Start Gateway first (port 8020)
    if not _is_port_in_use(8020):
        proc = _start_server("Bridge Gateway", 8020, "apps/bridge_gateway/main.py")
        if proc:
            started.append(("Gateway", 8020))
            await asyncio.sleep(0.5)  # Non-blocking wait

    # Start MCP Server (port 8010)
    if not _is_port_in_use(8010):
        proc = _start_server("MCP Server", 8010, "apps/mcp_server/main.py")
        if proc:
            started.append(("MCP Server", 8010))
            await asyncio.sleep(0.5)  # Non-blocking wait

    # Verify servers are responding (async with timeout)
    for name, port in started:
        for _ in range(10):  # Max 5 second wait
            if _is_port_in_use(port):
                break
            await asyncio.sleep(0.5)
        if not _is_port_in_use(port):
            print(
                f"Warning: {name} on port {port} not responding",
                file=__import__("sys").stderr,
            )

    if started:
        print(
            f"Backend servers started: {', '.join(f'{n} ({p})' for n, p in started)}",
            file=__import__("sys").stderr,
        )
    else:
        print("Backend servers already running", file=__import__("sys").stderr)


def ensure_servers_running() -> None:
    """Ensure backend servers are running (sync wrapper for backwards compat)."""
    # Run async version in a new event loop if needed
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # In async context, should use ensure_servers_running_async instead
            return
        loop.run_until_complete(ensure_servers_running_async())
    except RuntimeError:
        # No event loop, create one
        asyncio.run(ensure_servers_running_async())


# Ownership fields for all write-path tool schemas (optional, not in required)
_OWNERSHIP_PROPERTIES: dict[str, Any] = {
    "session_id": {"type": "string"},
    "strategy_id": {"type": "string"},
    "intent_id": {"type": "string"},
    "idempotency_key": {"type": "string"},
    "magic_number": {"type": ["number", "string"]},
}

_NUMERIC_FIELDS: dict[str, set[str]] = {
    "get_bars": {"count"},
    "deals_history": {"limit", "days"},
    "performance_summary": {"limit", "days"},
    "get_indicator": {
        "period",
        "fast",
        "slow",
        "signal",
        "deviation",
        "shift",
        "k_period",
        "d_period",
        "slowing",
        "tenkan",
        "kijun",
        "senkou",
        "window",
    },
    "get_ticks": {"count"},
    "get_chart_screenshot": {"width", "height"},
    "submit_market_order_via_bridge": {"volume_lots", "deviation_points", "sl", "tp"},
    "submit_market_order": {"volume_lots", "deviation_points", "sl", "tp"},
    "submit_pending_order": {"price", "volume_lots", "sl", "tp", "deviation"},
    "modify_order": {"new_price", "new_sl", "new_tp"},
    "modify_position_sl_tp": {"sl", "tp"},
    "close_position": {"volume"},
    "calculate_position_size": {
        "entry_price",
        "stop_loss_price",
        "risk_percent",
        "equity",
    },
    "validate_trade_setup": {"volume_lots", "entry_price", "sl", "tp"},
    "trail_position": {"distance_points", "lock_in_points"},
    "volatility_profile": {"lookback", "atr_period"},
    "correlation_matrix": {"lookback"},
    "multi_timeframe_indicators": {
        "period",
        "fast",
        "slow",
        "signal",
        "deviation",
        "shift",
        "k_period",
        "d_period",
        "slowing",
        "tenkan",
        "kijun",
        "senkou",
        "window",
    },
    # New: Trading context & coaching
    "trading/context": {"include_comparison"},
    "trading/coach": {
        "atr_value",
        "rsi",
        "ema_fast",
        "ema_slow",
        "sl_distance_points",
        "tp_distance_points",
        "indicator_agreements",
        "trades_today",
        "daily_pnl",
        "recent_consecutive_losses",
        "position_in_range",
    },
    "trading/decision_support": {"sl_distance_points", "tp_distance_points"},
    "trading/log_decision": {
        "entry_price",
        "exit_price",
        "sl",
        "tp",
        "volume_lots",
        "pnl",
        "atr_value",
        "atr_percent_of_price",
        "rsi_value",
        "confidence_level",
        "expected_move_points",
        "quality_rating",
    },
    "trading/reflect": {"limit"},
    "trading/insights": {"lookback_days"},
    "trading/agent_prompt": {
        "include_market_context",
        "include_news_context",
        "include_workflow",
        "include_trading_rules",
        "include_tool_guide",
        "include_metacognition",
        "live_account_context",
    },
    # New: Market analysis
    "market/regime": {"lookback", "atr_period"},
    "market/scan": {"atr_period"},
    # New: Bracket orders
    "place_bracket_order": {
        "buy_trigger",
        "sell_trigger",
        "volume_lots",
        "sl_atr_multiplier",
        "tp_atr_multiplier",
    },
    # New: Trailing stops
    "set_trailing_stop": {
        "distance_atr_multiplier",
        "check_interval_seconds",
        "lock_in_profit_after_atr",
    },
    "trailing_stop/cancel": set(),
    "trailing_stop/tick": set(),
    "trailing_stop/list": set(),
    # New: Long-polling resources
    "resources/market/wait_for_price": {"price", "timeout_seconds"},
    "resources/positions/monitor": {
        "timeout_seconds",
        "alert_at_pnl",
        "alert_at_price",
    },
    # Agent wait/timer tools
    "tools/wait/delay": {"duration_seconds"},
    "tools/wait/indicator": {
        "value",
        "period",
        "fast",
        "slow",
        "signal",
        "timeout_seconds",
        "check_interval_seconds",
    },
    # Economic calendar
    "economic_calendar": {"hours_ahead"},
}


def _coerce_numeric_args(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Coerce string values that look like numbers to actual numbers."""
    fields = _NUMERIC_FIELDS.get(tool_name, set())
    result = dict(args)
    for key in fields:
        val = result.get(key)
        if isinstance(val, str):
            try:
                result[key] = float(val) if "." in val else int(val)
            except (ValueError, TypeError):
                pass
    return result


async def _post_json(path: str, body: dict[str, Any]) -> dict[str, Any]:
    """POST to MCP server using persistent HTTP client (Keep-Alive)."""
    url = f"{BASE_URL}{path}"
    client = _get_http_client()
    r = await client.post(url, json=body)
    r.raise_for_status()
    return r.json()


async def _post_igs(endpoint: str, body: dict[str, Any]) -> dict[str, Any]:
    """Post to IGS-MCP news service using persistent HTTP client."""
    url = f"{IGS_URL}/{endpoint}"
    client = _get_igs_client()
    r = await client.post(url, json=body)
    r.raise_for_status()
    return r.json()


async def _get_json(path: str) -> Any:
    """GET from MCP server using persistent HTTP client (Keep-Alive)."""
    url = f"{BASE_URL}{path}"
    client = _get_http_client()
    r = await client.get(url)
    r.raise_for_status()
    return r.json()


TOOL_SPECS: dict[str, dict[str, Any]] = {
    # === DATA TOOLS ===
    "get_bars": {
        "description": (
            "What: Fetches OHLCV candle data for a symbol and timeframe via the MT5 EA bridge.\n"
            "\n"
            "Input:\n"
            '  - symbol: String. MT5 symbol name (e.g. "XAUUSD", "EURUSD", "US30"). Normalized internally.\n'
            '  - timeframe: String. Valid values: "M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN".\n'
            "  - count: Integer. Number of candles to return. Default: 100. Maximum: 5000.\n"
            "\n"
            'Output: {symbol, timeframe, data: [{timestamp, open, high, low, close, volume, spread}], source: "tcp"|"bridge"}\n'
            "  - Timestamps are broker server time (not UTC). Convert using symbol_info() for timezone offset.\n"
            "  - Returns {data: []} if symbol is invalid or bridge is disconnected (no error raised).\n"
            "  - Spread field is in points, not absolute price.\n"
            "\n"
            "Assumptions:\n"
            "  - Data includes only regular trading sessions (no gaps for holidays; MT5 fills with last-known values).\n"
            "  - Close price is unadjusted (no split/dividend adjustment \u2014 forex/crypto don't have these).\n"
            "  - Volume is tick volume (number of price changes), not real traded volume (MT5 limitation).\n"
            "  - Bridge latency: ~15-25ms via TCP, ~200-600ms via HTTP fallback.\n"
            "\n"
            "Composition: Primary input for get_indicator(), volatility_profile(), market/regime(), support_resistance()."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "timeframe": {"type": "string"},
                "count": {"type": ["number", "string"]},
            },
            "required": ["symbol", "timeframe"],
        },
    },
    "get_indicator": {
        "description": (
            "What: Computes a technical indicator from MT5's built-in calculation engine.\n"
            "\n"
            "Input:\n"
            "  - symbol: String. MT5 symbol name.\n"
            '  - timeframe: String. Valid: "M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN".\n'
            '  - indicator: String. Valid: "sma", "ema", "wma", "smma", "rsi", "macd", "bbands", "stoch", "atr", "adx", "dmi", "ichimoku", "obv", "cci".\n'
            "  - period: Integer | null. Lookback window. Default varies by indicator (see below).\n"
            "  - fast, slow, signal: Integer | null. MACD-specific parameters. Defaults: 12, 26, 9.\n"
            "  - deviation: Float | null. Bollinger Bands standard deviation multiplier. Default: 2.\n"
            "  - k_period, d_period, slowing: Integer | null. Stochastic parameters. Defaults: 5, 3, 3.\n"
            "  - tenkan, kijun, senkou: Integer | null. Ichimoku parameters. Defaults: 9, 26, 52.\n"
            "  - shift: Integer | null. Shift the indicator line N bars into the past.\n"
            "  - window: Integer | null. Number of indicator values to return (from most recent). Omit for single latest value.\n"
            "\n"
            "Default indicator defaults applied server-side:\n"
            "  - sma/ema: period=20\n"
            "  - rsi: period=14\n"
            "  - macd: fast=12, slow=26, signal=9\n"
            "  - bbands: period=20, deviation=2\n"
            "  - atr: period=14\n"
            "  - adx/dmi: period=14\n"
            "  - stoch: k_period=5, d_period=3, slowing=3\n"
            "  - ichimoku: tenkan=9, kijun=26, senkou=52\n"
            "  - cci: period=14\n"
            "\n"
            "Output: Shape varies by indicator:\n"
            "  - Single value (no window): {symbol, timeframe, indicator, value: float, data: [float], period: int}\n"
            "  - Series (with window): {symbol, timeframe, indicator, data: [float], period: int} where data[0] is oldest, data[-1] is most recent.\n"
            "  - MACD: {main: [float], signal: [float], histogram: [float]}\n"
            "  - Bollinger Bands: {upper: [float], middle: [float], lower: [float]}\n"
            "  - Stochastic: {k: [float], d: [float]}\n"
            "  - ADX: {adx: [float], plus_di: [float], minus_di: [float]}\n"
            "  - Ichimoku: {tenkan: [float], kijun: [float], senkou_a: [float], senkou_b: [float], chikou: [float]}\n"
            "\n"
            "Assumptions:\n"
            "  - Computed server-side by MT5 terminal (not Python). Uses MT5's native algorithms.\n"
            "  - When window is provided, the first N values for any indicator may be unreliable (warmup period). RSI needs period+1 bars, MACD needs ~100 bars for convergence.\n"
            "  - Returns {value: 0, data: []} if symbol is invalid or timeframe is unsupported (no error).\n"
            "  - data array is sorted oldest\u2192newest.\n"
            "  - Returns empty result for invalid indicator names.\n"
            "\n"
            "Composition: Takes output from get_bars() conceptually (but computes independently). Input for market/regime(), volatility_profile(), trading/coach(), build_chart() overlays."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "timeframe": {"type": "string"},
                "indicator": {"type": "string"},
                "period": {"type": ["number", "string", "null"]},
                "fast": {"type": ["number", "string", "null"]},
                "slow": {"type": ["number", "string", "null"]},
                "signal": {"type": ["number", "string", "null"]},
                "deviation": {"type": ["number", "string", "null"]},
                "shift": {"type": ["number", "string", "null"]},
                "k_period": {"type": ["number", "string", "null"]},
                "d_period": {"type": ["number", "string", "null"]},
                "slowing": {"type": ["number", "string", "null"]},
                "tenkan": {"type": ["number", "string", "null"]},
                "kijun": {"type": ["number", "string", "null"]},
                "senkou": {"type": ["number", "string", "null"]},
                "window": {"type": ["number", "string", "null"]},
            },
            "required": ["symbol", "timeframe", "indicator"],
        },
    },
    "get_ticks": {
        "description": (
            "What: Fetches recent tick data (bid/ask/last price updates) for a symbol.\n"
            "\n"
            "Input:\n"
            "  - symbol: String. MT5 symbol name.\n"
            "  - count: Integer. Number of ticks to return. Default: 200. Maximum: 2000.\n"
            "\n"
            'Output: {symbol, ticks: [{time, bid, ask, last, volume, flags}], source: "tcp"|"bridge"}\n'
            "  - Time is broker server time (Unix timestamp).\n"
            "  - Bid/ask/last are raw price values. Spread = ask - bid.\n"
            "  - Volume is tick volume for that individual tick.\n"
            "\n"
            "Assumptions:\n"
            "  - Only available during market hours. Returns empty ticks array when market is closed.\n"
            "  - Maximum lookback is limited by MT5 terminal's internal tick buffer (~few thousand ticks).\n"
            "  - Returns empty array for invalid symbols (no error).\n"
            "\n"
            "Composition: Use for precise entry timing. Complements get_bars() for sub-candle analysis."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "count": {"type": ["number", "string"]},
            },
            "required": ["symbol"],
        },
    },
    "symbol_info": {
        "description": (
            "What: Fetches symbol metadata: contract specifications, trading constraints, and pricing parameters.\n"
            "\n"
            "Input:\n"
            "  - symbol: String. MT5 symbol name.\n"
            "\n"
            "Output: {symbol, point, tick_size, tick_value, volume_min, volume_max, volume_step, stopsLevel, spread, margin_rate, trade_mode, ...}\n"
            "  - point: Smallest price increment (e.g. 0.01 for XAUUSD, 0.00001 for EURUSD).\n"
            "  - tick_size/tick_value: Used for position sizing calculations.\n"
            "  - volume_min/volume_max/volume_step: Lot size constraints.\n"
            "  - stopsLevel: Minimum distance (in points) for SL/TP from current price.\n"
            "  - spread: Current spread in points.\n"
            "\n"
            "Assumptions:\n"
            '  - Returns {symbol: "<input>", error: "..."} if symbol not found (check for error key).\n'
            "  - Values may change during market open/close transitions.\n"
            "  - Margin rate is broker-specific and may differ from theoretical.\n"
            "\n"
            "Composition: Required input for calculate_position_size(). Use before validate_trade_setup()."
        ),
        "schema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    },
    "deals_history": {
        "description": (
            "What: Fetches executed deal (fill) history from the MT5 terminal.\n"
            "\n"
            "Input:\n"
            "  - symbol: String | null. Filter by symbol. Omit for all symbols.\n"
            "  - limit: Integer. Maximum deals to return. Default: 100.\n"
            "  - days: Integer. Lookback window in days. Default: 30.\n"
            "\n"
            "Output: {deals: [{deal_id, order_id, symbol, side, volume, price, commission, swap, profit, time, type, entry}], total: int}\n"
            "  - profit is in account currency.\n"
            '  - type: "deal_type_buy", "deal_type_sell", "deal_type_balance", etc.\n'
            '  - entry: "deal_entry_in", "deal_entry_out", "deal_entry_inout".\n'
            "\n"
            "Assumptions:\n"
            "  - Only includes closed deals (fills), not pending orders.\n"
            "  - Returns empty deals array if no matching deals (no error).\n"
            "  - History depth limited by MT5 terminal settings (typically 1-3 months).\n"
            "\n"
            "Composition: Input for performance_summary(). Use with trading/reflect() to correlate decisions with outcomes."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": ["string", "null"]},
                "limit": {"type": ["number", "string"]},
                "days": {"type": ["number", "string"]},
            },
            "required": [],
        },
    },
    "performance_summary": {
        "description": (
            "What: Computes realized trading performance metrics from deal history.\n"
            "\n"
            "Input:\n"
            "  - symbol: String | null. Filter by symbol. Omit for all.\n"
            "  - limit: Integer. Max deals to analyze. Default: 100.\n"
            "  - days: Integer. Lookback in days. Default: 30.\n"
            "\n"
            "Output: {total_trades, winning_trades, losing_trades, win_rate, total_profit, avg_win, avg_loss, profit_factor, max_drawdown, avg_holding_time, ...}\n"
            "  - All monetary values in account currency.\n"
            "  - win_rate: winning_trades / total_trades (0.0-1.0).\n"
            "  - profit_factor: gross_profit / gross_loss.\n"
            "\n"
            "Assumptions:\n"
            "  - Only includes closed deals (realized P&L).\n"
            "  - Returns zero-valued metrics if no deals found (no error).\n"
            "  - Max drawdown is peak-to-trough from the deal series, not account-wide.\n"
            "\n"
            "Composition: Input for trading/insights(). Use with trading/reflect() for performance review."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": ["string", "null"]},
                "limit": {"type": ["number", "string"]},
                "days": {"type": ["number", "string"]},
            },
            "required": [],
        },
    },
    "get_order_book": {
        "description": (
            "What: Fetches the current order book (Depth of Market / DOM) snapshot for a symbol.\n"
            "\n"
            "Input:\n"
            "  - symbol: String. MT5 symbol name.\n"
            "\n"
            "Output: {symbol, bids: [{price, volume}], asks: [{price, volume}], timestamp: int}\n"
            "  - bids sorted highest\u2192lowest price. asks sorted lowest\u2192highest.\n"
            "  - Volume is in lots.\n"
            "  - Timestamp is Unix epoch.\n"
            "\n"
            "Assumptions:\n"
            "  - Only available for symbols with exchange-traded order book data (not all forex pairs support DOM).\n"
            "  - Returns {bids: [], asks: []} for symbols without order book data (no error).\n"
            "  - Snapshot is stale by the time received; MT5 does not stream real-time DOM.\n"
            "\n"
            "Composition: Use with validate_trade_setup() for liquidity assessment. Complements get_ticks() for bid/ask analysis."
        ),
        "schema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    },
    "account_summary": {
        "description": (
            "What: Fetches current trading account state.\n"
            "\n"
            "Input: None.\n"
            "\n"
            "Output: {account_id, name, balance, equity, margin, free_margin, currency, leverage, margin_level, ...}\n"
            "  - balance: Realized P&L (closed positions only).\n"
            "  - equity: balance + unrealized P&L (open positions).\n"
            "  - margin: Used margin for open positions.\n"
            "  - free_margin: equity - margin (available for new trades).\n"
            "  - margin_level: (equity / margin) \u00d7 100. Below 100% = no new positions. Below margin_call_level = liquidation risk.\n"
            "\n"
            "Assumptions:\n"
            "  - Returns partial data with null fields if bridge is disconnected.\n"
            "  - Values reflect real-time account state at call time.\n"
            "  - Currency is the account's base currency (not symbol-specific).\n"
            "\n"
            "Composition: Required input for calculate_position_size() (equity field). Use before any execution tool."
        ),
        "schema": {"type": "object"},
    },
    "positions_open": {
        "description": (
            "What: Lists all currently open positions.\n"
            "\n"
            "Input: None.\n"
            "\n"
            "Output: {positions: [{position_id, symbol, side, volume, entry_price, sl, tp, mark_price, profit, swap, commission, time, magic, comment}], count: int}\n"
            "  - mark_price: Current market price for the position's symbol.\n"
            "  - profit: Unrealized P&L in account currency.\n"
            "  - sl/tp: May be 0.0 if no stop is set.\n"
            "\n"
            "Assumptions:\n"
            "  - Returns empty positions array if no open positions (no error).\n"
            "  - Positions are from the connected account only.\n"
            "  - magic field identifies the strategy/EA that opened the position.\n"
            "\n"
            "Composition: Use before execution to check exposure. Input for modify_position_sl_tp(), close_position(), trailing stop tools."
        ),
        "schema": {"type": "object"},
    },
    "orders_pending": {
        "description": (
            "What: Lists all pending (unfilled) orders.\n"
            "\n"
            "Input: None.\n"
            "\n"
            "Output: {orders: [{order_id, symbol, side, kind, price, volume, sl, tp, time, expiration, status}], count: int}\n"
            '  - kind: "buy_limit", "sell_limit", "buy_stop", "sell_stop".\n'
            '  - status: "pending", "partially_filled", etc.\n'
            "\n"
            "Assumptions:\n"
            "  - Returns empty orders array if no pending orders (no error).\n"
            "  - Does NOT include open positions \u2014 use positions_open for those.\n"
            "  - Expired or cancelled orders are not included.\n"
            "\n"
            "Composition: Use before submit_pending_order() to avoid duplicates. Input for cancel_order(), modify_order()."
        ),
        "schema": {"type": "object"},
    },
    "bridge_status": {
        "description": (
            "What: Fetches the MT5 EA bridge heartbeat status.\n"
            "\n"
            "Input: None.\n"
            "\n"
            "Output: {connected: bool, login: int, server: string, trade_allowed: bool, last_heartbeat: int, ...}\n"
            "  - last_heartbeat: Unix timestamp of last EA heartbeat. Age > 30s indicates disconnection risk.\n"
            "  - trade_allowed: false during market close, weekend, or terminal error.\n"
            "\n"
            "Assumptions:\n"
            "  - Returns {connected: false} if gateway is unreachable (no error raised).\n"
            "  - Does not verify actual trading capability \u2014 only EA connectivity.\n"
            "\n"
            "Composition: Call first in any automated trading cycle to verify infrastructure health."
        ),
        "schema": {"type": "object"},
    },
    # === COMPUTATION TOOLS ===
    "volatility_profile": {
        "description": (
            "What: Computes a volatility summary for a symbol, combining ATR and bar-range analysis.\n"
            "\n"
            "Input:\n"
            "  - symbol: String. MT5 symbol name.\n"
            "  - timeframe: String. Valid timeframe string.\n"
            "  - lookback: Integer. Number of bars to analyze. Default: 20. Max: 500.\n"
            "  - atr_period: Integer. ATR calculation period. Default: 14. Valid: 2-500.\n"
            "\n"
            'Output: {symbol, timeframe, atr: {value, pct_of_price, raw}, avg_bar_range, max_bar_range, min_bar_range, spread_analysis: {avg_spread_points, max_spread_points}, volatility_regime: "low"|"normal"|"high"|"extreme"}\n'
            "  - atr.pct_of_price: ATR as percentage of current price (for cross-symbol comparison).\n"
            "  - volatility_regime is computed relative to the symbol's own historical ATR distribution.\n"
            "\n"
            "Assumptions:\n"
            "  - Requires get_bars() and get_indicator(atr) internally. Returns partial data if either fails.\n"
            "  - Spread analysis uses current spread, not historical spread distribution.\n"
            "  - Returns zero-valued output if symbol has no price data.\n"
            "\n"
            "Composition: Takes symbol+timeframe from get_bars(). Input for trading/context(), calculate_position_size() (indirectly via ATR)."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "timeframe": {"type": "string"},
                "lookback": {"type": ["number", "string"]},
                "atr_period": {"type": ["number", "string"]},
            },
            "required": ["symbol", "timeframe"],
        },
    },
    "multi_timeframe_indicators": {
        "description": (
            "What: Computes a single indicator across multiple timeframes in one call.\n"
            "\n"
            "Input:\n"
            "  - symbol: String. MT5 symbol name.\n"
            "  - indicator: String. Same valid values as get_indicator().\n"
            '  - timeframes: Array[String]. List of timeframes (e.g. ["M15", "H1", "H4", "D1"]). Maximum: 8.\n'
            "  - period, fast, slow, signal, deviation, etc.: Same as get_indicator(). Applied uniformly across all timeframes.\n"
            "\n"
            "Output: {symbol, indicator, readings: {timeframe: {value, data, ...}}}\n"
            "  - Each timeframe's value has the same structure as get_indicator()'s single-value output.\n"
            '  - If a timeframe fails, that key contains {error: "..."}.\n'
            "\n"
            "Assumptions:\n"
            "  - Each timeframe is computed independently (sequential calls under the hood).\n"
            "  - Total latency \u2248 N \u00d7 single-timeframe latency (not parallelized).\n"
            "  - All timeframes use the same indicator parameters.\n"
            "\n"
            "Composition: Alternative to calling get_indicator() N times. Input for confluence analysis across timeframes."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "indicator": {"type": "string"},
                "timeframes": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "period": {"type": ["number", "string", "null"]},
                "fast": {"type": ["number", "string", "null"]},
                "slow": {"type": ["number", "string", "null"]},
                "signal": {"type": ["number", "string", "null"]},
                "deviation": {"type": ["number", "string", "null"]},
                "shift": {"type": ["number", "string", "null"]},
                "k_period": {"type": ["number", "string", "null"]},
                "d_period": {"type": ["number", "string", "null"]},
                "slowing": {"type": ["number", "string", "null"]},
                "tenkan": {"type": ["number", "string", "null"]},
                "kijun": {"type": ["number", "string", "null"]},
                "senkou": {"type": ["number", "string", "null"]},
                "window": {"type": ["number", "string", "null"]},
            },
            "required": ["symbol", "indicator", "timeframes"],
        },
    },
    "correlation_matrix": {
        "description": (
            "What: Computes the Pearson correlation matrix of close-price returns across multiple symbols.\n"
            "\n"
            "Input:\n"
            "  - symbols: Array[String]. List of MT5 symbol names. Minimum: 2. Maximum: 10.\n"
            '  - timeframe: String. Timeframe for price data. Default: "H1".\n'
            "  - lookback: Integer. Number of bars for correlation. Default: 50. Min: 10.\n"
            "\n"
            "Output: {timeframe, lookback, matrix: {symbol_a: {symbol_b: correlation, ...}, ...}}\n"
            "  - Correlation values range -1.0 to 1.0.\n"
            "  - Diagonal (self-correlation) is always 1.0.\n"
            "\n"
            "Assumptions:\n"
            "  - Uses percentage returns, not raw prices, for stationarity.\n"
            "  - If symbols have different trading hours, correlation may be artificially low.\n"
            "  - Requires sufficient overlapping bars; returns reduced matrix if symbols have mismatched data.\n"
            "\n"
            "Composition: Use before opening multiple positions to detect correlated risk exposure. Takes data from get_bars() for each symbol."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "symbols": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "timeframe": {"type": "string"},
                "lookback": {"type": ["number", "string"]},
            },
            "required": ["symbols", "timeframe"],
        },
    },
    "support_resistance": {
        "description": (
            "What: Detects support and resistance levels from recent price action using swing high/low clustering.\n"
            "\n"
            "Input:\n"
            "  - symbol: String. MT5 symbol name.\n"
            '  - timeframe: String. Valid timeframe. Default: "H1".\n'
            "  - lookback: Integer. Bars to scan. Default: 100. Min: 20.\n"
            "\n"
            'Output: {symbol, support_levels: [float], resistance_levels: [float], method: "swing_highs_lows", timeframe, lookback}\n'
            "  - Levels are sorted by proximity to current price (closest first).\n"
            "  - Empty arrays if insufficient data or no swings detected.\n"
            "\n"
            "Assumptions:\n"
            "  - Uses a swing window of max(2, min(5, lookback/10)) bars for swing detection.\n"
            "  - Levels are price levels, not zones (no width/band provided).\n"
            "  - Does NOT use volume profile, pivot points, or Fibonacci \u2014 only price-based swing detection.\n"
            "\n"
            "Composition: Takes output from get_bars(). Input for SL/TP placement, breakout trigger levels, bracket order triggers."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "timeframe": {"type": "string"},
                "lookback": {"type": ["number", "string"]},
            },
            "required": ["symbol"],
        },
    },
    "market/regime": {
        "description": (
            "What: Classifies the current market state into one of four regimes based on ADX and EMA alignment.\n"
            "\n"
            "Input:\n"
            "  - symbol: String. MT5 symbol name.\n"
            "  - timeframe: String. Valid timeframe string.\n"
            "  - lookback: Integer. Bars to analyze. Default: 20.\n"
            "  - atr_period: Integer. ATR period for volatility context. Default: 14.\n"
            "\n"
            'Output: {symbol, timeframe, regime: "ranging"|"trending_up"|"trending_down"|"compressing", confidence: float, adx: float, ema_fast: float, ema_slow: float, atr: float}\n'
            "  - confidence: 0.0-1.0, based on ADX strength and EMA separation.\n"
            "  - compressing: Low volatility squeeze (narrowing Bollinger Bands + low ATR).\n"
            "\n"
            "Assumptions:\n"
            "  - Classification uses ADX threshold and EMA(20)/EMA(50) crossover logic internally.\n"
            "  - Regime is valid only for the specified timeframe; a symbol can be trending on H4 and ranging on M15.\n"
            '  - Returns {regime: "ranging", confidence: 0.0} if data is insufficient.\n'
            "\n"
            "Composition: Takes output from get_bars() + get_indicator(adx). Input for trading/coach(), trading/decision_support(), strategy selection."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "timeframe": {"type": "string"},
                "lookback": {"type": ["number", "string"]},
                "atr_period": {"type": ["number", "string"]},
            },
            "required": ["symbol", "timeframe"],
        },
    },
    "market/scan": {
        "description": (
            "What: Performs a multi-symbol market scan, returning price, ATR, and regime for each symbol.\n"
            "\n"
            "Input:\n"
            "  - symbols: Array[String]. List of MT5 symbol names. Maximum: 20.\n"
            '  - timeframe: String. Timeframe for analysis. Default: "H1".\n'
            "  - atr_period: Integer. ATR period. Default: 14.\n"
            "\n"
            "Output: {symbols: [{symbol, price, atr, atr_pct, regime, recommendation}], timeframe}\n"
            "  - recommendation: Generated suggestion based on regime + ATR context (not a trade signal).\n"
            "  - Each symbol's data is computed independently.\n"
            "\n"
            "Assumptions:\n"
            "  - Sequential processing: latency scales linearly with symbol count.\n"
            '  - If a symbol is invalid, its entry contains {symbol, error: "..."}.\n'
            "  - Recommendation is informational, not prescriptive.\n"
            "\n"
            "Composition: Batch alternative to calling get_bars() + market/regime() per symbol. Use for watchlist screening."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "symbols": {"type": "array", "items": {"type": "string"}},
                "timeframe": {"type": "string"},
                "atr_period": {"type": ["number", "string"]},
            },
            "required": ["symbols"],
        },
    },
    # === VISUALIZATION TOOLS ===
    "get_chart_screenshot": {
        "description": (
            "What: Captures a screenshot of the MT5 chart for a specific symbol and timeframe.\n"
            "\n"
            "Input:\n"
            "  - symbol: String. MT5 symbol name.\n"
            "  - timeframe: String. Valid timeframe. Determines which chart is captured.\n"
            "  - width: Integer. Image width in pixels. Default: 1280. Range: 640-3840.\n"
            "  - height: Integer. Image height in pixels. Default: 720. Range: 480-2160.\n"
            "\n"
            'Output: {image_base64: string, content_type: "image/png"}\n'
            "  - image_base64 is the full PNG encoded as base64. Decode before use.\n"
            "  - Typical file size: 200KB-2MB depending on resolution.\n"
            "\n"
            "Assumptions:\n"
            "  - Screenshot reflects the chart as configured in the MT5 terminal (indicators, templates applied by the EA).\n"
            "  - If MT5 terminal is minimized or headless, the image may be blank or low-quality.\n"
            '  - Returns {image_base64: ""} if chart capture fails (no error raised).\n'
            "  - Latency: 1-5 seconds (screenshot capture and encoding).\n"
            "\n"
            "Composition: Takes symbol+timeframe context from get_bars(). Feeds into visual analysis pipelines or Telegram reports."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "timeframe": {"type": "string"},
                "width": {"type": ["number", "string"]},
                "height": {"type": ["number", "string"]},
            },
            "required": ["symbol", "timeframe"],
        },
    },
    # === RISK TOOLS ===
    "calculate_position_size": {
        "description": (
            "What: Calculates optimal position size (in lots) using fixed-fractional risk model.\n"
            "\n"
            "Input:\n"
            "  - symbol: String. MT5 symbol name.\n"
            "  - entry_price: Float. Planned entry price.\n"
            "  - stop_loss_price: Float. Planned stop-loss price.\n"
            "  - risk_percent: Float. Percentage of account equity to risk. Range: 0.01-10.0 (0.01% to 10%).\n"
            "  - equity: Float | null. Account equity. If null, fetches from account_summary() automatically.\n"
            "\n"
            "Output: {symbol, lot_size: float, dollar_risk: float, risk_reward_ratio: float, pip_value: float, margin_required: float, warnings: [string]}\n"
            "  - lot_size: Rounded to symbol's volume_step (e.g. 0.01 for standard lots).\n"
            "  - dollar_risk: Absolute USD risk amount (entry - SL) \u00d7 lot_size \u00d7 tick_value.\n"
            "  - Warnings include: lot exceeds max_volume, SL too close to stopsLevel, risk > 5% of equity.\n"
            "\n"
            "Formula: Fixed fractional \u2014 lot_size = (equity \u00d7 risk_percent / 100) / |entry - SL| / tick_value, then rounded to volume_step.\n"
            "\n"
            "Assumptions:\n"
            "  - Does NOT account for slippage, commissions, swap, or gap risk.\n"
            "  - Single position only \u2014 no portfolio-level correlation adjustments.\n"
            "  - If entry_price == stop_loss_price, returns lot_size: 0 with error warning.\n"
            "  - Uses live symbol_info() for tick_value, volume_step, volume_max constraints.\n"
            "\n"
            "Composition: Takes equity from account_summary(). Uses symbol_info() for contract specs. Input for submit_market_order_via_bridge() volume_lots parameter."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "entry_price": {"type": ["number", "string"]},
                "stop_loss_price": {"type": ["number", "string"]},
                "risk_percent": {"type": ["number", "string"]},
                "equity": {"type": ["number", "string", "null"]},
            },
            "required": ["symbol", "entry_price", "stop_loss_price", "risk_percent"],
        },
    },
    "validate_trade_setup": {
        "description": (
            "What: Validates a proposed trade against broker constraints and market conditions.\n"
            "\n"
            "Input:\n"
            "  - symbol: String. MT5 symbol name.\n"
            '  - side: String. "buy" or "sell".\n'
            '  - order_kind: String. "market", "limit", or "stop".\n'
            "  - volume_lots: Float. Planned position size.\n"
            "  - entry_price: Float | null. For limit/stop orders. Ignored for market orders (uses current market price).\n"
            "  - sl: Float | null. Planned stop-loss price.\n"
            "  - tp: Float | null. Planned take-profit price.\n"
            "\n"
            "Output: {symbol, bid: float, ask: float, valid: bool, errors: [string], warnings: [string], required_margin: float}\n"
            "  - valid: true only if errors array is empty.\n"
            "  - errors: Hard violations (e.g. volume below minimum, SL within stopsLevel, insufficient margin).\n"
            "  - warnings: Soft advisories (e.g. SL/TP ratio unusual, high spread, market close approaching).\n"
            "  - required_margin: Estimated margin for this trade.\n"
            "\n"
            "Assumptions:\n"
            "  - Checks against LIVE broker constraints (stopsLevel, min_volume, max_volume, margin requirements).\n"
            "  - Market price fetched from order book at call time \u2014 may differ from execution price.\n"
            "  - Does NOT validate strategy logic (only mechanical constraints).\n"
            "\n"
            "Composition: Takes order book data via get_order_book() internally. Call before any submit_*_order tool."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "side": {"type": "string"},
                "order_kind": {"type": "string"},
                "volume_lots": {"type": ["number", "string"]},
                "entry_price": {"type": ["number", "string", "null"]},
                "sl": {"type": ["number", "string", "null"]},
                "tp": {"type": ["number", "string", "null"]},
            },
            "required": ["symbol", "side", "order_kind", "volume_lots"],
        },
    },
    # === EXECUTION TOOLS ===
    "submit_market_order_via_bridge": {
        "description": (
            "What: Submits a market order through the MT5 EA bridge.\n"
            "\n"
            "Input:\n"
            "  - intent_id: String. Unique identifier for tracking this order (e.g. UUID).\n"
            "  - strategy_id: String. Strategy name for audit trail.\n"
            '  - account_id: String. Target account identifier (e.g. "demo").\n'
            "  - symbol: String. MT5 symbol name.\n"
            '  - side: String. "buy" or "sell".\n'
            '  - order_kind: String. Must be "market".\n'
            "  - volume_lots: Float. Position size in lots. Must be within symbol's volume_min to volume_max, aligned to volume_step.\n"
            "  - sl: Float | null. Stop-loss price. Recommended but not required.\n"
            "  - tp: Float | null. Take-profit price.\n"
            "  - deviation_points: Integer. Maximum acceptable slippage in points. Default: 20.\n"
            "\n"
            'Output: {intent_id, status: "submitted"|"error", adapter: string, broker_order_id: string, retcode: string, message: string, raw: object}\n'
            '  - status: "submitted" on success, "error" on failure.\n'
            '  - retcode: MT5 trade return code label (e.g. "DONE", "REJECTED", "INVALID_STOPS", "NO_MONEY").\n'
            "  - On error, broker_order_id may be empty. Check raw for MT5 retcode details.\n"
            "\n"
            "Assumptions:\n"
            "  - Gated by TradingPolicy engine: may be rejected if daily trade limit, loss limit, or other policy rules are triggered.\n"
            "  - Order is sent via TCP bridge (preferred) or HTTP fallback. Execution latency: ~15-25ms TCP, ~200-600ms HTTP.\n"
            "  - Market orders fill at current market price \u2014 slippage is possible, bounded by deviation_points.\n"
            "  - If SL/TP provided, they are attached to the position simultaneously with entry.\n"
            "  - Does NOT validate SL/TP distance against stopsLevel \u2014 use validate_trade_setup() first.\n"
            "\n"
            "Composition: Input volume_lots from calculate_position_size(). Check with validate_trade_setup() before calling."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "intent_id": {"type": "string"},
                "strategy_id": {"type": "string"},
                "account_id": {"type": "string"},
                "symbol": {"type": "string"},
                "side": {"type": "string"},
                "order_kind": {"type": "string"},
                "volume_lots": {"type": ["number", "string"]},
                "deviation_points": {"type": ["number", "string"]},
                "sl": {"type": ["number", "string", "null"]},
                "tp": {"type": ["number", "string", "null"]},
                **_OWNERSHIP_PROPERTIES,
            },
            "required": [
                "intent_id",
                "strategy_id",
                "account_id",
                "symbol",
                "side",
                "order_kind",
                "volume_lots",
            ],
        },
    },
    "submit_market_order": {
        "description": (
            "What: Submits a market order (alternate endpoint with simplified parameters).\n"
            "\n"
            "Input:\n"
            "  - intent_id: String. Unique tracking identifier.\n"
            "  - strategy_id: String. Strategy name.\n"
            "  - account_id: String. Account identifier.\n"
            "  - symbol: String. MT5 symbol name.\n"
            '  - side: String. "buy" or "sell".\n'
            '  - order_kind: String. Must be "market".\n'
            "  - volume_lots: Float. Position size.\n"
            "  - sl: Float | null. Stop-loss price.\n"
            "  - tp: Float | null. Take-profit price.\n"
            "  - deviation_points: Integer. Max slippage in points. Default: 20.\n"
            "\n"
            "Output: Same as submit_market_order_via_bridge.\n"
            "\n"
            "Assumptions:\n"
            "  - Functionally identical to submit_market_order_via_bridge. Both route through the same execution gateway.\n"
            "  - Subject to TradingPolicy gates.\n"
            "\n"
            "Composition: See submit_market_order_via_bridge."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "intent_id": {"type": "string"},
                "strategy_id": {"type": "string"},
                "account_id": {"type": "string"},
                "symbol": {"type": "string"},
                "side": {"type": "string"},
                "order_kind": {"type": "string"},
                "volume_lots": {"type": ["number", "string"]},
                "deviation_points": {"type": ["number", "string"]},
                "sl": {"type": ["number", "string", "null"]},
                "tp": {"type": ["number", "string", "null"]},
                **_OWNERSHIP_PROPERTIES,
            },
            "required": [
                "intent_id",
                "strategy_id",
                "account_id",
                "symbol",
                "side",
                "order_kind",
                "volume_lots",
            ],
        },
    },
    "submit_pending_order": {
        "description": (
            "What: Submits a pending (limit or stop) order to the MT5 terminal.\n"
            "\n"
            "Input:\n"
            "  - symbol: String. MT5 symbol name.\n"
            '  - side: String. "buy" or "sell".\n'
            '  - kind: String. "limit" or "stop".\n'
            "  - price: Float. Trigger price for the pending order.\n"
            "  - volume_lots: Float. Position size in lots.\n"
            "  - sl: Float | null. Stop-loss (set when order fills).\n"
            "  - tp: Float | null. Take-profit (set when order fills).\n"
            "  - deviation: Integer. Maximum slippage in points. Default: 20.\n"
            "\n"
            'Output: {status: "placed"|"error", order_id: int, message: string, raw: object}\n'
            "  - order_id: MT5 order ticket number. Use with modify_order() and cancel_order().\n"
            "  - On error, order_id is null. Check raw for details.\n"
            "\n"
            "Assumptions:\n"
            "  - Pending orders are subject to the same stopsLevel constraints as market orders.\n"
            "  - If price is too close to current market (within stopsLevel), order is rejected.\n"
            "  - SL/TP are attached when the order fills, not when placed.\n"
            "  - Order may expire if the symbol has an expiration policy set by the broker.\n"
            "\n"
            'Composition: Input price from support_resistance() levels. Use validate_trade_setup(order_kind="limit"/"stop") before calling.'
        ),
        "schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "side": {"type": "string"},
                "kind": {"type": "string"},
                "price": {"type": ["number", "string"]},
                "volume_lots": {"type": ["number", "string"]},
                "sl": {"type": ["number", "string", "null"]},
                "tp": {"type": ["number", "string", "null"]},
                "deviation": {"type": ["number", "string"]},
                **_OWNERSHIP_PROPERTIES,
            },
            "required": ["symbol", "side", "kind", "price", "volume_lots"],
        },
    },
    "modify_order": {
        "description": (
            "What: Modifies a pending order's price, stop-loss, or take-profit.\n"
            "\n"
            "Input:\n"
            "  - order_id: String. MT5 pending order ticket number.\n"
            "  - new_price: Float | null. New trigger price. Omit to keep current.\n"
            "  - new_sl: Float | null. New stop-loss. Omit to keep current.\n"
            "  - new_tp: Float | null. New take-profit. Omit to keep current.\n"
            "\n"
            'Output: {status: "modified"|"error", message: string, raw: object}\n'
            "\n"
            "Assumptions:\n"
            "  - Only works on PENDING orders, not open positions. Use modify_position_sl_tp() for positions.\n"
            "  - New price must still respect stopsLevel distance from current market.\n"
            "  - Providing all null fields results in no modification.\n"
            "  - If order has already filled, returns error (order no longer pending).\n"
            "\n"
            "Composition: Use after orders_pending() to identify the target order_id."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "new_price": {"type": ["number", "string", "null"]},
                "new_sl": {"type": ["number", "string", "null"]},
                "new_tp": {"type": ["number", "string", "null"]},
                **_OWNERSHIP_PROPERTIES,
            },
            "required": ["order_id"],
        },
    },
    "modify_position_sl_tp": {
        "description": (
            "What: Adjusts the stop-loss and/or take-profit on an open position.\n"
            "\n"
            "Input:\n"
            "  - position_id: String. MT5 position ticket number.\n"
            "  - sl: Float | null. New stop-loss price. Null to remove SL (if broker allows).\n"
            "  - tp: Float | null. New take-profit price. Null to remove TP (if broker allows).\n"
            "\n"
            'Output: {status: "modified"|"error", message: string, raw: object}\n'
            "\n"
            "Assumptions:\n"
            "  - Works on OPEN positions only. Use modify_order() for pending orders.\n"
            "  - SL/TP must respect stopsLevel distance from current market price.\n"
            "  - Setting sl=null may remove the stop entirely (broker-dependent).\n"
            "  - Partial modifications are allowed (e.g. change SL but keep TP).\n"
            "\n"
            "Composition: Input position_id from positions_open(). Use with trailing stop tools for automated management."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "position_id": {"type": "string"},
                "sl": {"type": ["number", "string", "null"]},
                "tp": {"type": ["number", "string", "null"]},
                **_OWNERSHIP_PROPERTIES,
            },
            "required": ["position_id"],
        },
    },
    "close_position": {
        "description": (
            "What: Closes an open position, either fully or partially.\n"
            "\n"
            "Input:\n"
            "  - position_id: String. MT5 position ticket number.\n"
            "  - volume: Float | null. Amount to close in lots. Null or 0 = close entire position.\n"
            "\n"
            'Output: {status: "closed"|"error", message: string, deal_id: int, close_price: float, raw: object}\n'
            "\n"
            "Assumptions:\n"
            "  - Partial close: volume must be \u2264 position's current volume and \u2265 volume_min.\n"
            "  - Remaining volume (after partial close) must still meet volume_min or position is fully closed.\n"
            "  - Close price is the execution price (may differ from mark_price due to slippage).\n"
            "  - Closing a position realizes its P&L (affects account balance immediately).\n"
            "\n"
            "Composition: Input position_id from positions_open(). Use before close_all_positions() for selective exits."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "position_id": {"type": "string"},
                "volume": {"type": ["number", "string", "null"]},
                **_OWNERSHIP_PROPERTIES,
            },
            "required": ["position_id"],
        },
    },
    "close_all_positions": {
        "description": (
            "What: Closes all open positions, optionally filtered by symbol and/or side.\n"
            "\n"
            "Input:\n"
            "  - symbol: String | null. Filter by symbol. Null = all symbols.\n"
            '  - side: String. "buy", "sell", or "both". Default: "both".\n'
            "\n"
            "Output: {closed: [{position_id, status, message}], failed: [{position_id, error}], summary: {total_attempted, total_closed, total_failed}}\n"
            "\n"
            "Assumptions:\n"
            "  - Positions are closed sequentially. Market conditions may change between closures.\n"
            "  - Timeout: 60 seconds for all positions to close.\n"
            "  - Individual position failures are reported in the failed array; the tool continues closing remaining positions.\n"
            "  - Does NOT cancel pending orders \u2014 use cancel_all_orders() separately.\n"
            "\n"
            "Composition: Emergency exit tool. Check positions_open() before calling to verify scope."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": ["string", "null"]},
                "side": {"type": "string"},
                **_OWNERSHIP_PROPERTIES,
            },
            "required": [],
        },
    },
    "cancel_order": {
        "description": (
            "What: Cancels a single pending order by ticket number.\n"
            "\n"
            "Input:\n"
            "  - order_id: String. MT5 pending order ticket number.\n"
            "\n"
            'Output: {status: "cancelled"|"error", message: string, raw: object}\n'
            "\n"
            "Assumptions:\n"
            "  - Only cancels PENDING orders. Already-filled orders cannot be cancelled (use close_position instead).\n"
            "  - If order doesn't exist or is already filled, returns error.\n"
            "\n"
            "Composition: Input order_id from orders_pending()."
        ),
        "schema": {
            "type": "object",
            "properties": {"order_id": {"type": "string"}, **_OWNERSHIP_PROPERTIES},
            "required": ["order_id"],
        },
    },
    "cancel_all_orders": {
        "description": (
            "What: Cancels all pending orders, optionally filtered by symbol and/or side.\n"
            "\n"
            "Input:\n"
            "  - symbol: String | null. Filter by symbol. Null = all symbols.\n"
            '  - side: String. "buy", "sell", or "both". Default: "both".\n'
            "\n"
            "Output: {cancelled: [{order_id, status}], failed: [{order_id, error}], summary: {total_attempted, total_cancelled, total_failed}}\n"
            "\n"
            "Assumptions:\n"
            "  - Timeout: 60 seconds for all orders to cancel.\n"
            "  - Does NOT close open positions \u2014 use close_all_positions() separately.\n"
            "  - Individual failures reported in failed array.\n"
            "\n"
            "Composition: Check orders_pending() before calling to verify scope."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": ["string", "null"]},
                "side": {"type": "string"},
                **_OWNERSHIP_PROPERTIES,
            },
            "required": [],
        },
    },
    "trail_position": {
        "description": (
            "What: Manually computes and applies a trailing stop to an open position based on current market price.\n"
            "\n"
            "Input:\n"
            "  - position_id: String. MT5 position ticket number.\n"
            "  - distance_points: Integer. Distance (in points) between current price and new SL.\n"
            "  - lock_in_points: Integer | null. Minimum profit (in points) to lock in. New SL will not go below entry + lock_in_points for buys, or above entry - lock_in_points for sells. Default: 0.\n"
            "\n"
            'Output: {computed_sl: float, result: {status: "modified"|"error", ...}}\n'
            "\n"
            "Assumptions:\n"
            "  - One-shot operation: computes and applies trailing SL immediately. Does NOT create a persistent trailing stop.\n"
            "  - For buy positions: new_sl = current_ask - distance_points (bounded below by entry + lock_in_points and above by bid - point).\n"
            "  - For sell positions: new_sl = current_bid + distance_points (bounded above by entry - lock_in_points and below by ask + point).\n"
            "  - If computed SL would be worse than existing SL, the modification may fail (broker rejects).\n"
            "  - Requires live order book data for current bid/ask.\n"
            "\n"
            "Composition: Alternative to set_trailing_stop() for manual control. Takes position_id from positions_open()."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "position_id": {"type": "string"},
                "distance_points": {"type": ["number", "string"]},
                "lock_in_points": {"type": ["number", "string"]},
            },
            "required": ["position_id", "distance_points"],
        },
    },
    "place_bracket_order": {
        "description": (
            "What: Places paired BUY STOP and SELL STOP orders simultaneously for breakout capture.\n"
            "\n"
            "Input:\n"
            "  - symbol: String. MT5 symbol name.\n"
            "  - buy_trigger: Float. BUY STOP trigger price (must be above current ask).\n"
            "  - sell_trigger: Float. SELL STOP trigger price (must be below current bid).\n"
            "  - volume_lots: Float. Position size for each leg.\n"
            "  - sl_atr_multiplier: Float. SL distance as ATR multiplier. Default: 1.0.\n"
            "  - tp_atr_multiplier: Float. TP distance as ATR multiplier. Default: 2.0.\n"
            '  - strategy_id: String. Strategy identifier. Default: "bracket".\n'
            "  - rationale: String | null. Free-text reasoning for audit trail.\n"
            "\n"
            'Output: {buy_order_id: string|null, sell_order_id: string|null, status: "placed"|"partial"|"error", message: string, atr_used: float, computed_sl_buy: float, computed_tp_buy: float, computed_sl_sell: float, computed_tp_sell: float}\n'
            '  - status "partial" means one leg placed but the other failed.\n'
            "  - SL/TP are computed as: SL = trigger \u00b1 (ATR \u00d7 sl_atr_multiplier), TP = trigger \u00b1 (ATR \u00d7 tp_atr_multiplier).\n"
            "\n"
            "Assumptions:\n"
            "  - Both orders are placed independently. If one fails, the other may still succeed (partial status).\n"
            "  - ATR is fetched internally using current symbol's ATR(14) on H1 timeframe.\n"
            "  - When one leg fills, the other should be manually cancelled (no automatic OCO behavior in MT5).\n"
            "  - Requires buy_trigger > current ask and sell_trigger < current bid. Reversed triggers are rejected.\n"
            "\n"
            "Composition: Uses ATR internally. Input triggers from support_resistance() levels. Cancel the unfilled leg after one fills."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "buy_trigger": {"type": ["number", "string"]},
                "sell_trigger": {"type": ["number", "string"]},
                "volume_lots": {"type": ["number", "string"]},
                "sl_atr_multiplier": {"type": ["number", "string"]},
                "tp_atr_multiplier": {"type": ["number", "string"]},
                "strategy_id": {"type": "string"},
                "rationale": {"type": ["string", "null"]},
                **_OWNERSHIP_PROPERTIES,
            },
            "required": ["symbol", "buy_trigger", "sell_trigger", "volume_lots"],
        },
    },
    # === TRAILING STOP TOOLS ===
    "set_trailing_stop": {
        "description": (
            "What: Starts a server-side automated trailing stop for an open position.\n"
            "\n"
            "Input:\n"
            "  - position_id: String. MT5 position ticket number.\n"
            "  - distance_atr_multiplier: Float. SL distance as ATR multiplier. Default: 1.0. Valid: 0.5-5.0.\n"
            "  - check_interval_seconds: Integer. How often to check and update SL. Default: 10. Valid: 5-300.\n"
            "  - lock_in_profit_after_atr: Float. Begin trailing only after price moves this many ATR in profit. Default: 1.0.\n"
            "\n"
            'Output: {position_id, status: "active"|"stopped"|"error", message: string, initial_sl: float|null}\n'
            "\n"
            "Assumptions:\n"
            "  - Server-side: trailing logic runs on the MCP server, not in MT5. Requires the MCP server to stay running.\n"
            "  - SL is only moved in the profitable direction (never widened).\n"
            "  - Initial SL is computed as: entry_price \u00b1 (ATR \u00d7 distance_atr_multiplier).\n"
            "  - If MCP server restarts, active trailing stops are lost.\n"
            "  - Does NOT work on pending orders \u2014 only open positions.\n"
            "\n"
            "Composition: Alternative to trail_position() for automated management. Takes position_id from positions_open()."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "position_id": {"type": "string"},
                "distance_atr_multiplier": {"type": ["number", "string"]},
                "check_interval_seconds": {"type": ["number", "string"]},
                "lock_in_profit_after_atr": {"type": ["number", "string"]},
                **_OWNERSHIP_PROPERTIES,
            },
            "required": ["position_id"],
        },
    },
    "trailing_stop/tick": {
        "description": (
            "What: Processes all active trailing stops, updating SL positions based on current market prices.\n"
            "\n"
            "Input: None.\n"
            "\n"
            "Output: {processed: int, updated: [{position_id, old_sl, new_sl}], errors: [{position_id, error}]}\n"
            "\n"
            "Assumptions:\n"
            "  - Must be called periodically (e.g. every 10-30 seconds) to activate trailing stops.\n"
            "  - If not called, trailing stops do not update (no background thread).\n"
            "  - Returns {processed: 0} if no trailing stops are active.\n"
            "\n"
            "Composition: Call on a schedule after set_trailing_stop(). Complements trailing_stop/list() for monitoring."
        ),
        "schema": {"type": "object"},
    },
    "trailing_stop/cancel": {
        "description": (
            "What: Cancels an active server-side trailing stop for a position.\n"
            "\n"
            "Input:\n"
            "  - position_id: String. MT5 position ticket number.\n"
            "\n"
            'Output: {position_id, status: "cancelled"|"error", message: string}\n'
            "\n"
            "Assumptions:\n"
            "  - Does NOT close the position \u2014 only stops the automated trailing.\n"
            "  - The position's SL remains at its last updated value.\n"
            "  - Canceling a non-existent trailing stop returns an error.\n"
            "\n"
            "Composition: Use when manually taking over SL management."
        ),
        "schema": {
            "type": "object",
            "properties": {"position_id": {"type": "string"}},
            "required": ["position_id"],
        },
    },
    "trailing_stop/list": {
        "description": (
            "What: Lists all currently active server-side trailing stops.\n"
            "\n"
            "Input: None.\n"
            "\n"
            "Output: {trailing_stops: [{position_id, symbol, side, distance_atr_multiplier, current_sl, initial_sl, last_update, check_interval}], count: int}\n"
            "\n"
            "Assumptions:\n"
            "  - Returns empty list if no trailing stops are active (no error).\n"
            "  - Data reflects server-side state only (not MT5 terminal state).\n"
            "  - last_update is Unix timestamp of last SL modification.\n"
            "\n"
            "Composition: Use with trailing_stop/tick() to verify active trailing stops are being updated."
        ),
        "schema": {"type": "object"},
    },
    # === LONG-POLLING TOOLS ===
    "resources/market/wait_for_price": {
        "description": (
            "What: Long-polling price alert: holds the HTTP connection open until a price condition is met or timeout occurs.\n"
            "\n"
            "Input:\n"
            "  - symbol: String. MT5 symbol name.\n"
            '  - condition: String. "above", "below", or "crosses".\n'
            "  - price: Float. Target price level.\n"
            "  - timeout_seconds: Integer. Maximum time to wait. Default: 300 (5 minutes). Range: 10-600.\n"
            "\n"
            "Output: {symbol, condition: string, trigger_price: float, actual_price: float, triggered: bool, timed_out: bool}\n"
            "  - triggered: true if condition was met before timeout.\n"
            "  - timed_out: true if timeout_seconds elapsed without trigger.\n"
            "  - actual_price: Price at the moment of trigger (or last sampled price on timeout).\n"
            "\n"
            "Assumptions:\n"
            "  - Blocks the HTTP connection for up to timeout_seconds. Client must handle long-lived connections.\n"
            "  - Price is sampled at ~1-second intervals via tick data.\n"
            "  - If bridge disconnects during wait, returns {triggered: false, timed_out: true}.\n"
            "  - Not suitable for high-frequency monitoring \u2014 use get_ticks() for that.\n"
            "\n"
            "Composition: Alternative to polling get_ticks() in a loop. Use for event-driven entry triggers."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "condition": {"type": "string", "enum": ["above", "below", "crosses"]},
                "price": {"type": ["number", "string"]},
                "timeout_seconds": {"type": ["number", "string"]},
            },
            "required": ["symbol", "condition", "price"],
        },
    },
    "resources/positions/monitor": {
        "description": (
            "What: Long-polling position monitor: holds the connection open until a P&L or price alert fires, or timeout occurs.\n"
            "\n"
            "Input:\n"
            "  - position_id: String. MT5 position ticket number.\n"
            "  - alert_at_pnl: Array[Float]. Alert when position P&L reaches any of these values (in account currency).\n"
            "  - alert_at_price: Array[Float]. Alert when symbol price reaches any of these levels.\n"
            "  - timeout_seconds: Integer. Maximum wait time. Default: 600 (10 minutes). Range: 60-3600.\n"
            "\n"
            'Output: {position_id, alert_type: "pnl"|"price"|"timeout"|"closed", current_pnl: float|null, current_price: float|null, triggered_value: float|null, timed_out: bool}\n'
            '  - alert_type "closed" means the position was closed during monitoring.\n'
            "  - triggered_value is the P&L or price that triggered the alert.\n"
            "\n"
            "Assumptions:\n"
            "  - Blocks the HTTP connection for up to timeout_seconds.\n"
            "  - Samples position state at ~5-second intervals.\n"
            '  - If position is closed externally (not by the agent), alert_type will be "closed".\n'
            '  - If position_id doesn\'t exist, returns immediately with alert_type: "closed".\n'
            "\n"
            "Composition: Alternative to polling positions_open(). Use for hands-free position monitoring between active cycles."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "position_id": {"type": "string"},
                "alert_at_pnl": {"type": "array", "items": {"type": "number"}},
                "alert_at_price": {"type": "array", "items": {"type": "number"}},
                "timeout_seconds": {"type": ["number", "string"]},
            },
            "required": ["position_id"],
        },
    },
    # === AGENT WAIT/TIMER TOOLS ===
    "tools/wait/delay": {
        "description": (
            "What: Pauses execution for a specified duration. Use this in trading loops to wait between analysis cycles.\n"
            "\n"
            "Input:\n"
            "  - duration_seconds: Integer. How long to wait. Default: 60. Range: 1-3600.\n"
            "\n"
            "Output: {waited_seconds: int, resumed_at: string}\n"
            "  - resumed_at is ISO 8601 UTC timestamp.\n"
            "\n"
            "Assumptions:\n"
            "  - Blocks the connection for the full duration.\n"
            "  - Use for simple waits: 'wait 5 minutes then recheck market conditions'.\n"
            "  - For event-driven waits (price levels, indicator values), use tools/wait/indicator instead.\n"
            "\n"
            "Composition: Use in loops: analyze → wait/delay(300) → analyze again. Alternative to polling get_bars() repeatedly."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "duration_seconds": {"type": ["number", "string"]},
            },
            "required": [],
        },
    },
    "tools/wait/indicator": {
        "description": (
            "What: Long-polling wait until a technical indicator reaches a target value or condition.\n"
            "\n"
            "Input:\n"
            "  - symbol: String. MT5 symbol name.\n"
            '  - timeframe: String. Default: "H1".\n'
            '  - indicator: String. Same as get_indicator(): "rsi", "macd", "cci", "atr", "adx", "sma", "ema", etc.\n'
            '  - condition: String. "above", "below", "crosses", or "equals".\n'
            "  - value: Float. Target indicator value.\n"
            "  - period, fast, slow, signal: Integer | null. Indicator parameters (same as get_indicator).\n"
            "  - timeout_seconds: Integer. Max wait time. Default: 300 (5 minutes). Range: 10-3600.\n"
            "  - check_interval_seconds: Integer. Polling interval. Default: 5. Range: 1-60.\n"
            "\n"
            "Output: {symbol, indicator, condition, target_value, actual_value: float|null, triggered: bool, timed_out: bool}\n"
            "  - triggered: true if condition was met before timeout.\n"
            "  - timed_out: true if timeout elapsed without trigger.\n"
            "  - actual_value: Indicator value at trigger (or last sampled on timeout).\n"
            "\n"
            "Assumptions:\n"
            "  - Blocks the HTTP connection for up to timeout_seconds.\n"
            "  - Indicator is sampled at check_interval_seconds intervals.\n"
            "  - 'equals' uses 0.1% tolerance for floating-point comparison.\n"
            "  - 'crosses' returns immediately on first indicator update (use with caution).\n"
            "\n"
            "Composition: Use for event-driven entries: 'wait until RSI drops below 30, then buy'. Alternative to polling get_indicator() in a loop."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "timeframe": {"type": "string"},
                "indicator": {"type": "string"},
                "condition": {
                    "type": "string",
                    "enum": ["above", "below", "crosses", "equals"],
                },
                "value": {"type": ["number", "string"]},
                "period": {"type": ["number", "string", "null"]},
                "fast": {"type": ["number", "string", "null"]},
                "slow": {"type": ["number", "string", "null"]},
                "signal": {"type": ["number", "string", "null"]},
                "timeout_seconds": {"type": ["number", "string"]},
                "check_interval_seconds": {"type": ["number", "string"]},
            },
            "required": ["symbol", "indicator", "condition", "value"],
        },
    },
    # === METACOGNITION TOOLS ===
    "trading/log_decision": {
        "description": (
            "What: Records a trading decision with full reasoning metadata for self-learning and audit.\n"
            "\n"
            "Input:\n"
            "  - symbol: String. MT5 symbol name.\n"
            '  - side: String. "buy", "sell", or "neutral" (for hold decisions).\n'
            '  - action: String. Valid: "entry", "exit", "modify_sl", "modify_tp", "trail", "close", "monitor", "decision_to_wait".\n'
            "  - entry_price, exit_price, sl, tp: Float | null. Trade parameters as applicable.\n"
            "  - volume_lots: Float | null. Position size.\n"
            "  - pnl: Float | null. Realized or unrealized P&L.\n"
            "  - session_id: String | null. Session identifier for grouping related decisions.\n"
            "  - regime: String | null. Market regime at decision time (from market/regime).\n"
            "  - atr_value, atr_percent_of_price: Float | null. Volatility context.\n"
            "  - rsi_value: Float | null. RSI value at decision time.\n"
            "  - indicator_snapshot: Dict | null. Arbitrary indicator values for context.\n"
            "  - model_justification: String | null. Free-text reasoning for the decision.\n"
            "  - indicators_considered: Array[String] | null. List of indicators consulted.\n"
            "  - confidence_level: Float | null. Decision confidence. Range: 0.0-1.0.\n"
            "  - risk_assessment: String | null. Free-text risk evaluation.\n"
            '  - emotional_self_report: String | null. Valid: "calm", "cautious", "aggressive", "anxious", "uncertain", "confident".\n'
            "  - alternatives_considered: String | null. What other actions were evaluated.\n"
            "  - expected_duration: String | null. Expected holding period.\n"
            "  - expected_move_points: Float | null. Anticipated price movement.\n"
            '  - outcome: String | null. Filled on exit. Valid: "win", "loss", "breakeven", "still_open".\n'
            "  - lesson_learned: String | null. Post-trade reflection.\n"
            "  - would_do_differently: String | null. Improvement notes.\n"
            '  - mistake_category: String | null. Valid: "premature_exit", "late_entry", "wrong_regime", "ignored_signal", "revenge_trade", "overtrading", "perfect_trade".\n'
            "  - quality_rating: Integer | null. Trade quality. Range: 1-5.\n"
            "  - decision_id: String | null. For updating an existing decision entry.\n"
            "\n"
            'Output: {decision_id: string, status: "logged"|"updated"|"error", message: string}\n'
            "\n"
            "Assumptions:\n"
            "  - Decisions are persisted to SQLite journal. Survives server restarts.\n"
            "  - decision_id is returned and can be used to update the entry later (e.g. fill outcome on exit).\n"
            "  - All text fields accept any string \u2014 no validation on content.\n"
            "  - Empty/null fields are stored as-is; they don't trigger defaults.\n"
            "\n"
            "Composition: Call after every action (including the decision to wait). Input decision_id into trading/reflect() for querying."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "side": {"type": "string"},
                "action": {"type": "string"},
                "entry_price": {"type": ["number", "string", "null"]},
                "exit_price": {"type": ["number", "string", "null"]},
                "sl": {"type": ["number", "string", "null"]},
                "tp": {"type": ["number", "string", "null"]},
                "volume_lots": {"type": ["number", "string", "null"]},
                "pnl": {"type": ["number", "string", "null"]},
                "session_id": {"type": ["string", "null"]},
                "regime": {"type": ["string", "null"]},
                "atr_value": {"type": ["number", "string", "null"]},
                "rsi_value": {"type": ["number", "string", "null"]},
                "model_justification": {"type": ["string", "null"]},
                "indicators_considered": {"type": "array", "items": {"type": "string"}},
                "confidence_level": {"type": ["number", "string", "null"]},
                "emotional_self_report": {"type": ["string", "null"]},
                "alternatives_considered": {"type": ["string", "null"]},
                "outcome": {"type": ["string", "null"]},
                "lesson_learned": {"type": ["string", "null"]},
                "mistake_category": {"type": ["string", "null"]},
                "quality_rating": {"type": ["number", "string", "null"]},
                "decision_id": {"type": ["string", "null"]},
            },
            "required": ["symbol", "side", "action"],
        },
    },
    "trading/reflect": {
        "description": (
            "What: Queries the trade journal for past decisions, filterable by outcome, regime, emotion, or mistake type.\n"
            "\n"
            "Input:\n"
            "  - symbol: String | null. Filter by symbol.\n"
            '  - outcome: String | null. Filter by outcome ("win", "loss", "breakeven").\n'
            "  - regime: String | null. Filter by market regime.\n"
            "  - emotional_self_report: String | null. Filter by emotional state.\n"
            "  - mistake_category: String | null. Filter by mistake type.\n"
            "  - action: String | null. Filter by action type.\n"
            "  - limit: Integer. Maximum entries to return. Default: 50.\n"
            "\n"
            "Output: {decisions: [{decision_id, symbol, side, action, entry_price, pnl, regime, confidence_level, emotional_self_report, outcome, mistake_category, quality_rating, timestamp, ...}], count: int, query: {filters}}\n"
            "\n"
            "Assumptions:\n"
            "  - Returns empty decisions array if no matches (no error).\n"
            "  - Results sorted by timestamp descending (most recent first).\n"
            "  - Only returns fields that were populated at log time.\n"
            "\n"
            'Composition: Input for trading/insights(). Use for pattern recognition: "Show my losses in ranging regimes."'
        ),
        "schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": ["string", "null"]},
                "outcome": {"type": ["string", "null"]},
                "regime": {"type": ["string", "null"]},
                "emotional_self_report": {"type": ["string", "null"]},
                "mistake_category": {"type": ["string", "null"]},
                "action": {"type": ["string", "null"]},
                "limit": {"type": ["number", "string"]},
            },
            "required": [],
        },
    },
    "trading/insights": {
        "description": (
            "What: Analyzes the trade journal for aggregate patterns: win rates by regime, emotion, common mistakes.\n"
            "\n"
            "Input:\n"
            "  - lookback_days: Integer | null. Analysis window. Default: 7.\n"
            "\n"
            "Output: {lookback_days: int, total_decisions: int, win_rate: float, avg_pnl: float, win_rate_by_regime: {regime: float}, win_rate_by_emotion: {emotion: float}, common_mistakes: [{category, count, avg_loss}], avg_confidence_when_winning: float, avg_confidence_when_losing: float, recommendations: [string]}\n"
            "  - All rates are 0.0-1.0.\n"
            "  - recommendations: AI-actionable guidance derived from patterns.\n"
            "\n"
            "Assumptions:\n"
            "  - Only includes decisions with populated outcome fields (decisions_to_wait excluded from win rate).\n"
            "  - Returns zero-valued metrics if insufficient data (< 5 decisions).\n"
            "  - recommendations are template-generated, not LLM-generated.\n"
            "\n"
            "Composition: Takes data from trading/reflect(). Use periodically (e.g. daily) for strategy refinement."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "lookback_days": {"type": ["number", "string"]},
            },
            "required": [],
        },
    },
    # === COACHING/CONTEXT TOOLS ===
    "trading/context": {
        "description": (
            "What: Fetches live market context for a symbol: ATR, volatility assessment, point values, and spread analysis.\n"
            "\n"
            "Input:\n"
            "  - symbol: String. MT5 symbol name.\n"
            "  - include_comparison: Bool. If true, includes comparison to historical averages. Default: true.\n"
            "\n"
            "Output: {symbol, current_price: float, bid: float, ask: float, spread_points: float, atr_14: float, atr_pct_of_price: float, avg_atr: float, atr_percentile: float, volatility_assessment: string, point_value: float, lot_size_info: {min, max, step}, composure_notes: string, session: string}\n"
            "  - composure_notes: Contextual note about whether current volatility is unusual.\n"
            '  - session: Current forex trading session (e.g. "London", "New York", "Asian", "Closed").\n'
            "\n"
            "Assumptions:\n"
            "  - Uses ATR(14) on H1 timeframe as baseline.\n"
            "  - Comparison data based on last 100 bars of ATR history.\n"
            "  - Returns partial data with null fields if symbol has no market data.\n"
            '  - Volatility assessment is categorical ("low", "normal", "high", "extreme") based on ATR percentile.\n'
            "\n"
            "Composition: Takes symbol_info() and get_indicator(atr) internally. Input for trading/coach(), calculate_position_size()."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "include_comparison": {"type": "boolean"},
            },
            "required": ["symbol"],
        },
    },
    "trading/coach": {
        "description": (
            "What: Provides advisory feedback on a proposed trade setup using live market data. Does NOT block execution.\n"
            "\n"
            "Input:\n"
            "  - symbol: String. MT5 symbol name.\n"
            '  - side: String. "buy" or "sell".\n'
            "  - regime: String | null. Current market regime. If null, fetched internally.\n"
            "  - atr_value: Float | null. Current ATR. If null, fetched internally.\n"
            "  - rsi: Float | null. Current RSI value. If null, not evaluated.\n"
            "  - ema_fast, ema_slow: Float | null. Current EMA values. If null, not evaluated.\n"
            "  - sl_distance_points: Float | null. Planned SL distance in points.\n"
            "  - tp_distance_points: Float | null. Planned TP distance in points.\n"
            "  - indicator_agreements: Integer | null. Count of indicators aligned with the trade direction.\n"
            "  - trades_today: Integer. Number of trades taken today. Default: 0.\n"
            "  - daily_pnl: Float. Current daily P&L. Default: 0.0.\n"
            "  - recent_consecutive_losses: Integer. Number of consecutive losses. Default: 0.\n"
            "  - position_in_range: Float | null. Current price position as 0-100 percentile of recent range.\n"
            "\n"
            "Output: {symbol, side, advisory: {sl_atr_ratio: float, risk_reward: float, trend_alignment: string, bar_pattern_notes: string, volatility_notes: string, session_notes: string, confluence_score: int, warnings: [string], recommendations: [string]}, market_data: {atr, regime, rsi, ema_fast, ema_slow}}\n"
            "  - sl_atr_ratio: SL distance / ATR. Values < 1.0 indicate tight stops.\n"
            "  - risk_reward: TP distance / SL distance.\n"
            "  - confluence_score: 0-5, based on indicator alignment, trend, and regime fit.\n"
            '  - Warnings are factual observations (e.g. "SL is 0.3 ATR \u2014 below recommended minimum of 1.0 ATR").\n'
            "  - Recommendations are optional suggestions (not mandatory).\n"
            "\n"
            "Assumptions:\n"
            "  - Advisory, NOT a gate. Returns warnings/recommendations but does NOT block execution.\n"
            "  - Missing input parameters are fetched internally where possible; if unavailable, those checks are skipped.\n"
            "  - Does not account for account balance, policy limits, or open positions \u2014 those are separate concerns.\n"
            "\n"
            "Composition: Takes regime from market/regime(), ATR from get_indicator(atr). Call before execution tools for pre-trade validation."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "side": {"type": "string"},
                "regime": {"type": ["string", "null"]},
                "atr_value": {"type": ["number", "string", "null"]},
                "rsi": {"type": ["number", "string", "null"]},
                "ema_fast": {"type": ["number", "string", "null"]},
                "ema_slow": {"type": ["number", "string", "null"]},
                "sl_distance_points": {"type": ["number", "string", "null"]},
                "tp_distance_points": {"type": ["number", "string", "null"]},
                "indicator_agreements": {"type": ["number", "string", "null"]},
                "trades_today": {"type": ["number", "string"]},
                "daily_pnl": {"type": ["number", "string"]},
                "recent_consecutive_losses": {"type": ["number", "string"]},
                "position_in_range": {"type": ["number", "string", "null"]},
            },
            "required": ["symbol", "side"],
        },
    },
    "trading/decision_support": {
        "description": (
            "What: Single-call aggregation: regime + ATR + RSI + EMA(20) + EMA(50) + coaching feedback. Returns all data needed for an informed trade decision.\n"
            "\n"
            "Input:\n"
            "  - symbol: String. MT5 symbol name.\n"
            '  - side: String. "buy" or "sell".\n'
            "  - sl_distance_points: Float | null. Planned SL distance in points.\n"
            "  - tp_distance_points: Float | null. Planned TP distance in points.\n"
            "\n"
            "Output: {symbol, regime: {regime, confidence, adx}, atr: {value, pct_of_price}, rsi: {value}, ema: {ema_20, ema_50, alignment}, coaching: {confluence_score, warnings, recommendations}, execution_time_ms: int}\n"
            "  - execution_time_ms: Total time to gather all data (~400ms vs 3-5s for sequential individual calls).\n"
            "  - All indicator values computed at call time on the symbol's H1 timeframe.\n"
            "\n"
            "Assumptions:\n"
            "  - All indicators use H1 timeframe. Cannot specify custom timeframes.\n"
            "  - RSI uses period 14, EMAs use periods 20 and 50 (not configurable).\n"
            '  - If any internal fetch fails, that section contains {error: "..."}.\n'
            "  - Coaching feedback uses the same logic as trading/coach().\n"
            "\n"
            "Composition: Replaces sequential calls to market/regime() + trading/context() + get_indicator(rsi) + get_indicator(ema) + trading/coach(). Input for any execution decision."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "side": {"type": "string"},
                "sl_distance_points": {"type": ["number", "string", "null"]},
                "tp_distance_points": {"type": ["number", "string", "null"]},
            },
            "required": ["symbol", "side"],
        },
    },
    "trading/agent_prompt": {
        "description": (
            "What: Generates a complete system prompt for orienting a new trading agent session.\n"
            "\n"
            "Input:\n"
            "  - include_market_context: Bool. Include current market regime and volatility. Default: true.\n"
            "  - include_news_context: Bool. Include recent news headlines. Default: true.\n"
            "  - include_workflow: Bool. Include recommended analysis workflow. Default: true.\n"
            "  - include_trading_rules: Bool. Include trading policy rules. Default: true.\n"
            "  - include_tool_guide: Bool. Include tool usage guide. Default: true.\n"
            "  - include_metacognition: Bool. Include self-reflection guidelines. Default: true.\n"
            "  - live_account_context: Bool. Inject live account balance, equity, margin. Default: false.\n"
            "  - live_symbol_context: Array[String] | null. Symbols to inject live context for.\n"
            "\n"
            "Output: {system_prompt: string, sections: [string], generated_at: string, context_summary: {account, symbols, news_count, rules_count}}\n"
            "\n"
            "Assumptions:\n"
            "  - Prompt is generated at call time with live data when live_* flags are true.\n"
            "  - Stale if saved and reused later \u2014 regenerate for each new session.\n"
            "  - Does NOT include tool definitions (the MCP protocol provides those separately).\n"
            "\n"
            "Composition: Call once at the start of a new agent session. Combines data from account_summary(), market/regime(), economic_calendar(), and trading policy."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "include_market_context": {"type": "boolean"},
                "include_news_context": {"type": "boolean"},
                "include_workflow": {"type": "boolean"},
                "include_trading_rules": {"type": "boolean"},
                "include_tool_guide": {"type": "boolean"},
                "include_metacognition": {"type": "boolean"},
                "live_account_context": {"type": "boolean"},
                "live_symbol_context": {"type": "array", "items": {"type": "string"}},
            },
            "required": [],
        },
    },
    # === NEWS TOOLS ===
    "news_fetch": {
        "description": (
            "What: Fetches financial news articles from configured RSS feed sources.\n"
            "\n"
            "Input:\n"
            '  - pools: Array[String] | null. News pools to query. Default: ["FINANCIAL_MARKETS"]. Available pools via news/pools.\n'
            "  - limit: Integer. Max articles to return. Default: 20.\n"
            "  - keywords: Array[String] | null. Include only articles containing keywords.\n"
            "  - excludeKeywords: Array[String] | null. Exclude articles containing ANY keyword.\n"
            "  - enrichArticles: Bool. Apply NLP enrichment (sentiment, topics, entities). Default: false.\n"
            "  - countries: Array[String] | null. Filter by country codes.\n"
            "  - domains: Array[String] | null. Filter by source domains.\n"
            "\n"
            "Output: {articles: [{title, summary, url, source, published_at, currencies: [string], categories: [string]}], count: int, source: string}\n"
            "  - If enrichArticles is true, each article also includes: sentiment, topics, entities, summary.\n"
            "  - published_at is ISO 8601 UTC.\n"
            "\n"
            "Assumptions:\n"
            "  - Articles fetched from RSS feeds, not real-time push. Refresh latency: 5-15 minutes from publication.\n"
            "  - Returns empty articles array if no matches (no error).\n"
            "  - Currency matching is heuristic (based on title/body keyword matching), not guaranteed.\n"
            "  - enrichArticles=true increases latency significantly (NLP processing per article).\n"
            "\n"
            "Composition: Input for fundamental analysis before trades. Complements economic_calendar() for event awareness."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "pools": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": ["number", "string"]},
                "keywords": {"type": "array", "items": {"type": "string"}},
                "excludeKeywords": {"type": "array", "items": {"type": "string"}},
                "enrichArticles": {"type": "boolean"},
                "countries": {"type": "array", "items": {"type": "string"}},
                "domains": {"type": "array", "items": {"type": "string"}},
            },
            "required": [],
        },
    },
    "news_enrich": {
        "description": (
            "What: Applies NLP enrichment to news articles: sentiment analysis, topic extraction, entity recognition, and summarization.\n"
            "\n"
            "Input:\n"
            "  - items: Array[Object]. News articles to enrich (from news_fetch output).\n"
            '  - extract: Array[String] | null. Specific enrichment types. Valid: "sentiment", "topics", "entities", "summary". Default: all.\n'
            "\n"
            "Output: {enriched: [{...original_fields, sentiment: float, topics: [string], entities: [string], summary: string}]}\n"
            "  - sentiment: -1.0 (negative) to 1.0 (positive).\n"
            "  - Enrichment is applied in-place; original fields are preserved.\n"
            "\n"
            "Assumptions:\n"
            "  - Enrichment is computationally expensive for large item arrays (> 50 articles).\n"
            "  - Entity extraction covers currencies, central banks, and economic terms only.\n"
            "  - If enrichment fails for an item, that item is returned unchanged (no error).\n"
            "\n"
            "Composition: Takes output from news_fetch(). Use selectively for high-impact articles rather than bulk enrichment."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "items": {"type": "array", "items": {"type": "object"}},
                "extract": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["items"],
        },
    },
    "insights_trendingEntities": {
        "description": (
            "What: Detects entities (currencies, central banks, instruments) with increasing mention frequency in recent news.\n"
            "\n"
            "Input:\n"
            "  - timeWindowHours: Integer. Comparison window. Default: 24.\n"
            "  - minGrowth: Float. Minimum growth factor (e.g. 2.0 = 2x increase). Default: 1.5.\n"
            "  - minCurrentMentions: Integer. Minimum current mentions to qualify. Default: 3.\n"
            "\n"
            "Output: {trending: [{entity: string, current_mentions: int, previous_mentions: int, growth_factor: float, categories: [string]}], time_window: int}\n"
            "\n"
            "Assumptions:\n"
            "  - Compares mention frequency in the current window vs. the preceding window of equal length.\n"
            "  - Entity extraction is keyword-based, not NER (Named Entity Recognition).\n"
            "  - Returns empty trending array if no entities meet thresholds (no error).\n"
            "\n"
            "Composition: Use for detecting emerging market narratives. Input for adjusting watchlist priorities."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "timeWindowHours": {"type": ["number", "string"]},
                "minGrowth": {"type": ["number", "string"]},
                "minCurrentMentions": {"type": ["number", "string"]},
            },
            "required": [],
        },
    },
    # === NEW: ECONOMIC CALENDAR ===
    "economic_calendar": {
        "description": (
            "What: Fetches upcoming economic events and trading blackout windows.\n"
            "\n"
            "Input:\n"
            "  - hours_ahead: Integer. How far forward to look. Default: 24.\n"
            '  - currency: String | null. Filter by currency code (e.g. "USD", "EUR"). Omit for all.\n'
            '  - min_impact: String. Minimum event significance. Valid: "LOW", "MEDIUM", "HIGH", "CRITICAL". Default: "MEDIUM".\n'
            "\n"
            'Output: {events: [{name, currency, impact, timestamp, actual, forecast, previous}], event_count: int, blackout_windows: [{event_name, start_utc, end_utc, currency, impact, blackout_minutes}], current_blackout: {is_blackout: bool, events_causing_blackout: [...]}, source: "mt5_terminal_calendar"|"schedule_based_fallback"}\n'
            '  - If source is "schedule_based_fallback", events are estimated from recurring schedules, not real terminal data.\n'
            "  - Blackout windows indicate periods to avoid new entries (typically \u00b160-120 minutes around HIGH-impact events).\n"
            "\n"
            "Assumptions:\n"
            "  - Primary source is MT5 Terminal's native Economic Calendar API. Falls back to schedule-based estimates if unavailable.\n"
            '  - When source is "schedule_based_fallback", a warning field is included in the response.\n'
            "  - Returns empty events array during market holidays (no error).\n"
            "\n"
            "Composition: Check before any execution tool. Complements news_fetch() for fundamental awareness."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "hours_ahead": {"type": ["number", "string"]},
                "currency": {"type": ["string", "null"]},
                "min_impact": {"type": "string"},
            },
            "required": [],
        },
    },
    # === NEW: NEWS POOLS ===
    "news/pools": {
        "description": (
            "What: Lists available news source pools and individual sources for the news_fetch tool.\n"
            "\n"
            "Input: None.\n"
            "\n"
            "Output: {pools: [string], sources: [{id, name, url, category}]}\n"
            "\n"
            "Assumptions:\n"
            "  - Static metadata. Does not fetch actual news content.\n"
            "  - Pool names are case-sensitive strings used in news_fetch's pools parameter.\n"
            "\n"
            "Composition: Discover valid pool values before calling news_fetch()."
        ),
        "schema": {"type": "object"},
    },
}


server = Server("mt5-mcp")


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    tools: list[types.Tool] = []
    for name, spec in TOOL_SPECS.items():
        tools.append(
            types.Tool(
                name=name,
                description=spec.get("description", name),
                inputSchema=spec.get("schema", {"type": "object"}),
            )
        )
    return tools


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    args = _coerce_numeric_args(name, arguments or {})
    try:
        if name == "get_bars":
            res = await _post_json("/tools/get_bars", args)
        elif name == "get_indicator":
            res = await _post_json("/tools/get_indicator", args)
        elif name == "get_ticks":
            res = await _post_json("/tools/get_ticks", args)
        elif name == "symbol_info":
            symbol = args.get("symbol", "")
            res = await _get_json(f"/resources/symbols/{symbol}/info")
        elif name == "deals_history":
            query = []
            if args.get("symbol"):
                query.append(f"symbol={args['symbol']}")
            if args.get("limit") is not None:
                query.append(f"limit={args['limit']}")
            if args.get("days") is not None:
                query.append(f"days={args['days']}")
            suffix = f"?{'&'.join(query)}" if query else ""
            res = await _get_json(f"/resources/deals/history{suffix}")
        elif name == "performance_summary":
            query = []
            if args.get("symbol"):
                query.append(f"symbol={args['symbol']}")
            if args.get("limit") is not None:
                query.append(f"limit={args['limit']}")
            if args.get("days") is not None:
                query.append(f"days={args['days']}")
            suffix = f"?{'&'.join(query)}" if query else ""
            res = await _get_json(f"/resources/performance/summary{suffix}")
        elif name == "get_order_book":
            res = await _post_json("/tools/get_order_book", args)
        elif name == "get_chart_screenshot":
            res = await _post_json("/tools/get_chart_screenshot", args)
        elif name == "submit_market_order_via_bridge":
            res = await _post_json("/tools/submit_market_order_via_bridge", args)
        elif name == "submit_market_order":
            res = await _post_json("/tools/submit_market_order", args)
        elif name == "submit_pending_order":
            res = await _post_json("/tools/submit_pending_order", args)
        elif name == "modify_order":
            res = await _post_json("/tools/modify_order", args)
        elif name == "modify_position_sl_tp":
            res = await _post_json("/tools/modify_position_sl_tp", args)
        elif name == "close_position":
            res = await _post_json("/tools/close_position", args)
        elif name == "close_all_positions":
            res = await _post_json("/tools/close_all_positions", args)
        elif name == "cancel_order":
            res = await _post_json("/tools/cancel_order", args)
        elif name == "cancel_all_orders":
            res = await _post_json("/tools/cancel_all_orders", args)
        elif name == "calculate_position_size":
            res = await _post_json("/tools/calculate_position_size", args)
        elif name == "validate_trade_setup":
            res = await _post_json("/tools/validate_trade_setup", args)
        elif name == "trail_position":
            res = await _post_json("/tools/trail_position", args)
        elif name == "volatility_profile":
            res = await _post_json("/tools/volatility_profile", args)
        elif name == "multi_timeframe_indicators":
            res = await _post_json("/tools/multi_timeframe_indicators", args)
        elif name == "correlation_matrix":
            res = await _post_json("/tools/correlation_matrix", args)
        elif name == "support_resistance":
            res = await _post_json("/tools/support_resistance", args)
        elif name == "economic_calendar":
            res = await _post_json("/tools/trading/economic_calendar", args)
        elif name == "news/pools":
            res = await _post_json("/tools/news/pools", args)
        # Trading context & coaching
        elif name == "trading/context":
            res = await _post_json("/tools/trading/context", args)
        elif name == "trading/coach":
            res = await _post_json("/tools/trading/coach", args)
        elif name == "trading/decision_support":
            res = await _post_json("/tools/trading/decision_support", args)
        # Metacognition
        elif name == "trading/log_decision":
            res = await _post_json("/tools/trading/log_decision", args)
        elif name == "trading/reflect":
            res = await _post_json("/tools/trading/reflect", args)
        elif name == "trading/insights":
            lookback = args.get("lookback_days", 7)
            res = await _post_json(
                f"/tools/trading/insights?lookback_days={lookback}", {}
            )
        elif name == "trading/agent_prompt":
            res = await _post_json("/tools/trading/agent_prompt", args)
        # Market analysis
        elif name == "market/regime":
            res = await _post_json("/tools/market/regime", args)
        elif name == "market/scan":
            res = await _post_json("/tools/market/scan", args)
        # Bracket orders
        elif name == "place_bracket_order":
            res = await _post_json("/tools/place_bracket_order", args)
        # Trailing stops
        elif name == "set_trailing_stop":
            res = await _post_json("/tools/set_trailing_stop", args)
        elif name == "trailing_stop/tick":
            res = await _post_json("/tools/trailing_stop/tick", {})
        elif name == "trailing_stop/cancel":
            pid = args.get("position_id", "")
            res = await _post_json(f"/tools/trailing_stop/cancel?position_id={pid}", {})
        elif name == "trailing_stop/list":
            res = await _post_json("/tools/trailing_stop/list", {})
        # Long-polling resources
        elif name == "resources/market/wait_for_price":
            res = await _post_json("/resources/market/wait_for_price", args)
        elif name == "resources/positions/monitor":
            res = await _post_json("/resources/positions/monitor", args)
        # Agent wait/timer tools
        elif name == "tools/wait/delay":
            res = await _post_json("/tools/wait/delay", args)
        elif name == "tools/wait/indicator":
            res = await _post_json("/tools/wait/indicator", args)
        # IGS-MCP News & Macro
        elif name == "news_fetch":
            # Default to FINANCIAL_MARKETS pool if not specified
            if "pools" not in args:
                args["pools"] = ["FINANCIAL_MARKETS"]
            res = await _post_igs("news/fetch", args)
        elif name == "news_enrich":
            res = await _post_igs("news/enrich", args)
        elif name == "insights_trendingEntities":
            res = await _post_igs("insights/trendingEntities", args)
        elif name == "account_summary":
            res = await _get_json("/resources/account/summary")
        elif name == "positions_open":
            res = await _get_json("/resources/positions/open")
        elif name == "orders_pending":
            res = await _get_json("/resources/orders/pending")
        elif name == "bridge_status":
            res = await _get_json("/resources/mt5/bridge/status")
        else:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"Unknown tool: {name}")],
                is_error=True,
            )
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(res))]
        )
    except Exception as e:
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=f"Error: {e}")], is_error=True
        )


async def run() -> None:
    import sys
    import traceback

    try:
        # Ensure backend servers are running before initializing (async, non-blocking)
        await ensure_servers_running_async()

        async with mcp.server.stdio.stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())
    except Exception as e:
        print(f"MCP Server initialization failed: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run())
