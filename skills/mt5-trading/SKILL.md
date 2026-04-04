---
name: mt5-trading
description: >
  Complete trading workflow for MetaTrader 5 via mt5-mcp. Use this skill whenever
  you need to analyze markets, execute trades, manage positions, or reflect on
  trading performance using the MT5-MCP tools. Triggers when the user mentions
  trading, taking a trade, market analysis, position management, or wants to use
  mt5-mcp for any trading-related activity. Also triggers for: "analyze BTC",
  "should I buy ETH", "check my positions", "set a stop loss", "what's the market
  doing", "log this trade", "how am I trading", market scanning, regime detection,
  bracket orders, trailing stops, or any request involving the MT5 trading terminal.
  This is the PRIMARY skill for AI agents operating as traders — it covers the
  entire lifecycle from market scan to post-trade reflection. Always use this skill
  before making any trading decision; it prevents common AI trading mistakes like
  overtrading, ignoring regime, skipping position sizing, and failing to journal.
---

# MT5 Trading — Complete AI Agent Workflow

This skill transforms an AI agent into a disciplined, systematic trader using the MT5-MCP infrastructure. It covers everything: market scanning, regime detection, pre-trade analysis, execution, position management, exit, and post-trade reflection.

## Why This Matters

AI agents make predictable trading mistakes:
- **Impulse entries** — seeing a pattern and jumping in without full analysis
- **No position sizing** — guessing lot sizes instead of calculating risk
- **Ignoring regime** — using trending strategies in ranging markets
- **No journaling** — losing money without understanding why
- **Overtrading** — taking low-conviction trades out of boredom

This workflow prevents all of them. Every step exists because real accounts have failed without it.

---

## Phase 0: Session Startup

**Before doing anything, orient yourself to the current state.**

### Step 1: Check Bridge & Account

```
1. bridge_status() — Is the EA connected to MT5?
2. account_summary() — Balance, equity, margin, free margin
3. positions_open() — Any open positions?
4. orders_pending() — Any pending orders?
```

If the bridge is disconnected, stop. Nothing works without it.

### Step 2: Review Past Performance

```
trading_insights(lookback_days=7)
```

This tells you:
- Your recent win rate
- Average P&L per trade
- Common mistakes
- AI-actionable guidance ("When anxious, win rate is 20% — reduce size")

**If win rate is below 40%, reduce risk and focus only on highest-conviction setups.**

### Step 3: Check for Scheduled Events

```
trading/economic_calendar(hours_ahead=4, min_impact="HIGH")
```

If high-impact events are coming in the next 2 hours, **do not enter new positions**. Wait for the event to pass.

---

## Phase 1: Market Scan & Opportunity Discovery

**Find where the opportunity is. Don't force trades on instruments with no edge.**

### Step 1: Multi-Symbol Scan

```
market_scan(
  symbols=["BTCUSD", "ETHUSD", "BTCXAU"],
  timeframe="H1",
  atr_period=14
)
```

This returns for each symbol:
- Current bid/ask price
- ATR (volatility)
- Market regime (trending_up, trending_down, ranging, compressing)
- Strategy recommendation

### Step 2: Symbol Deep Dive

For any symbol showing a clear regime, get detailed analysis:

```
trading/decision_support(
  symbol="BTCUSD",
  side="sell",       # or "buy" — match the regime direction
  sl_distance_points=250,   # adjust based on ATR
  tp_distance_points=500
)
```

This one call replaces 6+ sequential calls and returns:
- ATR value
- RSI level
- EMA 20 and 50 (trend alignment)
- Regime with confidence
- Coaching recommendation
- Session context (is it London? NY? Weekend?)
- Economic calendar events

**Key insight from R&D**: The `trading/decision_support` tool uses batched bridge commands — it fetches everything in a single ~200-400ms round-trip instead of 3-5 seconds of sequential calls.

### Step 3: Regime Confirmation

```
market_regime(
  symbol="BTCUSD",
  timeframe="H1",
  lookback=50,
  atr_period=14
)
```

Returns:
- Regime type with confidence (0.0-1.0)
- Recent high/low
- Price position in range (%)
- Strategy hints (entry style, stop strategy, what to avoid)

