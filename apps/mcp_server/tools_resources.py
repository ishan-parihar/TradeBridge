import time
from datetime import datetime, timezone
from typing import Any, Optional

from mcp.types import ToolAnnotations

from mt5_mcp.observability.logging import logger
from . import mcp
from .shared import (
    get_gateway,
    get_http_client,
    get_settings_cached,
    _tcp_send_and_await,
    _parse_payload,
    _parse_payload_dict,
)

_READ_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)


# ---------------------------------------------------------------------------
# Tool 1: mt5_terminal_status
# ---------------------------------------------------------------------------
@mcp.tool(name="mt5_terminal_status", annotations=_READ_ANNOTATIONS)
def mt5_terminal_status() -> dict:
    try:
        result = get_gateway().terminal_status()
        return result.model_dump() if hasattr(result, "model_dump") else dict(result)
    except Exception as e:
        return {"error": "terminal_status_unavailable", "connected": False}


# ---------------------------------------------------------------------------
# Tool 2: mt5_account_summary
# ---------------------------------------------------------------------------
@mcp.tool(name="mt5_account_summary", annotations=_READ_ANNOTATIONS)
def mt5_account_summary() -> dict:
    # Path 1: Gateway adapter
    try:
        summary = get_gateway().account_summary()
        if summary.account_id is not None:
            return {
                "account": summary.model_dump()
                if hasattr(summary, "model_dump")
                else dict(summary),
                "snapshot_metadata": {
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                    "next_recommended_check_seconds": 300,
                    "data_freshness": "live",
                },
            }
    except Exception as e:
        logger.warning(f"mt5_account_summary: gateway adapter failed — {e}")

    # Path 2: TCP bridge
    try:
        result = _tcp_send_and_await("get_account", {}, timeout_s=10.0)
        data = _parse_payload_dict(result)
        if data:
            return {
                "account": data,
                "snapshot_metadata": {
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                    "next_recommended_check_seconds": 300,
                    "data_freshness": "live",
                },
            }
        else:
            logger.warning("mt5_account_summary: TCP bridge returned empty payload")
    except Exception as e:
        logger.warning(f"mt5_account_summary: TCP bridge failed — {e}")

    return {"error": "account_summary_unavailable"}


# ---------------------------------------------------------------------------
# Tool 3: mt5_symbol_info
# ---------------------------------------------------------------------------
@mcp.tool(name="mt5_symbol_info", annotations=_READ_ANNOTATIONS)
def mt5_symbol_info(symbol: str) -> dict:
    try:
        from mt5_mcp.adapters.common.symbol_utils import (
            denormalize_symbol,
            normalize_symbol,
        )

        result = get_gateway().adapter.get_symbol_info(symbol)
        if result is not None and getattr(result, "symbol", None):
            result.symbol = denormalize_symbol(result.symbol)
            return (
                result.model_dump() if hasattr(result, "model_dump") else dict(result)
            )
    except Exception:
        pass

    try:
        tcp_result = _tcp_send_and_await(
            "get_symbol_info", {"symbol": normalize_symbol(symbol)}, timeout_s=10.0
        )
        data = _parse_payload_dict(tcp_result)
        if data:
            return data
    except Exception:
        pass

    return {"error": "symbol_info_unavailable", "symbol": symbol}


# ---------------------------------------------------------------------------
# Tool 4: mt5_deals_history
# ---------------------------------------------------------------------------
@mcp.tool(name="mt5_deals_history", annotations=_READ_ANNOTATIONS)
def mt5_deals_history(
    limit: int = 100, symbol: Optional[str] = None, days: int = 30
) -> dict:
    try:
        deals = get_gateway().adapter.get_deals_history(
            limit=limit, symbol=symbol, days=days
        )
        if deals:
            return {
                "deals": [
                    d.model_dump() if hasattr(d, "model_dump") else dict(d)
                    for d in deals
                ]
            }
    except Exception as e:
        logger.warning(f"mt5_deals_history: gateway adapter failed — {e}")

    try:
        tcp_result = _tcp_send_and_await(
            "get_deals_history",
            {"limit": limit, "symbol": symbol or "", "days": days},
            timeout_s=15.0,
        )
        data = _parse_payload_dict(tcp_result)
        if data:
            return {"deals": data.get("deals", [])}
        else:
            logger.warning("mt5_deals_history: TCP bridge returned empty payload")
    except Exception as e:
        logger.warning(f"mt5_deals_history: TCP bridge failed — {e}")

    return {"deals": []}


