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


async def _post_json(path: str, body: dict[str, Any]) -> dict[str, Any]:
    url = f"{BASE_URL}{path}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(url, json=body)
        r.raise_for_status()
        return r.json()


async def _get_json(path: str) -> dict[str, Any]:
    url = f"{BASE_URL}{path}"
    async with httpx.AsyncClient(timeout=10.0) as client:
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
                "count": {"type": "number"},
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
                "period": {"type": ["number", "null"]},
                "fast": {"type": ["number", "null"]},
                "slow": {"type": ["number", "null"]},
                "signal": {"type": ["number", "null"]},
                "deviation": {"type": ["number", "null"]},
                "shift": {"type": ["number", "null"]},
                "k_period": {"type": ["number", "null"]},
                "d_period": {"type": ["number", "null"]},
                "slowing": {"type": ["number", "null"]},
                "tenkan": {"type": ["number", "null"]},
                "kijun": {"type": ["number", "null"]},
                "senkou": {"type": ["number", "null"]},
                "window": {"type": ["number", "null"]},
            },
            "required": ["symbol", "timeframe", "indicator"],
        },
    },
    "get_ticks": {
        "description": "Fetch recent ticks",
        "schema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}, "count": {"type": "number"}},
            "required": ["symbol"],
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
                "width": {"type": "number"},
                "height": {"type": "number"},
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
                "volume_lots": {"type": "number"},
                "deviation_points": {"type": "number"},
                "sl": {"type": ["number", "null"]},
                "tp": {"type": ["number", "null"]},
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
        "description": "Submit pending order",
        "schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "side": {"type": "string"},
                "kind": {"type": "string"},
                "price": {"type": "number"},
                "volume_lots": {"type": "number"},
                "sl": {"type": ["number", "null"]},
                "tp": {"type": ["number", "null"]},
                "deviation": {"type": "number"},
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
                "new_price": {"type": ["number", "null"]},
                "new_sl": {"type": ["number", "null"]},
                "new_tp": {"type": ["number", "null"]},
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
                "sl": {"type": ["number", "null"]},
                "tp": {"type": ["number", "null"]},
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
                "volume": {"type": ["number", "null"]},
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
    args = arguments or {}
    try:
        if name == "get_bars":
            res = await _post_json("/tools/get_bars", args)
        elif name == "get_indicator":
            res = await _post_json("/tools/get_indicator", args)
        elif name == "get_ticks":
            res = await _post_json("/tools/get_ticks", args)
        elif name == "get_order_book":
            res = await _post_json("/tools/get_order_book", args)
        elif name == "get_chart_screenshot":
            res = await _post_json("/tools/get_chart_screenshot", args)
        elif name == "submit_market_order_via_bridge":
            res = await _post_json("/tools/submit_market_order_via_bridge", args)
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