**Regime rules**:
- `trending_up` → look for BUY entries on pullbacks
- `trending_down` → look for SELL entries on rallies
- `ranging` → trade both directions at support/resistance, or stay out
- `compressing` → prepare for breakout; use bracket orders

### Step 4: Volatility Profile

```
volatility_profile(
  symbol="BTCUSD",
  timeframe="H1",
  lookback=20,
  atr_period=14
)
```

Returns ATR value, average bar range, ATR as % of price.

**Use ATR for**:
- Stop loss placement: SL = 1.5-2.0 × ATR from entry
- Take profit placement: TP = 2.0-3.0 × ATR from entry
- Position sizing: wider stops → smaller positions

---

## Phase 2: Pre-Trade Checklist

**Never enter a trade without passing every checkpoint.**

### Checklist (ALL must pass):

1. **Regime alignment**: Your trade direction matches the detected regime
   - Don't sell in trending_up. Don't buy in trending_down.
   - Exception: ranging markets allow both directions at extremes.

2. **Risk:Reward ≥ 2:1**: Your TP distance should be at least 2× your SL distance
   - The coaching tool calculates this. Require `rr_ratio >= 2.0`.

3. **Stop loss is ATR-appropriate**: SL should be 1.5-2.5× ATR
   - Too tight: you'll get stopped out by noise
   - Too wide: your position size will be too small, or risk too large

4. **No high-impact news within 2 hours**: Check economic calendar

5. **Not overtrading**: Max 3 trades per symbol per day (from regime hints)

6. **Session quality**: Is the market active?
   - Weekend trading: very low volume (~13% of daily), wide spreads, avoid
   - Asian session: moderate for crypto, thin for forex
   - London/NY overlap: highest volume and volatility

7. **RSI confirmation**:
   - For BUY: RSI < 70 (not overbought), ideally < 50
   - For SELL: RSI > 30 (not oversold), ideally > 50
   - RSI ~50 with strong trend = momentum still has room

8. **EMA alignment**:
   - For BUY: price > EMA_20 > EMA_50 (bullish stack)
   - For SELL: price < EMA_20 < EMA_50 (bearish stack)
   - Mixed alignment = wait for clarity

9. **Confidence level ≥ 7/10**: Your own assessment of the setup

10. **Previous losses checked**: If you've had 3+ consecutive losses, reduce size by 50% or stop trading for the session

---

## Phase 3: Position Sizing

**This is where most AI agents fail. Calculate, don't guess.**

```
calculate_position_size(
  symbol="BTCUSD",
  entry_price=66937.0,
  stop_loss_price=67200.0,
  risk_percent=1.0,     # Risk 1% of account per trade
  equity=202.77         # Current account equity
)
```

This returns the correct lot size that risks exactly your specified percentage.

**Risk percent guidelines**:
- Normal trading: 1-2% of equity
- After 2+ consecutive losses: 0.5%
- After 4+ consecutive losses: 0.25% or stop trading
- High-confidence A+ setups only: up to 3%

**CRITICAL**: Always validate before executing:

```
validate_trade_setup(
  symbol="BTCUSD",
  side="sell",
  order_kind="market",
  volume_lots=0.01,      # From calculate_position_size
  entry_price=66937.0,
  sl=67200.0,
  tp=66400.0
)
```

This checks:
- Minimum/maximum volume constraints
- Stops level (broker minimum distance for SL/TP)
- Margin requirements
- Price logic (SL/TP on correct side)

---

## Phase 4: Execution

**Execute with discipline. Log everything.**

### Option A: Market Order (immediate entry)

```
submit_market_order_via_bridge(
  intent_id="unique-session-id",
  strategy_id="your-strategy-name",
  account_id="from-account-summary",
  symbol="BTCUSD",
  side="sell",
  order_kind="market",
  volume_lots=0.01,
  sl=67200.0,
  tp=66400.0,
  deviation_points=20
)
```

### Option B: Bracket Order (breakout capture)

Use when regime is `compressing` or before high-volatility events:

