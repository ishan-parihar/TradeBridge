"""Opportunity Ranker — scores symbols by trade-readiness.

Agents have no way to know which symbols deserve attention NOW. This service
scores each symbol across 7 weighted factors and returns a ranked list with
scores, reasons, and skip flags.

Usage:
    ranker = OpportunityRanker()
    results = ranker.rank(
        symbols=["XAUUSD", "EURUSD", "GBPUSD"],
        snapshots={"XAUUSD": {...}, "EURUSD": {...}},  # from SymbolSnapshotService
        portfolio_positions=[...],  # optional: current open positions
    )
"""

from __future__ import annotations

from typing import Any, Optional


# Scoring weights — sum to 1.0
_WEIGHTS = {
    "regime_clarity": 0.20,
    "spread_atr_ratio": 0.15,
    "volatility_usability": 0.15,
    "session_quality": 0.15,
    "confluence": 0.15,
    "portfolio_overlap": 0.10,
    "calendar": 0.10,
}

_VALID_WEIGHT_KEYS = set(_WEIGHTS.keys())


class OpportunityRanker:
    """Score and rank symbols by opportunity quality."""

    @classmethod
    def get_default_weights(cls) -> dict:
        """Return a copy of the default scoring weights."""
        return dict(_WEIGHTS)

    def _validate_weights(
        self, weights: dict[str, float]
    ) -> tuple[dict[str, float] | None, str | None]:
        """Validate custom weights. Returns (weights, None) on success or (None, error)."""
        invalid_keys = set(weights.keys()) - _VALID_WEIGHT_KEYS
        if invalid_keys:
            return (
                None,
                f"Invalid weight keys: {invalid_keys}. Valid keys: {sorted(_VALID_WEIGHT_KEYS)}",
            )

        for key, val in weights.items():
            if val < 0:
                return (
                    None,
                    f"Weight for '{key}' is negative ({val}). All weights must be non-negative.",
                )

        total = sum(weights.values())
        if not (0.95 <= total <= 1.05):
            return None, f"Weights sum to {total:.3f}, must be between 0.95 and 1.05."

        return weights, None

    def rank(
        self,
        symbols: list[str],
        snapshots: dict[str, dict],
        *,
        portfolio_positions: Optional[list[dict]] = None,
        min_score: float = 50.0,
        weights: dict[str, float] | None = None,
    ) -> list[dict]:
        """Score and rank symbols by opportunity quality.

        Args:
            symbols: List of symbol names to evaluate
            snapshots: dict mapping symbol → snapshot dict (from
                SymbolSnapshotService.build())
            portfolio_positions: Current open positions for overlap detection
            min_score: Minimum score to include in results (below threshold
                still returned but with skip_reason)
            weights: Optional custom scoring weights. Must have valid keys
                (subset of: regime_clarity, spread_atr_ratio,
                volatility_usability, session_quality, confluence,
                portfolio_overlap, calendar) and values summing to ~1.0
                (0.95–1.05). Falls back to defaults with warning on invalid input.

        Returns:
            Ranked list of dicts with:
            - symbol: str
            - score: float (0-100)
            - factors: dict of individual factor scores
            - reasons: list[str] — why this score
            - skip_reason: str | None — why to skip (if below threshold)
            - recommendation: str — "trade" | "watch" | "skip"
            - weights_used: dict — the weights applied to compute the score
        """
        if not symbols:
            return []

        portfolio_positions = portfolio_positions or []

        active_weights = _WEIGHTS
        warning_message: str | None = None
        if weights is not None:
            validated, err = self._validate_weights(weights)
            if err:
                warning_message = f"Custom weights invalid ({err}), using defaults."
                active_weights = _WEIGHTS
            else:
                active_weights = validated

        weights_used = dict(active_weights)
        results: list[dict] = []

        for sym in symbols:
            sym_upper = sym.upper()
            snapshot = snapshots.get(sym_upper, snapshots.get(sym, {}))

            skip_reason = self._check_already_exposed(sym_upper, portfolio_positions)
            if skip_reason:
                results.append(
                    {
                        "symbol": sym_upper,
                        "score": 0.0,
                        "factors": {},
                        "reasons": ["Portfolio already has position in this symbol"],
                        "skip_reason": skip_reason,
                        "recommendation": "skip",
                        "weights_used": weights_used,
                    }
                )
                continue

            factors = self._score_factors(sym_upper, snapshot)

            skip_reason = self._check_hard_skips(sym_upper, snapshot, factors)
            if skip_reason:
                weighted_score = self._weighted_score(factors, active_weights)
                reasons = self._build_reasons(sym_upper, factors, snapshot)
                results.append(
                    {
                        "symbol": sym_upper,
                        "score": round(weighted_score, 1),
                        "factors": {k: round(v, 1) for k, v in factors.items()},
                        "reasons": reasons,
                        "skip_reason": skip_reason,
                        "recommendation": "skip",
                        "weights_used": weights_used,
                    }
                )
                continue

            weighted_score = self._weighted_score(factors, active_weights)
            reasons = self._build_reasons(sym_upper, factors, snapshot)

            if weighted_score >= min_score:
                recommendation = "trade"
                skip_reason = None
            elif weighted_score >= min_score * 0.6:
                recommendation = "watch"
                skip_reason = None
            else:
                recommendation = "skip"
                skip_reason = "below_threshold"
                reasons.append(
                    f"Score {weighted_score:.0f} below minimum {min_score:.0f}"
                )

            results.append(
                {
                    "symbol": sym_upper,
                    "score": round(weighted_score, 1),
                    "factors": {k: round(v, 1) for k, v in factors.items()},
                    "reasons": reasons,
                    "skip_reason": skip_reason,
                    "recommendation": recommendation,
                    "weights_used": weights_used,
                }
            )

        if warning_message:
            results.insert(0, {"warning": warning_message})

        results.sort(key=lambda r: r.get("score", 0), reverse=True)
        return results

    # ----------------------------------------------------------------
    # Factor scoring (each returns 0-100)
    # ----------------------------------------------------------------

    def _score_factors(self, symbol: str, snapshot: dict) -> dict[str, float]:
        """Score all 7 factors for a symbol."""
        return {
            "regime_clarity": self._score_regime(snapshot),
            "spread_atr_ratio": self._score_spread_atr(snapshot),
            "volatility_usability": self._score_volatility(snapshot),
            "session_quality": self._score_session(snapshot),
            "confluence": self._score_confluence(snapshot),
            "portfolio_overlap": 100.0,  # Already handled by _check_already_exposed
            "calendar": self._score_calendar(snapshot),
        }

    def _score_regime(self, snapshot: dict) -> float:
        """Regime clarity: trending=100, compressing=60, ranging=20, unknown=0."""
        regime_data = snapshot.get("regime", {})
        regime = (
            regime_data.get("regime", "unknown")
            if isinstance(regime_data, dict)
            else "unknown"
        )

        scores = {
            "trending_up": 100.0,
            "trending_down": 100.0,
            "compressing": 60.0,
            "ranging": 20.0,
            "unknown": 0.0,
        }
        return scores.get(regime, 0.0)

    def _score_spread_atr(self, snapshot: dict) -> float:
        """Spread/ATR ratio: < 5% = 100, > 20% = 0, linear between."""
        price = snapshot.get("price", {})
        spread_ratio = price.get("spread_ratio_atr")

        if spread_ratio is None:
            # Can't determine — neutral score
            return 50.0

        ratio_pct = spread_ratio * 100  # Convert to percentage

        if ratio_pct <= 5.0:
            return 100.0
        elif ratio_pct >= 20.0:
            return 0.0
        else:
            # Linear interpolation: 100 at 5%, 0 at 20%
            return 100.0 * (20.0 - ratio_pct) / 15.0

    def _score_volatility(self, snapshot: dict) -> float:
        """Volatility usability based on ATR percentile.

        - ATR in normal range (30th-95th percentile) = 100
        - ATR compressed (< 30th percentile) = 30
        - ATR extreme (> 95th percentile) = 40
        - 30th-50th percentile: linear 30→100
        - 50th-95th percentile: 100
        - 95th-100th percentile: linear 100→40
        """
        indicators = snapshot.get("indicators", {})
        atr_info = indicators.get("atr", {})
        atr_percentile = (
            atr_info.get("percentile") if isinstance(atr_info, dict) else None
        )

        if atr_percentile is None:
            return 50.0  # Unknown — neutral

        pct = float(atr_percentile)

        if pct < 30.0:
            # Compressed: score 30
            return 30.0
        elif pct < 50.0:
            # Transition from compressed to normal: 30→100
            return 30.0 + (pct - 30.0) / 20.0 * 70.0
        elif pct <= 95.0:
            # Normal range
            return 100.0
        else:
            # Extreme: 100→40
            return 100.0 - (pct - 95.0) / 5.0 * 60.0

    def _score_session(self, snapshot: dict) -> float:
        """Session quality based on current forex session.

        - London/NY overlap = 100
        - London or NY only = 70
        - Asian (Tokyo/Sydney) = 40
        - Closed = 0
        """
        session = snapshot.get("session_context", {})
        if not session or "error" in session:
            return 50.0  # Unknown — neutral

        if not session.get("is_market_open", True):
            return 0.0

        active_overlaps = session.get("active_overlaps", [])
        current_sessions = session.get("current_sessions", [])

        # London/NY overlap is best
        if "london_ny_overlap" in active_overlaps:
            return 100.0

        # Tokyo/London overlap is good
        if "tokyo_london_overlap" in active_overlaps:
            return 80.0

        # Individual sessions
        if "london" in current_sessions or "new_york" in current_sessions:
            return 70.0

        if "tokyo" in current_sessions:
            return 50.0

        if "sydney" in current_sessions:
            return 40.0

        # Market open but no session identified
        return 30.0

    def _score_confluence(self, snapshot: dict) -> float:
        """Indicator confluence: agreement on direction.

        - All agree (EMA alignment + RSI + MACD) = 100
        - Mixed (some agree, some neutral) = 50
        - Contradictory (signals conflict) = 10
        """
        indicators = snapshot.get("indicators", {})

        signals: list[str] = []  # "bullish", "bearish", or "neutral"

        # EMA alignment
        ema_alignment = indicators.get("ema_alignment")
        if ema_alignment:
            signals.append(ema_alignment)

        # RSI
        rsi_info = indicators.get("rsi", {})
        if isinstance(rsi_info, dict):
            rsi_value = rsi_info.get("value")
            if rsi_value is not None:
                if rsi_value > 60:
                    signals.append("bullish")
                elif rsi_value < 40:
                    signals.append("bearish")
                else:
                    signals.append("neutral")

        # MACD
        macd_info = indicators.get("macd", {})
        if isinstance(macd_info, dict):
            histogram = macd_info.get("histogram")
            if histogram is not None:
                if histogram > 0:
                    signals.append("bullish")
                elif histogram < 0:
                    signals.append("bearish")
                else:
                    signals.append("neutral")

        if not signals:
            return 50.0  # No data — neutral

        bullish = signals.count("bullish")
        bearish = signals.count("bearish")
        neutral = signals.count("neutral")

        # All agree (directional)
        if bullish > 0 and bearish == 0 and neutral == 0:
            return 100.0
        if bearish > 0 and bullish == 0 and neutral == 0:
            return 100.0

        # Contradictory (both bullish and bearish present)
        if bullish > 0 and bearish > 0:
            return 10.0

        # Mixed (some directional + neutrals)
        directional = bullish + bearish
        if directional > 0:
            # Weight by proportion of agreement
            agreement = max(bullish, bearish) / len(signals)
            return 50.0 + agreement * 50.0

        # All neutral
        return 50.0

    def _score_calendar(self, snapshot: dict) -> float:
        """Calendar risk: no high-impact events = 100, event in 1h = 30, in 30min = 0."""
        calendar_data = snapshot.get("calendar", {})
        if not calendar_data or "error" in calendar_data:
            return 100.0  # Unknown — assume safe

        if calendar_data.get("is_blackout"):
            return 0.0

        # Check upcoming events for time proximity
        events = calendar_data.get("upcoming_events", [])
        if not events:
            return 100.0

        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        min_minutes = float("inf")

        for event in events:
            event_str = event.get("event_date_utc", "")
            if not event_str:
                continue
            try:
                event_time = datetime.fromisoformat(event_str)
                if event_time.tzinfo is None:
                    event_time = event_time.replace(tzinfo=timezone.utc)
                diff_minutes = (event_time - now).total_seconds() / 60.0
                if 0 <= diff_minutes < min_minutes:
                    min_minutes = diff_minutes
            except (ValueError, TypeError):
                continue

        if min_minutes == float("inf"):
            return 100.0

        if min_minutes <= 30:
            return 0.0
        elif min_minutes <= 60:
            return 30.0
        else:
            return 100.0

    # ----------------------------------------------------------------
    # Skip checks
    # ----------------------------------------------------------------

    def _check_already_exposed(
        self, symbol: str, portfolio_positions: list[dict]
    ) -> str | None:
        """Check if portfolio already has a position in this symbol."""
        for pos in portfolio_positions:
            pos_symbol = pos.get("symbol", "").upper()
            if pos_symbol == symbol:
                return "already_exposed"
        return None

    def _check_hard_skips(
        self, symbol: str, snapshot: dict, factors: dict[str, float]
    ) -> str | None:
        """Check hard skip conditions that override scoring.

        Returns skip reason string or None.
        """
        # Market closed
        session = snapshot.get("session_context", {})
        if session and not session.get("is_market_open", True):
            return "market_closed"

        # Calendar blackout (event within 30 minutes)
        calendar_data = snapshot.get("calendar", {})
        if isinstance(calendar_data, dict) and calendar_data.get("is_blackout"):
            return "calendar_blackout"
        if factors.get("calendar", 100) == 0.0:
            return "calendar_blackout"

        # Spread too wide (> 20% of ATR)
        if factors.get("spread_atr_ratio", 100) == 0.0:
            return "spread_too_wide"

        # Low volatility (ATR below 30th percentile)
        if factors.get("volatility_usability", 100) <= 30.0:
            indicators = snapshot.get("indicators", {})
            atr_info = indicators.get("atr", {})
            atr_percentile = (
                atr_info.get("percentile") if isinstance(atr_info, dict) else None
            )
            if atr_percentile is not None and float(atr_percentile) < 30.0:
                return "low_volatility"

        # Ranging market with no compression breakout
        regime_data = snapshot.get("regime", {})
        regime = regime_data.get("regime") if isinstance(regime_data, dict) else None
        if regime == "ranging" and factors.get("regime_clarity", 0) <= 20.0:
            return "ranging_market"

        return None

    # ----------------------------------------------------------------
    # Helpers
    # ----------------------------------------------------------------

    def _weighted_score(
        self, factors: dict[str, float], weights: dict[str, float] | None = None
    ) -> float:
        """Compute weighted composite score from factor scores."""
        w = weights if weights is not None else _WEIGHTS
        total = 0.0
        for factor_name, weight in w.items():
            score = factors.get(factor_name, 50.0)
            total += score * weight
        return total

    def _build_reasons(
        self, symbol: str, factors: dict[str, float], snapshot: dict
    ) -> list[str]:
        """Build human-readable reasons for the score."""
        reasons: list[str] = []

        # Regime
        regime_score = factors.get("regime_clarity", 0)
        regime_data = snapshot.get("regime", {})
        regime = (
            regime_data.get("regime", "unknown")
            if isinstance(regime_data, dict)
            else "unknown"
        )
        if regime_score >= 80:
            reasons.append(f"Clear {regime.replace('_', ' ')} regime")
        elif regime_score >= 40:
            reasons.append(f"Moderate {regime.replace('_', ' ')} conditions")
        elif regime_score > 0:
            reasons.append(f"Weak {regime.replace('_', ' ')} setup")
        else:
            reasons.append("Unknown regime — no directional edge")

        # Spread
        spread_score = factors.get("spread_atr_ratio", 50)
        price = snapshot.get("price", {})
        spread_ratio = price.get("spread_ratio_atr")
        if spread_ratio is not None:
            spread_pct = spread_ratio * 100
            if spread_score >= 80:
                reasons.append(f"Tight spread ({spread_pct:.1f}% of ATR)")
            elif spread_score >= 40:
                reasons.append(f"Moderate spread ({spread_pct:.1f}% of ATR)")
            else:
                reasons.append(f"Wide spread ({spread_pct:.1f}% of ATR)")

        # Volatility
        vol_score = factors.get("volatility_usability", 50)
        indicators = snapshot.get("indicators", {})
        atr_info = indicators.get("atr", {})
        atr_percentile = (
            atr_info.get("percentile") if isinstance(atr_info, dict) else None
        )
        if atr_percentile is not None:
            if vol_score >= 80:
                reasons.append(
                    f"ATR in normal range ({atr_percentile:.0f}th percentile)"
                )
            elif vol_score >= 40:
                reasons.append(
                    f"ATR somewhat compressed ({atr_percentile:.0f}th percentile)"
                )
            else:
                reasons.append(
                    f"ATR very compressed ({atr_percentile:.0f}th percentile)"
                )

        # Session
        session_score = factors.get("session_quality", 50)
        if session_score >= 90:
            reasons.append("Optimal session (London/NY overlap)")
        elif session_score >= 60:
            reasons.append("Good session (London or NY)")
        elif session_score >= 30:
            reasons.append("Sub-optimal session (Asian hours)")
        else:
            reasons.append("Market closed or no session identified")

        # Confluence
        conf_score = factors.get("confluence", 50)
        if conf_score >= 80:
            reasons.append("Strong indicator confluence")
        elif conf_score >= 40:
            reasons.append("Mixed indicator signals")
        else:
            reasons.append("Contradictory indicator signals")

        # Calendar
        cal_score = factors.get("calendar", 100)
        if cal_score < 100:
            if cal_score == 0:
                reasons.append("Economic event within 30 minutes")
            elif cal_score <= 30:
                reasons.append("Economic event within 1 hour")

        return reasons
