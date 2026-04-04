"""Trading Context Injection — symbol-specific education for the AI agent.

When the AI agent feels stuck or uncertain, it queries this tool to get:
- What this symbol is and how it behaves
- What "1 point" means in dollars (for 0.01 lots)
- Normal ATR range, daily range, typical SL/TP distances
- Expected trade duration
- Common pitfalls and how to avoid them
- Volatility context: "Is 200 points a lot on this symbol?"

This gives the agent composure by understanding what's normal vs abnormal.
"""

from __future__ import annotations

from typing import Optional

# Symbol-specific context guides
# These are knowledge bases the AI can query when uncertain

SYMBOL_CONTEXTS: dict[str, dict] = {
    "BTCUSD": {
        "name": "Bitcoin vs US Dollar",
        "asset_class": "Cryptocurrency",
        "description": "Bitcoin is the largest cryptocurrency. Highly volatile, trades 24/7, moves in large swings.",
        "point_value": {
            "per_0_01_lot": "$0.01 per point",
            "per_0_1_lot": "$0.10 per point",
            "per_1_lot": "$1.00 per point",
            "explanation": "1 point = $1 price movement. On 0.01 lots, each point is worth $0.01.",
        },
        "typical_atr_h1": {
            "value": "300-500 points",
            "explanation": "On H1, BTCUSD typically moves 300-500 points per candle. This is NORMAL, not extreme.",
        },
        "typical_atr_d1": {
            "value": "1500-3000 points",
            "explanation": "On D1, BTCUSD typically moves 1500-3000 points per day. A 200-point move is just 7-13% of daily range — it's noise.",
        },
        "reasonable_sl": {
            "scalp": "100-200 points",
            "swing": "300-600 points",
            "position": "600-1500 points",
            "explanation": "SL should be placed beyond normal noise. 1x ATR is minimum. 1.5-2x ATR for swing trades.",
        },
        "reasonable_tp": {
            "scalp": "200-400 points",
            "swing": "600-1200 points",
            "position": "1500-3000+ points",
            "explanation": "TP should be 2-3x your SL distance for positive risk:reward.",
        },
        "expected_duration": {
            "scalp": "15 min - 2 hours",
            "swing": "4 hours - 2 days",
            "position": "2 days - 2 weeks",
        },
        "volatility_context": {
            "normal_daily_range": "1500-3000 points",
            "quiet_day": "< 1000 points",
            "volatile_day": "> 4000 points",
            "key_insight": "A 200-point move on BTCUSD is NOTHING. It's less than 10% of normal daily range. Don't panic. Don't micro-manage.",
        },
        "common_pitfalls": [
            "Exiting too early on small pullbacks — BTCUSD regularly pulls back 100-200 points within a trend",
            "Moving SL to breakeven too soon — wait for at least 1x ATR in profit",
            "Trading during low-liquidity hours (weekends, Asian session) — wider spreads, unpredictable moves",
            "Ignoring the higher timeframe — always check D1 trend before entering on H1",
        ],
        "trading_wisdom": "BTCUSD rewards patience. Set your SL based on ATR, then step away. Checking every minute will cause you to exit good trades on normal noise.",
    },
    "XAUUSD": {
        "name": "Gold vs US Dollar",
        "asset_class": "Commodity / Precious Metal",
        "description": "Gold is a safe-haven asset. Moves on geopolitical news, USD strength, and inflation expectations. Moderate volatility.",
        "point_value": {
            "per_0_01_lot": "$0.01 per point (1 cent per $0.01 price move)",
            "per_0_1_lot": "$0.10 per point",
            "per_1_lot": "$1.00 per point",
            "explanation": "1 point = $0.01 price movement in gold. On 0.01 lots, each point is worth $0.01.",
        },
        "typical_atr_h1": {
            "value": "20-40 points",
            "explanation": "On H1, gold typically moves 20-40 points ($0.20-$0.40) per candle.",
        },
        "typical_atr_d1": {
            "value": "150-300 points",
            "explanation": "On D1, gold typically moves $1.50-$3.00 per day (150-300 points).",
        },
        "reasonable_sl": {
            "scalp": "15-30 points",
            "swing": "40-80 points",
            "position": "100-200 points",
        },
        "reasonable_tp": {
            "scalp": "30-60 points",
            "swing": "80-160 points",
            "position": "200-400+ points",
        },
        "expected_duration": {
            "scalp": "15 min - 1 hour",
            "swing": "2 hours - 1 day",
            "position": "1 day - 1 week",
        },
        "volatility_context": {
            "normal_daily_range": "150-300 points",
            "quiet_day": "< 100 points",
            "volatile_day": "> 400 points (usually on NFP, FOMC, or geopolitical events)",
            "key_insight": "Gold moves in bursts. Most of the daily range happens in 2-3 sessions (London open, NY open, or news events).",
        },
        "common_pitfalls": [
            "Trading right before major news (NFP, FOMC) — spreads widen, price jumps",
            "Ignoring correlation with DXY (US Dollar Index) — gold moves inversely to USD",
            "Entering during Asian session — low volume, choppy price action",
        ],
        "trading_wisdom": "Gold respects technical levels well. Support/resistance, trendlines, and Fibonacci levels are reliable. Trade London/NY overlap for best liquidity.",
    },
    "EURUSD": {
        "name": "Euro vs US Dollar",
        "asset_class": "Forex",
        "description": "The most traded currency pair. Low volatility, tight spreads, respects technical analysis well.",
        "point_value": {
            "per_0_01_lot": "$0.10 per pip (10 points = 1 pip)",
            "per_0_1_lot": "$1.00 per pip",
            "per_1_lot": "$10.00 per pip",
            "explanation": "In forex, 1 pip = 10 points (0.0001 price movement). On EURUSD, 1 pip on 0.01 lots = $0.10.",
        },
        "typical_atr_h1": {
            "value": "8-15 points",
            "explanation": "On H1, EURUSD typically moves 8-15 points per candle. Very stable.",
        },
        "typical_atr_d1": {
            "value": "60-100 points",
            "explanation": "On D1, EURUSD typically moves 60-100 points (6-10 pips) per day.",
        },
        "reasonable_sl": {
            "scalp": "10-20 points",
            "swing": "25-50 points",
            "position": "50-100 points",
        },
        "reasonable_tp": {
            "scalp": "15-30 points",
            "swing": "50-100 points",
            "position": "100-200+ points",
        },
        "expected_duration": {
            "scalp": "5-30 minutes",
            "swing": "1-8 hours",
            "position": "1 day - 1 week",
        },
        "volatility_context": {
            "normal_daily_range": "60-100 points",
            "quiet_day": "< 40 points",
            "volatile_day": "> 150 points (news events)",
            "key_insight": "EURUSD is the MOST liquid pair. 200 points would be 2-3x normal daily range — that would be a MAJOR event. A 200-point SL on BTCUSD is tiny; on EURUSD it would be catastrophic.",
        },
        "common_pitfalls": [
            "Overtrading — tight spreads invite too many small trades that add up in commissions",
            "Ignoring economic calendar — ECB, Fed announcements cause big moves",
            "Trading during low-liquidity hours — price can be erratic outside London/NY overlap",
        ],
        "trading_wisdom": "EURUSD is a patient market. Wait for clear setups. Don't force trades. The best moves happen during London/NY overlap (8am-12pm EST).",
    },
    "GBPUSD": {
        "name": "British Pound vs US Dollar",
        "asset_class": "Forex",
        "description": "More volatile than EURUSD. Known as 'Cable'. Moves well on UK economic data.",
        "point_value": {
            "per_0_01_lot": "$0.10 per pip (10 points = 1 pip)",
            "per_0_1_lot": "$1.00 per pip",
            "per_1_lot": "$10.00 per pip",
        },
        "typical_atr_h1": {"value": "12-25 points"},
        "typical_atr_d1": {"value": "80-150 points"},
        "reasonable_sl": {
            "scalp": "15-30 points",
            "swing": "35-70 points",
            "position": "70-150 points",
        },
        "reasonable_tp": {
            "scalp": "25-50 points",
            "swing": "70-140 points",
            "position": "150-300 points",
        },
        "expected_duration": {
            "scalp": "10-45 minutes",
            "swing": "2-12 hours",
            "position": "1-5 days",
        },
        "volatility_context": {
            "normal_daily_range": "80-150 points",
            "key_insight": "GBPUSD is ~1.5x more volatile than EURUSD. Adjust your SL accordingly.",
        },
        "common_pitfalls": [
            "Using EURUSD-style SL distances — GBPUSD needs wider stops",
            "Ignoring Brexit/UK political news — causes sudden spikes",
        ],
        "trading_wisdom": "GBPUSD trends well but whipsaws more than EURUSD. Use wider SL and be prepared for deeper pullbacks.",
    },
}