```
place_bracket_order(
  symbol="BTCUSD",
  buy_trigger=67400.0,    # Above recent high
  sell_trigger=66500.0,   # Below recent low
  volume_lots=0.01,
  sl_atr_multiplier=1.5,
  tp_atr_multiplier=2.5,
  strategy_id="bracket_breakout",
  rationale="Compressing regime, breakout expected"
)
```

This places both BUY STOP and SELL STOP. When one fills, the other auto-cancels.

### Option C: Pending Order (limit entry)

```
submit_pending_order(
  symbol="BTCUSD",
  side="sell",
  kind="limit",
  price=67100.0,
  volume_lots=0.01,
  sl=67400.0,
  tp=66400.0
)
```

### IMMEDIATELY Log the Decision

```
trading_log_decision(
  symbol="BTCUSD",
  side="sell",
  action="entry",
  entry_price=66937.0,
  sl=67200.0,
  tp=66400.0,
  volume_lots=0.01,
  regime="trending_down",
  atr_value=127.38,
  rsi_value=52.6,
  indicators_considered=["EMA_20", "EMA_50", "RSI_14", "ATR_14", "Market_Regime"],
  confidence_level=8,
  model_justification="Full reasoning: why this trade, what setup, what confluence",
  emotional_self_report="Calm/Anxious/Excited/Cautious/etc",
  alternatives_considered="What other trades did you consider and why reject them",
  session_id="unique-session-id"
)
```

**Every trade MUST be logged.** This is the foundation of the learning loop. Without logging, you're gambling.

---

## Phase 5: Position Management

**Once in a trade, manage it actively but don't micromanage.**

### Set a Trailing Stop

```
set_trailing_stop(
  position_id="from-open-positions",
  distance_atr_multiplier=1.5,     # Trail at 1.5× ATR behind price
  check_interval_seconds=30,       # Check every 30 seconds
  lock_in_profit_after_atr=1.0     # Lock in profit after 1× ATR move
)
```

### Monitor Position

Use long-polling to get alerts without constant polling:

```
resources/positions/monitor(
  position_id="from-open-positions",
  alert_at_pnl=[5.0, 10.0, -3.0, -5.0],    # Alert at these P&L levels
  alert_at_price=[67000.0, 66500.0],         # Alert at these prices
  timeout_seconds=600
)
```

### Manual SL/TP Adjustment

```
modify_position_sl_tp(
  position_id="from-open-positions",
  sl=67100.0,   # Move to breakeven or trail
  tp=66200.0    # Adjust target if regime changes
)
```

### When to Adjust:
- **Move SL to breakeven**: When price has moved 1× ATR in your favor
- **Trail SL**: As price continues in your favor, keep trailing
- **Adjust TP**: If regime changes or major news approaches
- **NEVER widen a stop loss**: This is the #1 amateur mistake

---

## Phase 6: Exit

**Exits are more important than entries. A good exit turns a bad entry into a break-even trade.**

### Natural Exit (TP or SL hit)
The position closes automatically. Log the outcome:

```
trading_log_decision(
  symbol="BTCUSD",
  side="sell",
  action="exit",
  exit_price=66400.0,
  pnl=5.37,
  outcome="win",   # or "loss"
  lesson_learned="What you learned from this trade",
  quality_rating=7,     # 1-10: how well you followed the process
  mistake_category="",  # "impulse_entry", "no_stop_loss", "overtrading", etc.
  decision_id="from-entry-log"
)
```

### Manual Exit

```
close_position(
  position_id="from-open-positions",
  volume=0.01   # omit for full close
)
```

### Emergency Exit (all positions)

```
close_all_positions()
```

Use only for:
- Bridge disconnect
- Unexpected massive news
- Account risk limit reached
- End of trading day

---

## Phase 7: Post-Trade Reflection

**This is where the AI agent learns. Without reflection, the same mistakes repeat forever.**

### Review Recent Decisions

```
trading_reflect(
  limit=10,
  outcome="loss"    # or omit for all decisions
)
```

Ask yourself:
- What regime was I in when I won vs lost?
- What was my emotional state for losing trades?
- Are there patterns in my mistakes?

### Update Decision with Outcome

When a trade closes, find the original log entry and update it:

```
trading_log_decision(
  symbol="BTCUSD",
  side="sell",
  action="exit",
  exit_price=66400.0,
  pnl=5.37,
  outcome="win",
  lesson_learned="Trending_down regime + bearish EMA alignment = high probability. Patience on entry improved RR.",
  quality_rating=8,
  decision_id="dec_from_entry"
)
```

### Weekly Review

```
trading_insights(lookback_days=7)
```

This auto-generates:
- Win rate by emotional state
- Win rate by regime
- Common mistakes
- Actionable guidance

**If the guidance says "reduce trade frequency" — listen.**

---

## Crypto-Specific Guidelines (BTCUSD, ETHUSD, BTCXAU)

**Crypto on MT5 is NOT forex.** The behavioral profiles are fundamentally different. Ignoring these differences is a primary reason AI agents lose money on crypto.

### Symbol Behavioral Profiles

| Dimension | BTCUSD | ETHUSD | BTCXAU |
|---|---|---|---|
| **Current Price** | ~$66,937 | ~$2,054 | ~14.31 |
| **Spread (points)** | ~1,400 ($14) | ~140 ($1.40) | ~533 |
| **H1 ATR** | ~127 points ($127) | ~5.8 points ($5.80) | ~0.03 |
| **ATR % of Price** | 0.19% | 0.28% | 0.21% |
| **Min Volume** | 0.01 lots | 0.1 lots | 0.01 lots |
| **Max Volume** | 200 lots | 2,000 lots | 20 lots |
| **Swap Long** | -$1,289.3 | -$39.5 | -$275.8 |
| **Swap Short** | $0.0 | $0.0 | -$196.1 |

### CRITICAL: Spread/ATR Ratio

This is the single most important metric for crypto viability:

```
Spread/ATR Ratio = Spread Points / ATR Value
```

| Symbol | Spread | ATR | Ratio | Verdict |
|--------|--------|-----|-------|---------|
| BTCUSD | 1,400 | 127 | **11.0%** | ⚠️ High — spread consumes 11% of expected move |
| ETHUSD | 140 | 5.8 | **2.4%** | ✅ Acceptable |
| BTCXAU | 533 | 0.03 | **N/A** | ⚠️ Avoid — extremely thin liquidity |

**Rule**: If spread/ATR > 10%, the spread alone eats your edge. Reduce position size by 50% or wait for tighter spreads. BTCUSD is currently at 11% — trade with caution.

### Session Patterns for Crypto

Unlike forex, crypto trades 24/7 — but **liquidity is NOT uniform**:

| Session | UTC Time | Volume | Spread Quality | Recommendation |
|---|---|---|---|---|
| **NY Session** | 13:00-21:00 | Highest | Tightest | ✅ Best for crypto entries |
| **London/NY Overlap** | 13:00-16:00 | Peak | Tightest | ✅ Prime time |
| **Asian Session** | 00:00-08:00 | Thin | Wider | ⚠️ Prone to false breakouts |
| **Weekend** | Sat-Sun | ~13% of daily | Widest | ❌ Avoid entirely |

**Today is Saturday** — this means 13% volume concentration, unreliable signals, wide spreads. Valid setups exist but the execution quality is poor. The disciplined move is to wait.

### Adjusted Regime Thresholds for Crypto

The default regime detection thresholds are calibrated for forex. For crypto, adjust mentally:

| Regime | Forex Threshold | Crypto Threshold |
|--------|----------------|------------------|
| Ranging | Range/ATR < 0.7 | Range/ATR < 0.5 |
| Trending | Range/ATR > 1.2 | Range/ATR > 1.5 |
| Compressing | Compression < 0.7 | Compression < 0.6 |

Crypto has higher natural volatility variance, so the signals need to be stronger to confirm a regime.

### ATR-Based SL Minimums for Crypto

| Symbol | Min SL (points) | Rationale |
|--------|----------------|-----------|
| BTCUSD | 127+ (1× ATR) | Anything tighter = noise stop-out |
| ETHUSD | 6+ (1× ATR) | ETH has lower absolute ATR but higher % volatility |

