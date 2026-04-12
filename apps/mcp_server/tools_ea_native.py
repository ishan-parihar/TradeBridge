from __future__ import annotations

import time
import uuid
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field
from mcp.types import ToolAnnotations

from . import mcp
from .shared import (
    get_gateway,
    get_http_client,
    get_settings_cached,
    _tcp_send_and_await,
    _await_result,
    _parse_payload,
    _parse_payload_dict,
    _parse_indicator_value,
    _first_bid_ask,
    is_frozen,
    _check_frozen_response,
)
from mt5_mcp.adapters.common.symbol_utils import normalize_symbol, denormalize_symbol


# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_trailing_stops: dict[str, dict] = {}


def _resolve_order_id_from_tcp_or_gateway(
    cmd_type: str, payload: dict, timeout_s: float = 15.0
) -> dict | None:
    """Try TCP first, then fallback to HTTP gateway queue polling."""
    result = _tcp_send_and_await(cmd_type, payload, timeout_s=timeout_s)
    if result and result.get("status") == "completed":
        return result
    # TCP failed - try HTTP gateway fallback
    if result is None:
        try:
            settings = get_settings_cached()
            client = get_http_client()
            req = client.post(
                f"{settings.gateway_url}/bridge/commands/enqueue",
                params={"type": cmd_type, **payload},
                timeout=5.0,
            )
            req.raise_for_status()
            req_id = req.json().get("id") or req.json().get("request_id")
            if req_id:
                return _await_result(req_id, timeout_s=10.0)
        except Exception:
            pass
    return result


# ---------------------------------------------------------------------------
# Annotations
# ---------------------------------------------------------------------------

_EA_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
    openWorldHint=True,
)

_READ_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)


# ---------------------------------------------------------------------------
# Tool 1: mt5_place_bracket_order
# ---------------------------------------------------------------------------


@mcp.tool(name="mt5_place_bracket_order", annotations=_EA_ANNOTATIONS)
def mt5_place_bracket_order(
    symbol: str,
    volume_lots: float,
    buy_trigger: float,
    sell_trigger: float,
    sl_atr_multiplier: float = 1.5,
    tp_atr_multiplier: float = 2.0,
    session_id: Optional[str] = None,
    strategy_id: Optional[str] = None,
    intent_id: Optional[str] = None,
) -> dict:
    try:
        frozen = _check_frozen_response()
        if frozen:
            return frozen

        from mt5_mcp.policy.engine import get_policy

        decision = get_policy().validate_submit_order(
            environment=get_settings_cached().environment
        )
        if not decision.allowed:
            return {
                "status": "error",
                "error_code": "POLICY_BLOCKED",
                "message": decision.reason or "Policy blocked order",
                "details": decision.details,
            }

        atr_result = _tcp_send_and_await(
            "get_indicator",
            {
                "symbol": normalize_symbol(symbol),
                "timeframe": "H1",
                "indicator": "atr",
                "period": 14,
            },
        )
        atr_value = _parse_indicator_value(atr_result)
        if atr_value is None or atr_value <= 0:
            return {
                "status": "error",
                "error_code": "ATR_UNAVAILABLE",
                "message": "Could not retrieve ATR value for bracket calculation",
            }

        sl_distance = atr_value * sl_atr_multiplier
        tp_distance = atr_value * tp_atr_multiplier

        buy_sl = buy_trigger - sl_distance
        buy_tp = buy_trigger + tp_distance
        sell_sl = sell_trigger + sl_distance
        sell_tp = sell_trigger + tp_distance

        buy_result = _resolve_order_id_from_tcp_or_gateway(
            "submit_pending_order",
            {
                "symbol": normalize_symbol(symbol),
                "side": "buy",
                "kind": "stop",
                "price": buy_trigger,
                "volume_lots": volume_lots,
                "sl": buy_sl,
                "tp": buy_tp,
                "session_id": session_id,
                "strategy_id": strategy_id,
                "intent_id": intent_id,
                "idempotency_key": f"bracket_buy_{intent_id or 'none'}_{uuid.uuid4().hex[:6]}",
            },
        )
        if not buy_result or buy_result.get("status") != "completed":
            return {
                "status": "error",
                "error_code": "BUY_ORDER_FAILED",
                "message": "BUY STOP order failed",
                "details": buy_result,
            }

        buy_payload = _parse_payload_dict(buy_result)
        buy_order_id = (
            buy_payload.get("order")
            or buy_payload.get("order_id")
            or buy_payload.get("ticket")
        )

        sell_result = _resolve_order_id_from_tcp_or_gateway(
            "submit_pending_order",
            {
                "symbol": normalize_symbol(symbol),
                "side": "sell",
                "kind": "stop",
                "price": sell_trigger,
                "volume_lots": volume_lots,
                "sl": sell_sl,
                "tp": sell_tp,
                "session_id": session_id,
                "strategy_id": strategy_id,
                "intent_id": intent_id,
                "idempotency_key": f"bracket_sell_{intent_id or 'none'}_{uuid.uuid4().hex[:6]}",
            },
        )
        if not sell_result or sell_result.get("status") != "completed":
            return {
                "status": "error",
                "error_code": "SELL_ORDER_FAILED",
                "message": "SELL STOP order failed",
                "buy_order_id": buy_order_id,
                "details": sell_result,
            }

        sell_payload = _parse_payload_dict(sell_result)
        sell_order_id = (
            sell_payload.get("order")
            or sell_payload.get("order_id")
            or sell_payload.get("ticket")
        )

        return {
            "status": "placed",
            "buy_order_id": buy_order_id,
            "sell_order_id": sell_order_id,
            "buy_sl": buy_sl,
            "buy_tp": buy_tp,
            "sell_sl": sell_sl,
            "sell_tp": sell_tp,
            "atr_value": atr_value,
        }
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool 2: mt5_ea_bracket_start
# ---------------------------------------------------------------------------


