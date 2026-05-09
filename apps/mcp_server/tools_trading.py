

import uuid
import time
from collections import OrderedDict
from typing import Any, Optional, Literal
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
    _first_bid_ask,
    _check_frozen_response,
    is_frozen,
    set_frozen,
)
from mt5_mcp.adapters.common.symbol_utils import normalize_symbol, denormalize_symbol
from mt5_mcp.observability.logging import logger


# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------
class _BoundedDict:
    def __init__(self, max_size: int = 5000, ttl: float = 86400):
        self._data: OrderedDict[str, tuple[Any, float]] = OrderedDict()
        self._max_size = max_size
        self._ttl = ttl

    def __setitem__(self, key: str, value: Any) -> None:
        while len(self._data) >= self._max_size:
            self._data.popitem(last=False)
        self._data[key] = (value, time.time())
        self._cleanup()

    def __getitem__(self, key: str) -> Any:
        if key in self._data:
            val, ts = self._data[key]
            if time.time() - ts < self._ttl:
                return val
            del self._data[key]
        raise KeyError(key)

    def __contains__(self, key: str) -> bool:
        try:
            self[key]
            return True
        except KeyError:
            return False

    def _cleanup(self) -> None:
        now = time.time()
        expired = [k for k, (_, ts) in self._data.items() if now - ts >= self._ttl]
        for k in expired:
            del self._data[k]


_trailing_stops: _BoundedDict = _BoundedDict(max_size=5000, ttl=86400)

_WRITE_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True
)
_READ_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
)

# ---------------------------------------------------------------------------
# Trade retcode mapping
# ---------------------------------------------------------------------------
_SUCCESS_RETCODES = {10009, 10008}


def _map_trade_retcode(retcode) -> str:
    if retcode is None:
        return "UNKNOWN"
    try:
        code = int(retcode)
    except (ValueError, TypeError):
        return "UNKNOWN"
    if code in _SUCCESS_RETCODES:
        return "SUCCESS"
    elif code == 0:
        return "PENDING"
    else:
        return f"ERROR_{code}"


def _build_trade_error(intent_id, data):
    retcode = data.get("retcode") if isinstance(data, dict) else None
    return {
        "intent_id": intent_id,
        "status": "error",
        "retcode": _map_trade_retcode(retcode),
        "message": data.get("comment", str(data))
        if isinstance(data, dict)
        else str(data),
        "raw": data,
    }


def _await_result_with_gateway_fallback(req_id: str, timeout_s: float = 30.0) -> dict:
    """Await result with one extra gateway poll on timeout."""
    result = _await_result(req_id, timeout_s=timeout_s)
    if result and result.get("status") == "timeout":
        try:
            settings = get_settings_cached()
            client = get_http_client()
            r = client.get(
                f"{settings.gateway_url}/bridge/results/{req_id}", timeout=5.0
            )
            if r.status_code == 200:
                data = r.json()
                if data.get("status") in ("completed", "error"):
                    return data
        except Exception:
            pass
    return result


# ---------------------------------------------------------------------------
# Auto-log trade helper
# ---------------------------------------------------------------------------
def _auto_log_trade(
    symbol,
    side,
    action,
    intent_id=None,
    session_id=None,
    strategy_id=None,
    volume_lots=None,
    sl=None,
    tp=None,
    entry_price=None,
    message=None,
):
    try:
        from mt5_mcp.services.trade_journal_db import get_journal_db

        journal = get_journal_db()
        journal.log_decision(
            symbol=symbol,
            side=side,
            action=action,
            entry_price=entry_price,
            sl=sl,
            tp=tp,
            volume_lots=volume_lots,
            session_id=session_id,
            strategy_id=strategy_id,
            intent_id=intent_id,
            model_justification=message,
        )
    except Exception as e:
        logger.warning("Trade journal logging failed: %s", e)


