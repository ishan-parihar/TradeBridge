"""Data-driven Trading Coach — derives advice from live market conditions.

NOT template-based. Every warning and insight is computed from:
- Recent bar patterns (momentum, compression, exhaustion)
- ATR vs historical percentile (is volatility actually abnormal?)
- Order book depth (liquidity, slippage risk)
- Indicator momentum (is RSI diverging? EMAs crossing?)
- Trade journal history (is the agent on a losing streak?)
- Spread vs ATR ratio (is the spread eating into the edge?)

The coach NEVER blocks. It informs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CoachingAdvice:
    warnings: list[str] = field(default_factory=list)
    insights: list[str] = field(default_factory=list)
    confidence_factors: dict = field(default_factory=dict)
    recommendation: str = "neutral"
    blocking: bool = False
    raw_metrics: dict = field(default_factory=dict)


class TradingCoach:
    """Derives coaching advice from live market data, not hardcoded templates."""

    def evaluate(
        self,
        *,
        # Core identity
        symbol: str,
        side: str,
        # Market data (from live indicators/bars)
        atr_value: Optional[float] = None,
        atr_30d_avg: Optional[float] = None,
        atr_percentile: Optional[float] = None,
        rsi: Optional[float] = None,
        rsi_1h_ago: Optional[float] = None,
        ema_fast: Optional[float] = None,
        ema_slow: Optional[float] = None,
        current_price: Optional[float] = None,
        # Bar pattern data
        last_bar_range: Optional[float] = None,
        last_bar_body: Optional[float] = None,
        last_bar_direction: Optional[str] = None,  # "bullish", "bearish", "doji"
        recent_bars_compression: Optional[float] = None,  # ratio of recent range to ATR
        # Order book
        spread_points: Optional[float] = None,
        bid_ask_imbalance: Optional[
            float
        ] = None,  # positive = more bids, negative = more asks
        # Trade proposal
        proposed_sl_points: Optional[float] = None,
        proposed_tp_points: Optional[float] = None,
        point: Optional[float] = None,
        entry_price: Optional[float] = None,
        # Agent context
        indicator_agreements: Optional[int] = None,
        total_indicators_checked: Optional[int] = None,
        trades_today: int = 0,
        daily_pnl: float = 0.0,
        equity: Optional[float] = None,
        recent_consecutive_losses: int = 0,
        win_rate_last_10: Optional[float] = None,
        # Position in range (0-100%)
        position_in_range: Optional[float] = None,
        regime: Optional[str] = None,
    ) -> CoachingAdvice:
        """Evaluate a potential trade using ONLY live data.

        Every warning and insight is computed from the provided data.
        No template rules — if the data says it's fine, the coach says it's fine.
        """
        advice = CoachingAdvice()

        # ====== VOLATILITY ANALYSIS ======
        if atr_value is not None and current_price is not None and current_price > 0:
            atr_pct = atr_value / current_price * 100
            advice.raw_metrics["atr_pct"] = round(atr_pct, 3)

            # Compare current ATR to historical
            if atr_30d_avg is not None and atr_30d_avg > 0:
                atr_ratio = atr_value / atr_30d_avg
                advice.raw_metrics["atr_vs_avg"] = round(atr_ratio, 2)

                if atr_ratio > 1.5:
                    advice.insights.append(
                        f"Volatility is {atr_ratio:.1f}x the 30-day average. "
                        f"ATR is {atr_value:.0f} points vs {atr_30d_avg:.0f} typical. "
                        f"Expect larger-than-normal swings. Widen SL accordingly."
                    )
                elif atr_ratio < 0.5:
                    advice.insights.append(
                        f"Volatility is compressed at {atr_ratio:.1f}x the 30-day average. "
                        f"ATR is {atr_value:.0f} vs {atr_30d_avg:.0f} typical. "
                        f"Compression usually precedes a breakout. Consider bracket orders."
                    )
                else:
                    advice.insights.append(
                        f"Volatility is normal ({atr_ratio:.1f}x 30-day avg). "
                        f"ATR {atr_value:.0f} points — standard SL/TP distances apply."
                    )

            if atr_percentile is not None:
                advice.raw_metrics["atr_percentile"] = atr_percentile
                if atr_percentile > 80:
                    advice.warnings.append(
                        f"ATR is at the {atr_percentile:.0f}th percentile — very elevated. "
                        f"Price can move {atr_value:.0f}+ points on normal noise. "
                        f"Your SL needs to account for this."
                    )
                elif atr_percentile < 20:
                    advice.insights.append(
                        f"ATR is at the {atr_percentile:.0f}th percentile — very compressed. "
                        f"Market is coiling. A directional move is likely building."
                    )

            # Spread vs ATR — is the spread eating the edge?
            if spread_points is not None and spread_points > 0:
                spread_atr_ratio = spread_points / atr_value if atr_value > 0 else 999
                advice.raw_metrics["spread_atr_ratio"] = round(spread_atr_ratio, 3)
                if spread_atr_ratio > 0.1:
                    advice.warnings.append(
                        f"Spread is {spread_points:.0f} points, which is {spread_atr_ratio * 100:.0f}% of ATR. "
                        f"The spread alone consumes {spread_atr_ratio * 100:.0f}% of your expected move. "
                        f"Edge may be eroded by transaction costs."
                    )

        # ====== SL/TP ANALYSIS (data-driven, not arbitrary) ======
        if proposed_sl_points is not None and atr_value is not None and atr_value > 0:
            # Convert SL from points to price units for proper comparison with ATR
            sl_in_price = (
                proposed_sl_points * point if point is not None else proposed_sl_points
            )
            sl_atr = sl_in_price / atr_value
            advice.raw_metrics["sl_atr_ratio"] = round(sl_atr, 2)

            # What % of recent bars would this SL have survived?
            # If SL < average bar range, it gets stopped out by single-bar noise
            if last_bar_range is not None and last_bar_range > 0:
                sl_vs_bar = proposed_sl_points / last_bar_range
                if sl_vs_bar < 1.0:
                    advice.warnings.append(
                        f"Your SL ({proposed_sl_points:.0f}pts) is smaller than a single bar's range "
                        f"({last_bar_range:.0f}pts). Normal candlestick wicks will stop you out "
                        f"before the trade has room to develop."
                    )
                elif sl_vs_bar < 1.5:
                    advice.warnings.append(
                        f"Your SL ({proposed_sl_points:.0f}pts) is only {sl_vs_bar:.1f}x the last bar's "
                        f"range ({last_bar_range:.0f}pts). Tight — a single volatile candle can hit it."
                    )
                else:
                    advice.insights.append(
                        f"SL is {sl_vs_bar:.1f}x the last bar range — beyond single-candle noise."
                    )

            if sl_atr < 0.5:
                advice.warnings.append(
                    f"SL is {sl_atr:.1f}x ATR. This means normal volatility alone "
                    f"({atr_value:.0f}pts) is 2x+ your stop distance. "
                    f"You're not giving the trade room to breathe."
                )
            elif sl_atr < 1.0:
                advice.warnings.append(
                    f"SL is {sl_atr:.1f}x ATR — below the volatility floor. "
                    f"ATR of {atr_value:.0f}pts means price naturally moves this much without trending."
                )
            elif sl_atr <= 2.0:
                advice.insights.append(
                    f"SL is {sl_atr:.1f}x ATR — within the volatility-appropriate range."
                )
            else:
                advice.warnings.append(
                    f"SL is {sl_atr:.1f}x ATR — very wide. "
                    f"Make sure your TP justifies this risk distance."
                )

        # Risk:Reward
        if proposed_tp_points and proposed_sl_points and proposed_sl_points > 0:
            rr = proposed_tp_points / proposed_sl_points
            advice.raw_metrics["rr_ratio"] = round(rr, 2)

            # What win rate do you need to be profitable at this RR?
            required_wr = 1.0 / (rr + 1.0) * 100
            advice.raw_metrics["required_win_rate"] = round(required_wr, 1)

            if rr < 1.0:
                advice.warnings.append(
                    f"Risk:Reward is {rr:.1f}:1. You need a {required_wr:.0f}% win rate just to break even. "
                    f"You're risking more than you can make."
                )
            elif rr < 1.5:
                advice.warnings.append(
                    f"Risk:Reward is {rr:.1f}:1 — you need {required_wr:.0f}% win rate. "
                    f"Marginal edge. A 2:1 ratio would only need {1 / (2 + 1) * 100:.0f}%."
                )
            elif rr >= 2.0:
                advice.insights.append(
                    f"Risk:Reward is {rr:.1f}:1 — favorable. "
                    f"You only need {required_wr:.0f}% win rate to be profitable."
                )
            else:
                advice.insights.append(
                    f"Risk:Reward is {rr:.1f}:1 — acceptable. "
                    f"Need {required_wr:.0f}% win rate to break even."
                )

        # ====== MOMENTUM / INDICATOR ANALYSIS ======
        if rsi is not None:
            advice.raw_metrics["rsi"] = round(rsi, 1)

            # RSI direction (momentum)
            if rsi_1h_ago is not None:
                rsi_change = rsi - rsi_1h_ago
                advice.raw_metrics["rsi_momentum"] = round(rsi_change, 1)

                if side == "buy" and rsi_change < -10:
                    advice.warnings.append(
                        f"RSI was {rsi_1h_ago:.0f} an hour ago, now {rsi:.0f} — dropping fast. "
                        f"Momentum is shifting against your long entry."
                    )
                elif side == "sell" and rsi_change > 10:
                    advice.warnings.append(
                        f"RSI was {rsi_1h_ago:.0f} an hour ago, now {rsi:.0f} — rising fast. "
                        f"Momentum is shifting against your short entry."
                    )

            if rsi > 80:
                advice.insights.append(
                    f"RSI at {rsi:.0f} — deeply overbought. "
                    f"Long entries here are buying at exhaustion."
                )
            elif rsi < 20:
                advice.insights.append(
                    f"RSI at {rsi:.0f} — deeply oversold. "
                    f"Short entries here are selling at exhaustion."
                )

        # EMA alignment
        if ema_fast is not None and ema_slow is not None and current_price is not None:
            ema_aligned_bullish = ema_fast > ema_slow
            ema_aligned_bearish = ema_fast < ema_slow
            advice.raw_metrics["ema_alignment"] = (
                "bullish" if ema_aligned_bullish else "bearish"
            )

            if side == "buy" and ema_aligned_bearish:
                advice.warnings.append(
                    f"EMAs are bearish-aligned (fast {ema_fast:.0f} < slow {ema_slow:.0f}). "
                    f"Price is {current_price:.0f}, below both EMAs. "
                    f"You're entering long against the trend structure."
                )
            elif side == "sell" and ema_aligned_bullish:
                advice.warnings.append(
                    f"EMAs are bullish-aligned (fast {ema_fast:.0f} > slow {ema_slow:.0f}). "
                    f"Price is {current_price:.0f}, above both EMAs. "
                    f"You're entering short against the trend structure."
                )
            else:
                direction = "bullish" if ema_aligned_bullish else "bearish"
                advice.insights.append(
                    f"EMAs are {direction}-aligned — structure supports {side} entries."
                )

        # ====== BAR PATTERN ANALYSIS ======
        if (
            last_bar_body is not None
            and last_bar_range is not None
            and last_bar_range > 0
        ):
            body_ratio = (
                abs(last_bar_body) / last_bar_range if last_bar_range > 0 else 0
            )
            advice.raw_metrics["bar_body_ratio"] = round(body_ratio, 2)

            if body_ratio < 0.2:
                advice.insights.append(
                    f"Last bar was a doji/spinner (body is {body_ratio * 100:.0f}% of range). "
                    f"Indecision — market is choosing a direction."
                )
            elif body_ratio > 0.8:
                advice.insights.append(
                    f"Last bar had a strong body ({body_ratio * 100:.0f}% of range) "
                    f"{'bullish' if last_bar_direction == 'bullish' else 'bearish'}. "
                    f"Clear directional conviction."
                )

        # Compression detection
        if recent_bars_compression is not None:
            advice.raw_metrics["compression_ratio"] = round(recent_bars_compression, 2)
            if recent_bars_compression < 0.5:
                advice.insights.append(
                    f"Recent bars are compressed to {recent_bars_compression * 100:.0f}% of ATR. "
                    f"Market is coiling. A directional move is imminent — bracket orders on both sides "
                    f"capture the breakout without prediction."
                )
            elif recent_bars_compression > 1.5:
                advice.insights.append(
                    f"Recent bars are {recent_bars_compression:.1f}x ATR — wide ranges. "
                    f"High volatility environment. Use wider stops, smaller position size."
                )

        # ====== REGIME ALIGNMENT ======
        if regime and position_in_range is not None:
            advice.raw_metrics["regime"] = regime
            advice.raw_metrics["position_in_range"] = round(position_in_range, 1)

            if regime == "ranging":
                if 30 <= position_in_range <= 70:
                    advice.warnings.append(
                        f"Price is at {position_in_range:.0f}% of the range — middle ground. "
                        f"No edge here. In ranging markets, entries work best near support (<25%) "
                        f"or resistance (>75%), or via bracket orders to catch the break."
                    )
                elif position_in_range > 75 and side == "buy":
                    advice.warnings.append(
                        f"Price is near resistance ({position_in_range:.0f}% of range). "
                        f"Buying at the top of a range — high probability of rejection."
                    )
                elif position_in_range < 25 and side == "sell":
                    advice.warnings.append(
                        f"Price is near support ({position_in_range:.0f}% of range). "
                        f"Selling at the bottom of a range — high probability of bounce."
                    )
            elif regime == "compressing":
                advice.insights.append(
                    f"Market is compressing. {recent_bars_compression * 100:.0f}% of normal ATR. "
                    f"This is a setup condition, not an entry condition. "
                    f"Place bracket orders outside the compression zone."
                )

        # ====== INDICATOR CONFLUENCE ======
        if indicator_agreements is not None and total_indicators_checked is not None:
            confluence_pct = (
                indicator_agreements / total_indicators_checked
                if total_indicators_checked > 0
                else 0
            )
            advice.raw_metrics["confluence"] = (
                f"{indicator_agreements}/{total_indicators_checked}"
            )

            if confluence_pct >= 0.75:
                advice.insights.append(
                    f"{indicator_agreements}/{total_indicators_checked} indicators agree "
                    f"({confluence_pct * 100:.0f}% confluence). Strong signal alignment."
                )
            elif confluence_pct >= 0.5:
                advice.insights.append(
                    f"{indicator_agreements}/{total_indicators_checked} indicators agree "
                    f"({confluence_pct * 100:.0f}% confluence). Moderate alignment."
                )
            elif confluence_pct > 0:
                advice.warnings.append(
                    f"Only {indicator_agreements}/{total_indicators_checked} indicators agree "
                    f"({confluence_pct * 100:.0f}%). "
                    f"Most indicators disagree with this direction."
                )
            else:
                advice.warnings.append(
                    f"0/{total_indicators_checked} indicators support this direction. "
                    f"This entry has zero technical confirmation."
                )

        # ====== AGENT PERFORMANCE CONTEXT ======
        if recent_consecutive_losses >= 2:
            advice.warnings.append(
                f"{recent_consecutive_losses} consecutive losses. "
                f"This is the highest-risk moment for revenge trading — "
                f"the urge to 'make it back' leads to worse entries. "
                f"Step back. Review what went wrong. The market will be here in 30 minutes."
            )

        if win_rate_last_10 is not None and win_rate_last_10 < 30:
            advice.warnings.append(
                f"Your recent win rate is {win_rate_last_10:.0f}% over last 10 trades. "
                f"Something is off — either the regime has changed or your setup isn't working. "
                f"Consider reducing size or stepping out until you recalibrate."
            )

        if trades_today >= 5:
            advice.warnings.append(
                f"{trades_today} trades today. You're in overtrading territory. "
                f"Most profitable sessions have 1-3 trades. Daily P&L: ${daily_pnl:.2f}."
            )

        # ====== DETERMINE RECOMMENDATION ======
        warning_weight = sum(1 for w in advice.warnings)
        insight_weight = sum(1 for i in advice.insights)

        # Count negative vs positive confidence factors
        negative_factors = sum(
            1
            for v in advice.confidence_factors.values()
            if v is False or (isinstance(v, (int, float)) and v < 0.4)
        )
        positive_factors = sum(
            1
            for v in advice.confidence_factors.values()
            if v is True or (isinstance(v, (int, float)) and v > 0.7)
        )

        if warning_weight >= 4 or negative_factors >= 3:
            advice.recommendation = "strong_wait"
        elif warning_weight >= 2:
            advice.recommendation = "cautious_wait"
        elif positive_factors >= 2 and warning_weight == 0:
            advice.recommendation = "strong_entry"
        elif insight_weight > warning_weight:
            advice.recommendation = "cautious_entry"
        else:
            advice.recommendation = "neutral"

        return advice
