from __future__ import annotations

from pydantic import BaseModel
from typing import Literal, Optional


class OwnershipMixin(BaseModel):
    session_id: Optional[str] = None
    strategy_id: Optional[str] = None
    intent_id: Optional[str] = None
    idempotency_key: Optional[str] = None


class BarsRequest(BaseModel):
    symbol: str
    timeframe: str
    count: int = 100


class IndicatorRequest(BaseModel):
    symbol: str
    timeframe: str
    indicator: Literal[
        "sma",
        "ema",
        "wma",
        "smma",
        "rsi",
        "macd",
        "bbands",
        "stoch",
        "atr",
        "adx",
        "dmi",
        "ichimoku",
        "obv",
        "cci",
    ]
    # Common period
    period: int | None = None
    # MACD
    fast: int | None = None
    slow: int | None = None
    signal: int | None = None
    # BBands
    deviation: float | None = None
    shift: int | None = None
    # Stochastic
    k_period: int | None = None
    d_period: int | None = None
    slowing: int | None = None
    # Ichimoku
    tenkan: int | None = None
    kijun: int | None = None
    senkou: int | None = None
    # Series window
    window: int | None = None


class ChartScreenshotRequest(BaseModel):
    symbol: str
    timeframe: str
    width: int = 1280
    height: int = 720


class ChartScreenshotResult(BaseModel):
    image_base64: str
    content_type: str = "image/png"


class AccountSummaryResult(BaseModel):
    account_id: str | None = None
    name: str | None = None
    balance: float | None = None
    equity: float | None = None
    margin: float | None = None
    free_margin: float | None = None
    currency: str | None = None


class ModifyPositionSLTPRequest(OwnershipMixin):
    position_id: str
    sl: float | None = None
    tp: float | None = None


class ClosePositionRequest(OwnershipMixin):
    position_id: str
    volume: float | None = None


class SubmitPendingOrderRequest(OwnershipMixin):
    symbol: str
    side: Literal["buy", "sell"]
    kind: Literal["limit", "stop"]
    price: float
    volume_lots: float
    sl: float | None = None
    tp: float | None = None
    deviation: int = 20


class CancelOrderRequest(OwnershipMixin):
    order_id: str


class ModifyOrderRequest(OwnershipMixin):
    order_id: str
    new_price: float | None = None
    new_sl: float | None = None
    new_tp: float | None = None


class CloseAllPositionsRequest(OwnershipMixin):
    symbol: str | None = None
    side: Literal["buy", "sell", "both"] = "both"


class CancelAllOrdersRequest(OwnershipMixin):
    symbol: str | None = None
    side: Literal["buy", "sell", "both"] = "both"


class TicksRequest(BaseModel):
    symbol: str
    count: int = 200


class OrderBookRequest(BaseModel):
    symbol: str


class SymbolInfoRequest(BaseModel):
    symbol: str


class DealsHistoryRequest(BaseModel):
    symbol: str | None = None
    limit: int = 100
    days: int = 30


class PositionSizeRequest(BaseModel):
    symbol: str
    entry_price: float
    stop_loss_price: float
    risk_percent: float
    equity: float | None = None


class ValidateTradeSetupRequest(BaseModel):
    symbol: str
    side: Literal["buy", "sell"]
    order_kind: Literal["market", "limit", "stop"] = "market"
    volume_lots: float
    entry_price: float | None = None
    sl: float | None = None
    tp: float | None = None


class TrailPositionRequest(BaseModel):
    position_id: str
    distance_points: int
    lock_in_points: int = 0


class VolatilityProfileRequest(BaseModel):
    symbol: str
    timeframe: str
    lookback: int = 20
    atr_period: int = 14


class MultiTimeframeIndicatorRequest(BaseModel):
    symbol: str
    indicator: Literal[
        "sma",
        "ema",
        "wma",
        "smma",
        "rsi",
        "macd",
        "bbands",
        "stoch",
        "atr",
        "adx",
        "dmi",
        "ichimoku",
        "obv",
        "cci",
    ]
    timeframes: list[str]
    period: int | None = None
    fast: int | None = None
    slow: int | None = None
    signal: int | None = None
    deviation: float | None = None
    shift: int | None = None
    k_period: int | None = None
    d_period: int | None = None
    slowing: int | None = None
    tenkan: int | None = None
    kijun: int | None = None
    senkou: int | None = None
    window: int | None = None


