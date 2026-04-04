from __future__ import annotations

import asyncio
import json
import struct

import pytest

from apps.tcp_bridge.protocol import FrameParser, encode_frame


class TestEncodeFrame:
    def test_encodes_simple_dict(self):
        payload = {"type": "get_bars", "request_id": "abc123"}
        result = encode_frame(payload)

        header = result[:4]
        body = result[4:]
        length = struct.unpack("!I", header)[0]

        assert length == len(body)
        assert json.loads(body) == payload

    def test_encodes_with_nested_payload(self):
        payload = {"type": "submit_order", "request_id": "x", "symbol": "EURUSD"}
        result = encode_frame(payload)
        length = struct.unpack("!I", result[:4])[0]
        assert json.loads(result[4 : 4 + length]) == payload

    def test_compact_json(self):
        result = encode_frame({"a": 1, "b": "hello"})
        body = result[4:]
        assert b" " not in body

    def test_raises_on_oversized_payload(self):
        with pytest.raises(ValueError, match="too large"):
            encode_frame({"data": "x" * (17 * 1024 * 1024)})


class TestFrameParser:
    def test_complete_frame(self):
        parser = FrameParser()
        frame_data = encode_frame({"type": "test", "request_id": "1"})
        parser.feed(frame_data)

        assert parser.has_frame()
        result = parser.pop_frame()
        assert result["type"] == "test"
        assert result["request_id"] == "1"

    def test_partial_header(self):
        parser = FrameParser()
        parser.feed(b"\x00\x00")
        assert not parser.has_frame()

    def test_partial_payload(self):
        parser = FrameParser()
        frame_data = encode_frame({"type": "test"})
        parser.feed(frame_data[:6])
        assert not parser.has_frame()

        parser.feed(frame_data[6:])
        assert parser.has_frame()
        result = parser.pop_frame()
        assert result["type"] == "test"

    def test_multiple_frames_in_one_feed(self):
        parser = FrameParser()
        f1 = encode_frame({"type": "first"})
        f2 = encode_frame({"type": "second"})
        parser.feed(f1 + f2)

        assert parser.has_frame()
        r1 = parser.pop_frame()
        assert r1["type"] == "first"

        assert parser.has_frame()
        r2 = parser.pop_frame()
        assert r2["type"] == "second"

    def test_frames_arriving_byte_by_byte(self):
        parser = FrameParser()
        frame_data = encode_frame({"k": "v"})
        for byte in frame_data:
            parser.feed(bytes([byte]))

        assert parser.has_frame()
        result = parser.pop_frame()
        assert result == {"k": "v"}

    def test_reset(self):
        parser = FrameParser()
        parser.feed(encode_frame({"type": "test"}))
        parser.reset()
        assert not parser.has_frame()

    def test_raises_on_oversized_frame(self):
        parser = FrameParser()
        oversized_length = 17 * 1024 * 1024
        header = struct.pack("!I", oversized_length)
        parser.feed(header)

        with pytest.raises(ValueError, match="too large"):
            parser.has_frame()
