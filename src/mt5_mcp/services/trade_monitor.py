"""Core logic for trade monitoring: duration parsing and price bracket computation.

This module provides pure utility functions for parsing duration specifications,
computing target/invalidation price brackets, and checking price conditions.
It has no dependencies on MT5, FastAPI, or network operations.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass
class TradeMonitorResult:
    """Result of a price monitoring cycle.

    Attributes:
        symbol: The MT5 symbol being monitored.
        reason: The outcome of the check — "target_reached", "invalidation_hit", or "active".
        current_price: The most recent market price observed.
        bid: Current bid price.
        ask: Current ask price.
        distance_to_target_pips: Distance from current price to target, expressed in pips.
        distance_to_invalidation_pips: Distance from current price to invalidation, expressed in pips.
        elapsed_seconds: Seconds elapsed since monitoring started.
        duration_seconds: Total monitoring duration in seconds.
        market_context: Optional free-text context about market conditions.
    """

    symbol: str
    reason: str
    current_price: float
    bid: float
    ask: float
    distance_to_target_pips: float
    distance_to_invalidation_pips: float
    elapsed_seconds: float
    duration_seconds: int
    market_context: str | None = None


# Mapping of shorthand timeframe codes to seconds.
_TIMEFRAME_SHORTCUTS: dict[str, int] = {
    "M1": 60,
    "M5": 300,
    "M10": 600,
    "M15": 900,
    "M30": 1800,
    "H1": 3600,
    "H4": 14400,
    "D1": 86400,
    "W1": 604800,
    "MN": 2592000,
}

# Timeframe in seconds for resolving bar-count durations.
_TIMEFRAME_SECONDS: dict[str, int] = {
    "M1": 60,
    "M5": 300,
    "M10": 600,
    "M15": 900,
    "M30": 1800,
    "H1": 3600,
    "H4": 14400,
    "D1": 86400,
    "W1": 604800,
    "MN": 2592000,
}


def parse_duration(duration_str: str, default_timeframe: str = "H1") -> int:
    """Parse a duration specification string into seconds.

    Supported formats:
        - ``"M5"``, ``"M15"``, ``"H1"``, ``"D1"``, etc.: standard timeframe shortcuts
          returning the number of seconds in one bar of that timeframe.
        - ``"H1:4"``, ``"M15:10"``: N bars of the given timeframe (e.g. 4 H1 bars = 14400s).
        - ``"HH:MM"``: seconds remaining until the next occurrence of HH:MM UTC.
          If the time has already passed today, the computation rolls over to tomorrow.
        - ``"300"``: plain integer seconds.
        - ``"5m"``, ``"10m"``, ``"15m"``: minute shorthand (equivalent to M5, M10, M15).

    Args:
        duration_str: The duration specification to parse.
        default_timeframe: Timeframe used to resolve bar-count expressions when the
            format is ``"TIMEFRAME:N"``. Also used as the base for standalone integer
            interpretation when context requires it (though bare integers are treated
            as raw seconds regardless).

    Returns:
        Duration expressed as an integer number of seconds.

    Raises:
        ValueError: If the input string does not match any supported format.
    """
    if not duration_str or not isinstance(duration_str, str):
        raise ValueError(f"Invalid duration string: {duration_str!r}")

    s = duration_str.strip()

    # --- Minute shorthand: "5m", "10m", "15m" ---
    if s.endswith("m") and s[:-1].isdigit():
        minutes = int(s[:-1])
        if minutes <= 0:
            raise ValueError(f"Duration must be positive, got: {duration_str!r}")
        return minutes * 60

    # --- Timeframe shortcut: "M5", "H1", "D1" ---
    if s in _TIMEFRAME_SHORTCUTS:
        return _TIMEFRAME_SHORTCUTS[s]

    # --- Bar-count format: "H1:4", "M15:10" ---
    if ":" in s:
        parts = s.split(":")

        # Could be HH:MM time format (both parts are digits, first part is 0-23, second is 0-59)
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            hh, mm = int(parts[0]), int(parts[1])
            if 0 <= hh <= 23 and 0 <= mm <= 59:
                # HH:MM time format
                now = datetime.now(timezone.utc)
                target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
                if target <= now:
                    # Target already passed today, schedule for tomorrow
                    from datetime import timedelta

                    target += timedelta(days=1)
                return int((target - now).total_seconds())

        # TIMEFRAME:N bar-count format
        if len(parts) == 2:
            tf_part, count_part = parts[0], parts[1]
            if tf_part in _TIMEFRAME_SECONDS and count_part.isdigit():
                count = int(count_part)
                if count <= 0:
                    raise ValueError(
                        f"Bar count must be positive, got: {duration_str!r}"
                    )
                return _TIMEFRAME_SECONDS[tf_part] * count

        raise ValueError(
            f"Invalid duration format (expected TIMEFRAME:N or HH:MM): {duration_str!r}"
        )

    # --- Plain integer seconds: "300" ---
    if s.isdigit():
        value = int(s)
        if value <= 0:
            raise ValueError(f"Duration must be positive, got: {duration_str!r}")
        return value

    raise ValueError(
        f"Unrecognized duration format: {duration_str!r}. "
        f"Expected one of: timeframe shortcut (M5, H1, D1), "
        f"bar count (H1:4), time (14:30), minutes (5m), or raw seconds (300)."
    )


def _compute_boundary_distance(
    spec: dict[str, Any], side: str, symbol_info: dict[str, Any]
) -> float:
    """Compute the absolute price distance for a single boundary specification.

    Args:
        spec: Boundary specification dict. Must have a ``type`` key with one of:
              ``"price"``, ``"pips"``, or ``"atr"``.
        side: ``"buy"`` or ``"sell"``.
        symbol_info: Symbol metadata dict with at least a ``point`` key.

    Returns:
        The absolute price distance (always positive).

    Raises:
        ValueError: If the boundary type is unknown or required fields are missing.
    """
    boundary_type = spec.get("type")

    if boundary_type == "price":
        # Absolute price — the caller provides the full target/invalidation price.
        # The distance is computed by the caller from current_price.
        return spec["value"]

    if boundary_type == "pips":
        value = spec["value"]
        point = symbol_info["point"]
        pip = 10 * point
        return abs(value) * pip

    if boundary_type == "atr":
        multiplier = spec.get("multiplier", 1.0)
        atr_value = spec.get("atr_value")
        if not atr_value or atr_value == 0:
            raise ValueError(
                "ATR boundary requires a non-zero 'atr_value' in the specification."
            )
        return abs(multiplier) * abs(atr_value)

    raise ValueError(f"Unknown boundary type: {boundary_type!r}")


def compute_price_bracket(
    current_price: float,
    side: str,
    spec: dict[str, dict[str, Any]],
    symbol_info: dict[str, Any],
) -> dict[str, float]:
    """Compute target and invalidation prices from boundary specifications.

    Each boundary (``expected`` and ``invalidation``) in the spec can use one of
    three distance models:

    - **price**: ``{"type": "price", "value": 3000.0}`` — absolute price level.
    - **pips**: ``{"type": "pips", "value": 50}`` — N pips from current price
      (1 pip = 10 × point).
    - **atr**: ``{"type": "atr", "multiplier": 1.5, "atr_value": 10.0}`` — N × ATR
      from current price.

    For a **buy** side:
        - target = current_price + distance
        - invalidation = current_price - distance

    For a **sell** side:
        - target = current_price - distance
        - invalidation = current_price + distance

    Args:
        current_price: The current market price of the symbol.
        side: ``"buy"`` or ``"sell"``.
        spec: Dictionary with ``expected`` and ``invalidation`` keys, each mapping
              to a boundary specification dict (see above).
        symbol_info: Symbol metadata with at least a ``point`` key.

    Returns:
        Dictionary with keys:
            - ``target_price``: Computed target price level.
            - ``invalidation_price``: Computed invalidation (stop) price level.
            - ``target_pips``: Distance from current price to target in pips.
            - ``invalidation_pips``: Distance from current price to invalidation in pips.

    Raises:
        ValueError: If side is not "buy" or "sell", or if an ATR boundary lacks
                    a valid ``atr_value``.
    """
    if side not in ("buy", "sell"):
        raise ValueError(f"Side must be 'buy' or 'sell', got: {side!r}")

    point = symbol_info["point"]
    pip = 10 * point

    expected_spec = spec.get("expected", {})
    invalidation_spec = spec.get("invalidation", {})

    # Compute distances for each boundary.
    expected_boundary = _compute_boundary_distance(expected_spec, side, symbol_info)
    invalidation_boundary = _compute_boundary_distance(
        invalidation_spec, side, symbol_info
    )

    if side == "buy":
        # Price-type boundaries give absolute prices, not distances.
        if expected_spec.get("type") == "price":
            target_price = expected_boundary
        else:
            target_price = current_price + expected_boundary

        if invalidation_spec.get("type") == "price":
            invalidation_price = invalidation_boundary
        else:
            invalidation_price = current_price - invalidation_boundary
    else:  # sell
        if expected_spec.get("type") == "price":
            target_price = expected_boundary
        else:
            target_price = current_price - expected_boundary

        if invalidation_spec.get("type") == "price":
            invalidation_price = invalidation_boundary
        else:
            invalidation_price = current_price + invalidation_boundary

    # Convert distances to pip counts.
    target_distance = abs(target_price - current_price)
    invalidation_distance = abs(invalidation_price - current_price)

    target_pips = target_distance / pip if pip != 0 else 0.0
    invalidation_pips = invalidation_distance / pip if pip != 0 else 0.0

    return {
        "target_price": target_price,
        "invalidation_price": invalidation_price,
        "target_pips": target_pips,
        "invalidation_pips": invalidation_pips,
    }


def check_price_condition(
    current_price: float,
    bid: float,
    ask: float,
    target_price: float,
    invalidation_price: float,
    side: str,
) -> str:
    """Check whether the current price has reached a target or invalidation level.

    Price crossing logic depends on the trade direction:

    - **Buy**: target reached when price >= target; invalidation hit when price <= invalidation.
    - **Sell**: target reached when price <= target; invalidation hit when price >= invalidation.

    The ``ask`` price is used for buy-side checks (you exit at bid, but the relevant
    price for a buy position's profit is the bid). The ``bid`` price is used for sell-side
    checks. However, for simplicity, this function uses ``current_price`` as the primary
    reference, with ``bid`` and ``ask`` available for more precise calculations if needed.

    Args:
        current_price: The current market price (typically the last traded or mid price).
        bid: Current bid price.
        ask: Current ask price.
        target_price: The price level at which the trade target is considered reached.
        invalidation_price: The price level at which the trade is considered invalidated.
        side: ``"buy"`` or ``"sell"``.

    Returns:
        One of: ``"target_reached"``, ``"invalidation_hit"``, or ``"active"``.
    """
    if side == "buy":
        # For a long position, profit is realized at bid price.
        price = bid
        if price >= target_price:
            return "target_reached"
        if price <= invalidation_price:
            return "invalidation_hit"
    else:  # sell
        # For a short position, profit is realized at ask price.
        price = ask
        if price <= target_price:
            return "target_reached"
        if price >= invalidation_price:
            return "invalidation_hit"

    return "active"