# ---------------------------------------------------------------------------
# Tool 5: mt5_performance_summary
# ---------------------------------------------------------------------------
@mcp.tool(name="mt5_performance_summary", annotations=_READ_ANNOTATIONS)
def mt5_performance_summary(
    limit: int = 100, symbol: Optional[str] = None, days: int = 30
) -> dict:
    try:
        from mt5_mcp.services.agent_capabilities import summarize_deals

        deals_list = []
        try:
            deals = get_gateway().adapter.get_deals_history(
                limit=limit, symbol=symbol, days=days
            )
            if deals:
                deals_list = [
                    d.model_dump() if hasattr(d, "model_dump") else dict(d)
                    for d in deals
                ]
        except Exception:
            pass

        if not deals_list:
            try:
                tcp_result = _tcp_send_and_await(
                    "get_deals_history",
                    {"limit": limit, "symbol": symbol or "", "days": days},
                    timeout_s=15.0,
                )
                data = _parse_payload_dict(tcp_result)
                if data:
                    deals_list = data.get("deals", [])
            except Exception:
                pass

        return summarize_deals(deals_list)
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool 6: mt5_positions_open
# ---------------------------------------------------------------------------
def _compute_position_health(position: dict, symbol_info: dict) -> dict:
    from mt5_mcp.observability.logging import logger as _ch_logger

    health: dict[str, object] = {
        "distance_to_sl_pips": None,
        "distance_to_tp_pips": None,
        "pnl_percent_of_risk": None,
        "time_in_trade_minutes": None,
        "time_in_trade_bars_h1": None,
        "is_winning": None,
        "is_at_breakeven": None,
        "trail_eligible": None,
        "spread_cost_pips": None,
        "profit_multiple_of_spread": None,
    }

    try:
        mark_price = position.get("mark_price")
        entry_price = position.get("entry_price")
        sl = position.get("sl")
        tp = position.get("tp")
        volume = position.get("volume", 0.0)
        side = position.get("side", "buy")

        profit_raw = position.get("profit")
        if profit_raw is None:
            profit_raw = position.get("unrealized_pnl", 0.0)
        profit = float(profit_raw) if profit_raw is not None else 0.0

        mark_price = float(mark_price) if mark_price else None
        entry_price = float(entry_price) if entry_price else None
        volume = float(volume) if volume else 0.0

        point = symbol_info.get("point")
        point = float(point) if point else None
        tick_value = symbol_info.get("tick_value")
        tick_value = float(tick_value) if tick_value else None

        spread_raw = position.get("spread")
        if spread_raw is None:
            spread_raw = symbol_info.get("spread_points", 0)
        spread = int(spread_raw) if spread_raw is not None else 0

        if mark_price and point and point > 0:
            if sl and float(sl) > 0:
                sl_f = float(sl)
                if side == "buy":
                    health["distance_to_sl_pips"] = (mark_price - sl_f) / point
                else:
                    health["distance_to_sl_pips"] = (sl_f - mark_price) / point

            if tp and float(tp) > 0:
                tp_f = float(tp)
                if side == "buy":
                    health["distance_to_tp_pips"] = (tp_f - mark_price) / point
                else:
                    health["distance_to_tp_pips"] = (mark_price - tp_f) / point

        if entry_price and sl and float(sl) > 0 and tick_value and volume > 0:
            sl_f = float(sl)
            risk_per_lot = abs(entry_price - sl_f) * volume * tick_value
            if risk_per_lot > 0:
                health["pnl_percent_of_risk"] = (profit / risk_per_lot) * 100

        time_raw = position.get("time")
        if time_raw is not None:
            try:
                time_in_trade_minutes = int((time.time() - float(time_raw)) / 60)
                health["time_in_trade_minutes"] = time_in_trade_minutes
                health["time_in_trade_bars_h1"] = round(time_in_trade_minutes / 60.0, 1)
            except (ValueError, TypeError, OverflowError):
                pass
        else:
            opened_at = position.get("opened_at")
            if opened_at:
                try:
                    from datetime import datetime, timezone

                    if isinstance(opened_at, (int, float)):
                        opened_dt = datetime.fromtimestamp(
                            float(opened_at), tz=timezone.utc
                        )
                    else:
                        opened_dt = datetime.fromisoformat(
                            str(opened_at).replace("Z", "+00:00")
                        )
                    now = datetime.now(timezone.utc)
                    delta = now - opened_dt
                    time_in_trade_minutes = int(delta.total_seconds() / 60)
                    health["time_in_trade_minutes"] = time_in_trade_minutes
                    health["time_in_trade_bars_h1"] = round(
                        time_in_trade_minutes / 60.0, 1
                    )
                except (ValueError, TypeError, OverflowError):
                    pass

        health["is_winning"] = profit > 0

        if spread > 0 and tick_value and volume > 0:
            spread_cost_dollar = spread * volume * tick_value
            health["is_at_breakeven"] = abs(profit) < (spread_cost_dollar * 0.5)
            health["trail_eligible"] = health["is_winning"] and profit > (
                spread_cost_dollar * 2
            )
        else:
            health["is_at_breakeven"] = None
            health["trail_eligible"] = None

        health["spread_cost_pips"] = spread

        if spread > 0 and point and point > 0 and tick_value and volume > 0:
            denom = volume * tick_value
            profit_in_points = profit / denom if denom > 0 else 0
            health["profit_multiple_of_spread"] = profit_in_points / spread

    except Exception as e:
        _ch_logger.error(
            f"Health computation failed for position "
            f"{position.get('position_id', 'unknown')}: {e}"
        )

    trail_eligible = health.get("trail_eligible")
    is_at_breakeven = health.get("is_at_breakeven")
    is_winning = health.get("is_winning")
    distance_to_sl_pips = health.get("distance_to_sl_pips")
    pnl_percent_of_risk = health.get("pnl_percent_of_risk")
    time_in_trade_bars_h1 = health.get("time_in_trade_bars_h1")

    action_required = "none"
    action_reason = "Position healthy — no action needed"

    if (
        time_in_trade_bars_h1 is not None
        and time_in_trade_bars_h1 >= 16
        and pnl_percent_of_risk is not None
        and pnl_percent_of_risk < 50
    ):
        action_required = "stale_position"
        action_reason = (
            "Position open 16+ bars with < 0.5x ATR profit — close and redeploy"
        )
    elif (
        time_in_trade_bars_h1 is not None
        and time_in_trade_bars_h1 >= 12
        and is_winning is True
        and pnl_percent_of_risk is not None
        and pnl_percent_of_risk < 50
    ):
        action_required = "time_exit_approaching"
        action_reason = "Dead money — consider closing if no progress in 4 more bars"
    elif distance_to_sl_pips is not None and distance_to_sl_pips < 10:
        action_required = "invalidation_check"
        action_reason = "Price approaching stop loss — verify thesis still valid"
    elif trail_eligible is True and is_at_breakeven is False:
        action_required = "trail_to_breakeven"
        action_reason = "Profit > 2x spread — move SL to entry price"
    elif (
        trail_eligible is True
        and is_at_breakeven is True
        and distance_to_sl_pips is not None
        and distance_to_sl_pips > 0
    ):
        action_required = "trail_stop_closer"
        action_reason = "Position in profit — trail SL 0.5x ATR in profit direction"

    health["action_required"] = action_required
    health["action_reason"] = action_reason

    return health


