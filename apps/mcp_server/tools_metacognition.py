from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field
from mcp.types import ToolAnnotations

from . import mcp

_META_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
)


@mcp.tool(name="mt5_log_trade_decision", annotations=_META_ANNOTATIONS)
def mt5_log_trade_decision(
    symbol: Optional[str] = None,
    side: Optional[str] = None,
    action: Optional[str] = None,
    entry_price: Optional[float] = None,
    exit_price: Optional[float] = None,
    sl: Optional[float] = None,
    tp: Optional[float] = None,
    volume_lots: Optional[float] = None,
    pnl: Optional[float] = None,
    session_id: Optional[str] = None,
    regime: Optional[str] = None,
    atr_value: Optional[float] = None,
    atr_percent_of_price: Optional[float] = None,
    rsi_value: Optional[float] = None,
    indicator_snapshot: Optional[dict] = None,
    model_justification: Optional[str] = None,
    indicators_considered: Optional[list] = None,
    confidence_level: Optional[float] = None,
    risk_assessment: Optional[str] = None,
    emotional_self_report: Optional[str] = None,
    alternatives_considered: Optional[str] = None,
    expected_duration: Optional[str] = None,
    expected_move_points: Optional[float] = None,
    outcome: Optional[str] = None,
    lesson_learned: Optional[str] = None,
    would_do_differently: Optional[str] = None,
    mistake_category: Optional[str] = None,
    quality_rating: Optional[int] = None,
    decision_id: Optional[str] = None,
    note: Optional[str] = None,
) -> dict:
    try:
        from mt5_mcp.services.trade_journal_db import get_journal_db

        journal = get_journal_db()
    except Exception as e:
        return {"status": "error", "message": f"Journal unavailable: {e}"}

    try:
        if decision_id:
            updated = journal.update_decision(
                decision_id,
                exit_price=exit_price,
                pnl=pnl,
                outcome=outcome,
                lesson_learned=lesson_learned,
                would_do_differently=would_do_differently,
                mistake_category=mistake_category,
                quality_rating=quality_rating,
                note=note,
            )
            return {
                "status": "updated" if updated else "not_found",
                "decision_id": decision_id,
            }

        result_id = journal.log_decision(
            symbol=symbol or "UNKNOWN",
            side=side or "UNKNOWN",
            action=action or "UNKNOWN",
            entry_price=entry_price,
            exit_price=exit_price,
            sl=sl,
            tp=tp,
            volume_lots=volume_lots,
            pnl=pnl,
            session_id=session_id,
            regime=regime,
            atr_value=atr_value,
            atr_percent_of_price=atr_percent_of_price,
            rsi_value=rsi_value,
            indicator_snapshot=indicator_snapshot,
            model_justification=model_justification,
            indicators_considered=indicators_considered,
            confidence_level=confidence_level,
            risk_assessment=risk_assessment,
            emotional_self_report=emotional_self_report,
            alternatives_considered=alternatives_considered,
            expected_duration=expected_duration,
            expected_move_points=expected_move_points,
            outcome=outcome,
            lesson_learned=lesson_learned,
            would_do_differently=would_do_differently,
            mistake_category=mistake_category,
            quality_rating=quality_rating,
            note=note,
        )
        return {
            "status": "logged",
            "decision_id": result_id,
            "message": "Decision logged. Use this ID to update with outcome later.",
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(name="mt5_reflect_on_trades", annotations=_META_ANNOTATIONS)
def mt5_reflect_on_trades(
    symbol: Optional[str] = None,
    outcome: Optional[str] = None,
    regime: Optional[str] = None,
    emotional_self_report: Optional[str] = None,
    mistake_category: Optional[str] = None,
    action: Optional[str] = None,
    limit: int = 20,
) -> dict:
    try:
        from mt5_mcp.services.trade_journal_db import get_journal_db

        journal = get_journal_db()
    except Exception:
        return {"count": 0, "decisions": [], "warning": "Journal unavailable"}

    try:
        decisions = journal.query(
            symbol=symbol,
            outcome=outcome,
            regime=regime,
            emotional_self_report=emotional_self_report,
            mistake_category=mistake_category,
            action=action,
            limit=limit,
        )
        return {"count": len(decisions), "decisions": decisions}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(name="mt5_trading_insights", annotations=_META_ANNOTATIONS)
def mt5_trading_insights(
    symbol: Optional[str] = None,
    days: int = 7,
) -> dict:
    try:
        from mt5_mcp.services.trade_journal_db import get_journal_db

        journal = get_journal_db()
    except Exception:
        return {"error": "Journal unavailable"}

    try:
        insights = journal.get_reflection_insights(lookback_days=days)
        if symbol:
            all_decisions = journal.query(symbol=symbol, limit=1000)
            insights["filtered_symbol"] = symbol
            insights["symbol_decision_count"] = len(all_decisions)
        return insights
    except Exception as e:
        return {"error": str(e)}
