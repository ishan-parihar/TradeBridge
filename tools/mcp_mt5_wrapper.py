from __future__ import annotations

import os
import json
import asyncio
import subprocess
import time
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


_NUMERIC_FIELDS: dict[str, set[str]] = {
    "get_bars": {"count"},
    "deals_history": {"limit", "days"},
    "performance_summary": {"limit", "days"},
    "estimate_margin": {"volume_lots", "price_hint"},
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
    "trailing_stop/cancel": {},
    "trailing_stop/tick": {},
    "trailing_stop/list": {},
    # New: Long-polling resources
    "resources/market/wait_for_price": {"price", "timeout_seconds"},
    "resources/positions/monitor": {
        "timeout_seconds",
        "alert_at_pnl",
        "alert_at_price",
    },
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
    "get_bars": {
        "description": "Fetch bars via EA bridge",
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
        "description": "Compute indicator via EA bridge",
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
        "description": "Fetch recent ticks",
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
        "description": "Fetch tradable symbol metadata",
        "schema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    },
    "deals_history": {
        "description": "Fetch recent deal history",
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
        "description": "Summarize realized trading performance",
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
        "description": "Fetch order book snapshot",
        "schema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    },
    "get_chart_screenshot": {
        "description": "Get timeframe-aware chart screenshot",
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
    "submit_market_order_via_bridge": {
        "description": "Submit market order (demo policy)",
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
    "estimate_margin": {
        "description": "Estimate order margin requirement",
        "schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "side": {"type": "string"},
                "volume_lots": {"type": ["number", "string"]},
                "price_hint": {"type": ["number", "string", "null"]},
            },
            "required": ["symbol", "side", "volume_lots"],
        },
    },
    "submit_pending_order": {
        "description": "Submit pending order",
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
            },
            "required": ["symbol", "side", "kind", "price", "volume_lots"],
        },
    },
    "modify_order": {
        "description": "Modify pending order fields",
        "schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "new_price": {"type": ["number", "string", "null"]},
                "new_sl": {"type": ["number", "string", "null"]},
                "new_tp": {"type": ["number", "string", "null"]},
            },
            "required": ["order_id"],
        },
    },
    "modify_position_sl_tp": {
        "description": "Modify position SL/TP",
        "schema": {
            "type": "object",
            "properties": {
                "position_id": {"type": "string"},
                "sl": {"type": ["number", "string", "null"]},
                "tp": {"type": ["number", "string", "null"]},
            },
            "required": ["position_id"],
        },
    },
    "close_position": {
        "description": "Close position (partial or full)",
        "schema": {
            "type": "object",
            "properties": {
                "position_id": {"type": "string"},
                "volume": {"type": ["number", "string", "null"]},
            },
            "required": ["position_id"],
        },
    },
    "close_all_positions": {
        "description": "Close all positions by optional symbol/side",
        "schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": ["string", "null"]},
                "side": {"type": "string"},
            },
            "required": [],
        },
    },
    "cancel_order": {
        "description": "Cancel pending order by id",
        "schema": {
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
        },
    },
    "cancel_all_orders": {
        "description": "Cancel all orders by optional symbol/side",
        "schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": ["string", "null"]},
                "side": {"type": "string"},
            },
            "required": [],
        },
    },
    "calculate_position_size": {
        "description": "Calculate risk-based position size",
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
        "description": "Validate trade against broker constraints",
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
    "trail_position": {
        "description": "Trail stop from current mark",
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
    "volatility_profile": {
        "description": "Summarize ATR and bar ranges",
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
        "description": "Aggregate indicator across timeframes",
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
        "description": "Compute cross-symbol return correlation",
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
    # --- Trading Context & Coaching ---
    "trading/context": {
        "description": "LIVE market context: ATR, volatility assessment, point values, composure notes. Answers: 'Is 200 points a lot RIGHT NOW?'",
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
        "description": "Advisory coaching from live market data. Checks SL/ATR ratio, risk:reward, trend alignment, bar patterns. Does NOT block.",
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
        "description": "One-call: regime + ATR + RSI + EMAs + coaching advice. All data needed for an informed decision.",
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
    # --- Metacognition ---
    "trading/log_decision": {
        "description": "Log EVERY trading decision with reasoning: model_justification, emotional_self_report, confidence_level. Enables self-learning.",
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
        "description": "Query past decisions for metacognitive reflection. 'Show me my last 5 losses', 'What regime was I in when I won?'",
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
        "description": "Auto-patterns from journal: win rate by emotion, by regime, common mistakes, AI-actionable guidance.",
        "schema": {
            "type": "object",
            "properties": {
                "lookback_days": {"type": ["number", "string"]},
            },
            "required": [],
        },
    },
    "trading/agent_prompt": {
        "description": "Generate the complete system prompt that orients a new trading agent. Call at session start.",
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
    # --- Market Analysis ---
    "market/regime": {
        "description": "Detect market regime: ranging, trending_up, trending_down, compressing. With strategy hints.",
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
        "description": "Multi-symbol scan: price, ATR, regime for all symbols in one call.",
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
    # --- Bracket Orders ---
    "place_bracket_order": {
        "description": "Place paired BUY STOP + SELL STOP for breakout capture. SL/TP computed from ATR.",
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
            },
            "required": ["symbol", "buy_trigger", "sell_trigger", "volume_lots"],
        },
    },
    # --- Trailing Stops ---
    "set_trailing_stop": {
        "description": "Start server-side trailing stop. Auto-trails SL based on ATR.",
        "schema": {
            "type": "object",
            "properties": {
                "position_id": {"type": "string"},
                "distance_atr_multiplier": {"type": ["number", "string"]},
                "check_interval_seconds": {"type": ["number", "string"]},
                "lock_in_profit_after_atr": {"type": ["number", "string"]},
            },
            "required": ["position_id"],
        },
    },
    "trailing_stop/tick": {
        "description": "Process all active trailing stops. Call periodically.",
        "schema": {"type": "object"},
    },
    "trailing_stop/cancel": {
        "description": "Cancel an active trailing stop.",
        "schema": {
            "type": "object",
            "properties": {"position_id": {"type": "string"}},
            "required": ["position_id"],
        },
    },
    "trailing_stop/list": {
        "description": "List all active trailing stops.",
        "schema": {"type": "object"},
    },
    # --- Long-Polling Resources ---
    "resources/market/wait_for_price": {
        "description": "Long-polling price alert. Holds connection until price condition is met.",
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
        "description": "Long-polling position monitor. Alerts at P&L or price levels.",
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
    # --- IGS-MCP News & Macro ---
    "news_fetch": {
        "description": "Fetch latest financial news from FINANCIAL_MARKETS pool (FXStreet, ForexFactory, CoinDesk, CoinTelegraph, Kitco, Reuters, Bloomberg Crypto, OilPrice).",
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
        "description": "NLP enrichment of news items. Adds topics, entities, sentiment, summary. Use after news_fetch.",
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
        "description": "Detect entities with increasing mention frequency. Identifies emerging topics.",
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
    # --- Account & Positions ---
    "account_summary": {
        "description": "Get account summary",
        "schema": {"type": "object"},
    },
    "positions_open": {
        "description": "List open positions",
        "schema": {"type": "object"},
    },
    "orders_pending": {
        "description": "List pending orders",
        "schema": {"type": "object"},
    },
    "bridge_status": {
        "description": "Bridge heartbeat status",
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
        elif name == "estimate_margin":
            res = await _post_json("/tools/estimate_margin", args)
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