class CorrelationMatrixRequest(BaseModel):
    symbols: list[str]
    timeframe: str
    lookback: int = 50


# --- Phase 3: Bracket Orders ---


class BracketOrderRequest(OwnershipMixin):
    """Place paired BUY STOP + SELL STOP for breakout capture.

    When one fills, the other is auto-cancelled.
    Audit finding: bracket orders were the best strategy (+$3.70).
    """

    symbol: str
    buy_trigger: float  # BUY STOP price (above current)
    sell_trigger: float  # SELL STOP price (below current)
    volume_lots: float
    sl_atr_multiplier: float = 1.0  # SL distance as ATR multiplier
    tp_atr_multiplier: float = 2.0  # TP distance as ATR multiplier
    rationale: str | None = None


class BracketOrderResult(BaseModel):
    status: str
    buy_order_id: str | None = None
    sell_order_id: str | None = None
    message: str | None = None


class SetTrailingStopRequest(OwnershipMixin):
    """Start server-side trailing stop for a position."""

    position_id: str
    distance_atr_multiplier: float = 1.0
    check_interval_seconds: int = 10
    lock_in_profit_after_atr: float = 1.0  # Lock in profit after this many ATR


class TrailingStopResult(BaseModel):
    position_id: str
    status: str  # "active", "stopped", "error"
    message: str = ""
    initial_sl: float | None = None


# --- Phase 4: Price Alert (Long-Polling) ---


class PriceAlertRequest(BaseModel):
    """Long-polling price alert. Holds connection until triggered."""

    symbol: str
    condition: Literal["above", "below", "crosses"]
    price: float
    timeout_seconds: int = 300


class PriceAlertResult(BaseModel):
    symbol: str
    condition: str
    trigger_price: float
    actual_price: float
    triggered: bool
    timed_out: bool = False


# --- Phase 4: Position Monitor (Long-Polling) ---


class PositionMonitorRequest(BaseModel):
    """Long-polling position monitor."""

    position_id: str
    alert_at_pnl: list[float] = []  # Alert at these P&L levels
    alert_at_price: list[float] = []  # Alert at these price levels
    timeout_seconds: int = 600


class PositionMonitorResult(BaseModel):
    position_id: str
    alert_type: str | None = None  # "pnl", "price", "timeout", "closed"
    current_pnl: float | None = None
    current_price: float | None = None
    triggered_value: float | None = None
    timed_out: bool = False


# --- Phase 2: Market Regime ---


class MarketRegimeRequest(BaseModel):
    symbol: str
    timeframe: str
    lookback: int = 20
    atr_period: int = 14


# --- Phase 2: Trading Policy ---


class TradingPolicyStatusRequest(BaseModel):
    equity: float | None = None


class TradingPolicyConfigRequest(BaseModel):
    max_trades_per_day: int | None = None
    max_loss_per_day_pct: float | None = None
    min_rest_between_trades_min: int | None = None
    require_indicator_confluence: bool | None = None
    max_position_size_pct: float | None = None
    cooldown_after_consecutive_losses: int | None = None


# --- Phase 2: Trade Journal ---


class TradeJournalQueryRequest(BaseModel):
    symbol: str | None = None
    side: str | None = None
    strategy: str | None = None
    exit_reason: str | None = None
    limit: int = 100


class MarketScanRequest(BaseModel):
    """Multi-symbol market scan in one call."""

    symbols: list[str]
    timeframe: str = "H1"
    atr_period: int = 14


# --- Metacognition & AI Reasoning ---


