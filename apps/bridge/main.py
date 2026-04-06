"""
MT5 Bridge Service — Unified entry point for all MT5 bridge components.

Combines:
1. TCP Bridge Server (port 8025) — Low-latency TCP push to MT5 EA
2. HTTP Gateway (port 8020) — HTTP API + command queue
3. MCP Server (port 8010) — Model Context Protocol API

Single service, single process, 3 ports.
"""

from __future__ import annotations

import asyncio
import os
import signal

from mt5_mcp.observability.logging import setup_logging, logger

setup_logging()


async def run_tcp_bridge(stop_event: asyncio.Event):
    """Start TCP Bridge server (ports 8025, 8026, 8027)."""
    from apps.tcp_bridge.server import get_bridge_server

    ea_port = int(os.getenv("MT5_TCP_BRIDGE_PORT", "8025"))
    mcp_port = int(os.getenv("MT5_TCP_BRIDGE_MCP_PORT", "8026"))
    status_port = int(os.getenv("MT5_TCP_BRIDGE_STATUS_PORT", "8027"))

    server = get_bridge_server()

    async def handle_status(reader, writer):
        import json

        try:
            data = await reader.read(4096)
            if data and b"HTTP" in data[:20]:
                body = json.dumps(
                    {
                        "ea_connected": server.ea_connected,
                        "ea_address": server._ea_address,
                        "pending_commands": server.pending_count,
                    }
                ).encode()
                response = (
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: application/json\r\n"
                    b"Content-Length: " + str(len(body)).encode() + b"\r\n"
                    b"Connection: close\r\n"
                    b"\r\n" + body
                )
                writer.write(response)
                await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    status_srv = await asyncio.start_server(handle_status, "0.0.0.0", status_port)
    logger.info(f"TCP Bridge status HTTP on :{status_port}")

    async with status_srv:
        await server.start()
        logger.info(
            f"TCP Bridge running — EA:{ea_port} MCP:{mcp_port} Status:{status_port}"
        )
        await stop_event.wait()
        await server.stop()
        logger.info("TCP Bridge stopped")


async def run_http_gateway(stop_event: asyncio.Event):
    """Start HTTP Gateway on port 8020."""
    import uvicorn
    from apps.bridge_gateway.main import app

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=8020,
        log_level="info",
        access_log=False,
    )
    server = uvicorn.Server(config)
    # Override shutdown trigger
    server.install_signal_handlers = lambda: None
    logger.info("HTTP Gateway starting on :8020")
    asyncio.create_task(server.serve())
    await stop_event.wait()
    await server.shutdown()
    logger.info("HTTP Gateway stopped")


async def run_mcp_server(stop_event: asyncio.Event):
    """Start MCP Server on port 8010."""
    import uvicorn
    from apps.mcp_server.main import app

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=8010,
        log_level="info",
        access_log=False,
    )
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None
    logger.info("MCP Server starting on :8010")
    asyncio.create_task(server.serve())
    await stop_event.wait()
    await server.shutdown()
    logger.info("MCP Server stopped")


def main():
    logger.info("=" * 60)
    logger.info("MT5 Bridge Service — TCP + HTTP + MCP")
    logger.info("=" * 60)

    stop_event = asyncio.Event()

    def handle_signal(sig, frame):
        logger.info(f"Signal {sig} received, shutting down bridge...")
        stop_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    async def run_all():
        await asyncio.gather(
            run_tcp_bridge(stop_event),
            run_http_gateway(stop_event),
            run_mcp_server(stop_event),
        )

    try:
        asyncio.run(run_all())
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        logger.info("MT5 Bridge Service stopped")


if __name__ == "__main__":
    main()
