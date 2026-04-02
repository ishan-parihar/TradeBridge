from __future__ import annotations

import os
import json
import asyncio
from typing import Any

import httpx
from mcp import types
from mcp.server import Server
import mcp.server.stdio


BASE_URL = os.environ.get("MCP_HTTP_URL", "http://127.0.0.1:9131")


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
                "deviation_points": {"type": "number"},
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
                input_schema=spec.get("schema", {"type": "object"}),
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
    async with mcp.server.stdio.stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(run())