class TradeDecisionLogRequest(BaseModel):
    """Log a trading decision with full AI reasoning for metacognition."""

    symbol: str
    side: str
    action: str  # entry, exit, modify_sl, modify_tp, trail, close, monitor, decision_to_wait
    entry_price: float | None = None
    exit_price: float | None = None
    sl: float | None = None
    tp: float | None = None
    volume_lots: float | None = None
    pnl: float | None = None
    session_id: str | None = None

    # Market context (auto-fillable, but AI can override)
    regime: str | None = None
    atr_value: float | None = None
    atr_percent_of_price: float | None = None
    rsi_value: float | None = None
    indicator_snapshot: dict | None = None

    # AI reasoning (REQUIRED for metacognition)
    model_justification: str | None = None
    indicators_considered: list[str] | None = None
    confidence_level: float | None = None  # 0-1
    risk_assessment: str | None = None
    emotional_self_report: str | None = (
        None  # calm, cautious, aggressive, anxious, uncertain, confident
    )
    alternatives_considered: str | None = None
    expected_duration: str | None = None
    expected_move_points: float | None = None

    # Post-trade (filled on exit)
    outcome: str | None = None  # win, loss, breakeven, still_open
    lesson_learned: str | None = None
    would_do_differently: str | None = None
    mistake_category: str | None = (
        None  # premature_exit, late_entry, wrong_regime, ignored_signal, revenge_trade, overtrading, perfect_trade
    )
    quality_rating: int | None = None  # 1-5

    decision_id: str | None = None  # For updating existing decisions


class TradeJournalReflectionRequest(BaseModel):
    """Query journal for metacognitive reflection."""

    symbol: str | None = None
    outcome: str | None = None
    regime: str | None = None
    emotional_self_report: str | None = None
    mistake_category: str | None = None
    action: str | None = None
    limit: int = 50


class TradingContextRequest(BaseModel):
    """Get trading context/education for a symbol."""

    symbol: str
    include_comparison: bool = True


class TradingCoachRequest(BaseModel):
    """Get advisory coaching feedback for a potential trade."""

    symbol: str
    side: str
    regime: str | None = None
    atr_value: float | None = None
    rsi: float | None = None
    ema_fast: float | None = None
    ema_slow: float | None = None
    sl_distance_points: float | None = None
    tp_distance_points: float | None = None
    indicator_agreements: int | None = None
    trades_today: int = 0
    daily_pnl: float = 0.0
    recent_consecutive_losses: int = 0
    position_in_range: float | None = None  # 0-100


class AgentSystemPromptRequest(BaseModel):
    """Get the system prompt that orients a new trading agent."""

    include_market_context: bool = True
    include_news_context: bool = True
    include_workflow: bool = True
    include_trading_rules: bool = True
    include_tool_guide: bool = True
    include_metacognition: bool = True
    live_account_context: bool = False  # Fetch live account data and inject
    live_symbol_context: list[str] | None = None  # Symbols to inject context for
    include_recent_news: bool = False  # Fetch latest news and inject


class TradingDecisionSupportRequest(BaseModel):
    """One-call decision support: regime + ATR + RSI + EMAs + coaching."""

    symbol: str
    side: str
    sl_distance_points: float | None = None
    tp_distance_points: float | None = None


# ============================================================
# News & Session Awareness Requests
# ============================================================


class NewsFetchRequest(BaseModel):
    """Fetch forex-relevant news from RSS feeds."""

    pools: list[str] | None = (
        None  # ["FOREX_MAJOR", "CENTRAL_BANKS", "MACRO_ECONOMIC", "CRYPTO_FX", "GEOPOLITICAL_FX"]
    )
    currencies: list[str] | None = (
        None  # ["USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD"]
    )
    keywords: list[str] | None = None
    exclude_keywords: list[str] | None = None
    limit: int = 20
    hours_back: int = 6
    match_all: bool = False
    source_ids: list[str] | None = None
    enrich: bool = False  # Add sentiment/topics/entities


class EconomicCalendarRequest(BaseModel):
    """Get upcoming high-impact economic events."""

    hours_ahead: int = 24
    currency: str | None = None
    min_impact: str = "MEDIUM"  # "LOW", "MEDIUM", "HIGH", "CRITICAL"


class SessionContextRequest(BaseModel):
    """Get current forex trading session context."""

    symbol: str | None = None  # Optional: filter for specific pair
    include_all_pairs: bool = False  # Include session quality for all pairs
