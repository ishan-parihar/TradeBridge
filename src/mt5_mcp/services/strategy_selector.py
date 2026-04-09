"""Strategy selector — regime-based strategy recommendation.

Recommends the optimal strategy type given current market conditions,
pairing regime classification with entry style, risk parameters, and
exit methodology.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StrategyConfig:
    name: str
    regime: str
    entry_style: str
    stop_type: str
    take_profit_type: str
    max_positions: int
    risk_multiplier: float
    trailing: bool
    description: str


_STRATEGY_REGISTRY: dict[str, StrategyConfig] = {
    "pullback_trend": StrategyConfig(
        name="Pullback Trend",
        regime="trending_up",
        entry_style="limit_at_ema",
        stop_type="atr_below_swing",
        take_profit_type="next_resistance",
        max_positions=3,
        risk_multiplier=1.0,
        trailing=True,
        description="Enter on pullbacks to EMA20/EMA50 in established trends. Trail stops.",
    ),
    "pullback_trend_down": StrategyConfig(
        name="Pullback Short",
        regime="trending_down",
        entry_style="limit_at_ema",
        stop_type="atr_above_swing",
        take_profit_type="next_support",
        max_positions=3,
        risk_multiplier=1.0,
        trailing=True,
        description="Short on rallies to EMA20/EMA50 in downtrends. Trail stops.",
    ),
    "bracket_range": StrategyConfig(
        name="Bracket Range",
        regime="ranging",
        entry_style="bracket_orders",
        stop_type="wide_1x_atr",
        take_profit_type="opposite_boundary",
        max_positions=2,
        risk_multiplier=0.75,
        trailing=False,
        description="Place bracket orders at range boundaries. Wide stops, take profit at opposite side.",
    ),
    "breakout_compress": StrategyConfig(
        name="Volatility Breakout",
        regime="compressing",
        entry_style="stop_order_breakout",
        stop_type="inside_range",
        take_profit_type="atr_projection",
        max_positions=2,
        risk_multiplier=1.0,
        trailing=True,
        description="Enter on breakout from compression. Stop inside range. Trail on expansion.",
    ),
    "momentum_continuation": StrategyConfig(
        name="Momentum Continuation",
        regime="momentum_push",
        entry_style="market_on_pullback",
        stop_type="tight_0.5x_atr",
        take_profit_type="trailing",
        max_positions=2,
        risk_multiplier=1.25,
        trailing=True,
        description="Ride strong momentum. Tight stops, aggressive trailing. Scale in on continuation.",
    ),
    "mean_reversion_fade": StrategyConfig(
        name="Mean Reversion Fade",
        regime="mean_reversion",
        entry_style="limit_at_extreme",
        stop_type="beyond_band",
        take_profit_type="mean_reversion",
        max_positions=1,
        risk_multiplier=0.5,
        trailing=False,
        description="Fade RSI extremes in ranging market. Enter at Bollinger Band touch. Target mean.",
    ),
    "wide_volatility": StrategyConfig(
        name="Wide Volatility",
        regime="volatile_expansion",
        entry_style="pending_at_key_level",
        stop_type="very_wide_2x_atr",
        take_profit_type="wide_target",
        max_positions=1,
        risk_multiplier=0.5,
        trailing=False,
        description="Reduced size, wide stops. Only enter at major S/R. Avoid chop.",
    ),
    "patience_consolidation": StrategyConfig(
        name="Patience Consolidation",
        regime="low_volatility_consolidation",
        entry_style="wait_for_trigger",
        stop_type="standard_1x_atr",
        take_profit_type="standard_2x_atr",
        max_positions=1,
        risk_multiplier=0.5,
        trailing=False,
        description="Wait for volatility expansion. Do not trade in tight range.",
    ),
}


def select_strategy(regime: str) -> StrategyConfig:
    """Select the optimal strategy for the given regime."""
    mapping = {
        "trending_up": "pullback_trend",
        "trending_down": "pullback_trend_down",
        "ranging": "bracket_range",
        "compressing": "breakout_compress",
        "momentum_push": "momentum_continuation",
        "mean_reversion": "mean_reversion_fade",
        "volatile_expansion": "wide_volatility",
        "low_volatility_consolidation": "patience_consolidation",
    }
    key = mapping.get(regime, "bracket_range")
    return _STRATEGY_REGISTRY[key]


def list_strategies() -> list[dict]:
    """Return all available strategies as dicts."""
    return [
        {
            "name": s.name,
            "regime": s.regime,
            "entry_style": s.entry_style,
            "stop_type": s.stop_type,
            "take_profit_type": s.take_profit_type,
            "max_positions": s.max_positions,
            "risk_multiplier": s.risk_multiplier,
            "trailing": s.trailing,
            "description": s.description,
        }
        for s in _STRATEGY_REGISTRY.values()
    ]