def _enrich_positions_with_health(positions: list[dict]) -> list[dict]:
    if not positions:
        return positions

    symbols = list({p.get("symbol", "") for p in positions if p.get("symbol")})
    symbol_infos: dict[str, dict] = {}

    if len(symbols) == 1:
        sym = symbols[0]
        try:
            tcp_result = _tcp_send_and_await(
                "get_symbol_info", {"symbol": sym}, timeout_s=5.0
            )
            symbol_infos[sym] = _parse_payload_dict(tcp_result)
        except Exception as e:
            logger.warning(f"_enrich_positions: symbol_info({sym}) failed — {e}")
            symbol_infos[sym] = {}
    else:
        commands = [{"type": "get_symbol_info", "symbol": sym} for sym in set(symbols)]
        try:
            results = _batch_enqueue_and_await(commands, timeout_s=15.0)
            for sym, result in zip(set(symbols), results):
                symbol_infos[sym] = _parse_payload_dict(result) if result else {}
        except Exception as e:
            logger.warning(f"_enrich_positions: batch symbol_info failed — {e}")
            symbol_infos = {sym: {} for sym in set(symbols)}

    for position in positions:
        sym = position.get("symbol", "")
        sym_info = symbol_infos.get(sym, {})
        position["health"] = _compute_position_health(position, sym_info)

    return positions


