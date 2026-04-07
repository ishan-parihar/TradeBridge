"""Predefined operational mistake categories for AI trading agents.

These categories capture common failure modes in AI-driven trading,
enabling structured reflection and pattern recognition over time.
"""

from __future__ import annotations

from enum import Enum


class MistakeCategory(str, Enum):
    """Predefined operational mistake categories for AI trading agents."""

    DUPLICATE_INTENT = "duplicate_intent"
    """Same intent_id or idempotency_key submitted twice"""

    FOREIGN_PNL_CONFUSION = "foreign_pnl_confusion"
    """P&L attributed to wrong strategy (foreign trades mixed in)"""

    BRIDGE_BLINDNESS = "bridge_blindness"
    """Trade attempted while bridge was disconnected or unresponsive"""

    LOST_SL_TP_ON_MODIFY = "lost_sl_tp_on_modify"
    """Stop-loss or take-profit was lost during a position modification"""

    INVALID_STOPS_DISTANCE = "invalid_stops_distance"
    """SL/TP rejected by broker due to stopsLevel violation"""

    OVERSIZED_POSITION = "oversized_position"
    """Position size exceeded risk budget or margin limits"""

    PREMATURE_EXIT = "premature_exit"
    """Position closed before thesis played out (emotional exit)"""

    MISSING_ENTRY_RATIONALE = "missing_entry_rationale"
    """Trade entered without recorded decision justification"""

    WRONG_REGIME_STRATEGY = "wrong_regime_strategy"
    """Strategy used is inappropriate for current market regime"""

    CALENDAR_BLACKOUT = "calendar_blackout"
    """Trade entered during high-impact news blackout period"""

    PORTFOLIO_OVERLAP = "portfolio_overlap"
    """New position creates correlated exposure exceeding limits"""

    STALE_DATA = "stale_data"
    """Trading decision based on data older than acceptable freshness"""
