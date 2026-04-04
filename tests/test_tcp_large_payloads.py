"""Tests for TCP bridge handling of large payloads."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import struct
import time

import pytest

from apps.tcp_bridge.protocol import FrameParser, _MAX_PAYLOAD, encode_frame


async def _start_echo_server(
    host: str, port: int, delay: float = 0.0
) -> asyncio.Server:
    async def handle_client(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        buffer = bytearray()
        expected: int | None = None
        while True:
            data = await reader.read(65536)
            if not data:
                break
            buffer.extend(data)
            while True:
                if expected is None:
                    if len(buffer) < 4:
                        break
                    expected = struct.unpack("!I", buffer[:4])[0]
                if len(buffer) < 4 + expected:
                    break
                payload_bytes = bytes(buffer[4 : 4 + expected])
                del buffer[: 4 + expected]
                expected = None

                if delay > 0:
                    await asyncio.sleep(delay)

                writer.write(struct.pack("!I", len(payload_bytes)) + payload_bytes)
                await writer.drain()

    server = await asyncio.start_server(handle_client, host, port)
    await server.start_serving()
    return server


async def _start_screenshot_server(host: str, port: int) -> asyncio.Server:
    async def handle_client(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        buffer = bytearray()
        while True:
            data = await reader.read(65536)
            if not data:
                break
            buffer.extend(data)

            if len(buffer) < 4:
                continue
            length = struct.unpack("!I", buffer[:4])[0]
            if len(buffer) < 4 + length:
                continue

            request_bytes = bytes(buffer[4 : 4 + length])
            del buffer[: 4 + length]

            request = json.loads(request_bytes)
            request_id = request.get("request_id", "unknown")

            response = {
                "request_id": request_id,
                "status": "ok",
                "payload": {"image_base64": _generate_fake_png_base64()},
            }
            response_bytes = json.dumps(response, separators=(",", ":")).encode("utf-8")
            writer.write(struct.pack("!I", len(response_bytes)) + response_bytes)
            await writer.drain()

    server = await asyncio.start_server(handle_client, host, port)
    await server.start_serving()
    return server


async def _start_delayed_echo_server(
    host: str, port: int, delay: float = 0.1
) -> asyncio.Server:
    async def handle_client(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        buffer = bytearray()
        expected: int | None = None
        while True:
            data = await reader.read(65536)
            if not data:
                break
            buffer.extend(data)
            while True:
                if expected is None:
                    if len(buffer) < 4:
                        break
                    expected = struct.unpack("!I", buffer[:4])[0]
                if len(buffer) < 4 + expected:
                    break
                payload_bytes = bytes(buffer[4 : 4 + expected])
                del buffer[: 4 + expected]
                expected = None

                await asyncio.sleep(delay)

                writer.write(struct.pack("!I", len(payload_bytes)) + payload_bytes)
                await writer.drain()

    server = await asyncio.start_server(handle_client, host, port)
    await server.start_serving()
    return server


def _generate_fake_png_base64(size_bytes: int | None = None) -> str:
    if size_bytes is None:
        size_bytes = int(1024 * 1024 * 1.0)
    raw = os.urandom(size_bytes)
    return base64.b64encode(raw).decode("ascii")


def _make_request(type: str, extra: dict | None = None) -> dict:
    import uuid

    req = {"type": type, "request_id": str(uuid.uuid4())}
    if extra:
        req.update(extra)
    return req


async def _send_frame(writer: asyncio.StreamWriter, payload: dict) -> str:
    frame = encode_frame(payload)
    writer.write(frame)
    await writer.drain()
    return payload.get("request_id", "")


async def _recv_frame(reader: asyncio.StreamReader, timeout: float = 30.0) -> dict:
    buffer = bytearray()
    while len(buffer) < 4:
        chunk = await asyncio.wait_for(reader.read(65536), timeout=timeout)
        if not chunk:
            raise ConnectionError("Server closed connection")
        buffer.extend(chunk)

    length = struct.unpack("!I", buffer[:4])[0]
    remaining = 4 + length - len(buffer)
    while remaining > 0:
        chunk = await asyncio.wait_for(reader.read(remaining), timeout=timeout)
        if not chunk:
            raise ConnectionError("Server closed connection")
        buffer.extend(chunk)
        remaining = 4 + length - len(buffer)

    return json.loads(bytes(buffer[4 : 4 + length]))


_BASE_PORT = 19800


class TestLargePayloadRoundTrip:
    """Verify 100KB through 10MB payloads are handled without corruption."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "size_label,json_size_bytes",
        [
            ("100KB", 100 * 1024),
            ("1MB", 1 * 1024 * 1024),
            ("5MB", 5 * 1024 * 1024),
            ("10MB", 10 * 1024 * 1024),
        ],
    )
    async def test_large_payload_echo(self, size_label: str, json_size_bytes: int):
        port = _BASE_PORT + 10
        server = await _start_echo_server("127.0.0.1", port)

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            try:
                payload_data = os.urandom(json_size_bytes // 2).hex()[
                    : json_size_bytes - 64
                ]
                request = _make_request("echo_test", {"data": payload_data})

                start = time.monotonic()
                await _send_frame(writer, request)
                response = await _recv_frame(reader)
                latency_ms = (time.monotonic() - start) * 1000

                assert response.get("request_id") == request["request_id"]
                assert response.get("data") == payload_data, (
                    f"{size_label}: Data corruption detected — "
                    f"sent {len(payload_data)} chars, received "
                    f"{len(response.get('data', ''))} chars"
                )

                print(f"\n{size_label} ({json_size_bytes:,} bytes): {latency_ms:.1f}ms")

            finally:
                writer.close()
                await writer.wait_closed()
        finally:
            server.close()
            await server.wait_closed()