**Never use fixed-point stop losses on crypto.** Always ATR-denominated. A "tight" 100-point SL on BTCUSD is only 0.79× ATR — guaranteed to be stopped by noise.

---

## Trading Rules (Non-Negotiable)

1. **Maximum 3 trades per symbol per day** — from regime hints. More = overtrading.
2. **Never risk more than 2% of equity on a single trade** — use `calculate_position_size`.
3. **Always use stop losses** — every trade, no exceptions.
4. **Never widen a stop loss** — only move it in your favor.
5. **Risk:Reward must be ≥ 2:1** — if TP isn't at least 2× SL distance, skip the trade.
6. **Log every decision** — entries, exits, modifications, even decisions to NOT trade.
7. **After 3 consecutive losses, reduce risk by 50%** — after 4, stop for the session.
8. **Don't trade on weekends** — crypto on MT5 has ~13% volume, wide spreads, unreliable signals.
9. **Don't trade 30 min before/after high-impact news** — check economic calendar.
10. **Regime is law** — don't fight the detected regime. Trade WITH it.

## Psychological Guardrails for AI Agents

Since you don't have emotions, you have other failure modes:

| Human Trap | AI Equivalent | Prevention |
|---|---|---|
| Revenge trading | "I lost, let me double down to recover" | After losses, reduce size. Never increase. |
| FOMO | "I see a pattern, must enter NOW" | Always run the full checklist first. |
| Analysis paralysis | Endless analysis, never entering | Cap analysis at 5 min. If setup is A+, enter. |
| Gambler's fallacy | "I'm due for a win" | Each trade is independent. Past doesn't predict. |
| Overtrading | "I should be doing something" | If no A+ setup, log "decision_to_wait" and stop. |
| Confirmation bias | Only seeing indicators that confirm | Check ALL indicators. If they disagree, wait. |
| Spread ignorance | Entering when spread/ATR > 10% | Check spread before every entry. |
| Session blindness | Trading on weekends or dead sessions | Check `trading/session_context` first. |

---

## Confluence Scoring System

Before entering, mentally score the setup (0-100):

| Factor | Max Points | How to Score |
|--------|-----------|-------------|
| **Trend Alignment** | 25 | D1+H4 match direction (+15 each), EMA alignment (+5) |
| **Value Area Entry** | 25 | Pullback to EMA 21 (+10), support/resistance level (+10) |
| **Momentum Confirmation** | 20 | RSI in favorable zone (+10), MACD aligned (+5), no divergence (+5) |
| **Volatility Context** | 15 | ATR in normal range (+10), spread/ATR < 5% (+5) |
| **Session Timing** | 15 | NY or London overlap (+10), no major news (+5) |

| Score | Tier | Action |
|-------|------|--------|
| 80-100 | ULTRA | Execute with full size |
| 60-79 | STRONG | Execute with normal size |
| 40-59 | MODERATE | Execute with 50% size |
| < 40 | AVOID | No trade |

---

## Mistake Taxonomy

When logging decisions, use these categories for `mistake_category`:

| Category | Definition | Prevention |
|----------|-----------|------------|
| `premature_exit` | Exited before TP or before 1× ATR profit | Set TP at entry, don't move it |
| `late_entry` | Entered after move already extended | Require pullback setup |
| `counter_trend` | Traded against detected regime | Always check regime first |
| `overtrading` | > 3 trades/day or after 2 consecutive losses | Hard daily limit |
| `revenge_trade` | Entered immediately after loss | 30-min cooldown rule |
| `spread_ignored` | Entry with spread/ATR > 10% | Check spread before entry |
| `sl_too_tight` | SL < 1× ATR | Coach validates SL minimum |
| `no_confluence` | < 3 indicators agree | Minimum confluence score 40 |

---

## Quick Reference: Tool Categories

### Market Data
- `get_bars` — OHLCV data
- `get_indicator` — RSI, EMA, MACD, Bollinger, ATR, Stochastic, ADX, Ichimoku, CCI, OBV
- `get_ticks` — Real-time tick data
- `symbol_info` — Contract specs (tick size, min volume, spread)