def get_trading_context(
    symbol: str,
    *,
    current_atr: Optional[float] = None,
    include_comparison: bool = True,
) -> dict:
    """Get trading context for a symbol.

    This is the education/composure tool. When the AI agent is uncertain,
    it calls this to understand what's normal vs abnormal.

    Args:
        symbol: Trading symbol (e.g., "BTCUSD", "EURUSD")
        current_atr: Current ATR value — if provided, gives real-time context
        include_comparison: Include comparison with other symbols

    Returns:
        Context dict with symbol info, volatility context, and trading wisdom.
    """
    symbol_upper = symbol.upper()

    # Check known symbols
    context = SYMBOL_CONTEXTS.get(symbol_upper, None)

    if context is None:
        # Generic fallback for unknown symbols
        context = {
            "name": symbol,
            "asset_class": "Unknown",
            "description": f"No specific context available for {symbol}. Use ATR-based analysis.",
            "point_value": {
                "explanation": "Check symbol_info for exact point value and contract specifications."
            },
            "reasonable_sl": {
                "explanation": f"Use 1x ATR as minimum SL. Current ATR: {current_atr} (if available)."
            },
            "common_pitfalls": [
                "Not checking symbol specs before trading",
                "Using the same SL distance across different symbols",
            ],
            "trading_wisdom": "Always check ATR before entering. SL should be placed beyond normal noise.",
        }

    # Add real-time ATR context if provided
    if current_atr is not None:
        atr_key = "typical_atr_h1"
        typical_atr = context.get(atr_key, {}).get("value", "unknown")
        context["current_atr"] = current_atr
        context["atr_assessment"] = _assess_atr(current_atr, context)

    # Add comparison table if requested
    if include_comparison:
        context["comparison"] = _build_comparison_table()

    context["symbol"] = symbol_upper

    return context


