"""MCP client for communicating with Vibe-Trading over SSE."""

import json
from typing import Any, Optional

import httpx
import structlog

from .config import get_vibe_mcp_port
from .lifecycle import VibeBridgeLifecycle

logger = structlog.get_logger(__name__)

VIBE_TOOLS = [
    "list_skills",
    "load_skill",
    "backtest",
    "factor_analysis",
    "analyze_options",
    "pattern_recognition",
    "get_market_data",
    "web_search",
    "read_url",
    "read_document",
    "read_file",
    "write_file",
    "list_swarm_presets",
    "run_swarm",
    "get_swarm_status",
    "get_run_result",
    "list_runs",
]


class VibeBridgeClient:
    """HTTP client for Vibe-Trading MCP tools over SSE transport."""

    def __init__(self, lifecycle: Optional[VibeBridgeLifecycle] = None) -> None:
        self._lifecycle = lifecycle or VibeBridgeLifecycle()
        self._port = get_vibe_mcp_port()
        self._session_id: Optional[str] = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    async def ensure_ready(self) -> bool:
        """Ensure Vibe-Trading MCP server is running and ready."""
        return await self._lifecycle.ensure_running()

    async def call_tool(self, tool_name: str, arguments: dict[str, Any] | None = None) -> str:
        """Call a Vibe-Trading MCP tool by name.

        Args:
            tool_name: One of the 17 Vibe-Trading tool names.
            arguments: Tool-specific arguments dict.

        Returns:
            Raw string response from the tool (usually JSON).

        Raises:
            RuntimeError: If server is not running.
            ValueError: If tool_name is not a valid Vibe-Trading tool.
        """
        if tool_name not in VIBE_TOOLS:
            raise ValueError(f"Unknown Vibe-Trading tool: {tool_name}. Valid tools: {VIBE_TOOLS}")

        if not self._lifecycle.is_running:
            raise RuntimeError("Vibe-Trading MCP server is not running. Call ensure_ready() first.")

        return await self._call_rest_api(tool_name, arguments or {})

    async def _call_rest_api(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Call Vibe-Trading via its FastAPI REST API (more reliable than SSE for tools)."""
        async with httpx.AsyncClient(timeout=120.0) as client:
            request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": arguments,
                },
            }
            try:
                resp = await client.post(
                    f"{self.base_url}/messages",
                    json=request,
                    headers={"Content-Type": "application/json"},
                )
                if resp.status_code == 202:
                    return await self._read_sse_response(client, resp)
                else:
                    return json.dumps(
                        {
                            "status": "error",
                            "error": f"HTTP {resp.status_code}: {resp.text[:500]}",
                        }
                    )
            except httpx.ConnectError:
                return json.dumps(
                    {
                        "status": "error",
                        "error": f"Cannot connect to Vibe-Trading at {self.base_url}",
                    }
                )
            except httpx.TimeoutException:
                return json.dumps(
                    {
                        "status": "error",
                        "error": f"Vibe-Trading tool '{tool_name}' timed out after 120s",
                    }
                )

    async def _read_sse_response(self, client: httpx.AsyncClient, accept_resp) -> str:
        """Read the response from SSE stream after a tool call."""
        result_line = ""
        async for line in accept_resp.aiter_lines():
            if line.startswith("data: "):
                data = line[6:]
                try:
                    parsed = json.loads(data)
                    if parsed.get("result") is not None:
                        content = parsed["result"].get("content", [])
                        if content and isinstance(content, list):
                            texts = [c.get("text", "") for c in content if c.get("type") == "text"]
                            return "\n".join(texts)
                        return json.dumps(parsed["result"])
                    if parsed.get("error"):
                        return json.dumps({"status": "error", "error": str(parsed["error"])})
                except json.JSONDecodeError:
                    result_line = data
        return result_line or json.dumps({"status": "error", "error": "No response from SSE stream"})

    async def list_skills(self) -> str:
        """List all 69 Vibe-Trading finance skills."""
        return await self.call_tool("list_skills")

    async def get_market_data(
        self,
        codes: list[str],
        start_date: str,
        end_date: str,
        source: str = "auto",
        interval: str = "1D",
    ) -> str:
        """Fetch OHLCV market data."""
        return await self.call_tool(
            "get_market_data",
            {
                "codes": codes,
                "start_date": start_date,
                "end_date": end_date,
                "source": source,
                "interval": interval,
            },
        )

    async def run_swarm(self, preset_name: str, variables: dict[str, str]) -> str:
        """Run a multi-agent swarm team."""
        return await self.call_tool(
            "run_swarm",
            {
                "preset_name": preset_name,
                "variables": variables,
            },
        )

    async def backtest(self, run_dir: str) -> str:
        """Run a backtest."""
        return await self.call_tool("backtest", {"run_dir": run_dir})

    async def web_search(self, query: str, max_results: int = 5) -> str:
        """Search the web."""
        return await self.call_tool(
            "web_search",
            {
                "query": query,
                "max_results": max_results,
            },
        )

    def get_status(self) -> dict:
        """Get combined status of lifecycle and client."""
        return self._lifecycle.get_status()
