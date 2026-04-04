from __future__ import annotations

import os

from mt5_mcp.observability.logging import setup_logging, logger

from .server import get_bridge_server

setup_logging()


def main() -> None:
    ea_port = int(os.getenv("MT5_TCP_BRIDGE_PORT", "8025"))
    mcp_port = int(os.getenv("MT5_TCP_BRIDGE_MCP_PORT", "8026"))

    server = get_bridge_server()
    logger.info(f"Starting TCP Bridge Server — EA:{ea_port} MCP:{mcp_port}")

    import asyncio

    try:
        asyncio.run(server.start())
    except KeyboardInterrupt:
        logger.info("TCP Bridge Server stopped")


if __name__ == "__main__":
    main()
