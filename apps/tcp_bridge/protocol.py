"""Length-prefixed JSON framing protocol for TCP bridge.

Protocol:
    [4 bytes: payload length (big-endian uint32)] [N bytes: JSON payload]

This provides a simple, unambiguous framing mechanism that works reliably
across TCP's byte stream, avoiding issues with partial reads or message
boundaries.

EA → Server:
    {"type":"get_bars","request_id":"uuid","symbol":"EURUSD","timeframe":"H1","count":100}

Server → EA:
    {"request_id":"uuid","status":"ok","payload":{...}}
    or
    {"request_id":"uuid","status":"error","error":"description"}
"""

from __future__ import annotations

import json
import struct
from typing import Any


# Frame header: 4 bytes, big-endian uint32 for payload length
_HEADER_FORMAT = "!I"
_HEADER_SIZE = struct.calcsize(_HEADER_FORMAT)
# Max payload: 16 MB (generous enough for any MT5 response)
_MAX_PAYLOAD = 16 * 1024 * 1024


def encode_frame(payload: dict[str, Any]) -> bytes:
    """Encode a dict into a length-prefixed TCP frame.

    Args:
        payload: JSON-serializable dict.

    Returns:
        Bytes: [4-byte length][JSON bytes]
    """
    json_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    if len(json_bytes) > _MAX_PAYLOAD:
        raise ValueError(
            f"Payload too large: {len(json_bytes)} bytes (max {_MAX_PAYLOAD})"
        )
    header = struct.pack(_HEADER_FORMAT, len(json_bytes))
    return header + json_bytes


class FrameParser:
    """Incremental parser for length-prefixed JSON frames.

    Handles partial reads by maintaining an internal buffer.
    Call feed() with incoming bytes, then call pop_frame() to
    retrieve complete frames.

    Usage:
        parser = FrameParser()
        parser.feed(incoming_bytes)
        while parser.has_frame():
            frame = parser.pop_frame()
            process(frame)
    """

    __slots__ = ("_buffer", "_expected")

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._expected: int | None = (
            None  # None = reading header, int = reading payload
        )

    def feed(self, data: bytes) -> None:
        """Add incoming bytes to the buffer."""
        self._buffer.extend(data)

    def has_frame(self) -> bool:
        """Check if a complete frame is available."""
        if self._expected is None:
            # Need header
            if len(self._buffer) < _HEADER_SIZE:
                return False
            self._expected = struct.unpack(_HEADER_FORMAT, self._buffer[:_HEADER_SIZE])[
                0
            ]
            if self._expected > _MAX_PAYLOAD:
                raise ValueError(
                    f"Frame too large: {self._expected} bytes (max {_MAX_PAYLOAD})"
                )

        # Check if we have the full payload
        return len(self._buffer) >= _HEADER_SIZE + self._expected

    def pop_frame(self) -> dict[str, Any]:
        """Extract and return the next complete frame as a dict.

        Raises:
            RuntimeError: No complete frame available.
            json.JSONDecodeError: Invalid JSON in payload.
        """
        if not self.has_frame():
            raise RuntimeError("No complete frame available")

        # Slice out the complete frame
        frame_end = _HEADER_SIZE + self._expected
        json_bytes = bytes(self._buffer[_HEADER_SIZE:frame_end])

        # Remove processed bytes
        del self._buffer[:frame_end]
        self._expected = None

        return json.loads(json_bytes.decode("utf-8"))

    def reset(self) -> None:
        """Clear the buffer and reset state."""
        self._buffer.clear()
        self._expected = None
