from __future__ import annotations

import asyncio
import logging
import struct
import time
import uuid
from typing import Any

logger = logging.getLogger(__name__)


_HEADER_FORMAT = "!I"
_HEADER_SIZE = struct.calcsize(_HEADER_FORMAT)
_MAX_PAYLOAD = 16 * 1024 * 1024


class TCPBridgeClient:
    """TCP client for MCP server to communicate with the TCP Bridge Server.

    Connects to the TCP bridge server, sends commands, and awaits results.
    Each command gets a unique request_id and the response is matched by it.
    """

    __slots__ = (
        "_host",
        "_port",
        "_reader",
        "_writer",
        "_pending",
        "_recv_task",
        "_running",
        "_reconnect_task",
        "_max_reconnect_delay",
        "_connected_event",
    )

    def __init__(self, host: str = "127.0.0.1", port: int = 8026):
        self._host = host
        self._port = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._recv_task: asyncio.Task | None = None
        self._running: bool = False
        self._reconnect_task: asyncio.Task | None = None
        self._max_reconnect_delay: float = 30.0
        self._connected_event: asyncio.Event | None = None

    @property
    def is_connected(self) -> bool:
        return (
            self._writer is not None
            and self._writer.is_closing() is False
            and self._reader is not None
            and not self._reader.at_eof()
        )

    async def _reconnect_loop(self, max_reconnect_delay: float = 30.0) -> None:
        if self._connected_event is None:
            self._connected_event = asyncio.Event()
        self._connected_event.clear()
        delay = 1.0
        while self._running:
            try:
                self._reader, self._writer = await asyncio.open_connection(
                    self._host, self._port
                )
                if self._connected_event:
                    self._connected_event.set()
                delay = 1.0
                await self._recv_loop()
            except Exception:
                if not self._running:
                    break
                logger.warning("TCP bridge disconnected, reconnecting in %.1fs", delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, max_reconnect_delay)
            finally:
                for fut in self._pending.values():
                    if not fut.done():
                        fut.set_exception(ConnectionError("TCP bridge disconnected"))
                self._pending.clear()

    async def connect(self, max_reconnect_delay: float = 30.0) -> None:
        self._max_reconnect_delay = max_reconnect_delay
        self._running = True
        self._connected_event = asyncio.Event()
        self._reconnect_task = asyncio.create_task(self._reconnect_loop())
        await asyncio.wait_for(self._connected_event.wait(), timeout=5.0)

    async def close(self) -> None:
        self._running = False
        if self._reconnect_task:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
        if self._recv_task:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(ConnectionError("Client closed"))
        self._pending.clear()

    async def _recv_loop(self) -> None:
        buffer = bytearray()
        expected: int | None = None
        while True:
            data = await self._reader.read(65536)
            if not data:
                break
            buffer.extend(data)
            while True:
                if expected is None:
                    if len(buffer) < _HEADER_SIZE:
                        break
                    expected = struct.unpack(_HEADER_FORMAT, buffer[:_HEADER_SIZE])[0]
                if len(buffer) < _HEADER_SIZE + expected:
                    break
                json_bytes = bytes(buffer[_HEADER_SIZE : _HEADER_SIZE + expected])
                del buffer[: _HEADER_SIZE + expected]
                expected = None

                import json

                try:
                    frame = json.loads(json_bytes)
                except json.JSONDecodeError as e:
                    logger.warning(f"Malformed JSON frame from EA: {e}")
                    continue
                request_id = frame.get("request_id", "")
                fut = self._pending.pop(request_id, None)
                if fut and not fut.done():
                    fut.set_result(frame)

    async def send_command(
        self, type: str, payload: dict[str, Any], timeout: float = 20.0
    ) -> dict[str, Any]:
        request_id = str(uuid.uuid4())
        frame = {"type": type, "request_id": request_id, **payload}

        fut: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = fut

        json_bytes = json_dumps(frame).encode("utf-8")
        header = struct.pack(_HEADER_FORMAT, len(json_bytes))
        self._writer.write(header + json_bytes)
        await self._writer.drain()

        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(request_id, None)
            raise


_client_instance: TCPBridgeClient | None = None


def get_tcp_client() -> TCPBridgeClient:
    global _client_instance
    if _client_instance is None:
        import os

        host = os.getenv("MT5_TCP_BRIDGE_HOST", "127.0.0.1")
        port = int(os.getenv("MT5_TCP_BRIDGE_MCP_PORT", "8026"))
        _client_instance = TCPBridgeClient(host=host, port=port)
    return _client_instance


def json_dumps(obj: dict) -> str:
    import json

    return json.dumps(obj, separators=(",", ":"))
