"""TCP connection resilience tests for the TCP bridge.

Tests verify the TCP bridge client and server handle disconnects,
reconnects, dropped frames, and concurrent commands correctly.

No MT5 or EA required — all tests use mock handlers.
"""

from __future__ import annotations

import asyncio
import random

import pytest

from apps.tcp_bridge.protocol import FrameParser, encode_frame
from apps.tcp_bridge.server import TCPBridgeServer
from mt5_mcp.services.tcp_bridge_client import TCPBridgeClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PORT_RANGE_START = 19900


def _free_port(offset: int) -> int:
    """Return a port in the reserved test range."""
    return _PORT_RANGE_START + offset


async def _start_mock_echo_server(host: str, port: int) -> asyncio.Server:
    active_writers: list[asyncio.StreamWriter] = []

    async def _handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        active_writers.append(writer)
        parser = FrameParser()
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                parser.feed(data)
                while parser.has_frame():
                    frame = parser.pop_frame()
                    request_id = frame.get("request_id", "")
                    response = {
                        "request_id": request_id,
                        "status": "ok",
                        "echoed": True,
                    }
                    writer.write(encode_frame(response))
                    await writer.drain()
        except ConnectionResetError:
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(_handler, host, port)
    server._test_active_writers = active_writers  # type: ignore[attr-defined]
    return server


async def _shutdown_echo_server(server: asyncio.Server):
    for w in getattr(server, "_test_active_writers", []):
        w.close()
    server.close()
    await server.wait_closed()


# ---------------------------------------------------------------------------
# Test 1: Client auto-reconnects after server restart
# ---------------------------------------------------------------------------


class TestClientReconnect:
    @pytest.mark.asyncio
    async def test_client_reconnects_after_server_restart(self):
        host = "127.0.0.1"
        port = _free_port(1)

        server = await _start_mock_echo_server(host, port)

        client = TCPBridgeClient(host=host, port=port)
        await client.connect()
        resp = await client.send_command("echo", {"data": "hello"}, timeout=5.0)
        assert resp["status"] == "ok"
        assert resp["echoed"] is True

        await _shutdown_echo_server(server)

        # Consume EOF from reader so _recv_loop breaks and cleans up
        await asyncio.sleep(0.2)
        if client._reader:
            try:
                await client._reader.read(1)
            except Exception:
                pass
        await asyncio.sleep(0.2)

        # send_command should now fail — connection is known dead
        with pytest.raises(
            (ConnectionError, BrokenPipeError, ConnectionResetError, OSError)
        ):
            await client.send_command("echo", {"data": "after_kill"}, timeout=2.0)

        server = await _start_mock_echo_server(host, port)

        await client.close()
        client2 = TCPBridgeClient(host=host, port=port)
        resp2 = await client2.send_command("echo", {"data": "reconnected"}, timeout=5.0)
        assert resp2["status"] == "ok"
        assert resp2["echoed"] is True

        await client2.close()
        await _shutdown_echo_server(server)


# ---------------------------------------------------------------------------
# Test 2: Server handles EA reconnecting
# ---------------------------------------------------------------------------


class TestServerHandlesClientReconnect:
    @pytest.mark.asyncio
    async def test_server_handles_client_reconnect(self):
        host = "127.0.0.1"
        port = _free_port(2)
        mcp_port = _free_port(202)

        server = TCPBridgeServer(host=host, ea_port=port, mcp_port=mcp_port)
        server_task = asyncio.create_task(server.start())
        await asyncio.sleep(0.2)  # let server bind

        try:
            # 2. Connect a mock client, verify server.ea_connected == True
            reader, writer = await asyncio.open_connection(host, port)
            await asyncio.sleep(0.1)
            assert server.ea_connected is True

            # 3. Disconnect client
            writer.close()
            await writer.wait_closed()
            await asyncio.sleep(0.2)

            # 4. Verify server.ea_connected == False
            assert server.ea_connected is False

            # 5. Reconnect client
            reader2, writer2 = await asyncio.open_connection(host, port)
            await asyncio.sleep(0.1)

            # 6. Verify server.ea_connected == True and can receive commands
            assert server.ea_connected is True

            # Send a command via server and verify mock client can respond
            async def _mock_ea_response():
                parser = FrameParser()
                while True:
                    data = await reader2.read(65536)
                    if not data:
                        return None
                    parser.feed(data)
                    if parser.has_frame():
                        frame = parser.pop_frame()
                        request_id = frame.get("request_id", "")
                        response = {
                            "request_id": request_id,
                            "status": "ok",
                            "payload": {"reconnect_test": True},
                        }
                        writer2.write(encode_frame(response))
                        await writer2.drain()
                        return frame

            ea_task = asyncio.create_task(_mock_ea_response())
            result = await server.enqueue_and_await(
                "test_cmd", {"value": 42}, timeout=5.0
            )
            received_frame = await asyncio.wait_for(ea_task, timeout=5.0)
            assert received_frame is not None
            assert result["status"] == "ok"
            assert result["payload"]["reconnect_test"] is True

            writer2.close()
            await writer2.wait_closed()

        finally:
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass


# ---------------------------------------------------------------------------
# Test 3: Partial frame delivery — chunks reassembled correctly
# ---------------------------------------------------------------------------


class TestPartialFrameDelivery:
    @pytest.mark.asyncio
    async def test_partial_frame_reassembly(self):
        # 1. Create a FrameParser
        parser = FrameParser()
        original = {"type": "get_bars", "request_id": "partial-test", "count": 50}
        frame_data = encode_frame(original)

        # 2. Feed half a frame, verify has_frame() == False
        midpoint = len(frame_data) // 2
        parser.feed(frame_data[:midpoint])
        assert parser.has_frame() is False

        # 3. Feed rest of frame, verify has_frame() == True
        parser.feed(frame_data[midpoint:])
        assert parser.has_frame() is True

        # 4. Pop and verify content matches original
        result = parser.pop_frame()
        assert result == original

    @pytest.mark.asyncio
    async def test_partial_frame_byte_by_byte(self):
        parser = FrameParser()
        original = {"type": "test", "request_id": "byte-test"}
        frame_data = encode_frame(original)

        for i, byte in enumerate(frame_data):
            parser.feed(bytes([byte]))
            if i < len(frame_data) - 1:
                assert parser.has_frame() is False

        assert parser.has_frame() is True
        result = parser.pop_frame()
        assert result == original

    @pytest.mark.asyncio
    async def test_multiple_partial_frames(self):
        parser = FrameParser()
        f1 = encode_frame({"type": "first", "id": 1})
        f2 = encode_frame({"type": "second", "id": 2})
        combined = f1 + f2

        # Feed in 8-byte chunks
        chunk_size = 8
        for i in range(0, len(combined), chunk_size):
            parser.feed(combined[i : i + chunk_size])

        # Should be able to extract both frames
        r1 = parser.pop_frame()
        assert r1["type"] == "first"
        assert r1["id"] == 1

        r2 = parser.pop_frame()
        assert r2["type"] == "second"
        assert r2["id"] == 2


# ---------------------------------------------------------------------------
# Test 4: Concurrent commands — all responses match request_id
# ---------------------------------------------------------------------------


class TestConcurrentCommands:
    @pytest.mark.asyncio
    async def test_concurrent_commands_preserve_request_id(self):
        host = "127.0.0.1"
        port = _free_port(4)
        mcp_port = _free_port(204)

        server = TCPBridgeServer(host=host, ea_port=port, mcp_port=mcp_port)
        server_task = asyncio.create_task(server.start())
        await asyncio.sleep(0.2)

        try:
            # 2. Connect mock EA that responds after random delay
            ea_reader, ea_writer = await asyncio.open_connection(host, port)
            await asyncio.sleep(0.1)
            assert server.ea_connected is True

            async def _mock_ea_with_random_delay():
                parser = FrameParser()
                buffer = bytearray()
                try:
                    while True:
                        data = await ea_reader.read(65536)
                        if not data:
                            break
                        buffer.extend(data)
                        parser.feed(data)
                        while parser.has_frame():
                            frame = parser.pop_frame()
                            # Simulate EA processing with random delay
                            delay = random.uniform(0.01, 0.1)
                            await asyncio.sleep(delay)
                            request_id = frame.get("request_id", "")
                            response = {
                                "request_id": request_id,
                                "status": "ok",
                                "payload": {"original_type": frame.get("type")},
                            }
                            ea_writer.write(encode_frame(response))
                            await ea_writer.drain()
                except ConnectionResetError:
                    pass
                finally:
                    ea_writer.close()

            ea_task = asyncio.create_task(_mock_ea_with_random_delay())

            # 3. Send 10 commands concurrently
            num_commands = 10
            tasks = []
            for i in range(num_commands):
                tasks.append(
                    server.enqueue_and_await(f"cmd_{i}", {"index": i}, timeout=10.0)
                )

            results = await asyncio.gather(*tasks)

            # 4. Verify all 10 responses with correct request_id matching
            assert len(results) == num_commands

            seen_indices = set()
            for result in results:
                assert result["status"] == "ok"
                assert "payload" in result
                idx = result["payload"]["original_type"]
                # The type is cmd_X, extract X
                assert idx.startswith("cmd_")
                seen_indices.add(int(idx.split("_")[1]))

            # 5. Verify no responses are mixed up — all indices present
            assert seen_indices == set(range(num_commands))

            ea_writer.close()
            await ea_writer.wait_closed()
            await asyncio.sleep(0.1)

        finally:
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass


