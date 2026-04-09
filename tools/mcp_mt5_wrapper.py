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

_TOOL_NAME_ALIASES: dict[str, str] = {}


def _build_tool_name_aliases() -> dict[str, str]:
    if _TOOL_NAME_ALIASES:
        return _TOOL_NAME_ALIASES
    for canonical in TOOL_SPECS:
        normalized = "mt5-mcp_" + canonical.replace("/", "_")
        _TOOL_NAME_ALIASES[normalized] = canonical
    return _TOOL_NAME_ALIASES


def _normalize_tool_name(name: str) -> str:
    aliases = _build_tool_name_aliases()
    return aliases.get(name, name)


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
    "portfolio/risk": {"days", "limit"},
    "trail_position": {"distance_points", "lock_in_points"},
    "volatility_profile": {"lookback", "atr_period"},
    "analysis/divergence": {
        "lookback",
        "count",
        "macd_fast",
        "macd_slow",
        "macd_signal_period",
        "rsi_period",
        "swing_window",
    },
    "analysis/multi_bar_patterns": {"lookback", "period", "fib_lookback"},
    "analysis/volume_profile": {"lookback"},
    "analysis/momentum": {"lookback", "rsi", "atr"},
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
    "market/snapshot": {"bar_count"},
    "market/opportunity_rank": {"min_score"},
    "market/chart_intelligence": {"bar_count"},
    "portfolio/pre_trade_gate": {"volume_lots", "sl_distance"},
    "market/structure": {"swing_lookback", "confirm_bos_pips"},
    "strategy/selector": set(),
    "vwap": {"bar_count", "std_dev_multiplier"},
    "volume_at_price": {"bar_count", "num_bins"},
    "setup_probability": {"min_samples"},
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
    "ea_bracket/start": {"buy_order_ticket", "sell_order_ticket", "magic_filter"},
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
    "tools/wait/trade_monitor": {"check_interval_seconds"},
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
            "What: Fetches OHLCV candle data for a symbol/timeframe via the MT5 EA bridge.\n"
            "\n"
            "When: Use for fetching price history for technical analysis, indicator computation, chart visualization, or pattern recognition. Prefer over get_ticks() for historical/candle-level analysis; use get_ticks() for sub-candle precision.\n"
            "\n"
            "Output: {symbol: str, timeframe: str, data: [{timestamp: int, open: float, high: float, low: float, close: float, volume: int, spread: int}], source: 'tcp'|'bridge'}\n"
            "  - Timestamps are broker server time (not UTC). Spread is in points. Volume is tick volume (price changes count).\n"
            "  - Returns {data: []} if symbol invalid or bridge disconnected (no error).\n"
            "\n"
            "Assumptions: Max count=5000. Data includes only regular trading sessions (MT5 fills gaps with last-known values). Close prices are unadjusted. Bridge latency: ~15-25ms TCP, ~200-600ms HTTP fallback. Do NOT call repeatedly in tight loops — cache results.\n"
            "\n"
            "Composition: Primary input for get_indicator(), volatility_profile(), market/regime(), support_resistance(). Call bridge_status() first to verify connectivity. Chain → get_bars() → get_indicator() for full technical analysis."
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
            "What: Computes a technical indicator using MT5's built-in calculation engine.\n"
            "\n"
            "When: Use for computing specific technical indicators (RSI, MACD, EMA, BBands, ATR, ADX, Stochastic, Ichimoku, etc.) for entry/exit signals, crossover detection, or multi-indicator confluence. Prefer over manual Python calculation for accuracy and speed.\n"
            "\n"
            "Output: Shape varies by indicator:\n"
            "  - Single value (no window): {symbol, timeframe, indicator, value: float, data: [float], period: int}\n"
            "  - Series (with window): {symbol, timeframe, indicator, data: [float], period: int} — data[0]=oldest, data[-1]=newest\n"
            "  - MACD: {main: [float], signal: [float], histogram: [float]}\n"
            "  - BBands: {upper: [float], middle: [float], lower: [float]}\n"
            "  - Stoch: {k: [float], d: [float]}. ADX: {adx, plus_di, minus_di}. Ichimoku: {tenkan, kijun, senkou_a, senkou_b, chikou}\n"
            "  - Returns {value: 0, data: []} if symbol invalid (no error).\n"
            "\n"
            "Assumptions: Computed server-side by MT5 terminal. First N values of window series are unreliable (warmup: RSI needs period+1 bars, MACD needs ~100 bars). Use window=N to fetch series in one call instead of looping. Returns empty result for invalid indicator names.\n"
            "\n"
            "Composition: Chains after get_bars() conceptually. Input for market/regime(), volatility_profile(), trading/coach(), multi_timeframe_indicators(). Typical flow: get_bars() → get_indicator() → trading/decision_support() → execute."
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
            "When: Use for precise entry timing, bid/ask spread analysis, micro-price movement detection between candle closes, or tick volume spike confirmation. Prefer over get_bars() for sub-candle granularity.\n"
            "\n"
            "Output: {symbol: str, ticks: [{time: int, bid: float, ask: float, last: float, volume: int, flags: int}], source: 'tcp'|'bridge'}\n"
            "  - Time is Unix timestamp. Spread = ask - bid. Returns empty ticks array when market closed.\n"
            "\n"
            "Assumptions: Only available during market hours. Max lookback limited by MT5 terminal's tick buffer (~few thousand ticks). Max count=2000. Do NOT poll in rapid succession — use resources/market/wait_for_price() for event-driven waits.\n"
            "\n"
            "Composition: Complements get_bars() for sub-candle analysis. Chain → get_ticks() → precise entry timing → submit_market_order(). Use economic_calendar() to verify market is open before calling."
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
            "What: Fetches symbol metadata: contract specs, trading constraints, and pricing parameters.\n"
            "\n"
            "When: Use before calculate_position_size(), validate_trade_setup(), or any execution to get point size, volume limits, stopsLevel (min SL/TP distance), and margin rate. Required for understanding symbol-specific constraints.\n"
            "\n"
            "Output: {symbol: str, point: float, tick_size: float, tick_value: float, volume_min: float, volume_max: float, volume_step: float, stopsLevel: int, spread: int, margin_rate: float, trade_mode: str, ...}\n"
            "  - point: smallest price increment. stopsLevel: min SL/TP distance in points. volume_step: lot size granularity.\n"
            "  - Returns {symbol: '<input>', error: '...'} if not found.\n"
            "\n"
            "Assumptions: Values may change during market open/close transitions. Margin rate is broker-specific. Check for 'error' key in response.\n"
            "\n"
            "Composition: Required input for calculate_position_size(). Use before validate_trade_setup(). Chain → symbol_info() → calculate_position_size() → validate_trade_setup() → execute."
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
            "When: Use for reviewing past trade fills, analyzing entry/exit prices, computing realized P&L, or feeding performance_summary(). Prefer over positions_open() for historical closed trades.\n"
            "\n"
            "Output: {deals: [{deal_id: int, order_id: int, symbol: str, side: str, volume: float, price: float, commission: float, swap: float, profit: float, time: int, type: str, entry: str}], total: int}\n"
            "  - profit in account currency. type: deal_type_buy/sell/balance. entry: deal_entry_in/out/inout.\n"
            "  - Returns empty deals array if no matches (no error).\n"
            "\n"
            "Assumptions: Only includes closed deals (fills), not pending orders. History depth limited by MT5 terminal settings (typically 1-3 months). Default: last 100 deals, 30 days.\n"
            "\n"
            "Composition: Input for performance_summary(). Chain → deals_history() → performance_summary() → trading/insights() for full performance analysis. Use with trading/reflect() to correlate decisions with outcomes."
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
            "When: Use for quantitative performance review — win rate, profit factor, max drawdown, avg holding time. Prefer over manual calculation from deals_history() for pre-aggregated stats.\n"
            "\n"
            "Output: {total_trades: int, winning_trades: int, losing_trades: int, win_rate: float, total_profit: float, avg_win: float, avg_loss: float, profit_factor: float, max_drawdown: float, avg_holding_time: str, ...}\n"
            "  - All monetary values in account currency. win_rate: 0.0-1.0. profit_factor: gross_profit/gross_loss.\n"
            "  - Returns zero-valued metrics if no deals found (no error).\n"
            "\n"
            "Assumptions: Only includes closed deals (realized P&L). Max drawdown is peak-to-trough from the deal series, not account-wide. Default: 100 deals, 30 days.\n"
            "\n"
            "Composition: Consumes deals_history() internally. Input for trading/insights(). Chain → deals_history() → performance_summary() → trading/insights() for progressive analysis."
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
            "When: Use for assessing liquidity depth before large orders, detecting order book imbalances for short-term bias, or verifying current bid/ask spread before market execution. Prefer over get_ticks() for multi-level depth analysis.\n"
            "\n"
            "Output: {symbol: str, bids: [{price: float, volume: float}], asks: [{price: float, volume: float}], timestamp: int}\n"
            "  - bids sorted highest→lowest, asks lowest→highest. Volume in lots. Returns {bids: [], asks: []} for symbols without DOM (no error).\n"
            "\n"
            "Assumptions: Only available for exchange-traded symbols (not all forex pairs support DOM). Snapshot is stale by receipt time — MT5 does not stream real-time DOM. Do NOT poll continuously.\n"
            "\n"
            "Composition: Use with validate_trade_setup() for liquidity assessment. Complements get_ticks() for bid/ask analysis. Chain → get_order_book() → validate_trade_setup() → submit_market_order()."
        ),
        "schema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    },
    "account_summary": {
        "description": (
            "What: Fetches current trading account state (balance, equity, margin, leverage).\n"
            "\n"
            "When: Use before any trade to verify available margin and equity, during active sessions to monitor account health, or as input for calculate_position_size(). Prefer as first call in any trading cycle.\n"
            "\n"
            "Output: {account_id: int, name: str, balance: float, equity: float, margin: float, free_margin: float, currency: str, leverage: int, margin_level: float, ...}\n"
            "  - balance: realized P&L. equity: balance + unrealized P&L. margin_level: (equity/margin)×100. Below 100% = no new positions.\n"
            "\n"
            "Assumptions: Returns partial data with null fields if bridge disconnected. Values reflect real-time state at call time. Currency is account's base currency. Do NOT call in tight loops.\n"
            "\n"
            "Composition: Required input for calculate_position_size() (equity field). Call before any execution tool. Chain → account_summary() → calculate_position_size() → validate_trade_setup() → submit_market_order()."
        ),
        "schema": {"type": "object"},
    },
    "positions_open": {
        "description": (
            "What: Lists all currently open positions with P&L, SL/TP, entry details, health metrics, and time-based exit data.\n"
            "\n"
            "When: Use before placing new orders to assess current exposure, monitoring open position P&L during active trades, or identifying position IDs for SL/TP modifications or closures. Prefer over polling for state awareness.\n"
            "\n"
            "Output: {positions: [{position_id: int, symbol: str, side: str, volume: float, entry_price: float, sl: float, tp: float, mark_price: float, profit: float, swap: float, commission: float, time: int, magic: int, comment: str, health: object, time_health: object}], count: int, sync_status: object}\n"
            "  - mark_price: current market price. profit: unrealized P&L in account currency. sl/tp: 0.0 if not set.\n"
            "  - health: {distance_to_sl_pips, distance_to_tp_pips, pnl_percent_of_risk, time_in_trade_minutes, time_in_trade_bars_h1, is_winning, is_at_breakeven, trail_eligible, spread_cost_pips, profit_multiple_of_spread}\n"
            "  - time_health: {is_registered, bars_elapsed, bars_remaining, min_profit_points, current_profit_points} — requires PositionTimeManager EA\n"
            "  - sync_status: {positions_count, last_sync_age_ms, retry_count, stale_warning} — if stale_warning is true, data was stale on first attempt; reconcile before trading\n"
            "  - Returns empty positions array if none open (no error).\n"
            "\n"
            "Assumptions: Positions from connected account only. magic field identifies strategy/EA. Do NOT poll continuously — use resources/positions/monitor() for event-driven alerts. If sync_status.stale_warning is true, treat as Degraded mode (Phase 1).\n"
            "\n"
            "Composition: Input for modify_position_sl_tp(), close_position(), trailing stop tools. Chain → positions_open() → modify_position_sl_tp() or close_position(). Use portfolio/risk() for portfolio-level view. Use position.health fields for trailing decisions instead of manual computation."
        ),
        "schema": {"type": "object"},
    },
    "orders_pending": {
        "description": (
            "What: Lists all pending (unfilled) orders with type, price, and status.\n"
            "\n"
            "When: Use before placing pending orders to avoid duplicates, monitoring pending order status, or identifying order IDs for cancellation/modification. Prefer over positions_open() for unfilled orders.\n"
            "\n"
            "Output: {orders: [{order_id: int, symbol: str, side: str, kind: str, price: float, volume: float, sl: float, tp: float, time: int, expiration: int, status: str}], count: int}\n"
            "  - kind: 'buy_limit', 'sell_limit', 'buy_stop', 'sell_stop'. status: 'pending', 'partially_filled', etc.\n"
            "  - Returns empty orders array if none pending (no error). Does NOT include open positions.\n"
            "\n"
            "Assumptions: Expired/cancelled orders excluded. Pending orders may fill, expire, or be cancelled between calls. Do NOT poll in tight loops.\n"
            "\n"
            "Composition: Input for cancel_order(), modify_order(). Chain → orders_pending() → cancel_order() or modify_order(). Use ea_bracket/list() for EA-managed brackets."
        ),
        "schema": {"type": "object"},
    },
    "bridge_status": {
        "description": (
            "What: Fetches the MT5 EA bridge heartbeat status (connectivity, trade permission, last heartbeat).\n"
            "\n"
            "When: Use as the FIRST call in any automated trading cycle to verify infrastructure health before data or execution calls. Essential pre-flight check.\n"
            "\n"
            "Output: {connected: bool, login: int, server: str, trade_allowed: bool, last_heartbeat: int, ...}\n"
            "  - last_heartbeat: Unix timestamp. Age > 30s indicates disconnection risk. trade_allowed: false during market close/weekend/terminal error.\n"
            "  - Returns {connected: false} if gateway unreachable (no error raised).\n"
            "\n"
            "Assumptions: Verifies EA connectivity only, not actual trading capability. Does not validate account permissions or broker restrictions.\n"
            "\n"
            "Composition: Call first before any other tool. Chain → bridge_status() → if connected: account_summary() → positions_open() → analysis → execute."
        ),
        "schema": {"type": "object"},
    },
    # === COMPUTATION TOOLS ===
    "volatility_profile": {
        "description": (
            "What: Computes a volatility summary for a symbol, combining ATR and bar-range analysis.\n"
            "\n"
            "When: Use for assessing whether current volatility suits your strategy, setting SL distances via ATR multiples, detecting volatility squeezes preceding breakouts, or comparing volatility across symbols. Prefer over manual ATR calculation.\n"
            "\n"
            "Output: {symbol: str, timeframe: str, atr: {value: float, pct_of_price: float, raw: float}, avg_bar_range: float, max_bar_range: float, min_bar_range: float, spread_analysis: {avg_spread_points: int, max_spread_points: int}, volatility_regime: 'low'|'normal'|'high'|'extreme'}\n"
            "  - atr.pct_of_price enables cross-symbol comparison. volatility_regime is relative to symbol's own historical ATR distribution.\n"
            "\n"
            "Assumptions: Uses get_bars() and get_indicator(atr) internally. Returns partial data if either fails. Spread analysis uses current spread, not historical. Default: lookback=20, atr_period=14.\n"
            "\n"
            "Composition: Input for trading/context(), calculate_position_size() (indirectly via ATR), market/regime(). Chain → volatility_profile() → set appropriate SL distances → validate_trade_setup()."
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
    "analysis/divergence": {
        "description": (
            "What: Detects MACD-price and RSI-price divergence (bullish and bearish) to identify potential trend reversals.\n"
            "\n"
            "When: Use AFTER decision_support() signals a potential entry to confirm with divergence analysis. Use when checking for reversal signals, identifying hidden divergences indicating trend continuation, or avoiding entries into momentum exhaustion. Call as a supplementary signal before executing trades.\n"
            "\n"
            "Output: {bullish: [{type: 'macd_price'|'rsi_price'|'both', strength: 0.3-1.0, swing_low_idx: int, divergence_magnitude: float, description: str}, ...], bearish: [...], summary: {total_bullish: int, total_bearish: int, strongest_signal: str, divergence_score: -10 to +10}}\n"
            "  - divergence_score: negative = bearish bias, positive = bullish bias, magnitude = combined strength\n"
            "  - strength scoring: 0.3 (weak) to 1.0 (strong) based on geometric mean of price + indicator divergence magnitude\n"
            "\n"
            "Assumptions: Pure Python computation from OHLCV bars — computes MACD and RSI internally, zero EA/MQL5 changes needed. Uses swing detection with configurable window. Default: lookback=50, macd_fast=12, macd_slow=26, macd_signal_period=9, rsi_period=14, swing_window=5.\n"
            "\n"
            "Composition: Supplementary signal AFTER trading/decision_support(). Chain: decision_support() → analysis/divergence() → if divergence confirms, proceed with entry. Also useful with volatility_profile() to assess if divergence occurring during squeeze."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "timeframe": {"type": "string"},
                "lookback": {"type": ["number", "string"]},
                "count": {"type": ["number", "string"]},
                "macd_fast": {"type": ["number", "string"]},
                "macd_slow": {"type": ["number", "string"]},
                "macd_signal_period": {"type": ["number", "string"]},
                "rsi_period": {"type": ["number", "string"]},
                "swing_window": {"type": ["number", "string"]},
            },
            "required": ["symbol", "timeframe"],
        },
    },
    "analysis/multi_bar_patterns": {
        "description": (
            "What: Detects structural chart patterns spanning multiple bars — W-Bottom, M-Top, Bollinger Squeeze, Breakout, Gap, and Fibonacci retracement/extension levels.\n"
            "\n"
            "When: Use to identify swing structures that single-bar candlestick patterns miss. Use before placing breakout trades (squeeze detection), reversal trades (W-Bottom/M-Top), or Fibonacci-based entries. Also available integrated in market/chart_intelligence under multi_bar_patterns key.\n"
            "\n"
            "Output: {w_bottom: {status: 'confirmed'|'forming'|'none', score: +5/-5}, m_top: {status: 'confirmed'|'forming'|'none', score: -5/-2}, bollinger_squeeze: {squeezing: bool, score: +3}, breakout: {direction: 'bullish'|'bearish'|'none', score: ±3}, gaps: [{type, pct}], fibonacci: {at_0618: bool, score: +6}, summary: {total_bullish_signals: int, total_bearish_signals: int, net_pattern_score: int}}\n"
            "  - net_pattern_score: sum of all pattern scores. Positive = bullish structural bias, negative = bearish.\n"
            "  - W-Bottom/M-Top confirmed = neckline break occurred. Forming = two legs detected but no break yet.\n"
            "\n"
            "Assumptions: Pure Python from OHLCV bars. Bollinger Bands computed internally if not passed. Fibonacci uses 50-bar swing high/low. Default: period=20 for breakout/squeeze, fib_lookback=50.\n"
            "\n"
            "Composition: Input for market/chart_intelligence (integrated), trading/decision_support() supplement. Chain: analysis/multi_bar_patterns() → if squeeze detected: place_bracket_order(). If W-Bottom confirmed + divergence bullish: high confidence LONG."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "timeframe": {"type": "string"},
                "lookback": {"type": ["number", "string"]},
                "period": {"type": ["number", "string"]},
                "fib_lookback": {"type": ["number", "string"]},
            },
            "required": ["symbol", "timeframe"],
        },
    },
    "analysis/volume_profile": {
        "description": (
            "What: Detects volume anomalies — unusual activity beyond simple volume ratios. Identifies accumulation, distribution, weakness, and selling exhaustion patterns.\n"
            "\n"
            "When: Use to confirm breakouts (volume surge = valid), warn about weak moves (price up + declining volume = skepticism), detect distribution (price down + volume surge = danger), or identify drying-up conditions (low volume = consolidation).\n"
            "\n"
            "Output: {volume_ratio: float, volume_tier: 'extreme_surge'|'strong_surge'|'elevated'|'normal'|'drying_up', volume_trend: 'increasing'|'decreasing'|'stable', price_volume_signal: 'accumulation'|'distribution'|'weakness'|'selling_exhaustion'|'none', score: int, anomalies: [{bar_index, volume, ratio, price_change_pct}]}\n"
            "  - score: positive = bullish volume, negative = bearish volume\n"
            "  - anomalies: bars where volume exceeded 3x average\n"
            "\n"
            "Assumptions: MT5 tick volume (trade count). Thresholds adapted from legacy system for forex/CFD. Default: lookback=20.\n"
            "\n"
            "Composition: Input for trading/decision_support() to validate entry quality, breakout confirmation. Chain: analysis/volume_profile() → if score > 4 + breakout, high confidence entry. If score < -3 + entry signal, reduce size or wait."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "timeframe": {"type": "string"},
                "lookback": {"type": ["number", "string"]},
            },
            "required": ["symbol", "timeframe"],
        },
    },
    "analysis/momentum": {
        "description": (
            "What: Computes momentum penalty to detect chase risk — prevents entering at exhaustion points (buying tops, selling bottoms).\n"
            "\n"
            "When: Use BEFORE executing any trade to check if current momentum suggests chasing. Use when price has made a large move, when RSI is extreme (>80 or <20), or when you want to validate entry quality. This is a risk filter, not a signal generator.\n"
            "\n"
            "Output: {chase_tier: 'extreme_chase'|'strong_chase'|'moderate_chase'|'normal', chase_penalty: 0 to -12, range_atr_ratio: float, exhaustion_risk: bool, exhaustion_penalty: 0 or -4, rsi_signal: 'overbought'|'elevated'|'neutral'|'approaching_oversold'|'oversold', rsi_penalty: -4 to +4, total_penalty: int, recommendation: 'avoid_entry'|'reduce_size'|'caution'|'normal'}\n"
            "  - total_penalty: negative = chase risk, positive = contrarian opportunity\n"
            "  - recommendation: 'avoid_entry' = do not enter, 'reduce_size' = enter with 50% size, 'caution' = verify with other signals\n"
            "\n"
            "Assumptions: Uses ATR-based thresholds adapted for forex/CFD (not A-share limits). Requires bars data. RSI and ATR optional but improve accuracy. Default: lookback=50.\n"
            "\n"
            "Composition: Final gate BEFORE trade execution. Chain: opportunity_rank() → analysis/divergence() → analysis/volume_profile() → analysis/momentum() → if recommendation != 'avoid_entry', proceed. If total_penalty <= -6, reduce position size by 50%."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "timeframe": {"type": "string"},
                "lookback": {"type": ["number", "string"]},
                "rsi": {"type": ["number", "string"]},
                "atr": {"type": ["number", "string"]},
            },
            "required": ["symbol", "timeframe"],
        },
    },
    "multi_timeframe_indicators": {
        "description": (
            "What: Computes a single indicator across multiple timeframes in one call.\n"
            "\n"
            "When: Use for checking indicator alignment across timeframes for confluence, confirming higher timeframe trend before lower timeframe entries, or detecting timeframe divergence. Prefer over calling get_indicator() N times.\n"
            "\n"
            "Output: {symbol: str, indicator: str, readings: {timeframe: {value: float, data: [float], ...}}}\n"
            "  - Each timeframe's value matches get_indicator()'s single-value output. Failed timeframes contain {error: str}.\n"
            "\n"
            "Assumptions: Each timeframe computed independently (sequential, not parallelized). Max 8 timeframes. Total latency ≈ N × single-call latency. All timeframes use same indicator parameters. Default indicator params apply.\n"
            "\n"
            "Composition: Batch alternative to N × get_indicator(). Input for confluence analysis. Chain → multi_timeframe_indicators() → confirm alignment → trading/decision_support() → execute."
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
            "When: Use before opening positions on multiple symbols to assess combined risk, detecting hidden correlation in a diversified portfolio, or adjusting position sizes for correlated exposures. Require ≥2 symbols.\n"
            "\n"
            "Output: {timeframe: str, lookback: int, matrix: {symbol_a: {symbol_b: float, ...}, ...}}\n"
            "  - Correlation values -1.0 to 1.0. Diagonal (self-correlation) always 1.0.\n"
            "\n"
            "Assumptions: Uses percentage returns (not raw prices) for stationarity. Min 2, max 10 symbols. If symbols have different trading hours, correlation may be artificially low. Default: timeframe=H1, lookback=50.\n"
            "\n"
            "Composition: Input for portfolio/risk() correlation analysis. Chain → correlation_matrix() → if |corr| > 0.7, reduce position sizes → validate_trade_setup() with correlation_warning."
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
            "When: Use for identifying entry zones near support (buys) or resistance (sells), setting SL beyond S/R for invalidation-based exits, placing bracket order triggers beyond key levels, or setting TP at next resistance/support.\n"
            "\n"
            "Output: {symbol: str, support_levels: [float], resistance_levels: [float], method: 'swing_highs_lows', timeframe: str, lookback: int}\n"
            "  - Levels sorted by proximity to current price (closest first). Empty arrays if insufficient data. Min lookback=20, default=100.\n"
            "\n"
            "Assumptions: Uses swing window of max(2, min(5, lookback/10)) bars. Levels are price points (not zones with width). Does NOT use volume profile, pivots, or Fibonacci — only price-based swing detection. Not reliable in strongly trending markets.\n"
            "\n"
            "Composition: Takes get_bars() data internally. Input for SL/TP placement, bracket order triggers, breakout levels. Chain → support_resistance() → use levels for entry/SL/TP → place_bracket_order() or submit_pending_order()."
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
            "What: Classifies the current market state into one of four regimes: ranging, trending_up, trending_down, or compressing.\n"
            "\n"
            "When: Use before every trade to select appropriate strategy for current conditions, filter trade signals (only trend trades in trending regimes), or adjust position sizing (reduce in ranging/compressing). Essential pre-trade check.\n"
            "\n"
            "Output: {symbol: str, timeframe: str, regime: 'ranging'|'trending_up'|'trending_down'|'compressing', confidence: float, adx: float, ema_fast: float, ema_slow: float, atr: float}\n"
            "  - confidence: 0.0-1.0 based on ADX strength and EMA separation. compressing = low volatility squeeze.\n"
            "  - Returns {regime: 'ranging', confidence: 0.0} if data insufficient.\n"
            "\n"
            "Assumptions: Uses ADX threshold + EMA(20)/EMA(50) crossover logic. Regime valid only for specified timeframe — a symbol can be trending on H4 and ranging on M15. Default: lookback=20, atr_period=14.\n"
            "\n"
            "Composition: Input for trading/coach(), trading/decision_support(), strategy selection. Chain → market/regime() → select strategy → trading/coach() → execute."
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
            "When: Use for screening a watchlist of symbols for trading opportunities, identifying which symbols are in favorable regimes, quick market overview before a trading session, or filtering symbols by volatility. Max 20 symbols.\n"
            "\n"
            "Output: {symbols: [{symbol: str, price: float, atr: float, atr_pct: float, regime: str, recommendation: str}], timeframe: str}\n"
            "  - recommendation: informational suggestion based on regime+ATR (not a trade signal). Invalid symbols return {symbol, error: str}.\n"
            "\n"
            "Assumptions: Sequential processing — latency scales linearly with symbol count. Recommendation is informational, not prescriptive. Default: timeframe=H1, atr_period=14.\n"
            "\n"
            "Composition: Batch alternative to get_bars() + market/regime() per symbol. Chain → market/scan() → filter by regime → trading/context() on selected symbols → execute."
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
            "What: Captures a screenshot of the MT5 chart for a symbol/timeframe as base64-encoded PNG.\n"
            "\n"
            "When: Use for visual verification of chart patterns/indicators, generating visual reports for human review, or debugging indicator overlay configurations. NOT for automated price analysis — use get_bars()/get_indicator() instead.\n"
            "\n"
            "Output: {image_base64: str, content_type: 'image/png'}\n"
            "  - image_base64 is full PNG encoded as base64 (200KB-2MB). Returns empty string if capture fails (no error).\n"
            "\n"
            "Assumptions: Screenshot reflects chart as configured in MT5 terminal (indicators, templates applied by EA). If MT5 is minimized or headless, image may be blank/low-quality. Latency: 1-5 seconds. Default: 1280×720.\n"
            "\n"
            "Composition: Takes symbol+timeframe from get_bars() context. Feeds into visual analysis pipelines or Telegram reports. Chain → get_chart_screenshot() → visual analysis → decision."
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
            "When: Use before every trade to determine correct position size for risk management, when SL distance is known and risk_percent is defined by strategy, or comparing position sizes across symbols with different contract specs.\n"
            "\n"
            "Output: {symbol: str, lot_size: float, dollar_risk: float, risk_reward_ratio: float, pip_value: float, margin_required: float, warnings: [str]}\n"
            "  - lot_size: rounded to symbol's volume_step. Warnings include: lot exceeds max_volume, SL too close to stopsLevel, risk > 5% equity.\n"
            "  - Formula: lot_size = (equity × risk% / 100) / |entry - SL| / tick_value, rounded to volume_step.\n"
            "\n"
            "Assumptions: Does NOT account for slippage, commissions, swap, or gap risk. Single position only — no portfolio-level correlation adjustments. If entry == SL, returns lot_size: 0. Uses live symbol_info() for contract specs. If equity null, fetches from account_summary().\n"
            "\n"
            "Composition: Takes equity from account_summary(). Uses symbol_info() for contract specs. Output feeds submit_market_order_via_bridge(volume_lots). Chain → account_summary() → calculate_position_size() → validate_trade_setup() → execute."
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
            "When: MANDATORY pre-flight check before any order submission. Use for verifying SL/TP distances comply with broker's stopsLevel, checking margin requirements, or detecting correlated portfolio exposure.\n"
            "\n"
            "Output: {symbol: str, bid: float, ask: float, valid: bool, errors: [str], warnings: [str], required_margin: float, correlation_warning: {has_exposure: bool, same_symbol_positions: int, correlated_positions: [{symbol, correlation, existing_volume}], warning: str|null}}\n"
            "  - valid: true only if errors array empty. correlation_warning: warns if existing positions on same/correlated symbol (>0.7).\n"
            "\n"
            "Assumptions: Checks against LIVE broker constraints (stopsLevel, min/max volume, margin). Market price fetched at call time — may differ from execution price. Does NOT validate strategy logic (only mechanical constraints). correlation_warning uses static correlation matrix for major forex pairs.\n"
            "\n"
            "Composition: Call after calculate_position_size(), before any submit_*_order. Chain → calculate_position_size() → validate_trade_setup() → if valid: submit_market_order_via_bridge()."
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
    "portfolio/risk": {
        "description": (
            "What: Portfolio-wide risk analysis — aggregates exposure, concentration, and correlation across all open positions.\n"
            "\n"
            "When: Use before adding new positions to assess portfolio-wide impact, during periodic portfolio review to identify concentration risk, or when understanding net directional exposure (USD-long vs USD-short). Prefer over validate_trade_setup() for multi-position analysis.\n"
            "\n"
            "Output: {total_exposure_usd: float, net_exposure_usd: float, exposure_by_symbol: [{symbol: str, exposure_usd: float, net_exposure_usd: float, margin_usd: float}], risk_metrics: {concentration_ratio: float, max_single_position_pct: float, correlated_pairs: [{symbol_a: str, symbol_b: str, correlation: float}]}}\n"
            "  - total_exposure_usd: sum of absolute correlated exposure. correlated_pairs: pairs with |corr| > 0.5.\n"
            "  - Returns zeroed output if no open positions.\n"
            "\n"
            "Assumptions: Uses static correlation matrix for major forex pairs. Symbols outside matrix treated as uncorrelated. Default: 7 days lookback, 100 position limit.\n"
            "\n"
            "Composition: Uses positions_open() and account_summary() internally. Complements validate_trade_setup() (single-position vs portfolio-level). Chain → portfolio/risk() → if concentration high → reduce new position size → execute."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": ["string", "null"]},
                "days": {"type": ["number", "string"]},
                "limit": {"type": ["number", "string"]},
            },
            "required": [],
        },
    },
    # === EXECUTION TOOLS ===
    "submit_market_order_via_bridge": {
        "description": (
            "What: Submits a market order through the MT5 EA bridge with full audit trail.\n"
            "\n"
            "When: Use for immediate market entry when signal conditions are met, executing trades with auto-trailing stop via trail_config, or placing orders with pre-attached SL/TP for risk-managed entries.\n"
            "\n"
            "Output: {intent_id: str, status: 'submitted'|'error', adapter: str, broker_order_id: str, retcode: str, message: str, raw: object}\n"
            "  - retcode: MT5 trade return code ('DONE', 'REJECTED', 'INVALID_STOPS', 'NO_MONEY', etc.). On error, broker_order_id may be empty.\n"
            "\n"
            "Assumptions: Gated by TradingPolicy engine (daily trade limit, loss limit, etc.). TCP bridge: ~15-25ms, HTTP fallback: ~200-600ms. SL/TP attached simultaneously with entry. Does NOT validate SL/TP against stopsLevel — use validate_trade_setup() first.\n"
            "\n"
            "Composition: Takes volume_lots from calculate_position_size(). Must call validate_trade_setup() first. Chain → validate_trade_setup() → if valid: submit_market_order_via_bridge() → trading/log_decision()."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "intent_id": {"type": "string"},
                "strategy_id": {"type": "string"},
                "account_id": {"type": "string"},
                "symbol": {"type": "string"},
                "side": {"type": "string", "enum": ["buy", "sell"]},
                "order_kind": {
                    "type": "string",
                    "enum": ["market", "limit", "stop", "stop_limit"],
                },
                "volume_lots": {"type": ["number", "string"]},
                "deviation_points": {"type": ["number", "string"]},
                "sl": {"type": ["number", "string", "null"]},
                "tp": {"type": ["number", "string", "null"]},
                "trail_config": {
                    "type": "object",
                    "properties": {
                        "atr_multiplier": {"type": "number", "default": 2.0},
                        "lock_profit_atr": {"type": "number", "default": 1.0},
                        "check_interval_seconds": {"type": "integer", "default": 10},
                        "atr_timeframe": {"type": "string", "default": "H1"},
                        "atr_period": {"type": "integer", "default": 14},
                    },
                    "description": (
                        "Auto-trailing stop configuration. If provided, trailing activates "
                        "immediately after order fill. EA-side — persistent, survives all restarts. "
                        "ATR timeframe defaults to H1 but can be customized per position (M15 for faster trailing on volatile instruments, H4 for slower trailing on trends, D1 for swing positions). "
                        "EA expects flat JSON fields."
                    ),
                },
            },
            "required": ["intent_id", "symbol", "side", "volume_lots"],
        },
    },
    "submit_market_order": {
        "description": (
            "Legacy alias. Prefer submit_market_order_via_bridge for consistency.\n"
            "\n"
            "What: Submits a market order (alternate endpoint, functionally identical to submit_market_order_via_bridge).\n"
            "\n"
            "When: Same scenarios as submit_market_order_via_bridge — immediate market entry with audit trail. Pick one endpoint for consistency; do NOT use both in the same strategy.\n"
            "\n"
            "Output: {intent_id: str, status: 'submitted'|'error', adapter: str, broker_order_id: str, retcode: str, message: str, raw: object} — identical to submit_market_order_via_bridge.\n"
            "\n"
            "Assumptions: Functionally identical to submit_market_order_via_bridge. Both route through same execution gateway. Subject to TradingPolicy gates.\n"
            "\n"
            "Composition: Same chain as submit_market_order_via_bridge: validate_trade_setup() → submit_market_order() → trading/log_decision()."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "intent_id": {"type": "string"},
                "strategy_id": {"type": "string"},
                "account_id": {"type": "string"},
                "symbol": {"type": "string"},
                "side": {"type": "string", "enum": ["buy", "sell"]},
                "order_kind": {
                    "type": "string",
                    "enum": ["market", "limit", "stop", "stop_limit"],
                },
                "volume_lots": {"type": ["number", "string"]},
                "deviation_points": {"type": ["number", "string"]},
                "sl": {"type": ["number", "string", "null"]},
                "tp": {"type": ["number", "string", "null"]},
                "trail_config": {
                    "type": "object",
                    "properties": {
                        "atr_multiplier": {"type": "number", "default": 2.0},
                        "lock_profit_atr": {"type": "number", "default": 1.0},
                        "check_interval_seconds": {"type": "integer", "default": 10},
                        "atr_timeframe": {"type": "string", "default": "H1"},
                        "atr_period": {"type": "integer", "default": 14},
                    },
                    "description": (
                        "Auto-trailing stop configuration. If provided, trailing activates "
                        "immediately after order fill. EA expects flat JSON fields."
                    ),
                },
                **_OWNERSHIP_PROPERTIES,
            },
            "required": [
                "intent_id",
                "symbol",
                "side",
                "volume_lots",
            ],
        },
    },
    "submit_pending_order": {
        "description": (
            "What: Submits a pending (limit or stop) order to the MT5 terminal.\n"
            "\n"
            "When: Use for placing limit orders at support/resistance levels for better entry prices, setting stop orders for breakout entries, or scheduling entries at specific price levels without monitoring the market.\n"
            "\n"
            "Output: {status: 'placed'|'error', order_id: int|null, message: str, raw: object}\n"
            "  - order_id: MT5 order ticket number for use with modify_order() and cancel_order(). Null on error.\n"
            "\n"
            "Assumptions: Subject to stopsLevel constraints — price too close to current market will be rejected. SL/TP attach when order fills, not when placed. Order may expire per broker's expiration policy.\n"
            "\n"
            "Composition: Input price from support_resistance() levels. Chain → support_resistance() → validate_trade_setup(order_kind='limit'/'stop') → submit_pending_order() → ea_bracket/start() for OCO management."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "side": {"type": "string", "enum": ["buy", "sell"]},
                "kind": {"type": "string", "enum": ["limit", "stop"]},
                "price": {"type": ["number", "string"]},
                "volume_lots": {"type": ["number", "string"]},
                "sl": {"type": ["number", "string", "null"]},
                "tp": {"type": ["number", "string", "null"]},
                "deviation": {"type": ["number", "string"]},
                "trail_config": {
                    "type": "object",
                    "properties": {
                        "atr_multiplier": {"type": "number", "default": 2.0},
                        "lock_profit_atr": {"type": "number", "default": 1.0},
                        "check_interval_seconds": {"type": "integer", "default": 10},
                        "atr_timeframe": {"type": "string", "default": "H1"},
                        "atr_period": {"type": "integer", "default": 14},
                    },
                    "description": (
                        "Auto-trailing stop configuration. If provided, trailing activates "
                        "immediately after order fill. EA-side — persistent, survives all restarts. "
                        "ATR timeframe defaults to H1 but can be customized per position (M15 for faster trailing on volatile instruments, H4 for slower trailing on trends, D1 for swing positions). "
                        "EA expects flat JSON fields."
                    ),
                },
                **_OWNERSHIP_PROPERTIES,
            },
            "required": ["symbol", "side", "kind", "price", "volume_lots"],
        },
    },
    "modify_order": {
        "description": (
            "What: Modifies a pending order's price, stop-loss, or take-profit.\n"
            "\n"
            "When: Use for adjusting pending order trigger price as market conditions change, updating SL/TP on pending orders before they fill, or moving limit orders closer to market as price approaches.\n"
            "\n"
            "Output: {status: 'modified'|'error', message: str, raw: object}\n"
            "\n"
            "Assumptions: Only works on PENDING orders, NOT open positions (use modify_position_sl_tp() for those). New price must respect stopsLevel distance from current market. All null fields = no modification. If order already filled, returns error.\n"
            "\n"
            "Composition: Use after orders_pending() to identify target order_id. Chain → orders_pending() → modify_order() → verify via orders_pending()."
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
            "When: Use for moving SL to breakeven after favorable price movement, adjusting TP targets based on new support/resistance levels, or implementing manual trailing stop by progressively moving SL.\n"
            "\n"
            "Output: {status: 'modified'|'error', message: str, raw: object}\n"
            "\n"
            "Assumptions: Works on OPEN positions only (use modify_order() for pending orders). SL/TP must respect stopsLevel from current market price. Setting sl=null may remove stop entirely (broker-dependent). Partial modifications allowed (change SL but keep TP).\n"
            "\n"
            "Composition: Input position_id from positions_open(). Use with trailing stop tools for automated management. Chain → positions_open() → modify_position_sl_tp() → verify via positions_open()."
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
            "When: Use for exiting a specific position based on target hit or invalidation, partial profit-taking by closing a portion of a large position, or emergency exit of a single problematic position.\n"
            "\n"
            "Output: {status: 'closed'|'error', message: str, deal_id: int, close_price: float, raw: object}\n"
            "  - close_price is the execution price (may differ from mark_price due to slippage).\n"
            "\n"
            "Assumptions: Partial close: volume must be ≤ position's current volume and ≥ volume_min. Remaining volume after partial close must still meet volume_min or position fully closes. Closing realizes P&L immediately (affects balance). Null/0 volume = full close.\n"
            "\n"
            "Composition: Input position_id from positions_open(). Chain → positions_open() → close_position() → trading/log_decision(action='exit', outcome). Use before close_all_positions() for selective exits."
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
            "When: Use for emergency flattening during extreme market events, end-of-day position closure for day trading strategies, or risk circuit breaker when drawdown exceeds thresholds.\n"
            "\n"
            "Output: {closed: [{position_id: int, status: str, message: str}], failed: [{position_id: int, error: str}], summary: {total_attempted: int, total_closed: int, total_failed: int}}\n"
            "  - Individual failures reported in failed array; tool continues closing remaining positions.\n"
            "\n"
            "Assumptions: Positions closed sequentially — market conditions may change between closures. Timeout: 60 seconds. Does NOT cancel pending orders (use cancel_all_orders() separately).\n"
            "\n"
            "Composition: Emergency exit tool. Chain → positions_open() (verify scope) → close_all_positions() → cancel_all_orders() (full flat) → trading/log_decision()."
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
            "When: Use for cancelling a specific pending order that is no longer valid, removing the unfilled leg of a bracket order after one side fills, or cleaning up stale pending orders before placing new ones.\n"
            "\n"
            "Output: {status: 'cancelled'|'error', message: str, raw: object}\n"
            "\n"
            "Assumptions: Only cancels PENDING orders — already-filled orders cannot be cancelled (use close_position()). If order doesn't exist or already filled, returns error.\n"
            "\n"
            "Composition: Input order_id from orders_pending(). Chain → orders_pending() → cancel_order() → verify via orders_pending(). For EA-managed brackets, check ea_bracket/list() first — may need ea_bracket/stop() instead."
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
            "When: Use for flushing all pending orders before strategy reset or regime change, emergency cancellation during market events, or cleaning up stale orders at end of trading session.\n"
            "\n"
            "Output: {cancelled: [{order_id: int, status: str}], failed: [{order_id: int, error: str}], summary: {total_attempted: int, total_cancelled: int, total_failed: int}}\n"
            "\n"
            "Assumptions: Timeout: 60 seconds. Does NOT close open positions (use close_all_positions() separately). Individual failures reported in failed array.\n"
            "\n"
            "Composition: Chain → orders_pending() (verify scope) → cancel_all_orders() → verify via orders_pending(). For EA brackets, check ea_bracket/list() and stop them first."
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
            "What: Manually computes and applies a one-shot trailing stop to an open position based on current market price.\n"
            "\n"
            "When: Use for one-time manual trailing SL adjustment during active position management, fine-grained control over trailing distance without server-side automation, or ad-hoc SL updates based on real-time price action.\n"
            "\n"
            "Output: {computed_sl: float, result: {status: 'modified'|'error', ...}}\n"
            "\n"
            "Assumptions: ONE-SHOT operation — does NOT create persistent trailing stop. Buy: new_sl = current_ask - distance_points (bounded by entry + lock_in_points). Sell: new_sl = current_bid + distance_points. Requires live order book data.\n"
            "\n"
            "Composition: Alternative to set_trailing_stop() for manual control. Chain → positions_open() → get_order_book() (for bid/ask) → trail_position() → verify via positions_open(). For persistent trailing, use set_trailing_stop() instead."
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
            "When: Use for capturing breakouts from consolidation zones with bidirectional entries, trading news events where direction is uncertain but volatility is expected, or setting up straddle strategies around key support/resistance levels.\n"
            "\n"
            "Output: {buy_order_id: str|null, sell_order_id: str|null, status: 'placed'|'partial'|'error', message: str, atr_used: float, computed_sl_buy: float, computed_tp_buy: float, computed_sl_sell: float, computed_tp_sell: float}\n"
            "  - status 'partial' = one leg placed, other failed. SL/TP computed as: trigger ± (ATR × multiplier).\n"
            "\n"
            "Assumptions: Both orders placed independently — one may fail while other succeeds. ATR fetched internally using ATR(14) on H1. When one leg fills, the other must be manually cancelled (no auto-OCO in MT5). Requires buy_trigger > current ask and sell_trigger < current bid.\n"
            "\n"
            "Composition: Input triggers from support_resistance() levels. Chain → support_resistance() → place_bracket_order() → ea_bracket/start() (for auto-OCO) → cancel unfilled leg after one fills."
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
    # === EA-NATIVE BRACKET ORDER TOOLS ===
    "ea_bracket/start": {
        "description": (
            "What: Registers a pair of pending orders with the EA's native OCO bracket manager. When one leg fills, the EA automatically cancels the sibling. Survives MCP/gateway instability.\n"
            "\n"
            "When: Use immediately after placing bracket orders to enable automatic OCO management, setting up brackets that must survive MCP/gateway restarts, or when you need the EA to automatically cancel the sibling leg on fill.\n"
            "\n"
            "Output: {success: bool, message: str}\n"
            "\n"
            "Assumptions: Orders MUST already exist in MT5 before calling — use place_bracket_order() or submit_pending_order() first. bracket_id embedded in order comments for recovery after EA restart. EA must be running and connected. Use '0' for single-leg bracket.\n"
            "\n"
            "Composition: Call immediately after place_bracket_order() succeeds. Chain → place_bracket_order() → ea_bracket/start() → ea_bracket/tick() (periodic processing). Requires ea_bracket/tick() for OCO processing."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "buy_order_ticket": {"type": ["number", "string"]},
                "sell_order_ticket": {"type": ["number", "string"]},
                "bracket_id": {"type": "string"},
                "comment": {"type": ["string", "null"]},
                "magic_filter": {"type": ["number", "string", "null"]},
                **_OWNERSHIP_PROPERTIES,
            },
            "required": ["buy_order_ticket", "sell_order_ticket", "bracket_id"],
        },
    },
    "ea_bracket/stop": {
        "description": (
            "What: Stops and removes an EA-native bracket order. Cancels both legs if still pending and removes from tracking.\n"
            "\n"
            "When: Use for aborting a bracket strategy when market conditions change, cleaning up brackets before end of trading session, or emergency stop when bracket triggers are no longer valid.\n"
            "\n"
            "Output: {success: bool, message: str}\n"
            "\n"
            "Assumptions: Only cancels pending legs — already-filled legs are not affected. Removing a non-existent bracket returns error.\n"
            "\n"
            "Composition: Chain → ea_bracket/list() (verify exists) → ea_bracket/stop() → verify via ea_bracket/list(). Use to abandon bracket setup before either leg fills."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "bracket_id": {"type": "string"},
                **_OWNERSHIP_PROPERTIES,
            },
            "required": ["bracket_id"],
        },
    },
    "ea_bracket/list": {
        "description": (
            "What: Lists all active EA-native bracket order pairs being managed by the EA.\n"
            "\n"
            "When: Use for verifying bracket registration succeeded after ea_bracket/start(), auditing all active EA-managed brackets for health and status, or before ea_bracket/stop() to confirm bracket_id exists.\n"
            "\n"
            "Output: {brackets: [{bracket_id: str, buy_ticket: str, sell_ticket: str, magic_filter: int, created_at: str, buy_exists: bool, sell_exists: bool}], count: int}\n"
            "  - buy_exists/sell_exists reflect current pending order status. Returns empty array if none active (no error).\n"
            "\n"
            "Assumptions: buy_exists/sell_exists may not mean orders are still pending — cross-reference with orders_pending(). Mismatched exists flags indicate order may have filled or been cancelled.\n"
            "\n"
            "Composition: Chain → ea_bracket/list() → verify bracket health → ea_bracket/tick() (process) or ea_bracket/stop() (remove). Cross-reference with orders_pending() for order existence."
        ),
        "schema": {"type": "object"},
    },
    "ea_bracket/tick": {
        "description": (
            "What: Processes all active EA-native brackets, checking for fills and auto-cancelling orphan legs.\n"
            "\n"
            "When: Use for periodic processing of EA-native brackets for OCO auto-cancellation, as part of the main trading loop when EA brackets are active, or monitoring bracket fill events and orphan leg cancellation.\n"
            "\n"
            "Output: {processed: int, events: [{bracket_id: str, filled_leg: str, filled_ticket: str, cancelled_ticket: str, fill_price: float}], errors: int, active: int}\n"
            "\n"
            "Assumptions: Must be called periodically to activate OCO processing. If not called, EA's OnTimer() still processes brackets independently. Returns {processed: 0} if no brackets active.\n"
            "\n"
            "Composition: Call on a schedule after ea_bracket/start(). Chain → ea_bracket/list() → ea_bracket/tick() → handle events. Alternative to polling orders_pending() + cancel_order() for OCO management."
        ),
        "schema": {"type": "object"},
    },
    # === TRAILING STOP TOOLS ===
    "set_trailing_stop": {
        "description": (
            "LEGACY: Server-side automated trailing stop. Prefer trail_config in submit_market_order_via_bridge() or submit_pending_order() for persistent, EA-side trailing that survives restarts.\n"
            "\n"
            "What: Starts a server-side automated trailing stop for an open position.\n"
            "\n"
            "When: Use ONLY when position was submitted without trail_config. For new positions, always use trail_config in the order submission instead.\n"
            "\n"
            "Output: {position_id: str, status: 'active'|'stopped'|'error', message: str, initial_sl: float|null}\n"
            "\n"
            "Assumptions: Server-side — trailing logic runs on MCP server, not MT5. LOST on MCP server restart. Requires MCP server to stay running and periodic trailing_stop/tick() calls. SL only moved in profitable direction (never widened). Initial SL = entry ± (ATR × distance_atr_multiplier). Does NOT work on pending orders. Default: distance=1.0 ATR, interval=10s, lock_in=1.0 ATR.\n"
            "\n"
            "Composition: FALLBACK method. Chain → positions_open() → set_trailing_stop() → trailing_stop/tick() (periodic) → trailing_stop/list() (monitor). PRIMARY method: use trail_config in order submission."
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
            "When: Use for periodic processing of all active trailing stops (every 10-30 seconds recommended), updating SL positions based on latest market prices, or as part of the main trading loop when server-side trailing stops are active.\n"
            "\n"
            "Output: {processed: int, updated: [{position_id: int, old_sl: float, new_sl: float}], errors: [{position_id: int, error: str}]}\n"
            "\n"
            "Assumptions: Must be called periodically — trailing stops do NOT update automatically (no background thread). Returns {processed: 0} if no trailing stops active.\n"
            "\n"
            "Composition: Call on a schedule after set_trailing_stop(). Chain → set_trailing_stop() → loop: trailing_stop/tick() every 10-30s → trailing_stop/list() (monitor). Complements trailing_stop/list() for monitoring."
        ),
        "schema": {"type": "object"},
    },
    "trailing_stop/cancel": {
        "description": (
            "What: Cancels an active server-side trailing stop for a position.\n"
            "\n"
            "When: Use for transitioning from automated trailing to manual SL management, disabling trailing stop when market regime changes from trending to ranging, or before closing a position to clean up trailing stop state.\n"
            "\n"
            "Output: {position_id: str, status: 'cancelled'|'error', message: str}\n"
            "\n"
            "Assumptions: Does NOT close the position — only stops the automated trailing. Position's SL remains at its last updated value. Canceling a non-existent trailing stop returns error.\n"
            "\n"
            "Composition: Chain → trailing_stop/list() (verify active) → trailing_stop/cancel() → positions_open() (verify SL unchanged). Use when manually taking over SL management."
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
            "When: Use for auditing which positions have active server-side trailing stops, monitoring trailing stop health and last update timestamps, or before calling trailing_stop/tick() to confirm there are stops to process.\n"
            "\n"
            "Output: {trailing_stops: [{position_id: int, symbol: str, side: str, distance_atr_multiplier: float, current_sl: float, initial_sl: float, last_update: int, check_interval: int}], count: int}\n"
            "  - last_update is Unix timestamp of last SL modification. Returns empty list if none active (no error).\n"
            "\n"
            "Assumptions: Data reflects server-side state only (not MT5 terminal state). Trailing stops do NOT persist across MCP server restarts.\n"
            "\n"
            "Composition: Chain → trailing_stop/list() → trailing_stop/tick() (process) or trailing_stop/cancel() (remove). Cross-reference with positions_open() to verify positions still exist."
        ),
        "schema": {"type": "object"},
    },
    # === LONG-POLLING TOOLS ===
    "resources/market/wait_for_price": {
        "description": (
            "What: Long-polling price alert: holds HTTP connection open until a price condition is met or timeout occurs.\n"
            "\n"
            "When: Use for waiting for price to reach a specific level before entering a trade, event-driven entry without manual price polling, or monitoring price levels for breakout/reversal setups.\n"
            "\n"
            "Output: {symbol: str, condition: str, trigger_price: float, actual_price: float, triggered: bool, timed_out: bool}\n"
            "  - triggered: true if condition met before timeout. timed_out: true if timeout elapsed. actual_price: price at trigger (or last sampled on timeout).\n"
            "\n"
            "Assumptions: Blocks HTTP connection for up to timeout_seconds. Price sampled at ~1-second intervals. If bridge disconnects during wait, returns {triggered: false, timed_out: true}. Not suitable for high-frequency monitoring.\n"
            "\n"
            "Composition: Alternative to polling get_ticks() in a loop. Chain → support_resistance() → resources/market/wait_for_price() → on trigger: submit_market_order(). Use for event-driven entry triggers."
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
            "What: Long-polling position monitor: holds connection open until a P&L or price alert fires, or timeout occurs.\n"
            "\n"
            "When: Use for monitoring a position's P&L without constant polling, setting price-based alerts for active positions, or hands-free waiting between active trading cycles.\n"
            "\n"
            "Output: {position_id: str, alert_type: 'pnl'|'price'|'timeout'|'closed', current_pnl: float|null, current_price: float|null, triggered_value: float|null, timed_out: bool}\n"
            "  - alert_type 'closed' means position was closed during monitoring. triggered_value is the P&L or price that triggered.\n"
            "\n"
            "Assumptions: Blocks HTTP connection for up to timeout_seconds. Samples position state at ~5-second intervals. If position closed externally, alert_type='closed'. If position_id doesn't exist, returns immediately with alert_type='closed'.\n"
            "\n"
            "Composition: Alternative to polling positions_open(). Chain → positions_open() → resources/positions/monitor() → on alert: take action (close, modify SL, etc.). Use for hands-free position monitoring."
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
            "What: Pauses execution for a specified duration. Use in trading loops to wait between analysis cycles.\n"
            "\n"
            "When: Use for pausing between analysis cycles in a trading loop, waiting for a specific time interval before re-evaluating market conditions, or cooldown period after a trade before next signal check.\n"
            "\n"
            "Output: {waited_seconds: int, resumed_at: str}\n"
            "  - resumed_at is ISO 8601 UTC timestamp.\n"
            "\n"
            "Assumptions: Blocks the connection for the full duration. For event-driven waits (price levels, indicator values), use tools/wait/indicator instead. Default: 60s. Range: 1-3600s.\n"
            "\n"
            "Composition: Use in loops: analyze → tools/wait/delay(300) → analyze again. Alternative to polling get_bars() repeatedly. Chain → market analysis → tools/wait/delay() → re-analyze."
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
            "When: Use for waiting for specific indicator conditions before entry (e.g., RSI oversold), event-driven trading without manual polling loops, or confirming indicator crossovers/threshold breaches.\n"
            "\n"
            "Output: {symbol: str, indicator: str, condition: str, target_value: float, actual_value: float|null, triggered: bool, timed_out: bool}\n"
            "  - triggered: true if condition met before timeout. actual_value: indicator value at trigger (or last sampled on timeout).\n"
            "\n"
            "Assumptions: Blocks HTTP connection for up to timeout_seconds. Indicator sampled at check_interval_seconds intervals. 'equals' uses 0.1% tolerance. 'crosses' returns immediately on first update (may be noise). Default: timeout=300s, interval=5s.\n"
            "\n"
            "Composition: Use for event-driven entries: 'wait until RSI drops below 30, then buy'. Chain → get_indicator() (check current value) → tools/wait/indicator() → on trigger: submit_market_order(). Alternative to polling get_indicator() in a loop."
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
    "tools/wait/trade_monitor": {
        "description": (
            "What: Long-polling trade monitor: holds HTTP connection open until a price target or invalidation level is reached, or monitoring duration expires.\n"
            "\n"
            "When: Use for waiting for a trade setup to play out without manually polling, monitoring active trades with predefined target and invalidation levels, or event-driven trade management.\n"
            "\n"
            "Output: {symbol: str, reason: 'target_reached'|'invalidation_hit'|'timeout', current_price: float, bid: float, ask: float, target_price: float, invalidation_price: float, distance_to_target_pips: float, distance_to_invalidation_pips: float, elapsed_seconds: int, duration_seconds: int, market_context: {regime: str, atr: float, rsi: float, spread_points: int}}\n"
            "\n"
            "Assumptions: Blocks HTTP connection for up to duration_seconds. For buy: target above entry, invalidation below. For sell: target below, invalidation above. Uses bid for buy-side checks, ask for sell-side. ATR-type boundaries fetch ATR(14) on H1 internally. Max duration: 3600s.\n"
            "\n"
            "Composition: Use after validating a trade setup. Chain → validate_trade_setup() → tools/wait/trade_monitor() → on reason: take action. Alternative to polling get_order_book() + manual checks. Complements resources/market/wait_for_price() (handles both target AND invalidation)."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "side": {"type": "string", "enum": ["buy", "sell"]},
                "duration": {"type": "string"},
                "expected": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": ["price", "pips", "atr"]},
                        "value": {"type": ["number", "string"]},
                        "multiplier": {"type": ["number", "string"]},
                    },
                    "required": ["type"],
                },
                "invalidation": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": ["price", "pips", "atr"]},
                        "value": {"type": ["number", "string"]},
                        "multiplier": {"type": ["number", "string"]},
                    },
                    "required": ["type"],
                },
                "check_interval_seconds": {"type": ["number", "string"]},
            },
            "required": ["symbol", "side", "duration", "expected", "invalidation"],
        },
    },
    # === METACOGNITION TOOLS ===
    "trading/log_decision": {
        "description": (
            "What: Records a trading decision with full reasoning metadata for self-learning and audit.\n"
            "\n"
            "When: Use after every trading decision (entry, exit, modification, or decision to wait), recording the reasoning behind each trade for later analysis, or updating a decision entry with outcome data after position closes.\n"
            "\n"
            "Output: {decision_id: str, status: 'logged'|'updated'|'error', message: str}\n"
            "  - decision_id returned can be used to update the entry later (e.g., fill outcome on exit).\n"
            "\n"
            "Assumptions: Decisions persisted to SQLite journal (survives server restarts). All text fields accept any string. Empty/null fields stored as-is. Minimum required: symbol, side, action.\n"
            "\n"
            "Composition: Call after every action. Input decision_id into trading/reflect() for querying. Chain → execute trade → trading/log_decision() → later: trading/reflect(decision_id) → trading/insights()."
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
            "When: Use for investigating patterns in past trading decisions before making new ones, reviewing performance in specific market regimes or emotional states, or identifying recurring mistake categories for targeted improvement.\n"
            "\n"
            "Output: {decisions: [{decision_id: str, symbol: str, side: str, action: str, entry_price: float, pnl: float, regime: str, confidence_level: float, emotional_self_report: str, outcome: str, mistake_category: str, quality_rating: int, timestamp: str, ...}], count: int, query: {filters: object}}\n"
            "  - Results sorted by timestamp descending (most recent first). Returns empty array if no matches (no error).\n"
            "\n"
            "Assumptions: Only returns fields that were populated at log time. Default limit=50. Zero results may mean no matching decisions or unpopulated outcomes.\n"
            "\n"
            "Composition: Input for trading/insights(). Chain → trading/reflect(filters) → analyze patterns → trading/insights() → adjust strategy. Use for pattern recognition: 'Show my losses in ranging regimes.'"
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
            "When: Use for periodic review of trading performance (daily or weekly), identifying systemic weaknesses across regime/emotion/mistake categories, or getting AI-generated recommendations for strategy improvement.\n"
            "\n"
            "Output: {lookback_days: int, total_decisions: int, win_rate: float, avg_pnl: float, win_rate_by_regime: {regime: float}, win_rate_by_emotion: {emotion: float}, common_mistakes: [{category: str, count: int, avg_loss: float}], avg_confidence_when_winning: float, avg_confidence_when_losing: float, recommendations: [str]}\n"
            "  - All rates 0.0-1.0. recommendations: AI-actionable guidance derived from patterns.\n"
            "  - Returns zero-valued metrics if insufficient data (< 5 decisions).\n"
            "\n"
            "Assumptions: Only includes decisions with populated outcome fields. recommendations are template-generated, not LLM-generated. Default lookback=7 days.\n"
            "\n"
            "Composition: Takes data from trading/reflect(). Chain → trading/reflect() → trading/insights() → apply recommendations. Use periodically (e.g., daily) for strategy refinement."
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
            "What: Fetches live market context for a symbol: ATR, volatility assessment, point values, spread analysis, and trading session.\n"
            "\n"
            "When: Use for getting quick market context before analyzing a specific symbol, checking if current volatility is normal or extreme, identifying current trading session (London, New York, Asian, Closed), or verifying spread conditions before entry.\n"
            "\n"
            "Output: {symbol: str, current_price: float, bid: float, ask: float, spread_points: int, atr_14: float, atr_pct_of_price: float, avg_atr: float, atr_percentile: float, volatility_assessment: str, point_value: float, lot_size_info: {min: float, max: float, step: float}, composure_notes: str, session: str}\n"
            "  - session: 'London', 'New York', 'Asian', or 'Closed'. volatility_assessment: 'low', 'normal', 'high', 'extreme'.\n"
            "\n"
            "Assumptions: Uses ATR(14) on H1 as baseline. Comparison data based on last 100 bars of ATR history. Returns partial data with null fields if symbol has no market data. Default: include_comparison=true.\n"
            "\n"
            "Composition: Takes symbol_info() and get_indicator(atr) internally. Input for trading/coach(), calculate_position_size(). Chain → trading/context() → trading/coach() → execute."
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
            "When: Use for getting advisory feedback on a proposed trade setup before execution, checking SL/TP reasonableness relative to ATR, assessing confluence score from multiple indicator alignment, or monitoring overtrading risk.\n"
            "\n"
            "Output: {symbol: str, side: str, advisory: {sl_atr_ratio: float, risk_reward: float, trend_alignment: str, bar_pattern_notes: str, volatility_notes: str, session_notes: str, confluence_score: int, warnings: [str], recommendations: [str]}, market_data: {atr: float, regime: str, rsi: float, ema_fast: float, ema_slow: float}}\n"
            "  - confluence_score: 0-5. sl_atr_ratio < 1.0 = tight stops. Warnings are factual; recommendations are optional.\n"
            "\n"
            "Assumptions: Advisory only — does NOT block execution. Missing input parameters fetched internally where possible. Does not account for account balance, policy limits, or open positions.\n"
            "\n"
            "Composition: Takes regime from market/regime(), ATR from get_indicator(atr). Chain → trading/coach() → if confluence_score ≥ 3: validate_trade_setup() → execute. Not a substitute for validate_trade_setup() (doesn't check broker constraints)."
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
            "When: Use for getting all pre-trade analysis data in a single efficient call (~400ms), before every trade execution to ensure informed decision-making, or when you need regime, ATR, RSI, EMA alignment, and coaching feedback simultaneously.\n"
            "\n"
            "Output: {symbol: str, regime: {regime: str, confidence: float, adx: float}, atr: {value: float, pct_of_price: float}, rsi: {value: float}, ema: {ema_20: float, ema_50: float, alignment: str}, coaching: {confluence_score: int, warnings: [str], recommendations: [str]}, execution_time_ms: int}\n"
            "  - execution_time_ms: ~400ms vs 3-5s for sequential individual calls. All indicators on H1 timeframe.\n"
            "  - If any internal fetch fails, that section contains {error: str}.\n"
            "\n"
            "Assumptions: All indicators use H1 timeframe. RSI(14), EMA(20/50) are fixed (not configurable). Coaching feedback uses same logic as trading/coach().\n"
            "\n"
            "Composition: Replaces sequential calls to market/regime() + trading/context() + get_indicator(rsi) + get_indicator(ema) + trading/coach(). Chain → trading/decision_support() → if confluence_score ≥ 3: validate_trade_setup() → execute."
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
    # === NEWS TOOLS ===
    "news_fetch": {
        "description": (
            "What: Fetches financial news articles from configured RSS feed sources.\n"
            "\n"
            "When: Use for fundamental analysis before trades, detecting market-moving events, or supplementing technical analysis with news context. Prefer over news_enrich() for initial article retrieval.\n"
            "\n"
            "Output: {articles: [{title: str, summary: str, url: str, source: str, published_at: str, currencies: [str], categories: [str]}], count: int, source: str}\n"
            "  - If enrichArticles=true, each article also includes: sentiment: float, topics: [str], entities: [str], summary: str. published_at is ISO 8601 UTC.\n"
            "  - Returns empty articles array if no matches (no error). Default pool: FINANCIAL_MARKETS.\n"
            "\n"
            "Assumptions: Articles from RSS feeds, not real-time push. Refresh latency: 5-15 minutes from publication. Currency matching is heuristic. enrichArticles=true increases latency significantly (NLP per article).\n"
            "\n"
            "Composition: Input for fundamental analysis. Chain → news_fetch() → news_enrich() (for sentiment/topics) → economic_calendar() → decide. Complements economic_calendar() for event awareness."
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
            "When: Use for adding sentiment scores and topic/entity extraction to articles from news_fetch(), analyzing emotional tone of market news, or extracting currency/central bank mentions. Prefer selective enrichment for high-impact articles over bulk.\n"
            "\n"
            "Output: {enriched: [{...original_fields, sentiment: float, topics: [str], entities: [str], summary: str}]}\n"
            "  - sentiment: -1.0 (negative) to 1.0 (positive). Original fields preserved. Enrichment applied in-place.\n"
            "\n"
            "Assumptions: Computationally expensive for large arrays (> 50 articles). Entity extraction covers currencies, central banks, and economic terms only. If enrichment fails for an item, that item returned unchanged.\n"
            "\n"
            "Composition: Takes output from news_fetch(). Chain → news_fetch() → news_enrich() → analyze sentiment. Use selectively for high-impact articles rather than bulk."
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
            "When: Use for detecting emerging market narratives, adjusting watchlist priorities based on news momentum, or identifying which currencies/instruments are gaining media attention.\n"
            "\n"
            "Output: {trending: [{entity: str, current_mentions: int, previous_mentions: int, growth_factor: float, categories: [str]}], time_window: int}\n"
            "  - Compares mention frequency in current window vs preceding window of equal length. Default: 24h window, 1.5x growth, 3 min mentions.\n"
            "  - Returns empty trending array if no entities meet thresholds (no error).\n"
            "\n"
            "Assumptions: Entity extraction is keyword-based, not NER. Growth factor = current/previous mentions.\n"
            "\n"
            "Composition: Chain → insights_trendingEntities() → if entity correlates with watchlist symbol: news_fetch(keywords=[entity]) → adjust strategy. Use for detecting emerging narratives."
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
            "When: Use before any execution tool to check for HIGH/CRITICAL impact events, identifying trading blackout windows to avoid entries, or planning trades around economic releases.\n"
            "\n"
            "Output: {events: [{name: str, currency: str, impact: str, timestamp: int, actual: str, forecast: str, previous: str}], event_count: int, blackout_windows: [{event_name: str, start_utc: str, end_utc: str, currency: str, impact: str, blackout_minutes: int}], current_blackout: {is_blackout: bool, events_causing_blackout: [str]}, source: 'mt5_terminal_calendar'|'schedule_based_fallback'}\n"
            "  - Blackout windows: ±60-120 minutes around HIGH-impact events. source='schedule_based_fallback' means estimated data, not real terminal.\n"
            "  - Returns empty events during market holidays (no error). Default: 24h ahead, min_impact=MEDIUM.\n"
            "\n"
            "Assumptions: Primary source is MT5 Terminal's native Economic Calendar API. Falls back to schedule-based estimates. When source is 'schedule_based_fallback', a warning field is included.\n"
            "\n"
            "Composition: Check before any execution tool. Chain → economic_calendar() → if current_blackout: wait or reduce size → execute. Complements news_fetch() for fundamental awareness."
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
            "When: Use to discover valid pool values before calling news_fetch(), understanding which news sources are available, or selecting specific source pools for targeted news retrieval.\n"
            "\n"
            "Output: {pools: [str], sources: [{id: str, name: str, url: str, category: str}]}\n"
            "\n"
            "Assumptions: Static metadata — does not fetch actual news content. Pool names are case-sensitive strings used in news_fetch's pools parameter.\n"
            "\n"
            "Composition: Discover valid pool values before calling news_fetch(). Chain → news/pools() → news_fetch(pools=['FINANCIAL_MARKETS']) → analyze."
        ),
        "schema": {"type": "object"},
    },
    # === MARKET ADVANCED TOOLS ===
    "market/snapshot": {
        "description": (
            "What: One-call complete market context for a symbol.\n"
            "Returns bars, regime, ATR, RSI, EMAs, S/R levels, session context, and coaching in a single call.\n"
            "Output: {symbol, timeframe, bars, regime, atr, rsi, ema_20, ema_50, support, resistance, session_context, coaching}\n"
            "When: Use as first-pass analysis before deep-diving into a candidate. Replaces 5+ individual calls.\n"
            "Composition: market/snapshot() → if coaching indicates edge: deep-dive with analysis/* tools → decide."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "timeframe": {"type": "string"},
                "bar_count": {"type": ["number", "string"]},
                "include_coaching": {"type": "boolean"},
            },
            "required": ["symbol"],
        },
    },
    "market/opportunity_rank": {
        "description": (
            "What: Ranks multiple symbols by trade-readiness using 7 weighted factors.\n"
            "Returns composite scores 0-100, regime alignment, RSI position, spread quality, calendar risk.\n"
            "Output: {rankings: [{symbol, composite_score, factors: {...}}]}\n"
            "When: Use to find the best candidate among a watchlist. Replaces manual scan + analysis per symbol.\n"
            "Composition: market/opportunity_rank(symbols=[...]) → top candidate → analysis/divergence → execute/wait."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "symbols": {"type": "array", "items": {"type": "string"}},
                "timeframe": {"type": "string"},
                "min_score": {"type": ["number", "string"]},
                "weights": {"type": "object"},
            },
            "required": ["symbols"],
        },
    },
    "market/chart_intelligence": {
        "description": (
            "What: Unified chart analysis bundle — screenshot + S/R + patterns + indicators in one call.\n"
            "Output: {patterns, support_resistance, indicators, screenshot_base64 (optional)}\n"
            "When: Use for comprehensive structural analysis of a single symbol. Best for setup identification.\n"
            "Composition: market/chart_intelligence() → pattern detected → analysis/divergence confirms → execute."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "timeframe": {"type": "string"},
                "include_screenshot": {"type": "boolean"},
                "bar_count": {"type": ["number", "string"]},
            },
            "required": ["symbol"],
        },
    },
    "portfolio/exposure": {
        "description": (
            "What: Portfolio exposure across all open positions.\n"
            "Output: {total_exposure_usd, net_exposure_usd, by_symbol, by_side, margin_used, free_margin_pct}\n"
            "When: Use before adding new positions to check portfolio-level risk.\n"
            "Composition: portfolio/exposure() → if exposure < threshold → calculate_position_size → execute."
        ),
        "schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    "portfolio/pre_trade_gate": {
        "description": (
            "What: Pre-trade safety check — validates if a new trade would breach portfolio risk limits.\n"
            "Output: {allowed: bool, reason: str, current_exposure, projected_exposure, risk_metrics}\n"
            "When: Use as final gate before any order submission.\n"
            "Composition: calculate_position_size() → portfolio/pre_trade_gate() → if allowed: submit_order()."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "side": {"type": "string", "enum": ["buy", "sell"]},
                "volume_lots": {"type": ["number", "string"]},
                "sl_distance": {"type": ["number", "string"]},
            },
            "required": ["symbol", "side"],
        },
    },
    "market/structure": {
        "description": (
            "What: Detects market structure — BOS, ChoCh, HH/HL/LH/LL labeling, trend health.\n"
            "Output: {structure: bullish|bearish|ranging|transitioning, trend_health: strong|weakening|exhausted,\n"
            "  swing_points: [...], last_bos: {...}, last_choch: {...}, recent_structure: [HH,HL,...]}\n"
            "When: Use to confirm trend health before entering. Essential for 'hunter' behavior — avoid entries on exhausted trends.\n"
            "Composition: market/structure() → if structure=trending AND health=strong → execute; if health=exhausted → wait."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "timeframe": {"type": "string"},
                "swing_lookback": {"type": ["number", "string"]},
                "confirm_bos_pips": {"type": ["number", "string"]},
            },
            "required": ["symbol"],
        },
    },
    "strategy/selector": {
        "description": (
            "What: Returns the optimal strategy for current regime, with entry style, stop type, TP type, risk multiplier.\n"
            "Output: {recommended: {...}, all_strategies: [{name, regime, entry_style, ...}]}\n"
            "When: Use after regime detection to get specific execution parameters. 8 strategies: pullback_trend, bracket_range,\n"
            "  breakout_compress, momentum_continuation, mean_reversion_fade, wide_volatility, patience_consolidation.\n"
            "Composition: market/regime() → strategy/selector(regime) → use entry_style for order type → execute."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "regime": {"type": "string"},
            },
            "required": [],
        },
    },
    "vwap": {
        "description": (
            "What: Computes VWAP with deviation bands from OHLCV bars using tick volume.\n"
            "Output: {current_vwap, vwap_deviation_upper, vwap_deviation_lower, distance_from_vwap_pct, price_position}\n"
            "When: Use to find institutional reference prices. Price at VWAP = fair value. Above/below = premium/discount.\n"
            "Composition: vwap() → if price_at_vwap and regime=trending → enter; if price_far_from_vwap → wait for mean reversion."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "timeframe": {"type": "string"},
                "bar_count": {"type": ["number", "string"]},
                "std_dev_multiplier": {"type": ["number", "string"]},
            },
            "required": ["symbol"],
        },
    },
    "volume_at_price": {
        "description": (
            "What: Volume-at-Price profile — Point of Control (POC) and Value Area from tick volume.\n"
            "Output: {poc, value_area_high, value_area_low, value_area_width, current_price_position, distribution}\n"
            "When: Use to find high-volume nodes where institutions trade. POC = magnet. Value Area edges = support/resistance.\n"
            "Composition: volume_at_price() → if price at POC → wait for breakout; if price at VA edge → enter toward POC."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "timeframe": {"type": "string"},
                "bar_count": {"type": ["number", "string"]},
                "num_bins": {"type": ["number", "string"]},
            },
            "required": ["symbol"],
        },
    },
    "setup_probability": {
        "description": (
            "What: Estimates win rate for current setup from historical journal data.\n"
            "Output: {estimated_win_rate, sample_size, confidence, recommendation, win_rate_by_regime, common_mistakes}\n"
            "When: Use before entering any trade to check if this setup has historically been profitable.\n"
            "Composition: setup_probability(symbol=X, regime=Y) → if win_rate > 55% → execute; if < 45% → skip."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "regime": {"type": "string"},
                "session": {"type": "string"},
                "min_samples": {"type": ["number", "string"]},
            },
            "required": [],
        },
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
    # Normalize aliased tool names (e.g., mt5-mcp_tools_wait_delay → tools/wait/delay)
    name = _normalize_tool_name(name)
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
        elif name == "portfolio/risk":
            res = await _post_json("/tools/portfolio/risk", args)
        elif name == "trail_position":
            res = await _post_json("/tools/trail_position", args)
        elif name == "volatility_profile":
            res = await _post_json("/tools/volatility_profile", args)
        elif name == "analysis/divergence":
            res = await _post_json("/tools/analysis/divergence", args)
        elif name == "analysis/multi_bar_patterns":
            res = await _post_json("/tools/analysis/multi_bar_patterns", args)
        elif name == "analysis/volume_profile":
            res = await _post_json("/tools/analysis/volume_profile", args)
        elif name == "analysis/momentum":
            res = await _post_json("/tools/analysis/momentum", args)
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
        # Market analysis
        elif name == "market/regime":
            res = await _post_json("/tools/market/regime", args)
        elif name == "market/scan":
            res = await _post_json("/tools/market/scan", args)
        # Bracket orders
        elif name == "place_bracket_order":
            res = await _post_json("/tools/place_bracket_order", args)
        # EA-native bracket orders
        elif name == "ea_bracket/start":
            res = await _post_json("/tools/ea_bracket/start", args)
        elif name == "ea_bracket/stop":
            res = await _post_json("/tools/ea_bracket/stop", args)
        elif name == "ea_bracket/list":
            res = await _post_json("/tools/ea_bracket/list", {})
        elif name == "ea_bracket/tick":
            res = await _post_json("/tools/ea_bracket/tick", {})
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
        elif name == "tools/wait/trade_monitor":
            res = await _post_json("/tools/wait/trade_monitor", args)
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
        elif name == "market/snapshot":
            res = await _post_json("/tools/market/snapshot", args)
        elif name == "market/opportunity_rank":
            res = await _post_json("/tools/market/opportunity_rank", args)
        elif name == "market/chart_intelligence":
            res = await _post_json("/tools/market/chart_intelligence", args)
        elif name == "portfolio/exposure":
            res = await _post_json("/tools/portfolio/exposure", args)
        elif name == "portfolio/pre_trade_gate":
            res = await _post_json("/tools/portfolio/pre_trade_gate", args)
        elif name == "market/structure":
            res = await _post_json("/tools/market/structure", args)
        elif name == "strategy/selector":
            res = await _post_json("/tools/strategy/selector", args)
        elif name == "vwap":
            res = await _post_json("/tools/vwap", args)
        elif name == "volume_at_price":
            res = await _post_json("/tools/volume_at_price", args)
        elif name == "setup_probability":
            res = await _post_json("/tools/setup_probability", args)
        else:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"Unknown tool: {name}")]
            )
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(res))]
        )
    except Exception as e:
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=f"Error: {e}")]
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
