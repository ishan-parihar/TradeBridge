#!/usr/bin/env python3
"""MCP stdio server for MT5-MCP bridge - proxies to HTTP API"""
import os
import sys
import json
import asyncio
from typing import Any

import httpx
from mcp.server import Server
import mcp.server.stdio

BASE_URL = os.environ.get("MT5_HTTP_URL", "http://127.0.0.1:8010")

server = Server("mt5-mcp")

async def _post(path: str, data: dict) -> dict:
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(f"{BASE_URL}{path}", json=data)
        r.raise_for_status()
        return r.json()

async def _get(path: str) -> dict:
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{BASE_URL}{path}")
        r.raise_for_status()
        return r.json()

@server.tool("get_bars")
async def get_bars(symbol: str, timeframe: str, count: int = 100) -> dict:
    """Get OHLCV bars from MT5"""
    return await _post("/tools/get_bars", {"symbol": symbol, "timeframe": timeframe, "count": count})

@server.tool("get_indicator")
async def get_indicator(symbol: str, timeframe: str, indicator: str, **kwargs) -> dict:
    """Get technical indicator value"""
    params = {"symbol": symbol, "timeframe": timeframe, "indicator": indicator, **kwargs}
    return await _post("/tools/get_indicator", params)

@server.tool("get_ticks")
async def get_ticks(symbol: str, count: int = 100) -> dict:
    """Get recent ticks"""
    return await _post("/tools/get_ticks", {"symbol": symbol, "count": count})

@server.tool("get_order_book")
async def get_order_book(symbol: str) -> dict:
    """Get order book snapshot"""
    return await _post("/tools/get_order_book", {"symbol": symbol})

@server.tool("get_account_summary")
async def get_account_summary() -> dict:
    """Get account summary"""
    return await _get("/tools/get_account_summary")

@server.tool("get_positions")
async def get_positions() -> list:
    """Get open positions"""
    return await _get("/tools/get_positions")

@server.tool("submit_market_order")
async def submit_market_order(symbol: str, side: str, volume_lots: float, **kwargs) -> dict:
    """Submit market order"""
    return await _post("/tools/submit_market_order", {"symbol": symbol, "side": side, "volume_lots": volume_lots, **kwargs})

@server.tool("close_position")
async def close_position(position_id: int, volume_lots: float = None) -> dict:
    """Close position"""
    params = {"position_id": position_id}
    if volume_lots:
        params["volume_lots"] = volume_lots
    return await _post("/tools/close_position", params)

@server.tool("get_chart_screenshot")
async def get_chart_screenshot(symbol: str, timeframe: str, width: int = 1920, height: int = 1080) -> dict:
    """Get chart screenshot as base64"""
    return await _post("/tools/get_chart_screenshot", {"symbol": symbol, "timeframe": timeframe, "width": width, "height": height})

async def main():
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())