@mcp.tool(name="mt5_positions_open", annotations=_READ_ANNOTATIONS)
def mt5_positions_open() -> dict:
    try:
        positions = get_gateway().adapter.get_positions()
        if positions:
            position_dicts = [
                p.model_dump() if hasattr(p, "model_dump") else dict(p)
                for p in positions
            ]
            _enrich_positions_with_health(position_dicts)
            return {
                "positions": position_dicts,
                "sync_status": {
                    "positions_count": len(position_dicts),
                    "last_sync_age_ms": 0,
                    "retry_count": 0,
                    "stale_warning": False,
                },
                "snapshot_metadata": {
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                    "next_recommended_check_seconds": 600,
                    "data_freshness": "live",
                },
            }
    except Exception as e:
        logger.warning(f"mt5_positions_open: gateway adapter failed — {e}")

    try:
        tcp_result = _tcp_send_and_await("get_positions", {}, timeout_s=10.0)
        data = _parse_payload_dict(tcp_result)
        pos_list = data.get("positions", []) if data else []
        if pos_list:
            _enrich_positions_with_health(pos_list)
            return {
                "positions": pos_list,
                "sync_status": {
                    "positions_count": len(pos_list),
                    "last_sync_age_ms": 0,
                    "retry_count": 0,
                    "stale_warning": False,
                },
                "snapshot_metadata": {
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                    "next_recommended_check_seconds": 600,
                    "data_freshness": "live",
                },
            }
    except Exception as e:
        logger.warning(f"mt5_positions_open: TCP bridge failed — {e}")

    return {
        "positions": [],
        "sync_status": {
            "positions_count": 0,
            "last_sync_age_ms": 0,
            "retry_count": 0,
            "stale_warning": False,
        },
        "snapshot_metadata": {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "next_recommended_check_seconds": 600,
            "data_freshness": "live",
        },
    }


# ---------------------------------------------------------------------------
# Tool 7: mt5_orders_pending
# ---------------------------------------------------------------------------
@mcp.tool(name="mt5_orders_pending", annotations=_READ_ANNOTATIONS)
def mt5_orders_pending() -> dict:
    try:
        orders = get_gateway().adapter.get_orders()
        if orders:
            order_list = [
                o.model_dump() if hasattr(o, "model_dump") else dict(o) for o in orders
            ]
            return {
                "orders": order_list,
                "snapshot_metadata": {
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                    "next_recommended_check_seconds": 120,
                    "data_freshness": "live",
                },
            }
    except Exception as e:
        logger.warning(f"mt5_orders_pending: gateway adapter failed — {e}")

    try:
        tcp_result = _tcp_send_and_await("get_orders", {}, timeout_s=10.0)
        data = _parse_payload_dict(tcp_result)
        order_list = data.get("orders", []) if data else []
        return {
            "orders": order_list,
            "snapshot_metadata": {
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "next_recommended_check_seconds": 120,
                "data_freshness": "live",
            },
        }
    except Exception as e:
        logger.warning(f"mt5_orders_pending: TCP bridge failed — {e}")

    return {
        "orders": [],
        "snapshot_metadata": {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "next_recommended_check_seconds": 120,
            "data_freshness": "live",
        },
    }


# ---------------------------------------------------------------------------
# Tool 8: mt5_bridge_status
# ---------------------------------------------------------------------------
@mcp.tool(name="mt5_bridge_status", annotations=_READ_ANNOTATIONS)
def mt5_bridge_status() -> dict:
    try:
        client = get_http_client()
        r = client.get(f"{get_settings_cached().gateway_url}/bridge/terminal/status")
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.warning(f"mt5_bridge_status: HTTP check failed — {e}")
        return {
            "connected": False,
            "message": "Bridge status unavailable",
            "error": str(e),
        }