class TestScreenshotSimulation:
    """Verify base64 PNG screenshot responses are received intact."""

    @pytest.mark.asyncio
    async def test_screenshot_base64_roundtrip(self):
        port = _BASE_PORT + 20
        server = await _start_screenshot_server("127.0.0.1", port)

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            try:
                request = _make_request(
                    "get_chart_screenshot",
                    {
                        "symbol": "XAUUSD",
                        "timeframe": "H1",
                    },
                )

                await _send_frame(writer, request)
                response = await _recv_frame(reader)

                assert response["status"] == "ok"
                assert "payload" in response
                assert "image_base64" in response["payload"]

                base64_data = response["payload"]["image_base64"]

                assert len(base64_data) > 0, "Empty base64 response"

                decoded = base64.b64decode(base64_data)
                assert len(decoded) > 0, "Decoded data is empty"

                assert len(decoded) >= 500_000, (
                    f"Decoded too small: {len(decoded)} bytes"
                )

                print(
                    f"\nScreenshot test: base64 length={len(base64_data)}, "
                    f"decoded={len(decoded):,} bytes"
                )

            finally:
                writer.close()
                await writer.wait_closed()
        finally:
            server.close()
            await server.wait_closed()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("size_kb", [500, 1024, 2048])
    async def test_screenshot_various_sizes(self, size_kb: int):
        port = _BASE_PORT + 21
        expected_b64 = _generate_fake_png_base64(size_kb * 1024)

        async def handle_screenshot(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            buffer = bytearray()
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                buffer.extend(data)
                if len(buffer) < 4:
                    continue
                length = struct.unpack("!I", buffer[:4])[0]
                if len(buffer) < 4 + length:
                    continue
                request_bytes = bytes(buffer[4 : 4 + length])
                del buffer[: 4 + length]
                request = json.loads(request_bytes)

                response = {
                    "request_id": request.get("request_id", ""),
                    "status": "ok",
                    "payload": {"image_base64": expected_b64},
                }
                resp_bytes = json.dumps(response, separators=(",", ":")).encode("utf-8")
                writer.write(struct.pack("!I", len(resp_bytes)) + resp_bytes)
                await writer.drain()

        server = await asyncio.start_server(handle_screenshot, "127.0.0.1", port)
        await server.start_serving()

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            try:
                request = _make_request("get_chart_screenshot")
                await _send_frame(writer, request)
                response = await _recv_frame(reader)

                received_b64 = response["payload"]["image_base64"]
                assert received_b64 == expected_b64, (
                    f"Base64 mismatch at {size_kb}KB: "
                    f"expected len={len(expected_b64)}, got len={len(received_b64)}"
                )

                decoded = base64.b64decode(received_b64)
                assert len(decoded) == size_kb * 1024

            finally:
                writer.close()
                await writer.wait_closed()
        finally:
            server.close()
            await server.wait_closed()


class TestMaxPayloadBoundary:
    """Verify behavior at and beyond the 16MB protocol limit."""

    def test_15mb_payload_succeeds(self):
        raw_bytes = 7_900_000
        payload = {"data": os.urandom(raw_bytes).hex()}
        frame = encode_frame(payload)
        json_size = len(frame) - 4
        assert json_size > 15 * 1024 * 1024

        parser = FrameParser()
        parser.feed(frame)
        assert parser.has_frame()
        result = parser.pop_frame()
        assert len(result["data"]) == len(payload["data"])

    def test_16mb_plus_one_raises(self):
        oversized_data = "x" * (_MAX_PAYLOAD + 1)
        with pytest.raises(ValueError, match="too large"):
            encode_frame({"data": oversized_data})

    def test_exactly_16mb_raises(self):
        data = "x" * _MAX_PAYLOAD
        with pytest.raises(ValueError, match="too large"):
            encode_frame({"data": data})

    def test_frame_parser_rejects_oversized_header(self):
        parser = FrameParser()
        oversized = 17 * 1024 * 1024
        header = struct.pack("!I", oversized)
        parser.feed(header)

        with pytest.raises(ValueError, match="too large"):
            parser.has_frame()


class TestConcurrentLargePayloads:
    """Verify multiple large payloads don't interfere with each other."""

    @pytest.mark.asyncio
    async def test_concurrent_500kb_payloads(self):
        port = _BASE_PORT + 30
        server = await _start_delayed_echo_server("127.0.0.1", port, delay=0.05)

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)

            payload_size = 500 * 1024
            num_requests = 3
            request_ids: list[str] = []

            for i in range(num_requests):
                payload_data = os.urandom(payload_size).hex()
                request = _make_request(
                    "concurrent_test",
                    {
                        "data": payload_data,
                        "index": i,
                    },
                )
                request_ids.append(request["request_id"])
                await _send_frame(writer, request)

            responses: list[dict] = []
            for _ in range(num_requests):
                resp = await _recv_frame(reader, timeout=30.0)
                responses.append(resp)

            response_ids = {r["request_id"] for r in responses}
            for req_id in request_ids:
                assert req_id in response_ids, (
                    f"Request {req_id} not found in responses"
                )

            for resp in responses:
                assert resp.get("status") != "error", f"Got error: {resp}"
                assert "data" in resp, f"Missing data in response: {resp.keys()}"
                assert len(resp["data"]) == payload_size * 2, (
                    f"Data length mismatch: expected {payload_size * 2}, "
                    f"got {len(resp['data'])}"
                )

            assert len(response_ids) == num_requests, (
                "Duplicate request IDs in responses"
            )

            writer.close()
            await writer.wait_closed()
        finally:
            server.close()
            await server.wait_closed()

    @pytest.mark.asyncio
    async def test_concurrent_separate_connections(self):
        port = _BASE_PORT + 31
        server = await _start_echo_server("127.0.0.1", port)

        try:
            payload_size = 256 * 1024
            num_connections = 3

            async def client_task(conn_id: int) -> dict:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                try:
                    unique_data = (
                        f"conn_{conn_id}_" + os.urandom(payload_size - 10).hex()
                    )
                    request = _make_request("isolation_test", {"data": unique_data})
                    await _send_frame(writer, request)
                    response = await _recv_frame(reader, timeout=15.0)
                    return {
                        "conn_id": conn_id,
                        "request_id": request["request_id"],
                        "response": response,
                        "sent_data": unique_data,
                    }
                finally:
                    writer.close()
                    await writer.wait_closed()

            results = await asyncio.gather(
                *[client_task(i) for i in range(num_connections)]
            )

            for r in results:
                resp = r["response"]
                assert resp["request_id"] == r["request_id"], (
                    f"Connection {r['conn_id']}: request_id mismatch"
                )
                assert resp.get("data") == r["sent_data"], (
                    f"Connection {r['conn_id']}: data corruption"
                )

        finally:
            server.close()
            await server.wait_closed()


