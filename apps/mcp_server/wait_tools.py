#!/usr/bin/env python3
"""MCP Wait Tools — DEPRECATED.

Wait tools migrated to HTTP REST on port 8010.
MCP client enforces ~60s timeout on all tool calls, making long waits impossible.

HTTP REST endpoints (apps/mcp_server/main.py):
  POST /tools/wait/delay
  POST /tools/wait/trade_monitor
  POST /tools/wait/indicator
  POST /resources/market/wait_for_price

All bug fixes (BUG-001 through BUG-009) preserved in HTTP endpoints.
"""

from mcp.server.fastmcp import FastMCP


def create_wait_mcp_server() -> FastMCP:
    mcp = FastMCP("mt5-wait-tools")
    return mcp


def register_tools(mcp: FastMCP) -> None:
    pass