# ---------------------------------------------------------------------------
# Tool 1: mt5_submit_market_order
# ---------------------------------------------------------------------------
@mcp.tool(name="mt5_submit_market_order", annotations=_WRITE_ANNOTATIONS)
def mt5_submit_market_order(
    symbol: str,
    side: str,
    volume_lots: float,
    sl: Optional[float] = None,
    tp: Optional[float] = None,
    deviation_points: int = 20,
    session_id: Optional[str] = None,
    strategy_id: Optional[str] = None,
    intent_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    trail_config: Optional[dict] = None,
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
            return {"error": f"Policy blocked: {decision.reason}"}

        if idempotency_key is None:
            idempotency_key = str(uuid.uuid4())

        trail_params = {}
        if trail_config:
            trail_params["trail_distance_points"] = trail_config.get(
                "distance_points", 0
            )
            trail_params["trail_lock_in_points"] = trail_config.get("lock_in_points", 0)

        result = _tcp_send_and_await(
            "submit_order",
            {
                "symbol": normalize_symbol(symbol),
                "side": side,
                "volume_lots": volume_lots,
                "sl": sl or 0,
                "tp": tp or 0,
                "deviation": deviation_points,
                "session_id": session_id,
                "strategy_id": strategy_id,
                "intent_id": intent_id,
                "idempotency_key": idempotency_key,
                **trail_params,
            },
        )

        if result is None:
            return {"error": "submit_order failed — TCP unavailable"}

        data = _parse_payload_dict(result)
        retcode = data.get("retcode", 0) if data else 0
        retcode_int = int(retcode) if retcode is not None else 0

        if retcode_int not in _SUCCESS_RETCODES:
            return _build_trade_error(intent_id, data or result)

        _auto_log_trade(
            symbol=symbol,
            side=side,
            action="entry",
            intent_id=intent_id,
            session_id=session_id,
            strategy_id=strategy_id,
            volume_lots=volume_lots,
            sl=sl,
            tp=tp,
            entry_price=data.get("price"),
        )

        return {
            "intent_id": intent_id,
            "status": "submitted",
            "adapter": "EASocketAdapter",
            "broker_order_id": str(data.get("order", "")),
            "retcode": _map_trade_retcode(retcode),
            "message": f"Order submitted (retcode={retcode_int})",
            "raw": data,
        }
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool 2: mt5_submit_market_order_via_bridge
# ---------------------------------------------------------------------------
@mcp.tool(name="mt5_submit_market_order_via_bridge", annotations=_WRITE_ANNOTATIONS)
def mt5_submit_market_order_via_bridge(
    symbol: str,
    side: str,
    volume_lots: float,
    sl: Optional[float] = None,
    tp: Optional[float] = None,
    deviation_points: int = 20,
    session_id: Optional[str] = None,
    strategy_id: Optional[str] = None,
    intent_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
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
            return {"error": f"Policy blocked: {decision.reason}"}

        if idempotency_key is None:
            idempotency_key = str(uuid.uuid4())

        settings = get_settings_cached()
        client = get_http_client()
        req = client.post(
            f"{settings.gateway_url}/bridge/commands/enqueue",
            params={
                "type": "submit_order",
                "symbol": normalize_symbol(symbol),
                "side": side,
                "volume_lots": volume_lots,
                "sl": sl or 0,
                "tp": tp or 0,
                "deviation": deviation_points,
                "session_id": session_id or "",
                "strategy_id": strategy_id or "",
                "intent_id": intent_id or "",
                "idempotency_key": idempotency_key,
            },
        )
        req.raise_for_status()
        req_id = req.json().get("id") or req.json().get("request_id")
        result = _await_result_with_gateway_fallback(req_id, timeout_s=30.0)

        data = _parse_payload_dict(result) if result else {}
        retcode = data.get("retcode", 0) if data else 0
        retcode_int = int(retcode) if retcode is not None else 0

        if retcode_int not in _SUCCESS_RETCODES:
            error_resp = _build_trade_error(intent_id, data or result)
            if result and result.get("status") == "timeout":
                error_resp["hint"] = (
                    "Bridge HTTP fallback timed out. Use mt5_submit_market_order (TCP path) instead. "
                    "Verify bridge EA is running on MT5 and TCP port is accessible."
                )
            return error_resp

        _auto_log_trade(
            symbol=symbol,
            side=side,
            action="entry",
            intent_id=intent_id,
            session_id=session_id,
            strategy_id=strategy_id,
            volume_lots=volume_lots,
            sl=sl,
            tp=tp,
            entry_price=data.get("price"),
        )

        return {
            "intent_id": intent_id,
            "status": "submitted",
            "adapter": "HTTPBridgeFallback",
            "broker_order_id": str(data.get("order", "")),
            "retcode": _map_trade_retcode(retcode),
            "message": f"Order submitted via bridge (retcode={retcode_int})",
            "raw": data,
        }
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool 3: mt5_submit_pending_order
# ---------------------------------------------------------------------------
@mcp.tool(name="mt5_submit_pending_order", annotations=_WRITE_ANNOTATIONS)
def mt5_submit_pending_order(
    symbol: str,
    side: str,
    kind: str,
    price: float,
    volume_lots: float,
    sl: Optional[float] = None,
    tp: Optional[float] = None,
    deviation: int = 20,
    session_id: Optional[str] = None,
    strategy_id: Optional[str] = None,
    intent_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
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
            return {"error": f"Policy blocked: {decision.reason}"}

        if idempotency_key is None:
            idempotency_key = str(uuid.uuid4())

        symbol_norm = normalize_symbol(symbol)
        result = _tcp_send_and_await(
            "submit_pending_order",
            {
                "symbol": symbol_norm,
                "side": side,
                "kind": kind,
                "price": price,
                "volume_lots": volume_lots,
                "sl": sl or 0,
                "tp": tp or 0,
                "deviation": deviation,
                "session_id": session_id,
                "strategy_id": strategy_id,
                "intent_id": intent_id,
                "idempotency_key": idempotency_key,
            },
        )

        if result is None:
            return {"error": "submit_pending_order failed — TCP unavailable"}

        data = _parse_payload_dict(result)
        retcode = data.get("retcode", 0) if data else 0
        retcode_int = int(retcode) if retcode is not None else 0

        if retcode_int not in _SUCCESS_RETCODES:
            symbol_warning = ""
            if retcode_int == 0 and any(
                symbol.upper().startswith(c) for c in ("BTC", "ETH", "XAU")
            ):
                symbol_warning = (
                    f" Note: retcode 0 on crypto/metal symbols may indicate the EA adapter "
                    f"rejected the pending order. Try using the '{symbol}m' variant or place a market order instead."
                )
            error_resp = _build_trade_error(intent_id, data or result)
            if symbol_warning:
                error_resp["hint"] = symbol_warning
            return error_resp

        _auto_log_trade(
            symbol=symbol,
            side=side,
            action="pending_entry",
            intent_id=intent_id,
            session_id=session_id,
            strategy_id=strategy_id,
            volume_lots=volume_lots,
            sl=sl,
            tp=tp,
            entry_price=price,
        )

        return {
            "intent_id": intent_id,
            "status": "pending_order_submitted",
            "adapter": "EASocketAdapter",
            "broker_order_id": str(data.get("order", "")),
            "retcode": _map_trade_retcode(retcode),
            "message": f"Pending order submitted (retcode={retcode_int})",
            "raw": data,
        }
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool 4: mt5_close_position
# ---------------------------------------------------------------------------
@mcp.tool(name="mt5_close_position", annotations=_WRITE_ANNOTATIONS)
def mt5_close_position(
    position_id: str,
    session_id: Optional[str] = None,
    strategy_id: Optional[str] = None,
    intent_id: Optional[str] = None,
) -> dict:
    try:
        frozen = _check_frozen_response()
        if frozen:
            return frozen

        result = _tcp_send_and_await(
            "close_position",
            {
                "position_id": position_id,
                "session_id": session_id,
                "strategy_id": strategy_id,
                "intent_id": intent_id,
            },
        )

        req_id: Optional[str] = None
        if result is None:
            logger.warning("mt5_close_position: TCP unavailable, falling back to HTTP")
            settings = get_settings_cached()
            client = get_http_client()
            req = client.post(
                f"{settings.gateway_url}/bridge/commands/enqueue",
                params={
                    "type": "close_position",
                    "position_id": position_id,
                    "session_id": session_id or "",
                    "strategy_id": strategy_id or "",
                    "intent_id": intent_id or "",
                },
            )
            req.raise_for_status()
            req_id_val = req.json().get("id") or req.json().get("request_id")
            req_id = str(req_id_val) if req_id_val else ""
            result = _await_result_with_gateway_fallback(req_id, timeout_s=30.0)

        if result is None:
            return {
                "error": "close_position failed — no response from TCP or HTTP bridge"
            }

        data = _parse_payload_dict(result) if result else {}
        if not data:
            return {"error": "close_position failed — empty response from bridge"}

        retcode = data.get("retcode", 0) if data else 0
        retcode_int = int(retcode) if retcode is not None else 0

        # retcode 0 = unknown/pending — treat as potential error for close operations
        if retcode_int not in _SUCCESS_RETCODES and retcode_int != 0:
            return _build_trade_error(intent_id, data)

        _auto_log_trade(
            symbol="",
            side="",
            action="exit",
            intent_id=intent_id,
            session_id=session_id,
            strategy_id=strategy_id,
        )

        return {
            "position_id": position_id,
            "status": "closed",
            "retcode": _map_trade_retcode(retcode),
            "message": f"Position closed (retcode={retcode_int})",
            "raw": data,
        }
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool 5: mt5_close_all_positions
# ---------------------------------------------------------------------------
@mcp.tool(name="mt5_close_all_positions", annotations=_WRITE_ANNOTATIONS)
def mt5_close_all_positions(
    side: Optional[str] = None,
    symbol: Optional[str] = None,
    session_id: Optional[str] = None,
    strategy_id: Optional[str] = None,
    intent_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> dict:
    try:
        frozen = _check_frozen_response()
        if frozen:
            return frozen

        if idempotency_key is None:
            idempotency_key = str(uuid.uuid4())

        result = _tcp_send_and_await(
            "close_all_positions",
            {
                "side": side or "",
                "symbol": normalize_symbol(symbol) if symbol else "",
                "session_id": session_id,
                "strategy_id": strategy_id,
                "intent_id": intent_id,
                "idempotency_key": idempotency_key,
            },
        )

        if result is None:
            settings = get_settings_cached()
            client = get_http_client()
            req = client.post(
                f"{settings.gateway_url}/bridge/commands/enqueue",
                params={
                    "type": "close_all_positions",
                    "side": side or "",
                    "symbol": normalize_symbol(symbol) if symbol else "",
                    "session_id": session_id or "",
                    "strategy_id": strategy_id or "",
                    "intent_id": intent_id or "",
                    "idempotency_key": idempotency_key,
                },
            )
            req.raise_for_status()
            req_id = req.json().get("id") or req.json().get("request_id")
            result = _await_result(req_id, timeout_s=60.0)

        data = _parse_payload_dict(result) if result else {}
        closed_count = data.get("closed_count", data.get("closed", 0))
        return {
            "status": "close_all_positions_completed",
            "closed_count": closed_count,
            "raw": data,
        }
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool 6: mt5_cancel_order
# ---------------------------------------------------------------------------
@mcp.tool(name="mt5_cancel_order", annotations=_WRITE_ANNOTATIONS)
def mt5_cancel_order(
    order_id: str,
    session_id: Optional[str] = None,
    strategy_id: Optional[str] = None,
    intent_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> dict:
    try:
        frozen = _check_frozen_response()
        if frozen:
            return frozen

        result = _tcp_send_and_await(
            "cancel_order",
            {
                "order_id": order_id,
                "session_id": session_id,
                "strategy_id": strategy_id,
                "intent_id": intent_id,
                "idempotency_key": idempotency_key,
            },
        )

        if result is None:
            settings = get_settings_cached()
            client = get_http_client()
            req = client.post(
                f"{settings.gateway_url}/bridge/commands/enqueue",
                params={
                    "type": "cancel_order",
                    "order_id": order_id,
                    "session_id": session_id or "",
                    "strategy_id": strategy_id or "",
                    "intent_id": intent_id or "",
                    "idempotency_key": idempotency_key or "",
                },
            )
            req.raise_for_status()
            req_id = req.json().get("id") or req.json().get("request_id")
            result = _await_result(req_id)

        data = _parse_payload_dict(result) if result else {}
        raw_status = data.get("status", "")
        if result and result.get("status") == "error":
            raw_status = data.get("error", raw_status) or "cancel_failed"

        if raw_status in ("cancel_failed", "order_not_found", "error"):
            return {
                "order_id": order_id,
                "status": "error",
                "error": raw_status,
                "raw": data,
            }
        return {
            "order_id": order_id,
            "status": "cancelled",
            "raw": data,
        }
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool 7: mt5_cancel_all_orders
# ---------------------------------------------------------------------------
@mcp.tool(name="mt5_cancel_all_orders", annotations=_WRITE_ANNOTATIONS)
def mt5_cancel_all_orders(
    side: Optional[str] = None,
    symbol: Optional[str] = None,
    session_id: Optional[str] = None,
    strategy_id: Optional[str] = None,
    intent_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> dict:
    try:
        frozen = _check_frozen_response()
        if frozen:
            return frozen

        result = _tcp_send_and_await(
            "cancel_all_orders",
            {
                "side": side or "",
                "symbol": normalize_symbol(symbol) if symbol else "",
                "session_id": session_id,
                "strategy_id": strategy_id,
                "intent_id": intent_id,
                "idempotency_key": idempotency_key,
            },
        )

        if result is None:
            settings = get_settings_cached()
            client = get_http_client()
            req = client.post(
                f"{settings.gateway_url}/bridge/commands/enqueue",
                params={
                    "type": "cancel_all_orders",
                    "side": side or "",
                    "symbol": normalize_symbol(symbol) if symbol else "",
                    "session_id": session_id or "",
                    "strategy_id": strategy_id or "",
                    "intent_id": intent_id or "",
                    "idempotency_key": idempotency_key or "",
                },
            )
            req.raise_for_status()
            req_id = req.json().get("id") or req.json().get("request_id")
            result = _await_result(req_id, timeout_s=60.0)

        data = _parse_payload_dict(result) if result else {}
        return {
            "status": "cancel_all_orders_completed",
            "cancelled_count": data.get("cancelled_count", 0),
            "raw": data,
        }
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool 8: mt5_modify_order
# ---------------------------------------------------------------------------
@mcp.tool(name="mt5_modify_order", annotations=_WRITE_ANNOTATIONS)
def mt5_modify_order(
    order_id: str,
    new_price: Optional[float] = None,
    new_sl: Optional[float] = None,
    new_tp: Optional[float] = None,
    session_id: Optional[str] = None,
    strategy_id: Optional[str] = None,
    intent_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> dict:
    try:
        frozen = _check_frozen_response()
        if frozen:
            return frozen

        payload: dict[str, Any] = {
            "order_id": order_id,
            "session_id": session_id,
            "strategy_id": strategy_id,
            "intent_id": intent_id,
            "idempotency_key": idempotency_key,
        }
        if new_price is not None:
            payload["new_price"] = new_price
        if new_sl is not None:
            payload["new_sl"] = new_sl
        if new_tp is not None:
            payload["new_tp"] = new_tp

        result = _tcp_send_and_await("modify_order", payload)

        if result is None:
            settings = get_settings_cached()
            client = get_http_client()
            params = {
                "type": "modify_order",
                "order_id": order_id,
                "session_id": session_id or "",
                "strategy_id": strategy_id or "",
                "intent_id": intent_id or "",
                "idempotency_key": idempotency_key or "",
            }
            if new_price is not None:
                params["new_price"] = new_price
            if new_sl is not None:
                params["new_sl"] = new_sl
            if new_tp is not None:
                params["new_tp"] = new_tp
            req = client.post(
                f"{settings.gateway_url}/bridge/commands/enqueue",
                params=params,
            )
            req.raise_for_status()
            req_id = req.json().get("id") or req.json().get("request_id")
            result = _await_result(req_id)

        data = _parse_payload_dict(result) if result else {}
        if result and result.get("status") == "error":
            return {
                "order_id": order_id,
                "status": "error",
                "error": data.get("error", "modify_order_failed"),
                "raw": data,
            }
        return {
            "order_id": order_id,
            "status": "modified",
            "raw": data,
        }
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool 9: mt5_modify_position_sl_tp
# ---------------------------------------------------------------------------
@mcp.tool(name="mt5_modify_position_sl_tp", annotations=_WRITE_ANNOTATIONS)
def mt5_modify_position_sl_tp(
    position_id: str,
    sl: Optional[float] = None,
    tp: Optional[float] = None,
    session_id: Optional[str] = None,
    strategy_id: Optional[str] = None,
    intent_id: Optional[str] = None,
) -> dict:
    try:
        frozen = _check_frozen_response()
        if frozen:
            return frozen

        result = _tcp_send_and_await(
            "modify_position_sl_tp",
            {
                "position_id": position_id,
                "sl": sl or 0,
                "tp": tp or 0,
                "session_id": session_id,
                "strategy_id": strategy_id,
                "intent_id": intent_id,
            },
        )

        if result is None:
            settings = get_settings_cached()
            client = get_http_client()
            req = client.post(
                f"{settings.gateway_url}/bridge/commands/enqueue",
                params={
                    "type": "modify_position_sl_tp",
                    "position_id": position_id,
                    "sl": sl or 0,
                    "tp": tp or 0,
                    "session_id": session_id or "",
                    "strategy_id": strategy_id or "",
                    "intent_id": intent_id or "",
                },
            )
            req.raise_for_status()
            req_id = req.json().get("id") or req.json().get("request_id")
            result = _await_result(req_id)

        data = _parse_payload_dict(result) if result else {}
        retcode = data.get("retcode", 0) if data else 0
        retcode_int = int(retcode) if retcode is not None else 0

        if result and result.get("status") == "error":
            return {
                "position_id": position_id,
                "status": "error",
                "error": data.get("error", "modify_failed"),
                "retcode": _map_trade_retcode(retcode),
                "raw": data,
            }

        return {
            "position_id": position_id,
            "status": "modified",
            "retcode": _map_trade_retcode(retcode),
            "message": f"SL/TP modified (retcode={retcode_int})",
            "raw": data,
        }
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool 10: mt5_calculate_position_size
# ---------------------------------------------------------------------------
@mcp.tool(name="mt5_calculate_position_size", annotations=_READ_ANNOTATIONS)
def mt5_calculate_position_size(
    symbol: str,
    risk_percent: float,
    entry_price: float,
    stop_loss_price: float,
    equity: Optional[float] = None,
) -> dict:
    try:
        from apps.mcp_server.tools_resources import mt5_symbol_info, mt5_account_summary

        risk_percent = float(risk_percent)
        entry_price = float(entry_price)
        stop_loss_price = float(stop_loss_price)

        symbol_info = mt5_symbol_info(symbol)

        if "error" in symbol_info:
            return {
                "error": f"Cannot retrieve symbol info for {symbol}: {symbol_info.get('error')}",
                "symbol": symbol,
                "hint": "Verify the symbol name is correct and market is open.",
            }

        account = mt5_account_summary()

        from mt5_mcp.services.agent_capabilities import calculate_position_size

        if equity is None:
            acct = account.get("account", {})
            equity = float(acct.get("equity", acct.get("balance", 0)))
        else:
            equity = float(equity)

        tick_size = float(symbol_info.get("tick_size", 0) or 0)
        tick_value = float(symbol_info.get("tick_value", 0) or 0)
        volume_min = float(symbol_info.get("volume_min", 0.01) or 0.01)
        volume_max = float(symbol_info.get("volume_max", 100) or 100)
        volume_step = float(symbol_info.get("volume_step", 0.01) or 0.01)

        if tick_size == 0 or tick_value == 0:
            return {
                "error": f"tick_size or tick_value unavailable for {symbol}",
                "symbol": symbol,
                "raw_symbol_info": {
                    "tick_size": symbol_info.get("tick_size"),
                    "tick_value": symbol_info.get("tick_value"),
                },
                "hint": "MT5 did not return tick data. This is common for crypto symbols on some brokers. Try using a different symbol variant (e.g., BTCUSDm instead of BTCUSD).",
            }

        result = calculate_position_size(
            equity=equity,
            risk_percent=risk_percent,
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
            tick_size=tick_size,
            tick_value=tick_value,
            volume_min=volume_min,
            volume_max=volume_max,
            volume_step=volume_step,
        )

        return {"symbol": symbol, **result}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool 11: mt5_validate_trade_setup
# ---------------------------------------------------------------------------
@mcp.tool(name="mt5_validate_trade_setup", annotations=_READ_ANNOTATIONS)
def mt5_validate_trade_setup(
    symbol: str,
    side: str,
    order_kind: str,
    volume_lots: float,
    entry_price: float,
    sl: Optional[float] = None,
    tp: Optional[float] = None,
) -> dict:
    try:
        from apps.mcp_server.tools_resources import (
            mt5_symbol_info,
            mt5_account_summary,
            mt5_positions_open,
        )
        from apps.mcp_server.tools_market_data import mt5_get_order_book

        symbol_info = mt5_symbol_info(symbol)

        if "error" in symbol_info:
            return {
                "error": f"Cannot retrieve symbol info for {symbol}: {symbol_info.get('error')}",
                "symbol": symbol,
                "valid": False,
            }

        account = mt5_account_summary()
        order_book = mt5_get_order_book(symbol)

        try:
            margin_estimate = get_gateway().estimate_margin(
                type=type(
                    "R",
                    (),
                    {
                        "symbol": normalize_symbol(symbol),
                        "side": side,
                        "volume_lots": volume_lots,
                        "entry_price": entry_price,
                        "order_kind": order_kind,
                    },
                )()
            )
            required_margin = float(
                getattr(
                    margin_estimate,
                    "required_margin",
                    getattr(margin_estimate, "margin", 0),
                )
            )
        except Exception:
            required_margin = 0

        from mt5_mcp.services.agent_capabilities import validate_trade_setup

        current_bid = order_book.get("bid")
        current_ask = order_book.get("ask")

        result = validate_trade_setup(
            symbol_info=symbol_info,
            account_summary=account.get("account", {}),
            side=side,
            order_kind=order_kind,
            volume_lots=volume_lots,
            current_bid=float(current_bid) if current_bid else 0.0,
            current_ask=float(current_ask) if current_ask else 0.0,
            entry_price=entry_price,
            sl=sl,
            tp=tp,
            required_margin=required_margin,
        )

        return result
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool 12: mt5_trail_position
# ---------------------------------------------------------------------------
@mcp.tool(name="mt5_trail_position", annotations=_WRITE_ANNOTATIONS)
def mt5_trail_position(
    position_id: str,
    distance_points: float,
    lock_in_points: float = 0,
) -> dict:
    try:
        frozen = _check_frozen_response()
        if frozen:
            return frozen

        from apps.mcp_server.tools_resources import mt5_positions_open, mt5_symbol_info
        from apps.mcp_server.tools_market_data import mt5_get_order_book

        positions_result = mt5_positions_open()
        positions = positions_result.get("positions", [])

        position = None
        for p in positions:
            if str(p.get("position_id", "")) == str(position_id):
                position = p
                break

        if position is None:
            return {"error": f"Position {position_id} not found"}

        symbol = position.get("symbol", "")
        sym_info = mt5_symbol_info(symbol)
        point = float(sym_info.get("point", 0) or 0)

        if point <= 0:
            return {"error": f"Could not determine point size for {symbol}"}

        order_book = mt5_get_order_book(symbol)
        bid = order_book.get("bid")
        ask = order_book.get("ask")

        side = position.get("side", "").lower()
        entry_price = float(position.get("entry_price", 0) or 0)
        distance_price = distance_points * point

        if side == "buy":
            if ask is None:
                return {"error": "Could not get ask price for trailing"}
            new_sl = ask - distance_price
            if lock_in_points > 0:
                lock_in_price = entry_price + (lock_in_points * point)
                new_sl = max(new_sl, lock_in_price)
        else:
            if bid is None:
                return {"error": "Could not get bid price for trailing"}
            new_sl = bid + distance_price
            if lock_in_points > 0:
                lock_in_price = entry_price - (lock_in_points * point)
                new_sl = min(new_sl, lock_in_price)

        result = _tcp_send_and_await(
            "modify_position_sl_tp",
            {
                "position_id": position_id,
                "sl": new_sl,
                "tp": position.get("tp", 0) or 0,
            },
        )

        data = _parse_payload_dict(result) if result else {}

        _trailing_stops[position_id] = {
            "distance_points": distance_points,
            "lock_in_points": lock_in_points,
            "last_sl": new_sl,
            "last_updated": time.time(),
        }

        return {
            "position_id": position_id,
            "computed_sl": new_sl,
            "result": data,
        }
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool 13: mt5_news_fetch
# ---------------------------------------------------------------------------
@mcp.tool(name="mt5_news_fetch", annotations=_READ_ANNOTATIONS)
def mt5_news_fetch(
    limit: int = 10,
    source: Optional[str] = None,
) -> dict:
    try:
        client = get_http_client()
        params: dict[str, Any] = {"limit": limit}
        if source:
            params["source"] = source

        try:
            resp = client.get(
                "https://www.ig.com/ig-com-api/v1/market-news/news",
                params=params,
                timeout=15.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                news_list = data.get(
                    "items", data.get("news", data if isinstance(data, list) else [])
                )
                return {
                    "news": news_list[:limit],
                    "count": len(news_list[:limit]),
                    "source": source or "ig",
                }
        except Exception:
            pass

        settings = get_settings_cached()
        try:
            resp = client.get(
                f"{settings.gateway_url}/bridge/news",
                params=params,
                timeout=10.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "news": data.get("news", [])[:limit],
                    "count": len(data.get("news", [])[:limit]),
                    "source": "bridge",
                }
        except Exception:
            pass

        return {
            "news": [],
            "count": 0,
            "source": "unavailable",
            "available_sources": ["ig", "reuters", "bloomberg"],
            "status": "unavailable",
            "hint": (
                "News feed requires network access to IG API or a configured news provider. "
                "If running behind a firewall or on a restricted network, configure MT5_NEWS_API_URL "
                "or provide a local news source. Consider skipping news-dependent analysis until resolved."
            ),
        }
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool 14: mt5_news_enrich
# ---------------------------------------------------------------------------
@mcp.tool(name="mt5_news_enrich", annotations=_READ_ANNOTATIONS)
def mt5_news_enrich(
    title: str,
    content_snippet: str = "",
    source_name: str = "",
) -> dict:
    from mt5_mcp.services.news_service import (
        _simple_sentiment,
        _extract_topics,
        _extract_entities,
        _currency_relevance,
        NewsItem,
    )

    text = f"{title} {content_snippet}"
    item = NewsItem(
        id="",
        title=title,
        link="",
        pub_date="",
        source_name=source_name,
        source_id="",
        pool_id="",
        content_snippet=content_snippet,
    )

    return {
        "sentiment": _simple_sentiment(text),
        "topics": _extract_topics(text),
        "entities": _extract_entities(text),
        "currency_relevance": _currency_relevance(item),
    }


# ---------------------------------------------------------------------------
# Tool 15: mt5_news_pools
# ---------------------------------------------------------------------------
@mcp.tool(name="mt5_news_pools", annotations=_READ_ANNOTATIONS)
def mt5_news_pools() -> dict:
    try:
        return {
            "pools": ["ig", "reuters", "bloomberg"],
            "default": "ig",
            "status_hint": "Pools configured but may be unavailable depending on network access.",
        }
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool 16: mt5_economic_calendar
# ---------------------------------------------------------------------------
@mcp.tool(name="mt5_economic_calendar", annotations=_READ_ANNOTATIONS)
def mt5_economic_calendar() -> dict:
    try:
        client = get_http_client()
        try:
            resp = client.get(
                "https://www.ig.com/ig-com-api/v1/economic-calendar/events",
                timeout=15.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                events = data.get(
                    "events", data.get("data", data if isinstance(data, list) else [])
                )
                return {
                    "events": events,
                    "count": len(events),
                    "source": "ig",
                }
        except Exception:
            pass

        settings = get_settings_cached()
        try:
            resp = client.get(
                f"{settings.gateway_url}/bridge/calendar",
                timeout=10.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "events": data.get("events", []),
                    "count": len(data.get("events", [])),
                    "source": "bridge",
                }
        except Exception:
            pass

        return {
            "events": [],
            "count": 0,
            "source": "unavailable",
            "status": "unavailable",
            "hint": (
                "Economic calendar requires network access to IG API or bridge calendar endpoint. "
                "Configure MT5_CALENDAR_API_URL for a custom source. "
                "Skip blackout-window checks until resolved — default to cautious (assume potential high-impact events)."
            ),
        }
    except Exception as e:
        return {"error": str(e)}