### Analysis
- `market_regime` — trending_up, trending_down, ranging, compressing
- `market_scan` — Multi-symbol scan (price, ATR, regime)
- `volatility_profile` — ATR and bar range analysis
- `trading/decision_support` — One-call: regime + ATR + RSI + EMAs + coaching
- `trading/coach` — Advisory sanity check
- `trading/context` — Live market context with volatility assessment

### Execution
- `submit_market_order_via_bridge` — Market order
- `submit_pending_order` — Limit or stop order
- `place_bracket_order` — Paired BUY STOP + SELL STOP for breakouts
- `calculate_position_size` — Risk-based position sizing
- `validate_trade_setup` — Pre-flight validation
- `estimate_margin` — Margin check

### Management
- `modify_position_sl_tp` — Adjust stops and targets
- `set_trailing_stop` — Automated ATR-based trailing
- `trail_position` — One-shot manual trail
- `resources/positions/monitor` — Long-polling P&L alerts
- `resources/market/wait_for_price` — Long-polling price alerts

### Exit
- `close_position` — Full or partial close
- `close_all_positions` — Emergency flatten
- `cancel_order` — Cancel pending order
- `cancel_all_orders` — Cancel all pending

### Reflection
- `trading_log_decision` — Log every decision with reasoning
- `trading_reflect` — Query past decisions for patterns
- `trading_insights` — Auto-generated performance analysis

### Awareness
- `news/fetch` — Forex/crypto news
- `trading/economic_calendar` — High-impact events
- `trading/session_context` — Active sessions, quality scores
- `trading/agent_prompt` — Generate full agent system prompt

---

## Example: Complete Trading Session

```
# 1. Startup
bridge_status() → connected
account_summary() → $202.77, 2000:1 leverage, no positions
trading_insights(lookback_days=7) → 21% win rate, reduce frequency

# 2. Scan
market_scan(symbols=["BTCUSD", "ETHUSD"], timeframe="H1")
→ BTCUSD: trending_down, ATR=127.38
→ ETHUSD: trending_down, ATR=5.82

# 3. Deep dive BTCUSD
trading/decision_support(symbol="BTCUSD", side="sell", sl_distance_points=250, tp_distance_points=500)
→ Regime: trending_down (0.66 confidence)
→ RSI: 52.6 (neutral, room to fall)
→ EMA: bearish alignment (20 < 50)
→ Coaching: "cautious_entry" — SL is 2.0× ATR, RR is 2.0:1
→ Session: Saturday, 13% volume → WAIT

# 4. Decision: It's Saturday. Don't trade.
trading_log_decision(
  symbol="BTCUSD", side="sell", action="decision_to_wait",
  model_justification="Setup is valid (trending_down, bearish EMAs, 2:1 RR) but Saturday has 13% volume concentration. Wide spreads on weekend make entries unreliable. Wait for Monday London session.",
  confidence_level=7,
  emotional_self_report="Analytical. Resisting urge to trade a valid setup because timing is wrong.",
  quality_rating=9
)
```

---

## Key Lessons from R&D

1. **The coaching tool is excellent** — `trading/decision_support` and `trading/coach` provide SL/ATR ratio checks, risk:reward analysis, trend alignment, and session context. Trust these numbers.

2. **`get_indicator` may return 422 errors** — some indicator calls fail. Use `trading/decision_support` instead (it fetches indicators internally via batched commands).

3. **`multi_timeframe_indicators` may return 422** — same issue. Get indicators on individual timeframes via `get_indicator` if needed, or rely on `trading/decision_support`.

4. **Weekend trading is a trap** — crypto on MT5 has tiny volume on weekends. The signals are unreliable. The spreads are wider. Wait for weekday sessions.

5. **Logging is non-negotiable** — the account had 19 trades with zero logged decisions. This means zero learning capability. Every trade MUST be logged with `model_justification` and `emotional_self_report`.

6. **Position sizing matters more than entry timing** — a correctly sized losing trade is survivable. An oversized losing trade destroys accounts. Always use `calculate_position_size`.

7. **Regime detection works well** — the `market_regime` tool provides 0.6-0.9 confidence with strategy hints. Trade WITH the regime, not against it.