@mcp.tool(name="mt5_ea_bracket_start", annotations=_EA_ANNOTATIONS)
def mt5_ea_bracket_start(
    buy_order_ticket: str,
    sell_order_ticket: str,
    bracket_id: str,
    comment: str = "",
    magic_filter: int = 0,
) -> dict:
    """Register two existing pending orders as a bracket.

    The orders must already exist (placed via mt5_submit_pending_order).
    This links them so that when one fills, the other is auto-cancelled.
    """
    try:
        frozen = _check_frozen_response()
        if frozen:
            return frozen

        params = {
            "buy_order_ticket": buy_order_ticket,
            "sell_order_ticket": sell_order_ticket,
            "bracket_id": bracket_id,
        }
        if comment:
            params["comment"] = comment
        if magic_filter:
            params["magic_filter"] = magic_filter

        tcp_result = _tcp_send_and_await("bracket_start", params)
        if tcp_result and tcp_result.get("status") == "completed":
            payload = _parse_payload_dict(tcp_result)
            if payload:
                return payload

        from mt5_mcp.adapters.ea_bridge_adapter.adapter import EABridgeAdapter

        try:
            adapter = EABridgeAdapter()
            result = adapter.ea_bracket_start(
                buy_order_ticket=buy_order_ticket,
                sell_order_ticket=sell_order_ticket,
                bracket_id=bracket_id,
                comment=comment,
                magic_filter=magic_filter,
            )
            if result.get("status") == "completed":
                return result.get("result", {}).get("payload", result)
        except Exception:
            pass

        return {
            "status": "error",
            "message": "bracket_start failed via both TCP and HTTP",
        }
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool 3: mt5_ea_bracket_stop
# ---------------------------------------------------------------------------


@mcp.tool(name="mt5_ea_bracket_stop", annotations=_EA_ANNOTATIONS)
def mt5_ea_bracket_stop(
    bracket_id: str,
    intent_id: Optional[str] = None,
) -> dict:
    try:
        result = _tcp_send_and_await(
            "bracket_stop",
            {
                "bracket_id": bracket_id,
                "intent_id": intent_id,
            },
        )
        if result and result.get("status") == "completed":
            payload = _parse_payload_dict(result)
            if payload:
                return payload
        return {"status": "error", "message": "bracket_stop failed", "details": result}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool 4: mt5_ea_bracket_list
# ---------------------------------------------------------------------------


@mcp.tool(name="mt5_ea_bracket_list", annotations=_READ_ANNOTATIONS)
def mt5_ea_bracket_list() -> dict:
    try:
        result = _tcp_send_and_await("bracket_list", {})
        if result and result.get("status") == "completed":
            payload = _parse_payload_dict(result)
            return {"brackets": payload.get("brackets", payload.get("list", []))}
        return {"brackets": []}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool 5: mt5_ea_bracket_tick
# ---------------------------------------------------------------------------


@mcp.tool(name="mt5_ea_bracket_tick", annotations=_EA_ANNOTATIONS)
def mt5_ea_bracket_tick() -> dict:
    try:
        result = _tcp_send_and_await("bracket_tick", {})
        if result and result.get("status") == "completed":
            payload = _parse_payload_dict(result)
            if payload:
                return payload
        return result or {"status": "no_result"}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool 6: mt5_set_trailing_stop
# ---------------------------------------------------------------------------


