from __future__ import annotations

import time
from typing import Any, Optional

from mcp.types import ToolAnnotations

from . import mcp
from .shared import (
    _check_frozen_response,
    _parse_payload,
    _parse_payload_dict,
    _tcp_send_and_await,
    get_gateway,
    get_http_client,
    get_settings_cached,
    is_frozen,
    set_frozen,
    thaw,
    _shutdown_state,
)
from mt5_mcp.adapters.common.symbol_utils import normalize_symbol

_READ_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
)
_WRITE_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True
)


@mcp.tool(annotations=_READ_ANNOTATIONS)
def mt5_health() -> dict:
    try:
        subsystems: dict[str, Any] = {}

        try:
            gw_status = get_gateway().terminal_status()
            connected = getattr(gw_status, "connected", False)
            subsystems["gateway"] = {
                "status": "ok" if connected else "unreachable",
                "details": gw_status.model_dump()
                if hasattr(gw_status, "model_dump")
                else dict(gw_status)
                if hasattr(gw_status, "__dict__")
                else str(gw_status),
            }
        except Exception as e:
            subsystems["gateway"] = {"status": "error", "error": str(e)}

        # HTTP bridge health check
        try:
            resp = get_http_client().post(
                f"{get_settings_cached().gateway_url}/bridge/health"
            )
            subsystems["bridge"] = {
                "status": "ok" if resp.status_code == 200 else "degraded",
                "http_status": resp.status_code,
            }
        except Exception as e:
            subsystems["bridge"] = {"status": "error", "error": str(e)}

        # TCP check
        try:
            tcp_result = _tcp_send_and_await("get_positions", {}, timeout_s=5.0)
            if tcp_result and tcp_result.get("status") == "completed":
                subsystems["tcp"] = {"status": "ok"}
            else:
                subsystems["tcp"] = {"status": "degraded", "result": tcp_result}
        except Exception as e:
            subsystems["tcp"] = {"status": "error", "error": str(e)}

        # Aggregate overall status
        statuses = [s.get("status") for s in subsystems.values()]
        if "error" in statuses or "unreachable" in statuses:
            overall = "unhealthy"
        elif "degraded" in statuses:
            overall = "degraded"
        else:
            overall = "healthy"

        return {
            "status": overall,
            "subsystems": subsystems,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "frozen": is_frozen(),
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(annotations=_READ_ANNOTATIONS)
def mt5_tool_status() -> dict:
    try:
        # Reads check
        try:
            get_gateway().terminal_status()
            reads_ok = True
        except Exception:
            reads_ok = False

        # Writes check
        failing_writes: list[str] = []
        try:
            result = _tcp_send_and_await("get_account", {}, timeout_s=5.0)
            if not result or result.get("status") != "completed":
                failing_writes.append("get_account")
            writes_ok = len(failing_writes) == 0
        except Exception:
            writes_ok = False
            failing_writes.append("tcp_bridge")

        # Waits and analysis are pure Python, always ok
        waits_ok = True
        analysis_ok = True
        failing_analysis: list[str] = []

        if reads_ok and writes_ok and waits_ok and analysis_ok:
            overall = "ok"
        else:
            overall = "degraded"

        return {
            "reads": {"status": "ok" if reads_ok else "degraded"},
            "writes": {
                "status": "ok" if writes_ok else "degraded",
                "failing": failing_writes,
            },
            "waits": {"status": "ok" if waits_ok else "degraded"},
            "analysis": {
                "status": "ok" if analysis_ok else "degraded",
                "failing": failing_analysis,
            },
            "overall": overall,
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(annotations=_READ_ANNOTATIONS)
def mt5_freeze_status() -> dict:
    try:
        return {
            "frozen": _shutdown_state["frozen"],
            "frozen_at": _shutdown_state["frozen_at"],
            "frozen_by": _shutdown_state["frozen_by"],
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(annotations=_WRITE_ANNOTATIONS)
def mt5_thaw() -> dict:
    try:
        thaw()
        return {"status": "unfrozen", "message": "Trading has been resumed"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(annotations=_WRITE_ANNOTATIONS)
def mt5_safe_shutdown(
    mode: str = "freeze",
    session_id: Optional[str] = None,
    strategy_id: Optional[str] = None,
    intent_id: Optional[str] = None,
) -> dict:
    try:
        if mode not in ("flatten", "freeze", "full"):
            return {
                "error": f"Invalid mode: {mode}. Must be one of: flatten, freeze, full"
            }

        positions_closed: list[Any] = []
        orders_cancelled: list[Any] = []
        failed: list[Any] = []

        # Fetch current positions and orders via TCP
        pos_result = _tcp_send_and_await("get_positions", {})
        orders_result = _tcp_send_and_await("get_orders", {})

        pos_data = _parse_payload_dict(pos_result) if pos_result else {}
        orders_data = _parse_payload_dict(orders_result) if orders_result else {}

        positions = pos_data.get("positions", pos_data.get("data", []))
        orders = orders_data.get("orders", orders_data.get("data", []))

        total_positions = len(positions) if isinstance(positions, list) else 0
        total_orders = len(orders) if isinstance(orders, list) else 0

        # Filter owned positions/orders
        def _is_owned(item: dict) -> bool:
            if session_id and item.get("session_id") == session_id:
                return True
            if strategy_id and item.get("strategy_id") == strategy_id:
                return True
            if not session_id and not strategy_id:
                return True
            return False

        owned_positions = [p for p in positions if isinstance(p, dict) and _is_owned(p)]
        owned_orders = [o for o in orders if isinstance(o, dict) and _is_owned(o)]

        # Flatten: close positions
        if mode in ("flatten", "full"):
            for pos in owned_positions:
                pos_id = pos.get("position_id") or pos.get("ticket") or pos.get("id")
                if not pos_id:
                    failed.append(
                        {
                            "action": "close_position",
                            "item": pos,
                            "reason": "no position_id",
                        }
                    )
                    continue
                try:
                    close_result = _tcp_send_and_await(
                        "close_position",
                        {
                            "position_id": pos_id,
                            "session_id": session_id,
                            "strategy_id": strategy_id,
                        },
                    )
                    if close_result and close_result.get("status") == "completed":
                        positions_closed.append(pos_id)
                    else:
                        failed.append(
                            {
                                "action": "close_position",
                                "position_id": pos_id,
                                "result": close_result,
                            }
                        )
                except Exception as e:
                    failed.append(
                        {
                            "action": "close_position",
                            "position_id": pos_id,
                            "error": str(e),
                        }
                    )

        # Freeze: cancel orders
        if mode in ("freeze", "full"):
            for order in owned_orders:
                order_id = (
                    order.get("order_id") or order.get("ticket") or order.get("id")
                )
                if not order_id:
                    failed.append(
                        {
                            "action": "cancel_order",
                            "item": order,
                            "reason": "no order_id",
                        }
                    )
                    continue
                try:
                    cancel_result = _tcp_send_and_await(
                        "cancel_order",
                        {
                            "order_id": order_id,
                            "session_id": session_id,
                            "strategy_id": strategy_id,
                        },
                    )
                    if cancel_result and cancel_result.get("status") == "completed":
                        orders_cancelled.append(order_id)
                    else:
                        failed.append(
                            {
                                "action": "cancel_order",
                                "order_id": order_id,
                                "result": cancel_result,
                            }
                        )
                except Exception as e:
                    failed.append(
                        {
                            "action": "cancel_order",
                            "order_id": order_id,
                            "error": str(e),
                        }
                    )

        # Full: freeze after cleanup
        if mode == "full":
            set_frozen(True, by=intent_id or "safe_shutdown")

        return {
            "mode": mode,
            "positions_closed": positions_closed,
            "orders_cancelled": orders_cancelled,
            "failed": failed,
            "summary": {
                "total_positions_found": total_positions,
                "total_orders_found": total_orders,
                "positions_closed": len(positions_closed),
                "orders_cancelled": len(orders_cancelled),
                "failed": len(failed),
            },
        }
    except Exception as e:
        return {"error": str(e)}
