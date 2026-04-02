from __future__ import annotations

from typing import Any


def map_timeframe(mt5: Any, tf: str) -> int:
    """Map string timeframe to MT5 constant, default to M1 if unknown.

    Accepted examples: M1, M5, M15, M30, H1, H4, D1, W1, MN1
    """
    tf = tf.upper()
    mapping = {
        "M1": getattr(mt5, "TIMEFRAME_M1", 1),
        "M5": getattr(mt5, "TIMEFRAME_M5", 5),
        "M15": getattr(mt5, "TIMEFRAME_M15", 15),
        "M30": getattr(mt5, "TIMEFRAME_M30", 30),
        "H1": getattr(mt5, "TIMEFRAME_H1", 60),
        "H4": getattr(mt5, "TIMEFRAME_H4", 240),
        "D1": getattr(mt5, "TIMEFRAME_D1", 1440),
        "W1": getattr(mt5, "TIMEFRAME_W1", 10080),
        "MN1": getattr(mt5, "TIMEFRAME_MN1", 43200),
    }
    return mapping.get(tf, getattr(mt5, "TIMEFRAME_M1", 1))
