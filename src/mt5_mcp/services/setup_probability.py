"""Setup probability estimator — predicts win rate from journal history.

Queries past trades to estimate the probability of success for the
current setup based on regime, session, pattern, and indicator confluence.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SetupProbabilityResult:
    estimated_win_rate: float
    sample_size: int
    confidence: str
    similar_trades: list[dict] = field(default_factory=list)
    win_rate_by_regime: dict[str, float] = field(default_factory=dict)
    win_rate_by_session: dict[str, float] = field(default_factory=dict)
    common_mistakes: list[dict] = field(default_factory=list)
    recommendation: str = "insufficient_data"


def estimate_setup_probability(
    trades: list[dict],
    current_regime: str | None = None,
    current_session: str | None = None,
    current_symbol: str | None = None,
    min_samples: int = 5,
) -> SetupProbabilityResult:
    """Estimate win rate for a setup based on historical journal data.

    Args:
        trades: List of trade journal entries with fields:
            symbol, regime, session_context (optional), outcome (win/loss),
            pnl, mistake_category (optional), quality_rating (optional).
        current_regime: Current market regime to filter by.
        current_session: Current trading session (London/NY/Tokyo/Sydney).
        current_symbol: Symbol being considered.
        min_samples: Minimum trades needed for a reliable estimate.

    Returns:
        SetupProbabilityResult with estimated win rate and context.
    """
    if not trades:
        return SetupProbabilityResult(
            estimated_win_rate=0.0,
            sample_size=0,
            confidence="none",
            recommendation="insufficient_data",
        )

    # Filter to completed trades with known outcome
    completed = [t for t in trades if t.get("outcome") in ("win", "loss")]
    if not completed:
        return SetupProbabilityResult(
            estimated_win_rate=0.0,
            sample_size=0,
            confidence="none",
            recommendation="insufficient_data",
        )

    # Base win rate
    wins = [t for t in completed if t.get("outcome") == "win"]
    base_wr = len(wins) / len(completed) * 100

    # Win rate by regime
    regime_wr = _win_rate_by_field(completed, "regime")

    # Win rate by session
    session_wr = _win_rate_by_field(completed, "session_id")

    # Common mistakes
    mistakes = _common_mistakes(completed)

    # Filtered estimate
    filtered = completed
    if current_regime:
        filtered = [t for t in filtered if t.get("regime") == current_regime]
    if current_symbol:
        filtered = [t for t in filtered if t.get("symbol") == current_symbol]

    if len(filtered) >= min_samples:
        filtered_wins = [t for t in filtered if t.get("outcome") == "win"]
        est_wr = len(filtered_wins) / len(filtered) * 100
        confidence = "high" if len(filtered) >= 20 else "medium"
    elif len(filtered) >= 2:
        # Blend with base rate
        filtered_wr = (
            len([t for t in filtered if t.get("outcome") == "win"])
            / len(filtered)
            * 100
        )
        blend_weight = min(len(filtered) / min_samples, 0.8)
        est_wr = filtered_wr * blend_weight + base_wr * (1 - blend_weight)
        confidence = "low"
    else:
        est_wr = base_wr
        confidence = "very_low"

    if est_wr >= 60:
        recommendation = "favorable"
    elif est_wr >= 50:
        recommendation = "neutral"
    elif est_wr >= 40:
        recommendation = "caution"
    else:
        recommendation = "avoid"

    return SetupProbabilityResult(
        estimated_win_rate=round(est_wr, 1),
        sample_size=len(filtered),
        confidence=confidence,
        similar_trades=[
            {
                "symbol": t.get("symbol", ""),
                "regime": t.get("regime", ""),
                "outcome": t.get("outcome", ""),
                "pnl": t.get("pnl", 0),
            }
            for t in filtered[-10:]
        ],
        win_rate_by_regime=regime_wr,
        win_rate_by_session=session_wr,
        common_mistakes=mistakes,
        recommendation=recommendation,
    )


def _win_rate_by_field(trades: list[dict], field: str) -> dict[str, float]:
    result: dict[str, float] = {}
    grouped: dict[str, list[dict]] = {}
    for t in trades:
        val = t.get(field, "unknown")
        grouped.setdefault(val, []).append(t)

    for val, group in grouped.items():
        wins = sum(1 for t in group if t.get("outcome") == "win")
        result[val] = round(wins / len(group) * 100, 1) if group else 0.0

    return result


def _common_mistakes(trades: list[dict]) -> list[dict]:
    counts: dict[str, int] = {}
    for t in trades:
        mistake = t.get("mistake_category")
        if mistake and mistake != "none" and mistake != "no_mistake":
            counts[mistake] = counts.get(mistake, 0) + 1

    return sorted(
        [{"mistake": k, "count": v} for k, v in counts.items()],
        key=lambda x: x["count"],
        reverse=True,
    )[:5]