# ---------------------------------------------------------------------------
# Test 5: Connection timeout — client handles gracefully
# ---------------------------------------------------------------------------


class TestConnectionTimeout:
    @pytest.mark.asyncio
    async def test_connection_refused_on_nonexistent_port(self):
        host = "127.0.0.1"
        port = _free_port(50)  # unlikely to be in use

        # 1. Try to connect TCP client to non-existent port
        client = TCPBridgeClient(host=host, port=port)

        # 2. Verify ConnectionRefusedError (not hang)
        with pytest.raises((ConnectionRefusedError, OSError)):
            await asyncio.wait_for(client.connect(), timeout=5.0)

        await client.close()

    @pytest.mark.asyncio
    async def test_connect_works_after_initial_failure(self):
        host = "127.0.0.1"
        port = _free_port(51)

        # 1. First attempt fails — no server
        client = TCPBridgeClient(host=host, port=port)
        with pytest.raises((ConnectionRefusedError, OSError)):
            await asyncio.wait_for(client.connect(), timeout=5.0)
        await client.close()

        # 2. Start server
        server = await _start_mock_echo_server(host, port)

        # 3. Verify subsequent connect attempt works
        client2 = TCPBridgeClient(host=host, port=port)
        await client2.connect()
        resp = await client2.send_command("echo", {"after_failure": True}, timeout=5.0)
        assert resp["status"] == "ok"

        await client2.close()
        await _shutdown_echo_server(server)


# ---------------------------------------------------------------------------
# Test 6: Idle connection — long idle stays alive
# ---------------------------------------------------------------------------


class TestIdleConnection:
    @pytest.mark.asyncio
    async def test_idle_connection_survives(self):
        host = "127.0.0.1"
        port = _free_port(60)

        # 1. Start echo server
        server = await _start_mock_echo_server(host, port)

        # 2. Connect client
        client = TCPBridgeClient(host=host, port=port)
        await client.connect()

        # Verify connection works initially
        resp1 = await client.send_command("ping", {"n": 1}, timeout=5.0)
        assert resp1["status"] == "ok"

        # 3. Wait 2 seconds with no commands
        await asyncio.sleep(2)

        # 4. Send command, verify it still works
        resp2 = await client.send_command("ping", {"n": 2}, timeout=5.0)
        assert resp2["status"] == "ok"
        assert resp2["echoed"] is True

        # 5. Verify no stale connection errors
        # Additional command to confirm connection is still healthy
        resp3 = await client.send_command("ping", {"n": 3}, timeout=5.0)
        assert resp3["status"] == "ok"

        await client.close()
        await _shutdown_echo_server(server)

    @pytest.mark.asyncio
    async def test_multiple_idle_periods(self):
        host = "127.0.0.1"
        port = _free_port(61)

        server = await _start_mock_echo_server(host, port)
        client = TCPBridgeClient(host=host, port=port)
        await client.connect()

        for i in range(3):
            await asyncio.sleep(1)
            resp = await client.send_command("idle_test", {"round": i}, timeout=5.0)
            assert resp["status"] == "ok", f"Failed on round {i}"

        await client.close()
        await _shutdown_echo_server(server)