@mcp.tool(name="mt5_set_trailing_stop", annotations=_EA_ANNOTATIONS)
def mt5_set_trailing_stop(
    position_id: str,
    distance_atr_multiplier: float = 1.5,
    lock_in_profit_after_atr: float = 1.0,
    check_interval_seconds: int = 30,
) -> dict:
    try:
        from .tools_resources import mt5_positions_open, mt5_symbol_info

        positions_result = mt5_positions_open()
        positions = positions_result.get("positions", [])
        position = None
        for p in positions:
            if (
                p.get("position_id") == position_id
                or p.get("id") == position_id
                or p.get("ticket") == position_id
            ):
                position = p
                break

        if position is None:
            return {
                "status": "error",
                "position_id": position_id,
                "message": "Position not found",
            }

        sym = position.get("symbol", "")
        sym_info = mt5_symbol_info(sym)
        point = sym_info.get("point", 0.00001)
        if isinstance(point, str):
            point = float(point)

        atr_result = _tcp_send_and_await(
            "get_indicator",
            {
                "symbol": normalize_symbol(sym),
                "timeframe": "H1",
                "indicator": "atr",
                "period": 14,
            },
        )
        atr_value = _parse_indicator_value(atr_result)
        if atr_value is None or atr_value <= 0:
            return {
                "status": "error",
                "error_code": "ATR_UNAVAILABLE",
                "message": "Could not retrieve ATR value",
            }

        distance_points = int(atr_value * distance_atr_multiplier / point)
        lock_in_points = int(atr_value * lock_in_profit_after_atr / point)

        _trailing_stops[position_id] = {
            "position_id": position_id,
            "symbol": sym,
            "side": position.get("side", "buy"),
            "distance_points": distance_points,
            "lock_in_points": lock_in_points,
            "check_interval": check_interval_seconds,
            "initial_sl": position.get("sl"),
            "active": True,
            "last_check": time.time(),
        }

        return {
            "status": "active",
            "position_id": position_id,
            "distance_points": distance_points,
            "lock_in_points": lock_in_points,
        }
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool 7: mt5_trailing_stop_cancel
# ---------------------------------------------------------------------------


@mcp.tool(name="mt5_trailing_stop_cancel", annotations=_EA_ANNOTATIONS)
def mt5_trailing_stop_cancel(position_id: str) -> dict:
    try:
        if position_id in _trailing_stops:
            _trailing_stops[position_id]["active"] = False
            del _trailing_stops[position_id]
            return {"status": "cancelled", "position_id": position_id}
        return {"status": "not_found", "position_id": position_id}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool 8: mt5_trailing_stop_list
# ---------------------------------------------------------------------------


@mcp.tool(name="mt5_trailing_stop_list", annotations=_READ_ANNOTATIONS)
def mt5_trailing_stop_list() -> dict:
    try:
        active = {
            pid: {k: v for k, v in ts.items() if k != "last_check"}
            for pid, ts in _trailing_stops.items()
            if ts.get("active")
        }
        return {"active_stops": active, "count": len(active)}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool 9: mt5_ea_trailing_start
# ---------------------------------------------------------------------------


@mcp.tool(name="mt5_ea_trailing_start", annotations=_EA_ANNOTATIONS)
def mt5_ea_trailing_start(
    position_id: str,
    distance_atr_multiplier: float = 1.5,
    lock_in_profit_after_atr: float = 1.0,
    check_interval_seconds: int = 30,
    intent_id: Optional[str] = None,
) -> dict:
    try:
        result = _tcp_send_and_await(
            "trailing_start",
            {
                "position_id": position_id,
                "distance_atr_multiplier": distance_atr_multiplier,
                "lock_in_profit_after_atr": lock_in_profit_after_atr,
                "check_interval_seconds": check_interval_seconds,
                "intent_id": intent_id,
            },
        )
        if result and result.get("status") == "completed":
            payload = _parse_payload_dict(result)
            if payload:
                return payload
        return result or {"status": "no_result"}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool 10: mt5_ea_trailing_stop
# ---------------------------------------------------------------------------


@mcp.tool(name="mt5_ea_trailing_stop", annotations=_EA_ANNOTATIONS)
def mt5_ea_trailing_stop(
    position_id: str,
    intent_id: Optional[str] = None,
) -> dict:
    try:
        result = _tcp_send_and_await(
            "trailing_stop",
            {
                "position_id": position_id,
                "intent_id": intent_id,
            },
        )
        if result and result.get("status") == "completed":
            payload = _parse_payload_dict(result)
            if payload:
                return payload
        return result or {"status": "no_result"}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool 11: mt5_ea_trailing_list
# ---------------------------------------------------------------------------


@mcp.tool(name="mt5_ea_trailing_list", annotations=_READ_ANNOTATIONS)
def mt5_ea_trailing_list() -> dict:
    try:
        result = _tcp_send_and_await("trailing_list", {})
        if result and result.get("status") == "completed":
            payload = _parse_payload_dict(result)
            if payload:
                return payload
        return result or {"trailing_stops": []}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool 12: mt5_ea_trailing_tick
# ---------------------------------------------------------------------------


@mcp.tool(name="mt5_ea_trailing_tick", annotations=_EA_ANNOTATIONS)
def mt5_ea_trailing_tick() -> dict:
    try:
        result = _tcp_send_and_await("trailing_tick", {})
        if result and result.get("status") == "completed":
            payload = _parse_payload_dict(result)
            if payload:
                return payload
        return result or {"status": "no_result"}
    except Exception as e:
        return {"error": str(e)}