def _assess_atr(current_atr: float, context: dict) -> dict:
    """Assess current ATR against typical values."""
    # Try to extract numeric range from typical_atr
    typical = context.get("typical_atr_h1", {}).get("value", "")
    volatility = context.get("volatility_context", {})

    assessment = {
        "current_atr": current_atr,
        "assessment": "unknown",
        "message": "",
    }

    # Simple heuristic based on known symbols
    symbol = context.get("symbol", "")

    if "BTCUSD" in symbol:
        if current_atr < 200:
            assessment["assessment"] = "compressed"
            assessment["message"] = (
                f"ATR {current_atr:.0f} is below normal (300-500). Market is compressing — breakout likely."
            )
        elif current_atr > 600:
            assessment["assessment"] = "elevated"
            assessment["message"] = (
                f"ATR {current_atr:.0f} is above normal (300-500). High volatility — expect larger swings, use wider SL."
            )
        else:
            assessment["assessment"] = "normal"
            assessment["message"] = (
                f"ATR {current_atr:.0f} is within normal range (300-500). Standard SL/TP distances apply."
            )
    elif "XAUUSD" in symbol:
        if current_atr < 15:
            assessment["assessment"] = "compressed"
            assessment["message"] = (
                f"ATR {current_atr:.0f} is below normal (20-40). Low volatility."
            )
        elif current_atr > 50:
            assessment["assessment"] = "elevated"
            assessment["message"] = (
                f"ATR {current_atr:.0f} is above normal (20-40). High volatility — widen your stops."
            )
        else:
            assessment["assessment"] = "normal"
            assessment["message"] = (
                f"ATR {current_atr:.0f} is within normal range (20-40)."
            )
    elif "EURUSD" in symbol:
        if current_atr < 5:
            assessment["assessment"] = "compressed"
            assessment["message"] = (
                f"ATR {current_atr:.0f} is below normal (8-15). Very quiet market."
            )
        elif current_atr > 20:
            assessment["assessment"] = "elevated"
            assessment["message"] = (
                f"ATR {current_atr:.0f} is above normal (8-15). Elevated volatility — possibly news-driven."
            )
        else:
            assessment["assessment"] = "normal"
            assessment["message"] = (
                f"ATR {current_atr:.0f} is within normal range (8-15)."
            )
    else:
        assessment["assessment"] = "unknown"
        assessment["message"] = (
            f"ATR {current_atr:.0f} — no baseline available for comparison. Use ATR-based SL (1x ATR minimum)."
        )

    return assessment


def _build_comparison_table() -> list[dict]:
    """Build a comparison table across known symbols."""
    comparison = []
    for sym, ctx in SYMBOL_CONTEXTS.items():
        point_info = ctx.get("point_value", {}).get("per_0_01_lot", "N/A")
        atr = ctx.get("typical_atr_h1", {}).get("value", "N/A")
        daily = ctx.get("volatility_context", {}).get("normal_daily_range", "N/A")
        comparison.append(
            {
                "symbol": sym,
                "point_value_0_01": point_info,
                "typical_atr_h1": atr,
                "normal_daily_range": daily,
            }
        )
    return comparison