class TestPartialLargeFrameReassembly:
    """Verify large frames are correctly reassembled from small chunks."""

    def test_1mb_frame_in_4kb_chunks(self):
        payload_data = os.urandom(1024 * 1024).hex()
        payload = {
            "type": "large_data",
            "request_id": "chunk-test-1",
            "data": payload_data,
        }

        frame = encode_frame(payload)
        chunk_size = 4096

        parser = FrameParser()

        for i in range(0, len(frame), chunk_size):
            chunk = frame[i : i + chunk_size]
            parser.feed(chunk)

            if i + chunk_size < len(frame):
                assert not parser.has_frame(), (
                    f"has_frame() returned True prematurely at byte {i}"
                )

        assert parser.has_frame(), "has_frame() is False after all chunks fed"

        result = parser.pop_frame()
        assert result["request_id"] == "chunk-test-1"
        assert result["data"] == payload_data
        assert result["type"] == "large_data"

    def test_5mb_frame_in_variable_chunks(self):
        payload_data = os.urandom(5 * 1024 * 1024).hex()
        payload = {
            "type": "variable_chunk",
            "request_id": "var-chunk",
            "data": payload_data,
        }

        frame = encode_frame(payload)

        parser = FrameParser()
        offset = 0
        chunk_sizes = [1024, 8192, 4096, 16384, 2048, 65536, 32768]

        while offset < len(frame):
            size = chunk_sizes.pop(0) if chunk_sizes else 4096
            chunk = frame[offset : offset + size]
            parser.feed(chunk)
            offset += size

            if offset < len(frame):
                assert not parser.has_frame()

        assert parser.has_frame()
        result = parser.pop_frame()
        assert result["request_id"] == "var-chunk"
        assert len(result["data"]) == len(payload_data)

    def test_multiple_large_frames_chunked(self):
        frame1 = encode_frame(
            {
                "type": "multi_1",
                "request_id": "multi-frame-1",
                "data": os.urandom(500 * 1024).hex(),
            }
        )
        frame2 = encode_frame(
            {
                "type": "multi_2",
                "request_id": "multi-frame-2",
                "data": os.urandom(500 * 1024).hex(),
            }
        )

        chunk_size = 4096
        parser = FrameParser()

        for i in range(0, len(frame1), chunk_size):
            parser.feed(frame1[i : i + chunk_size])

        assert parser.has_frame()
        result1 = parser.pop_frame()
        assert result1["request_id"] == "multi-frame-1"

        for i in range(0, len(frame2), chunk_size):
            parser.feed(frame2[i : i + chunk_size])

        assert parser.has_frame()
        result2 = parser.pop_frame()
        assert result2["request_id"] == "multi-frame-2"

    def test_byte_by_byte_large_frame(self):
        payload = {
            "type": "byte_test",
            "request_id": "byte-by-byte",
            "data": os.urandom(100 * 1024).hex(),
        }
        frame = encode_frame(payload)

        parser = FrameParser()
        for i, byte_val in enumerate(frame):
            parser.feed(bytes([byte_val]))
            if i < len(frame) - 1:
                assert not parser.has_frame(), f"Premature frame at byte {i}"

        assert parser.has_frame()
        result = parser.pop_frame()
        assert result["request_id"] == "byte-by-byte"
        assert result["type"] == "byte_test"
