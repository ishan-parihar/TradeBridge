"""Consolidation engine — extracts semantic patterns from episodic trade data."""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

JOURNAL_PATH = Path.home() / ".mt5-mcp" / "trading_journal.db"
CONSOLIDATION_THRESHOLD = 10


def get_recent_trades(n: int = CONSOLIDATION_THRESHOLD) -> list[dict[str, Any]]:
    if not JOURNAL_PATH.exists():
        return []
    conn = sqlite3.connect(str(JOURNAL_PATH))
    conn.row_factory = sqlite3.Row
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='trade_decisions'"
    ).fetchall()
    if not tables:
        logger.warning("No 'trade_decisions' table found in %s", JOURNAL_PATH)
        conn.close()
        return []
    cursor = conn.execute(
        "SELECT * FROM trade_decisions ORDER BY timestamp DESC LIMIT ?",
        (n,),
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def compute_statistics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {}
    total = len(trades)
    pnl = lambda t: (t.get("pnl") or 0)
    wins = [t for t in trades if pnl(t) > 0]
    losses = [t for t in trades if pnl(t) <= 0]

    by_regime: dict[str, dict[str, Any]] = {}
    for t in trades:
        regime = t.get("regime") or "unknown"
        if regime not in by_regime:
            by_regime[regime] = {"total": 0, "wins": 0, "total_pnl": 0.0}
        by_regime[regime]["total"] += 1
        by_regime[regime]["total_pnl"] += pnl(t)
        if pnl(t) > 0:
            by_regime[regime]["wins"] += 1

    by_symbol: dict[str, dict[str, Any]] = {}
    for t in trades:
        sym = t.get("symbol") or "unknown"
        if sym not in by_symbol:
            by_symbol[sym] = {"total": 0, "wins": 0, "total_pnl": 0.0}
        by_symbol[sym]["total"] += 1
        by_symbol[sym]["total_pnl"] += pnl(t)
        if pnl(t) > 0:
            by_symbol[sym]["wins"] += 1

    by_emotion: dict[str, dict[str, Any]] = {}
    for t in trades:
        emotion = t.get("emotional_self_report") or "unknown"
        if emotion not in by_emotion:
            by_emotion[emotion] = {"total": 0, "wins": 0, "total_pnl": 0.0}
        by_emotion[emotion]["total"] += 1
        by_emotion[emotion]["total_pnl"] += pnl(t)
        if pnl(t) > 0:
            by_emotion[emotion]["wins"] += 1

    for data in (by_regime, by_symbol, by_emotion):
        for key in data:
            d = data[key]
            d["win_rate"] = d["wins"] / d["total"] if d["total"] > 0 else 0

    return {
        "total": total,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / total if total > 0 else 0,
        "total_pnl": sum(pnl(t) for t in trades),
        "by_regime": by_regime,
        "by_symbol": by_symbol,
        "by_emotion": by_emotion,
    }


def extract_patterns(stats: dict[str, Any]) -> list[dict[str, str]]:
    patterns = []

    for regime, data in stats.get("by_regime", {}).items():
        wr = data["win_rate"]
        if data["total"] >= 3:
            if wr < 0.3:
                patterns.append(
                    {
                        "text": f"Avoid entries in {regime} regime — win rate {wr:.0%} over {data['total']} trades. Total PnL: ${data['total_pnl']:.2f}",
                        "pattern_id": f"regime_avoid_{regime}",
                        "metadata": {
                            "type": "regime_warning",
                            "regime": regime,
                            "confidence": 1.0 - wr,
                            "trade_count": data["total"],
                            "valid": True,
                        },
                    }
                )
            elif wr > 0.6:
                patterns.append(
                    {
                        "text": f"Favor {regime} regime — win rate {wr:.0%} over {data['total']} trades. Total PnL: ${data['total_pnl']:.2f}",
                        "pattern_id": f"regime_favor_{regime}",
                        "metadata": {
                            "type": "regime_preference",
                            "regime": regime,
                            "confidence": wr,
                            "trade_count": data["total"],
                            "valid": True,
                        },
                    }
                )

    for symbol, data in stats.get("by_symbol", {}).items():
        wr = data["win_rate"]
        if data["total"] >= 3:
            if wr < 0.3:
                patterns.append(
                    {
                        "text": f"Exercise caution with {symbol} — win rate {wr:.0%} over {data['total']} trades. Total PnL: ${data['total_pnl']:.2f}",
                        "pattern_id": f"symbol_caution_{symbol}",
                        "metadata": {
                            "type": "symbol_warning",
                            "symbol": symbol,
                            "confidence": 1.0 - wr,
                            "trade_count": data["total"],
                            "valid": True,
                        },
                    }
                )

    for emotion, data in stats.get("by_emotion", {}).items():
        wr = data["win_rate"]
        if data["total"] >= 3 and emotion != "calm" and wr < 0.4:
            patterns.append(
                {
                    "text": f"When feeling {emotion}, win rate drops to {wr:.0%} over {data['total']} trades. Step back and wait for calm.",
                    "pattern_id": f"emotion_warning_{emotion}",
                    "metadata": {
                        "type": "emotion_warning",
                        "emotion": emotion,
                        "confidence": 1.0 - wr,
                        "trade_count": data["total"],
                        "valid": True,
                    },
                }
            )

    return patterns


def consolidate(mcp_client=None) -> list[dict[str, Any]]:
    trades = get_recent_trades(CONSOLIDATION_THRESHOLD)
    if not trades:
        logger.info("No trades to consolidate")
        return []

    stats = compute_statistics(trades)
    patterns = extract_patterns(stats)

    if not patterns:
        logger.info("No new patterns extracted from %d trades", len(trades))
        return []

    from mt5_mcp.autonomous.semantic_memory import SemanticMemory

    memory = SemanticMemory()

    stored = []
    for p in patterns:
        doc_id = memory.add_pattern(
            pattern_id=p["pattern_id"],
            text=p["text"],
            metadata=p["metadata"],
        )
        stored.append({"id": doc_id, **p})
        logger.info("Stored pattern: %s", p["text"][:80])

    logger.info(
        "Consolidation complete: %d patterns from %d trades", len(stored), len(trades)
    )
    return stored
