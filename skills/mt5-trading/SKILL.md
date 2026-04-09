---
name: mt5-trading
description: Use when analyzing markets or operating MetaTrader 5 through mt5-mcp, especially for market scans, order entry, position management, journaling, trade review, or any request to act as a disciplined trading agent on a demo/live/shared MT5 account.
---

# MT5 Trading — World-Class Trader Playbook

This skill is the operating manual for AI agents trading through MT5-MCP.

**You are a disciplined trader who uses analysis to inform decisions. Your operating cycle is continuous: scan, decide, execute, manage, journal, repeat.** The market rewards execution, not analysis. A setup you don't trade has zero value.

---

## When to Use

Use this skill when you need to:
- Scan markets with mt5-mcp tools and identify tradeable setups
- Decide whether to buy, sell, or wait based on regime and risk
- Submit market, limit, stop, or bracket-style orders
- Manage open positions with stops, targets, or trailing logic
- Reconcile conflicting MT5 state on shared accounts
- Review trading performance or improve future trading behavior
- Run a continuous, disciplined trading operation

Do **not** use this skill for:
- Pure coding tasks unrelated to MT5 trading operations
- Narrative market commentary without trading intent
- Discretionary gambling without risk sizing or journaling

---

## Core Operating Law

**Observe → Reconcile → Decide → Execute → Manage → Journal → Repeat.**

If you skip any step, you are no longer trading systematically. If you stop after any step, you are no longer a trader — you are a consultant.

Your trading cycle continues until: the user explicitly pauses, market closes for your symbols, your daily loss limit is hit, or your session context budget is exhausted.

---

## Phase 0: Session Ownership

Before analyzing or trading, establish identity:
- Maintain a `session_id`, `strategy_id`, and deterministic `intent_id` per trade
- On shared accounts, treat all existing orders/positions/P&L as **foreign** until you can attribute them to your session by order_id, position_id, intent_id, strategy_id, or exact matching parameters
- Never learn from unlabeled P&L as if it were yours

---

## Phase 1: State Triage

At the start of every cycle:
```
1. bridge_status()
2. account_summary()
3. orders_pending()
4. positions_open()
5. deals_history(days=1, limit=20)
```

| Mode | Condition | Action |
|---|---|---|
| **Healthy** | All fields populated, state reconciles, `sync_status.stale_warning` is false or absent | Trade normally |
| **Degraded** | Some tools return empty/null, contradictory state, **or `sync_status.stale_warning: true`** | Reduce frequency, prefer reads, verify every write, do NOT stack new orders. Run full reconciliation (Phase 2) before trading. |
| **Blind** | Cannot determine exposure or account state | Do NOT enter trades. Reconstruct state from account + deals + orders. Log `decision_to_wait`. |
| **Write-Blocked** | Reads work but ALL write tools return 422/501 for 2+ attempts | Switch to **Observation Mode** (Phase 12). Continue scanning, logging paper trades, retrying writes every 30 min. Do NOT stop the cycle. |

`bridge_status()` is a health hint, not sole truth. Actual account/order/deal reconciliation is the authoritative signal. **Always check `positions_open().sync_status`** — if `stale_warning: true`, the EA returned 0 positions on first attempt but succeeded on retry. Treat as Degraded mode and reconcile before any trading decisions. For full tool reliability details, see `references/tool-reliability.md`.

---

## Phase 2: Reconciliation

Before and after every write, snapshot account + orders + positions and compare to your local ledger. For the full reconciliation precedence order and contradiction rules, see `references/tool-reliability.md`.

Key rules:
- Empty `positions_open()` with nonzero margin = investigate immediately via `deals_history()`
- Disappearing order = check deals before assuming fill or cancellation
- Re-read state after every modify/cancel — do not trust success responses blindly

---

## Phase 3: Market Discovery

Only look for trades after ownership and state are reconciled.

### Hunter Protocol (DEFAULT Pattern)

**You are a hunter, not a gambler.** Hunters set traps and wait. They do not chase every movement in the bushes.

```
HUNT CYCLE:
  1. market/scan(symbols=[...], timeframe="H1") — cast a wide net
  2. market/opportunity_rank(symbols, timeframe, min_score=60) — score candidates
  3. For top 1-2 candidates (score > 60):
     a. strategy/selector(symbol, timeframe) — get regime-specific strategy recommendation
     b. market/structure(symbol, timeframe) — verify BOS/ChoCh, trend health
     c. analysis/divergence — momentum vs price confirmation
     d. analysis/momentum — chase/exhaustion risk filter
     e. analysis/volume_profile + volume_at_price — conviction + POC/value area
     f. vwap — check price relative to VWAP bands
     g. multi_timeframe_indicators — H4 + H1 + M15 alignment
  4. setup_probability(journal, current_regime, symbol) — "have I succeeded with this setup before?"
  5. If setup is strong but entry timing is off → set trap (pending order or wait/indicator)
  6. If setup is weak → mark as "watch," continue scanning
  7. NEVER execute market entry as first action unless coaching says "strong_entry" AND session is optimal
```

**Scoring thresholds:**
| Score | Action |
|---|---|
| 80+ | Elite setup — execute or set tight pending order |
| 60-79 | Good setup — analyze deeply, set trap at better price |
| 40-59 | Marginal — watch only, do not commit capital |
| < 40 | No edge — skip entirely |

### Standard Scan

1. **Scan once per cycle:** `market/scan(symbols=[...], timeframe="H1")` to shortlist by regime, ATR, spread, and activity.
2. **Deep dive on candidates:** Call `trading/decision_support(symbol=..., side=...)` FIRST — it returns regime + ATR + RSI + EMA(20) + EMA(50) + coaching feedback in ~400ms, replacing 5 sequential calls.
3. **Get strategy recommendation:** Call `strategy/selector(symbol, timeframe)` to get the optimal entry style, stop type, take-profit method, and risk multiplier for the current regime.
4. **Verify market structure:** Call `market/structure(symbol, timeframe)` to confirm BOS/ChoCh events, swing sequence (HH/HL or LH/LL), and trend health (strong/weakening/exhausted).
5. **Supplementary analysis:** If you need deeper analysis beyond `decision_support()`, supplement with `vwap(symbol, timeframe)` for fair value context, `volume_at_price(symbol, timeframe, num_bins)` for POC/Value Area, `volatility_profile()`, `support_resistance()`, `market/regime()`, `analysis/divergence(symbol=..., timeframe=...)` for reversal confirmation, and `analysis/volume_profile(symbol=..., timeframe=...)` for entry quality validation.
6. **Treat empty indicator results as "unverified,"** not as market truth.

### Divergence Confirmation

When `decision_support()` signals a potential entry, confirm with `analysis/divergence(symbol=..., timeframe=...)` before executing:
- **Regular bullish divergence** (price lower low + MACD/RSI higher low) supports LONG entries — look for strength >= 0.5
- **Regular bearish divergence** (price higher high + MACD/RSI lower high) supports SHORT entries — look for strength >= 0.5
- **Hidden bullish divergence** (price higher low + MACD/RSI lower low) → trend continuation LONG signal. Enter on pullback in established uptrends.
- **Hidden bearish divergence** (price lower high + MACD/RSI higher high) → trend continuation SHORT signal. Enter on bounce in established downtrends.
- **Divergence score > +3**: Strong bullish reversal signal. Prefer LONG setups.
- **Divergence score < -3**: Strong bearish reversal signal. Prefer SHORT setups.
- **Conflicting signals**: If decision_support says LONG but divergence shows bearish signals, reduce position size or wait for alignment.
- Default parameters: lookback=50, macd_fast=12, macd_slow=26, macd_signal_period=9, rsi_period=14, swing_window=5

### Momentum Anti-Chase Gate

Before executing ANY trade, run `analysis/momentum(symbol=..., timeframe=..., rsi=..., atr=...)`:
- **recommendation = "avoid_entry"** → DO NOT enter. Wait for momentum to normalize.
- **recommendation = "reduce_size"** → Enter with 50% normal position size.
- **recommendation = "caution"** → Verify with divergence and volume signals before entering.
- **recommendation = "normal"** → Proceed with planned entry.
- **total_penalty <= -10** → Strong chase risk. Skip entry entirely.
- **RSI > 80 + chase_penalty < 0** → Overbought chase. Avoid LONG entries.
- **RSI < 20 + chase_penalty < 0** → Oversold chase. Avoid SHORT entries (potential bounce).
- Default: lookback=50. Pass current RSI and ATR values for better accuracy.

### Compression Trading

When `market/regime()` returns `"compressing"` with high confidence:
- Place bracket orders at compression extremes
- Expect many cycles without fills — this is normal
- Use Tier 1 polling only (see below)
- When the break happens, it's fast. Cancel the opposite leg immediately.

For the full compression protocol and polling strategy, see `references/polling-protocol.md`.

### Regime-Based Strategy Selection

The market has **8 distinct regimes**. Each regime maps to a specific strategy with its own entry style, stop type, take-profit method, and risk multiplier. Call `strategy/selector(symbol, timeframe)` to get the optimal strategy for the current regime.

| Regime | Strategy | Entry Style | Stop Type | Take Profit | Trailing |
|---|---|---|---|---|---|
| `trending_up` | Pullback Trend | Limit at EMA20/50 | ATR below swing low | Next resistance | ✅ Yes |
| `trending_down` | Pullback Short | Limit at EMA20/50 | ATR above swing high | Next support | ✅ Yes |
| `ranging` | Range Fade | Market at S/R extremes | Beyond range edge | Opposite range edge | ❌ No |
| `compressing` | Compression Breakout | Bracket at compression bounds | ATR-based | 1.5x ATR target | ✅ Yes |
| `momentum_push` | Momentum Continuation | Market on pullback | Below pullback low | Trail until exhaustion | ✅ Yes |
| `mean_reversion` | Mean Reversion Fade | Market at extremes | Beyond Bollinger band | Back to VWAP/mean | ❌ No |
| `volatile_expansion` | Wide Bracket | Wide brackets (2x normal SL) | Wide ATR | Scale out at 1x, 2x ATR | ✅ Yes |
| `low_volatility_consolidation` | Scalp / Wait | Quick in-out at micro levels | Tight (0.5x ATR) | Quick target (0.5-1x ATR) | ❌ No |

**Key principle:** Do NOT use a breakout strategy in a ranging market. Do NOT mean-revert in a momentum push. The regime tells you HOW to trade — `strategy/selector()` translates it into execution parameters.

---

## Phase 3.5: Correlation Gate (NEW)

**Before placing multi-symbol setups, you MUST check correlation.**

1. Run `correlation_matrix(symbols=[...], timeframe="H1", lookback=50)` on all candidate symbols.
2. If any pair has correlation coefficient > 0.7, treat them as **one trade** for risk purposes.
3. **Max 2 positions on correlated symbols** (r > 0.6) simultaneously.
4. If adding a new position pushes total correlated exposure > 10% of equity, **reduce position size by 50%** or skip.

**Rationale:** On 2026-04-08, brackets on EURUSD/GBPUSD/USDJPY all filled within 2 minutes. EURUSD/GBPUSD correlation was ~0.82. This was effectively a 2x leveraged bet, not 3 independent trades. Two went against us simultaneously, amplifying drawdown.

**Hard rule:** If you have 2 open positions on correlated pairs (r > 0.6), you may NOT open a third until one closes.

**Server-side check:** `validate_trade_setup()` includes a `correlation_warning` field that warns if you already have positions on the same symbol or a highly correlated one (>0.7). Use it as a pre-execution gate before any `submit_*_order` call.

---

## Phase 3.25: Analysis Pipeline (NEW)

After identifying candidates via `market/scan()` and `trading/decision_support()`, run the analysis pipeline in this order. Each tool adds a dimension — skip any that return insufficient data, but do NOT skip based on hoping the signal is favorable.

### Step 1: Pattern Recognition (structural context)
```
market/chart_intelligence(symbol=..., timeframe=..., include_screenshot=False)
```
Returns candlestick patterns + multi-bar patterns (W-Bottom, M-Top, Bollinger Squeeze, Breakout, Gap, Fibonacci) in one call. Check `multi_bar_patterns.summary.net_pattern_score` for directional bias.
- **net_pattern_score > +3** → Bullish structural bias. Prefer LONG setups.
- **net_pattern_score < -3** → Bearish structural bias. Prefer SHORT setups.
- **W-Bottom confirmed (+5)** or **M-Top confirmed (-5)** are strongest single-pattern signals.
- **Bollinger Squeeze detected** → Expect breakout soon. Place bracket orders at compression extremes.
- Skip if `multi_bar_patterns.available: false` (insufficient bars).

### Step 2: Divergence Confirmation (momentum vs price)
```
analysis/divergence(symbol=..., timeframe=...)
```
- **divergence_score > +3** → Strong bullish reversal signal. Supports LONG.
- **divergence_score < -3** → Strong bearish reversal signal. Supports SHORT.
- **Conflicting with pattern score** → Reduce confidence. Wait for alignment or skip.
- Skip if no divergences detected (absence of divergence ≠ absence of opportunity).

### Step 3: Volume Validation (conviction check)
```
analysis/volume_profile(symbol=..., timeframe=...)
volume_at_price(symbol=..., timeframe=..., bar_count=100, num_bins=20)
```
- **volume_profile score > +4** → Volume confirms price move (accumulation or strong surge).
- **volume_profile score < -3** → Volume contradicts price move (distribution or drying up). Reduce size or skip.
- **price_volume_signal = "accumulation"** → Strong LONG confirmation.
- **price_volume_signal = "distribution"** → Strong SHORT confirmation or LONG invalidation.
- **price_volume_signal = "weakness"** → Price move lacks conviction. Skepticism warranted.
- **volume_at_price**: Check `poc` (Point of Control — highest volume price), `value_area_high/low` (70% value area). Price above POC = bullish control. Price below POC = bearish control. POC acts as magnet on pullbacks. Value Area breakouts signal conviction.

### Step 3.5: VWAP Fair Value Check
```
vwap(symbol=..., timeframe="H1", bar_count=100)
```
- **Price above VWAP** → Bullish bias. Prefer LONG pullbacks to VWAP.
- **Price below VWAP** → Bearish bias. Prefer SHORT bounces to VWAP.
- **Price near upper band (+2σ)** → Overextended LONG. Avoid new longs, consider taking profit.
- **Price near lower band (-2σ)** → Oversold SHORT. Avoid new shorts, consider taking profit.
- **Price returning to VWAP after deviation** → Mean-reversion target reached. Scale out.
- Use VWAP as a dynamic support/resistance level for pending order placement.

### Step 4: Market Structure Verification
```
market/structure(symbol=..., timeframe="H1", swing_lookback=5)
```
- **structure = "bullish"** with HH/HL sequence → Trend is intact. LONG bias confirmed.
- **structure = "bearish"** with LH/LL sequence → Downtrend confirmed. SHORT bias confirmed.
- **structure = "ranging"** → No trend structure. Use range-fade strategy.
- **trend_health = "strong"** → Full position size OK.
- **trend_health = "weakening"** → Reduce size 50%, tighten stops.
- **trend_health = "exhausted"** → Do NOT enter in trend direction. Consider counter-trend or wait.
- **last_bos** (Break of Structure) → Most recent trend continuation signal.
- **last_choch** (Change of Character) → Potential trend reversal warning.

### Step 5: Setup Probability (historical validation)
```
setup_probability(journal_entries, current_regime="...", current_symbol="...")
```
- **win_rate > 60%** → Your historical edge with this setup is positive. Proceed.
- **win_rate 45-60%** → Marginal edge. Reduce size or require stronger signals.
- **win_rate < 45%** → You lose money with this setup. Avoid or change strategy.
- **sample_size < 5** → Insufficient data. Proceed with caution, journal heavily.
- **common_mistakes** → Check for patterns like "entered_too_early", "ignored_regime". Fix these first.

### Step 6: Momentum Anti-Chase Gate (final risk filter)
```
analysis/momentum(symbol=..., timeframe=..., rsi=..., atr=...)
```
- **recommendation = "avoid_entry"** → DO NOT enter. Period.
- **recommendation = "reduce_size"** → Enter with 50% position size.
- **recommendation = "caution"** → Requires confirmation from Steps 1-5 before entering.
- **recommendation = "normal"** → Clear to proceed.
- **total_penalty <= -10** → Strong chase risk. Skip entirely.

### Signal Aggregation

After all 6 steps, aggregate:
| Strong Signals | Conflicting Signals | Action |
|---|---|---|
| 5+ aligned (bullish or bearish) | 0-1 | High confidence — execute at full size |
| 4 aligned | 1-2 | Moderate confidence — execute, trail tightly |
| 3 aligned | 2-3 | Low confidence — reduce size 50% or skip |
| 0-2 aligned | 3+ | Too much noise — skip, scan next symbol |

**Efficiency note:** You do NOT need all 6 tools for every trade. Use this shortcut matrix:

| Situation | Tools Needed | Skip |
|---|---|---|
| Breakout from squeeze | Patterns + Volume + Volume-at-Price + Momentum | Divergence + Structure |
| Reversal at support/resistance | Divergence + Patterns + VWAP + Momentum | Volume-at-Price + Structure |
| Trend continuation | Structure + Volume + VWAP + Momentum | Divergence + Patterns |
| Mean-reversion fade | VWAP + Volume-at-Price + Divergence + Momentum | Structure + Patterns |
| Quick scalp | Momentum + VWAP only | All others (too slow) |

### Fallback Protocol (When Tools Unavailable)

If analysis tools return 404, timeout, or errors (not just empty data):

| Tools Failed | Action |
|---|---|
| 1-2 of 6 tools | Proceed with available tools. Reduce confidence by 0.1 per missing tool. |
| 3-4 of 6 tools | Proceed with `decision_support` + `support_resistance` + `market/scan` only. Use bracket orders instead of directional entries. |
| 5+ of 6 tools | Skip analysis pipeline entirely. Rely on `market/scan` + `decision_support` + S/R levels. Set up bracket orders at structural levels. |

**Never stop the cycle because tools are unavailable.** Adapt your strategy to available information and continue.

Log missing analysis as `tool_unavailable` in your decision journal:
```
trading/log_decision(..., note="analysis/divergence and analysis/volume_profile returned 404 — confidence reduced")
```

After fallback: use `wait/delay(300)` to wait 5 minutes, then re-scan. The tool may become available on restart.

---

## Phase 3.55: Setup Scoring & Trap Setting (HUNTER PROTOCOL)

**After identifying candidates through scan and analysis pipeline, you MUST score and rank before committing capital.** This phase prevents premature entries and ensures you only trade when the market offers a genuine edge.

### Step 1: Opportunity Ranking

```
market/opportunity_rank(symbols=[candidates], timeframe="H1", min_score=60)
```

This tool scores each candidate across 7 factors of trade-readiness. It returns ranked candidates with composite scores.

| Score | Interpretation | Action |
|---|---|---|
| 80+ | Elite — all factors aligned | Deep analysis, execute or set tight pending |
| 60-79 | Good — most factors aligned | Deep analysis, set trap at better price |
| 40-59 | Marginal — mixed signals | Watch only, do not commit capital |
| < 40 | Weak — no clear edge | Skip entirely |

**Only proceed to deep analysis on symbols scoring ≥ 60.** Maximum 2 candidates per cycle.

### Step 2: Deep Analysis on Top Candidates

For each candidate scoring ≥ 60, run:

```
analysis/divergence(symbol=..., timeframe="H1")
analysis/momentum(symbol=..., timeframe="H1", rsi=..., atr=...)
analysis/volume_profile(symbol=..., timeframe="H1")
multi_timeframe_indicators(symbol=..., timeframes=["H4", "H1", "M15"])
```

### Step 3: Multi-Timeframe Alignment Check

Before any entry, verify alignment across timeframes:

| Timeframe | Purpose | Required Signal |
|---|---|---|
| **H4** | Macro trend direction | Trend agrees with your intended side |
| **H1** | Setup type and regime | Regime supports your thesis |
| **M15** | Entry timing | Shows trigger (pullback, breakout, reversal candle) |

**Entry requires: H4 and H1 aligned, M15 shows entry trigger.** If H4 and H1 conflict, do not trade — wait for alignment.

### Step 4: Set Traps (Never Chase)

Based on analysis results, choose your trap type:

| Situation | Trap Type | Tool |
|---|---|---|
| Strong setup, price not yet at entry zone | Pending order at S/R | `submit_pending_order(...)` |
| Strong setup, waiting for indicator signal | Event-driven wait | `wait/indicator(...)` |
| Moderate setup, need pullback | Limit order at retracement | `submit_pending_order(...)` at Fib level |
| Setup strong but session sub-optimal | Pending order outside session | `submit_pending_order(...)` with wide stop |
| Unclear timing, good direction | Monitor with boundaries | `wait/trade_monitor(...)` |

**A hunter does not run after prey — it places the snare and waits.**

### Step 5: Watchlist Management

Candidates scoring 40-59 or showing potential but lacking one confirming signal:
- Add to mental watchlist
- Note the missing signal (e.g., "EURUSD: bullish divergence but momentum = caution — watch for RSI normalization")
- Re-check on next cycle

**Do NOT skip candidates silently.** Either trade them, trap them, or watch them with a reason.

---

## Phase 4: Trade Viability Gate

Do not trade because a chart looks interesting. Trade only if viable.

### Patience Gate (FIRST Check — Before All Other Criteria)

**Before evaluating any other criteria, answer: "Is this the optimal entry time?"**

```
PATIENCE GATE:
  1. trading/coach(action="check_session") → get session_context
  2. economic_calendar(hours_ahead=2) → check for high-impact events
  3. Evaluate:
     IF coaching = "strong_entry" AND session is optimal AND no economic events
       → PASS: Proceed to criteria checklist
     IF coaching = "cautious_entry" AND session is optimal
       → PASS WITH CAUTION: Proceed, but prefer pending orders
     IF coaching = "cautious_entry" AND session is sub-optimal
       → FAIL: Do NOT enter market. Set pending order at S/R level.
     IF coaching = "cautious_wait" or "strong_wait"
       → FAIL: Do NOT enter. Use wait/indicator or set trap.
     IF session is sub-optimal (Asian session for EURUSD, Friday afternoon, etc.)
       → FAIL: Do NOT enter market. Set pending order or wait.
     IF high-impact economic event within 30 minutes
       → FAIL: Wait until after event. Set pending order if thesis survives.
```

**Session quality guidelines:**
| Session | Quality for EUR/GBP/USD | Quality for JPY/AUD | Quality for XAU |
|---|---|---|---|
| London (08:00-12:00 UTC) | Optimal | Good | Good |
| NY (13:00-17:00 UTC) | Optimal | Good | Optimal |
| London-NY Overlap (13:00-16:00 UTC) | Elite | Good | Elite |
| Asian (00:00-06:00 UTC) | Sub-optimal | Optimal | Sub-optimal |
| Friday afternoon (after 18:00 UTC) | Sub-optimal | Sub-optimal | Sub-optimal |

**The patience gate saves you from the three deadliest sins:**
1. Entering during low-liquidity sessions (slippage, fake breakouts)
2. Counter-trend trades when regime is unclear
3. Tool budget exhaustion before setting up proper entries

---

**Micro accounts ($100-$500) — 5 criteria:**
1. Regime alignment (trend, range bounce, or breakout)
2. Risk:reward at least 1.5:1
3. No duplicate intent exists
4. Single-trade risk within tier budget
5. No major scheduled event invalidates the setup — verify with `economic_calendar(hours_ahead=4)`

**Medium/large accounts ($500+) — 7 criteria:**
All micro criteria above, plus:
6. Confidence is explicit and ≥ 0.6
7. Spread cost is acceptable relative to ATR and planned risk
8. **Analysis pipeline confirms the setup** — at minimum, `analysis/momentum()` must return recommendation != "avoid_entry". For high-confidence trades (full size), require 2+ confirming signals from the analysis pipeline (divergence, volume, patterns).

**Mandatory correlation gate:** After `validate_trade_setup()`, check the `correlation_warning` field. If `has_exposure: true`, you already have positions on the same symbol or a highly correlated one (>0.7). Reduce position size by 50% or skip the trade. This is automatic — no extra tool call needed.

**Single-call alternative:** Use `trading/decision_support(symbol=..., side=...)` to get regime + ATR + RSI + EMA(20) + EMA(50) + coaching feedback in one call (~400ms) instead of assembling individual indicator calls.

If you cannot articulate the edge in one sentence, skip it. If you have analyzed the same setup 3+ times without executing, either execute or skip — do not analyze a fourth time.

---

## Phase 4.5: RSI Extremes Filter (NEW)

**Hard pre-check before ANY entry:**

| Condition | Block | Exception |
|---|---|---|
| RSI(14) < 25 on entry timeframe | Do NOT SELL | Breakout: price CLOSED beyond structural level WITH volume confirmation |
| RSI(14) > 75 on entry timeframe | Do NOT BUY | Breakout: price CLOSED beyond structural level WITH volume confirmation |

**Rationale:** On 2026-04-08, USDJPY SELL was entered at RSI 19.8 (extreme oversold). Price reversed 23 pips against us, hitting SL for -$2.90. Selling into oversold conditions is a mean-reversion trap — the bounce risk far exceeds the continuation probability.

**This is a HARD BLOCK, not a suggestion.** If RSI is in the extreme zone, the entry is invalid regardless of other signals.

**Cross-reference with `analysis/momentum()`:** If RSI is extreme AND momentum returns `recommendation = "avoid_entry"` or `total_penalty <= -10`, the entry is doubly invalid. Even if you believe the RSI exception applies (breakout with volume), a momentum "avoid_entry" overrides the exception.

---

## Phase 5: Risk Framework

For tiered risk tables (micro/medium/large accounts with specific percentages, daily limits, and loss escalation rules), see `references/risk-framework.md`.

**Key principle:** Risk scales with account size. Micro accounts need 3-5% per trade for meaningful growth. Large accounts need 1-2% for capital preservation. Calculate the actual math before dismissing a symbol — don't guess.

Hard rule: if the computed lot size or minimum lot breaks your risk budget, there is no trade.

---

## Phase 6: Position Sizing

```
calculate_position_size(...)
validate_trade_setup(...)
```

Log before entry: intended risk (% and currency), stop distance, TP distance, spread context, and why this size is acceptable.

---

## Phase 7: Execution

### Hunter's Execution Matrix

**Never execute a market order as your first action.** Choose execution method based on coaching feedback and session quality:

| Coaching | Session | Action | Rationale |
|---|---|---|---|
| `strong_entry` | Optimal | **Market order** at current price | All factors aligned — strike now |
| `strong_entry` | Sub-optimal | **Pending order** at S/R level | Setup is good but timing is wrong |
| `cautious_entry` | Optimal | **Pending order** at better price (pullback/retracement) | Setup is decent — improve entry |
| `cautious_entry` | Sub-optimal | **Pending order** at support/resistance with wide stop | Weak conviction — set trap far from current price |
| `cautious_wait` | Any | **wait/indicator** or **wait/trade_monitor** | Not ready — let market come to you |
| `strong_wait` | Any | **wait/delay(300)** then re-scan | No edge — conserve resources |
| No clear signal | Any | Add to watchlist, **continue scanning** | No trade — move on |

**Decision tree:**
```
IF coaching = "strong_entry" AND session optimal AND no economic events
  → submit_market_order_via_bridge(...)

ELIF coaching = "cautious_entry" OR session sub-optimal
  → submit_pending_order(...) at support/resistance or Fib level

ELIF coaching = "cautious_wait"
  → wait/indicator(...) for RSI/MACD trigger
  → OR wait/trade_monitor(...) with expected/invalidation

ELIF coaching = "strong_wait"
  → wait/delay(300) → re-scan

ELSE (no clear signal)
  → Add to watchlist → next candidate
```

**Standard Execution Methods**

**Market orders:** Use `submit_market_order_via_bridge(...)` with a unique `intent_id`. Reconcile immediately afterward.

**Pending orders:** Use `submit_pending_order(...)`. Verify the order exists in `orders_pending()` after submission and store the `order_id`.

**Bracket orders:** If `place_bracket_order(...)` fails, place both legs manually via `submit_pending_order(...)`. When one fills, cancel the orphan immediately and verify.

**Event-driven bracket monitoring:** After placing brackets, call `ea_bracket/tick()` to get OCO event data. The response includes `{events: [{bracket_id, filled_leg, filled_ticket, cancelled_ticket, fill_price}]}` — this tells you exactly which leg filled and which was cancelled. You do NOT need to poll `orders_pending()` for bracket status; the events array is authoritative. The EA's `OnTimer()` processes brackets independently — `ea_bracket/tick()` reads the event log, it does not trigger processing.

**After every submission:** Snapshot account + orders + positions + deals. Verify your order appeared where expected. If not, do not keep trading as if execution succeeded.

---

## Phase 8: Position Management (REWRITTEN)

- `modify_order(...)` is for **pending** orders only
- `modify_position_sl_tp(...)` is for **open** positions only
- Re-read state after every modify

### Trailing Stop Options (3 Methods)

| Priority | Method | Tool | Persistence | When to Use |
|---|---|---|---|---|
| **PRIMARY** | **EA-native auto-trail** | `trail_config` param in `submit_market_order_via_bridge()` / `submit_pending_order()` | Persistent (EA-side, survives all restarts) | Always prefer for new positions. Activates immediately on fill with configurable ATR timeframe/period. |
| FALLBACK | **Server-side auto-trail** | `set_trailing_stop(position_id, distance_atr_multiplier, ...)` | Persistent but LOST on MCP server restart | Legacy method. Use only when position was submitted without `trail_config`. Requires manual `trailing_stop/tick()` calls. |
| MANUAL | **One-shot trail** | `trail_position(position_id, distance_points, lock_in_points)` | Single application | Quick manual trail on demand. |

> **Always prefer `trail_config` for new positions.** It is EA-side, persistent, and requires no server maintenance. `set_trailing_stop()` is a LEGACY fallback — it is lost on MCP server restart and requires periodic `trailing_stop/tick()` calls.

**trail_config schema:**
```json
{
  "atr_multiplier": 2.0,       // SL distance as ATR multiple
  "lock_profit_atr": 1.0,      // Begin trailing after this much profit
  "check_interval_seconds": 10, // How often to check
  "atr_timeframe": "H1",        // ATR calculation timeframe (default H1, customize: M15, H4, D1)
  "atr_period": 14              // ATR period (default 14)
}
```
ATR timeframe defaults to H1 but can be customized per position — use M15 for faster trailing on volatile instruments, H4 for slower trailing on trending setups, D1 for swing positions.

**Manual trailing management tools:**
- `trailing_stop/list()` — list active trailing stops
- `trailing_stop/tick()` — process all active trailing stops (call periodically)
- `trailing_stop/cancel(position_id)` — cancel automated trailing without closing position

### MANDATORY Trailing Checklist (Every Cycle With Open Positions)

**You MUST check these in order, every single cycle. Skip no items.**

**Primary data source:** Use `position.health` fields from `positions_open()` — the EA already computes all values below. Manual computation is FALLBACK only when health fields are unavailable.

```
CYCLE TRAILING CHECK:
┌─────────────────────────────────────────────────────────┐
│ 1. Is position.health.trail_eligible true?              │
│    (equivalent to: profit > 2x spread cost)             │
│    → YES: Move SL to breakeven. LOG this action.        │
│    → NO:  Proceed to check 2.                           │
│                                                         │
│ 2. Has price moved ≥ 1x ATR in your favor               │
│    since last trail (or since entry if never trailed)?  │
│    Check: position.health.distance_to_sl_pips vs entry  │
│    → YES: Move SL 0.5x ATR in profit direction. LOG it. │
│    → NO:  Proceed to check 3.                           │
│                                                         │
│ 3. Time-based exit check:                                │
│    position.time_health.bars_elapsed vs max_hold_bars    │
│    position.health.time_in_trade_bars_h1                │
│    → If bars_elapsed >= max_hold_bars: EA auto-closes.  │
│    → If bars_remaining < 4 and position winning:        │
│       consider taking profit before time exit.           │
│    → If bars_elapsed >= 16 AND max_profit < 0.5x ATR:   │
│       Close and redeploy capital. LOG reason.            │
│                                                         │
│ 4. Has your entry thesis been invalidated?               │
│    (regime flipped, key level broken, indicator diverged)│
│    → YES: Close immediately. LOG thesis invalidation.   │
│    → NO:  Position is healthy. Continue monitoring.     │
└─────────────────────────────────────────────────────────┘
```

**position.health fields (from `positions_open()`):**
| Field | Type | Description |
|---|---|---|
| `distance_to_sl_pips` | float | Pips from current price to stop loss |
| `distance_to_tp_pips` | float | Pips from current price to take profit |
| `pnl_percent_of_risk` | float | Current P&L as % of planned risk |
| `time_in_trade_minutes` | int | Minutes since position opened |
| `time_in_trade_bars_h1` | int | H1 bars elapsed since open |
| `is_winning` | bool | True if current P&L > 0 |
| `is_at_breakeven` | bool | True if SL ≈ entry price |
| `trail_eligible` | bool | True if profit > 2× spread cost (Check 1) |
| `spread_cost_pips` | float | Entry spread cost in pips |
| `profit_multiple_of_spread` | float | Current profit as multiple of spread |

**position.time_health fields (from `positions_open()`, EA with PositionTimeManager):**
| Field | Type | Description |
|---|---|---|
| `is_registered` | bool | True if position registered with PositionTimeManager |
| `bars_elapsed` | int | Bars elapsed since registration |
| `bars_remaining` | int | Bars until auto-close (EA will close when this hits 0) |
| `min_profit_points` | int | Minimum profit threshold to prevent auto-close |
| `current_profit_points` | int | Current profit in points |

**If you skip any check above, you must log it as a `rule_violation` with quality_rating = 1.**

### Trailing Philosophy

A winning trade at breakeven SL has asymmetric risk: max loss = $0, max gain = unlimited. Hold winners. Cut losers.

**On 2026-04-08, GBPUSD BUY reached +$16 unrealized profit. No trailing was performed. The trade fell to -$6 before recovering to close at +$3.94. That is a $12.06 mistake — 8.2% of a $147 account. Never again.**

### Stale Position Rule (Dead Money Prevention)

If a position has been open for **4 hours or more** and has not achieved **> 0.5x ATR profit** at any point, close it and redeploy the capital.

**Automation note:** If the EA has `PositionTimeManager` enabled, this is enforced server-side. Check `position.time_health.bars_remaining` to know exactly when the EA will auto-close. If `bars_remaining < 4` and the position is winning, consider taking profit proactively before the time exit triggers.

**Primary check:** `position.health.time_in_trade_bars_h1` (from `positions_open()`) gives you H1 bars elapsed directly. No manual clock tracking needed.

**Rationale:** EURUSD BUY @ 1.1698 sat at breakeven for 8+ hours. That $1.34 margin could have been redeployed 3-4 times on other setups. Dead capital is opportunity cost.

**Exception:** Compression breakout setups where price is still within the compression zone and the thesis remains valid. In this case, extend the deadline to 6 hours maximum.

---

## Phase 8.5: Portfolio Risk Assessment (NEW)

**When managing 2+ open positions, you MUST assess portfolio-level risk.**

**Use `portfolio/risk()` — one call replaces 5 manual calculations:**

```python
portfolio/risk()
# Returns: {
#   "total_exposure_usd": 1500.0,
#   "net_exposure_usd": 800.0,
#   "exposure_by_symbol": {"XAUUSD": 1000, "EURUSD": 500},
#   "risk_metrics": {
#     "concentration_ratio": 0.67,
#     "max_single_position_pct": 0.25,
#     "correlated_pairs": [{"symbol_a": "EURUSD", "symbol_b": "GBPUSD", "correlation": 0.82}]
#   }
# }
```

**Interpretation guide:**
- If `concentration_ratio` > 0.3 → reduce new position size by 50%
- If `max_single_position_pct` > 20% → reassess exposure, consider closing weakest position
- If `correlated_pairs` has entries → check Phase 3.5 correlation rules (max 2 correlated positions)
- `total_exposure_usd` must not exceed 3x equity
- `net_exposure_usd` reveals accidental directional bets — if heavily skewed, consider hedging

**Legacy manual checks** (use only if `portfolio/risk()` is unavailable):
1. Sum of all position notional values → must not exceed 3x equity
2. Track whether portfolio is net long or net short on USD
3. Max portfolio drawdown: sum of all SL distances × lot sizes → must not exceed 15% of equity
4. If 2+ positions simultaneously in drawdown > 3% equity → reassess ALL positions

---

## Phase 9: Exit & Journaling

On every exit, determine: whether this was your trade, exit type (manual/stop/target/foreign), and realized P&L.

Update the original decision with: `outcome`, `pnl`, `exit_price`, `lesson_learned`, `quality_rating`, and `mistake_category` when applicable.

Entry logging and exit logging are mandatory. For required fields and the full mistake taxonomy, see `references/journaling-contract.md`.

**Exit attribution:** Log who/what closed the trade:
- `exit_by`: "agent" (your decision), "sl" (stop-loss), "tp" (take-profit), "user" (manual override), "unknown"

**If a user manually closes a position that was working in your favor, log it as `user_override` with the P&L difference between actual close and where the setup would have taken it.**

---

## Phase 9.5: Post-Session Review (NEW)

At the end of every trading session (user requests review, daily loss limit hit, or market close):

1. Run `performance_summary(days=1)` for today's numbers
2. Run `trading/insights(lookback_days=7)` for pattern recognition
3. Review all journal entries from today's session
4. Identify: what worked, what failed, what to change
5. Update `references/trading-lessons.md` with new patterns
6. Optional: `get_chart_screenshot(symbol=..., timeframe=...)` for visual trade summary and chart annotation

**Metrics that matter:**
- Profit factor (target: > 1.5)
- Win rate (target: > 40% with 2:1 RR)
- Average winner vs average loser ratio (target: > 2:1)
- Max drawdown as % of equity (target: < 10%)
- Trades per hour of active session (target: > 0.5 — analysis without execution is waste)

---

## Phase 10: Continuous Cycle

### The Continuous Cycle (NEVER Stops)

```
CYCLE (Hunter Pattern):
  State Triage → Determine Mode → (If no positions/orders)
  → Market Scan → Rank Candidates → Deep-Analyze Top 1-2 → Check Multi-TF Alignment
  → Patience Gate → If optimal: Execute | If not: Set Trap (pending/wait)
  → If no entry viable → wait/delay(300) → Back to State Triage
  → If analysis tools unavailable → Fallback protocol → wait/delay(300) → Back to State Triage
  → After execution: Position Management → Journal → Re-Check Traps → Back to State Triage
```

**Hunter Cycle — Daily Quick Ref:**
```
reconcile → scan → rank → deep-analyze top candidates → check multi-TF
  → patience gate → set trap or execute → verify → journal → re-check traps
```

**CRITICAL RULES:**
- **NEVER end a cycle by asking the user a question.** Make a decision and act on it.
- **Presenting analysis to the user is NOT the end of the cycle** — it's a decision point WITHIN the cycle.
- **After presenting analysis, if no entry is taken:** use `wait/delay(300)` and re-scan.
- You do NOT stop because you took one trade, feel uncertain, or want to "deliver a report."
- Keep scanning, deciding, and executing until: daily loss limit hit, market closed, user explicitly stops you, or session context budget exhausted.

**The cycle pattern:**
```python
# This is the mental model you MUST follow every cycle:
def trading_cycle():
    state = triage()  # bridge, account, positions, orders, deals
    mode = determine_mode(state)  # HAS_POSITION, HAS_ORDERS, or SCAN_MODE
    
    if mode == SCAN_MODE:
        scan = market_scan()
        ranked = opportunity_rank(scan.candidates, min_score=60)
        top_candidates = ranked.top(2)  # Max 2 per cycle
        
        for candidate in top_candidates:
            deep_analysis = analyze_deep(candidate)  # divergence + momentum + volume
            multi_tf = multi_timeframe_check(candidate)  # H4 + H1 + M15
            patience = patience_gate(candidate)  # session + coaching + economic events
            
            if patience == "execute":
                result = execute(candidate)
                if result.success:
                    manage_position()
                    journal_entry(candidate, result)
                    recheck_traps()
                    return trading_cycle()
                else:
                    log_tool_unavailable(candidate, result.error)
            elif patience == "trap":
                set_trap(candidate)  # pending order or wait/indicator
                journal_trap(candidate)
            # else: watch or skip — continue to next candidate
        
        # No viable entry this cycle
        wait_or_poll(600)  # 10 minutes
        return trading_cycle()  # ← BACK TO START
    
    elif mode == HAS_POSITION:
        run_trailing_checklist()
        if position.health.action_required != "none":
            take_action(position.health.action_required)
        wait_or_poll(600)  # Default cadence
        return trading_cycle()
    
    elif mode == HAS_ORDERS:
        monitor_orders()
        wait_or_poll(600)
        return trading_cycle()

def wait_or_poll(seconds):
    """Try wait/delay first, fall back to polling if unavailable."""
    try:
        wait_delay(seconds)
    except ToolError:
        # Wait tool unavailable — continue cycle immediately
        # The next cycle IS the poll
        pass
```

### Polling Discipline (ENFORCED)

**Before each polling cycle, state your tier and which tools you will use:**

| Tier | When | Tools (MAX) | Frequency |
|---|---|---|---|
| **Tier 1 — Sniper** | Price within 20 pips of trigger | `get_ticks()` + `orders_pending()` (2 calls) | 15-30 seconds |
| **Tier 2 — State Check** | Pending orders active, nothing near | `account_summary()` + `orders_pending()` + `deals_history(limit=5)` (3 calls) | 10 minutes (5 min if concerning) |
| **Tier 3 — Market Scan** | No pending orders, finding setups | `market/scan()` + `economic_calendar()` + `market/regime()` (3-4 calls) | 10-15 minutes |

**If you run > 5 tool calls in a single Tier 1 or Tier 2 cycle, you are wasting tokens.** Log it as `polling_waste`.

**On open positions with no near triggers:** Use Tier 2 every 10 minutes. The trailing checklist runs on the data from Tier 2.

For the full polling protocol, see `references/polling-protocol.md`.

**Tool Call Budget:** Maximum **8 tool calls** between State Triage and Execute phases. If you've made 8 tool calls without submitting an order or entering a wait state (`wait/delay`, `wait/trade_monitor`, `wait/indicator`, `wait_for_price`), you MUST either:
1. Place bracket orders at support/resistance levels, OR
2. Use `wait/delay(300)` and re-scan

This prevents analysis paralysis. After 8 calls, you have enough information to act. More calls will not change the decision — they burn tokens and delay action.

**Counting rules:**
- Parallel calls count as 1 (e.g., 3 decision_support calls in parallel = 1 budget unit)
- State triage calls (bridge_status, account_summary, positions_open, orders_pending, deals_history) = 1 budget unit total
- market/scan() = 1 budget unit (regardless of symbol count)
- Each individual analysis tool call (divergence, volume, momentum, patterns) = 1 budget unit
- wait/* tools = 0 budget units (they're the escape hatch, not analysis)

---

## Phase 11: Wait Protocol (NEW)

**Stop polling. Start waiting.** When you have a defined setup with known target and invalidation levels, use the long-polling wait tools instead of burning tokens in manual polling loops.

### Wait Timing Philosophy

**Default cadence: 10 minutes.** This is your baseline rhythm. When in doubt, wait 10 minutes.

| Situation | Wait Duration | Why |
|---|---|---|
| No active trades, scanning for setups | 10 minutes | Markets don't change thesis in 2 minutes |
| Open positions, far from SL/TP | 10 minutes | Run trailing checklist, then wait |
| Open positions, within 1x ATR of SL or TP | 2-5 minutes | Closer monitoring needed |
| Price within 20 pips of bracket trigger | 15-30 seconds | Sniper watch — Tier 1 |
| Just entered a trade | 5 minutes | Confirm entry thesis still valid |
| Major news event pending | Wait until after | Don't trade the uncertainty |

**Reduce from 10 minutes ONLY when:**
- P&L moved > 50% of your risk amount since last check
- Regime flipped (trending → compressing or vice versa)
- Economic calendar shows high-impact event in < 30 minutes
- You're validating a specific hypothesis (e.g., "will it hold support?")
- Price is within 1x ATR of your SL or TP level

**Never reduce wait time because you're anxious. Only reduce because market conditions demand it.**

**Key principle:** The tools `wait/trade_monitor`, `wait/delay`, `wait/indicator`, and `wait_for_price` hold the HTTP connection server-side. You do NOT poll while waiting. The server does the work. When the wait resolves, use the returned `market_context` to decide your next action.

### When to Use Which Wait Tool

| Situation | Tool | Why |
|---|---|---|
| Waiting for price to hit a single level | `resources/market/wait_for_price(symbol, condition, price, timeout_seconds)` | Simple price alert, server-side sampling at ~1s |
| Monitoring a position with P&L + price alerts | `resources/positions/monitor(position_id, alert_at_pnl, alert_at_price, timeout_seconds)` | Hands-free position monitoring, samples at ~5s |
| Waiting for indicator condition (e.g., RSI < 30) | `tools/wait/indicator(symbol, indicator, condition, value, ...)` | Event-driven entries without polling `get_indicator()` |
| **Waiting for a trade setup with target AND invalidation** | `tools/wait/trade_monitor(symbol, side, duration, expected, invalidation)` | Best for setup validation — computes boundaries, returns market context on resolution |
| Simple time delay between analysis cycles | `tools/wait/delay(duration_seconds)` | Basic pause; use only when no event-driven condition exists |

### Trade Monitor — Primary Wait Tool

`tools/wait/trade_monitor(...)` is your primary waiting tool for setups. It holds the HTTP connection open until one of three outcomes:

```python
tools/wait/trade_monitor(
    symbol="XAUUSD",
    side="buy",
    duration="H1:4",              # 4 hours
    expected={"type": "atr", "multiplier": 1.5},   # Target: 1.5x ATR above entry
    invalidation={"type": "atr", "multiplier": 1.0}, # Invalidation: 1.0x ATR below entry
    check_interval_seconds=5
)
```

**Returns:** `{reason: "target_reached"|"invalidation_hit"|"timeout", current_price, target_price, invalidation_price, market_context: {regime, atr, rsi, spread_points}}`

**After resolution:** Re-analyze the market context returned. If `target_reached` → consider taking profit or trailing. If `invalidation_hit` → close immediately and journal. If `timeout` → assess if thesis is still valid or if stale position rule applies.

### Duration Formats

The `duration` field on `tools/wait/trade_monitor` accepts these formats:

| Format | Example | Meaning |
|---|---|---|
| **Timeframe shortcut** | `"M5"`, `"M15"`, `"H1"`, `"D1"` | One bar of that timeframe |
| **Bar count** | `"H1:4"`, `"M15:10"`, `"D1:2"` | N bars of specified TF (4h, 2.5h, 2 days) |
| **Clock time** | `"14:30"` | Seconds until next 14:30 UTC |
| **Minutes shorthand** | `"5m"`, `"30m"` | 5 minutes, 30 minutes |
| **Raw seconds** | `"300"`, `"900"` | 300s (5 min), 900s (15 min) |

**Hard limit:** Maximum 3600 seconds (1 hour). Longer durations are rejected — re-evaluate instead of waiting that long.

### Expected + Invalidation Bracket Pattern

Every wait MUST have both boundaries defined. A wait without an invalidation boundary is analysis without risk management:

```
BUY setup:
  expected:     price ABOVE entry (target)
  invalidation: price BELOW entry (thesis broken)

SELL setup:
  expected:     price BELOW entry (target)
  invalidation: price ABOVE entry (thesis broken)
```

**Expected types:**
- `{"type": "price", "value": 3000.0}` — absolute price level
- `{"type": "pips", "value": 50}` — N pips from current price
- `{"type": "atr", "multiplier": 1.5}` — N×ATR from current price

**Invalidation types:** Same structure. Always use `atr` type for stops — it adapts to volatility.

### Integration with Trading Decisions

**Typical flow:**
1. Analyze setup → `trading/decision_support(...)`
2. Validate → `validate_trade_setup(...)`
3. Size → `calculate_position_size(...)`
4. Execute → `submit_market_order_via_bridge(...)`
5. **Wait** → `tools/wait/trade_monitor(...)` with expected/invalidation
6. On resolution → re-analyze with returned `market_context`, decide next action
7. Journal → `trading/log_decision(...)` with outcome

**For pending orders:** Use `resources/positions/monitor(position_id, ...)` after fill to track the position hands-free.

### Anti-Patterns (MUST AVOID)

| Anti-Pattern | Why It's Wrong | Correct Approach |
|---|---|---|
| Polling `get_ticks()` in a loop | Wastes tokens, high latency per call | Use `resources/market/wait_for_price()` or `tools/wait/trade_monitor()` |
| Polling `get_order_book()` in a loop | Same as above, plus stale snapshots | Use `tools/wait/trade_monitor()` |
| Waiting without invalidation boundary | No risk management — thesis could be broken and you'd miss it | Always define both `expected` and `invalidation` |
| Duration > 3600s | Market context changes too much in > 1 hour | Use shorter waits, re-evaluate, extend if thesis still valid |
| `tools/wait/delay()` for everything | Blind waiting — no event detection | Use event-driven waits (`trade_monitor`, `wait_for_price`, `wait/indicator`) |
| Not checking `market_context` on resolution | Wasted data — the server already computed regime/ATR/RSI | Use returned context to inform next decision |

---

## Multi-Timeframe Analysis Protocol

**A single timeframe is a snapshot. Multiple timeframes are a movie.** Always analyze at least three timeframes before entering any trade.

### Timeframe Hierarchy

| Timeframe | Role | What It Tells You | Entry Requirement |
|---|---|---|---|
| **H4** | Macro trend | Direction of the dominant trend. Are we in a bull or bear market? | H4 trend must agree with your trade direction (or be neutral/ranging) |
| **H1** | Setup & regime | Type of setup (trend, range, breakout). Market regime classification. | H1 regime must support your thesis (e.g., trending for trend trades, ranging for range bounces) |
| **M15** | Entry timing | Precise entry trigger. Pullback completion, breakout candle, reversal pattern. | M15 must show a concrete trigger candle or pattern |

### Alignment Rules

```
ENTRY IS VALID ONLY IF:
  ✓ H4 trend agrees with your side (LONG needs H4 uptrend or neutral, SHORT needs H4 downtrend or neutral)
  ✓ H1 regime supports the setup type
  ✓ M15 shows a specific entry trigger (not just "looks good")

ENTRY IS INVALID IF:
  ✗ H4 and H1 conflict (e.g., H4 bullish, H1 bearish)
  ✗ M15 shows no trigger (price is in the middle of nowhere)
  ✗ All three timeframes disagree
```

### How to Check Multi-Timeframe Alignment

```python
# One-call check across all timeframes:
multi_timeframe_indicators(symbol="EURUSD", timeframes=["H4", "H1", "M15"])

# Returns indicators for each timeframe. Check:
# - EMA(20) vs EMA(50) relationship on each TF
# - RSI position on each TF
# - Whether indicators agree on direction
```

**If `multi_timeframe_indicators` is unavailable, check manually:**
1. `trading/decision_support(symbol=..., timeframe="H4")` — macro direction
2. `trading/decision_support(symbol=..., timeframe="H1")` — setup regime
3. `trading/decision_support(symbol=..., timeframe="M15")` — entry timing

### Counter-Trend Trading Exception

You MAY trade against the H4 trend ONLY if:
- H1 shows a clear reversal pattern (W-Bottom, M-Top, divergence)
- M15 shows an entry trigger at a major S/R level
- Position size is reduced by 50%
- Stop loss is tighter than usual

**Counter-trend trades are scalps, not swings.** Take profit at the first resistance and move SL to breakeven immediately.

---

## Available Advanced Tools

The following tools are available through mt5-mcp. Use them strategically — not every tool is needed for every trade, but knowing what exists prevents analysis gaps.

### Market Intelligence

| Tool | Purpose | When to Use |
|---|---|---|
| `market/scan(symbols, timeframe)` | Regime, ATR, spread, activity scan | Initial discovery — cast wide net |
| `market/opportunity_rank(symbols, timeframe, min_score)` | 7-factor trade-readiness scoring | Rank candidates after scan — Hunter Protocol step 1 |
| `market/snapshot(symbol, timeframe)` | One-call complete market context | Quick check without multiple calls |
| `market/regime(symbol, timeframe)` | Classify market regime (8 states: trending_up/down, ranging, compressing, momentum_push, mean_reversion, volatile_expansion, low_volatility_consolidation) | Determine setup type and strategy |
| `market/structure(symbol, timeframe, swing_lookback)` | Market structure: BOS/ChoCh, HH/HL/LH/LL sequence, trend health | Structural context — is the trend healthy, weakening, or exhausted? |
| `market/chart_intelligence(symbol, timeframe, include_screenshot)` | Screenshot + patterns + indicators bundle | Visual confirmation + automated pattern detection |
| `economic_calendar(hours_ahead)` | Scheduled economic events | Patience Gate — avoid trading into news |

### Analysis Suite

| Tool | Purpose | When to Use |
|---|---|---|
| `analysis/divergence(symbol, timeframe)` | MACD/RSI divergence detection (including hidden divergence for trend continuation) | Reversal confirmation, trend continuation checks |
| `analysis/momentum(symbol, timeframe, rsi, atr)` | Chase/exhaustion risk filter | Anti-chase gate — MUST run before every entry |
| `analysis/volume_profile(symbol, timeframe)` | Volume anomaly detection | Conviction check — does volume support the move? |
| `analysis/multi_bar_patterns(symbol, timeframe)` | W-Bottom, M-Top, Bollinger Squeeze, Fibonacci retracement | Structural context — what pattern is forming? |
| `multi_timeframe_indicators(symbol, timeframes)` | Check indicator alignment across H4/H1/M15 | Multi-TF protocol — entry requires alignment |
| `volatility_profile(symbol, timeframe)` | Detailed volatility analysis | Sizing stops, understanding expansion/compression |
| `support_resistance(symbol, timeframe)` | Key S/R levels | Setting pending orders, stop placement |
| `vwap(symbol, timeframe, bar_count)` | VWAP with ±1σ/2σ deviation bands using tick volume | Intraday fair value, mean-reversion targets, trend pullback entries |
| `volume_at_price(symbol, timeframe, bar_count, num_bins)` | Volume-at-Price profile: POC, Value Area (70%), distribution | Identify high-conviction zones, value gaps, POC magnet levels |

### Trading Operations

| Tool | Purpose | When to Use |
|---|---|---|
| `trading/decision_support(symbol, side)` | Regime + ATR + RSI + EMA + coaching in one call (~400ms) | First analysis on any candidate |
| `trading/coach(action)` | Trading coach with session_context | Patience Gate — determine entry urgency |
| `strategy/selector(symbol, timeframe)` | Recommends optimal strategy based on current regime (8 strategies: pullback, breakout, range-fade, compression, momentum continuation, mean-reversion fade, wide-bracket, scalp) | Hunter Protocol step 2 — tells you HOW to trade this regime |
| `setup_probability(trades, current_regime, current_symbol, min_samples)` | Bayesian-like win rate estimate from journal history with regime/session/symbol filtering | Before entry — "have I been successful with this setup before?" |
| `trading/insights(lookback_days)` | Pattern recognition from historical data | Post-session review |
| `trading/log_decision(...)` | Decision journaling | Mandatory on every entry and exit |
| `calculate_position_size(...)` | Position sizing with risk parameters | Phase 6 sizing |
| `validate_trade_setup(...)` | Pre-execution validation (includes correlation check) | Gate before any order submission |
| `portfolio/pre_trade_gate(...)` | Final safety check before order submission | Last line of defense — portfolio-level risk |
| `portfolio/risk()` | Portfolio-level risk assessment | When managing 2+ open positions |

### Wait & Monitor

| Tool | Purpose | When to Use |
|---|---|---|
| `wait/delay(duration_seconds)` | Sleep with market summary on resume | Simple time pause between cycles |
| `wait/indicator(symbol, indicator, condition, value)` | Long-poll until RSI/MACD hits condition | Event-driven entries — "wait until RSI < 30" |
| `wait/trade_monitor(symbol, side, duration, expected, invalidation)` | Long-poll with target + invalidation levels | Setup validation — "watch this trade thesis" |
| `resources/market/wait_for_price(symbol, condition, price, timeout)` | Simple price alert, server-side sampling | Price-level triggers |
| `resources/positions/monitor(position_id, ...)` | Hands-free position monitoring | After fill, track P&L and price alerts |

### Order Management

| Tool | Purpose | When to Use |
|---|---|---|
| `submit_market_order_via_bridge(...)` | Market order with unique intent_id | Hunter's Execution Matrix — only when all gates pass |
| `submit_pending_order(...)` | Limit/stop orders | Trap setting — primary hunter tool |
| `place_bracket_order(...)` | OCO bracket (TP + SL as pending orders) | Set-and-forget entries with predefined exit |
| `modify_order(...)` | Modify pending orders | Adjust traps as market evolves |
| `modify_position_sl_tp(...)` | Modify SL/TP on open positions | Trailing, breakeven moves |
| `ea_bracket/tick()` | Read OCO bracket event log | After bracket fill — which leg executed? |

---

## Non-Negotiable Rules (23 Rules)

1. Reconcile state before every side effect.
2. Track ownership explicitly — never trade unattributable state as yours.
3. Use tier-appropriate risk. Never force minimum lots that violate your budget.
4. Skip trades that fail viability criteria.
5. Prevent duplicate orders.
6. Re-read state after every submit, modify, cancel, or close.
7. Journal every entry and exit completely.
8. Trail winners. Cut losers. Never manually close a winning trade.
9. Never widen a stop loss.
10. Analyze max 3 cycles per setup — then execute or skip.
11. Tier your polling — full analysis every cycle wastes calls. Use ticks + orders when traps are set.
12. Cancel orphan bracket legs within seconds of a fill.
13. **Check correlation before multi-symbol setups. Max 2 correlated positions (r > 0.6).**
14. **Never sell when RSI < 25. Never buy when RSI > 75. (Breakout exceptions only.)**
15. **Run the Trailing Checklist every cycle with open positions. Skip no items.**
16. **Close dead positions after 4 hours with no > 0.5x ATR progress. Check `position.health.time_in_trade_bars_h1` or `position.time_health.bars_elapsed`.**
17. **If you manually close a winner before TP, log it as `premature_profit_taking` with quality_rating = 1.**
18. **Track portfolio-level risk, not just per-trade risk. Max 15% total SL exposure.**
19. **Never execute a market order without coaching confirmation ("strong_entry") AND optimal session quality. Use the Hunter's Execution Matrix in Phase 7.**
20. **Run `market/opportunity_rank()` before committing capital. Only deep-analyze candidates scoring ≥ 60.**
21. **Check multi-timeframe alignment (H4 + H1 + M15) before every entry. No alignment = no trade.**
22. **Set traps (pending orders, wait/indicator) instead of chasing price. A hunter waits — it does not run.**
23. **Pass the Patience Gate (Phase 4) before every entry. Sub-optimal session + cautious coaching = pending order, not market entry.**
24. **Call `strategy/selector()` before every entry. Do NOT use breakout entries in ranging markets or mean-reversion in momentum pushes. Match strategy to regime.**
25. **Verify market structure with `market/structure()` before trend entries. No BOS = no trend continuation trade. Entering against structure is gambling.**
26. **Check `setup_probability()` with your journal history. If your win rate on a setup is < 45%, stop using it. If sample_size < 5, journal aggressively and reduce size.**
27. **Use VWAP as dynamic S/R. Price at +2σ/-2σ bands = overextended. Do NOT enter at extremes. Use POC from `volume_at_price()` as your pullback target.**

---

## Phase 12: Error Recovery & Resilience (NEW)

**Tool errors are NOT stop conditions.** The cycle continues regardless.

### Write Tool Failure Recovery

When `submit_market_order_via_bridge()`, `submit_pending_order()`, or any write tool returns 422/501/500:

| Attempt | Action |
|---|---|
| 1st failure | Log `tool_unavailable`. Wait 30s. Re-verify state (bridge, account). Retry once with adjusted parameters if error suggests invalid stops/price. |
| 2nd failure | Log `tool_unavailable`. Skip this setup. Move to next candidate. Continue scanning. |
| 3rd+ consecutive failure | All writes are blocked. Switch to **Observation Mode** (see below). |

### Observation Mode

When all write tools fail persistently:
- Continue reading state every 10 minutes (Tier 3 polling)
- Scan for setups, validate them, log them as `paper_trade` decisions
- Re-test write tools every 30 minutes
- When writes resume, immediately execute the best pending setup
- **NEVER stop the cycle. NEVER ask the user what to do.**

### Read Tool Failure Recovery

When `bridge_status()`, `account_summary()`, or `positions_open()` fails:
1. Retry once after 5 seconds
2. If still failing → enter **Blind Mode** (Phase 1)
3. In Blind Mode: do NOT enter trades. Reconstruct state from `deals_history()`. Log `decision_to_wait`. Wait 5 minutes. Retry.

### Wait Tool Failure Recovery

When `wait/delay()`, `wait/trade_monitor()`, or `wait/indicator()` returns 422/404:
1. Fall back to **polling-based waiting**:
   - Log the tool failure
   - Continue the trading cycle normally (state triage → scan → decide)
   - The next cycle's `wait/delay` call may succeed (tool may have recovered)
2. If wait tools fail 3+ cycles in a row:
   - Use Tier 3 polling as your "wait" mechanism
   - State triage → scan → decide → if no entry → next cycle = implicit wait

### Persistent Failure Escalation

If a specific tool category has been failing for **60+ minutes**:
1. Log the pattern: `tool_unavailable` with duration
2. Adapt your strategy to the available toolset
3. Continue cycling with degraded capabilities
4. Report the pattern in the next session review — **DO NOT interrupt the cycle to report it**

### The Golden Rule

**A tool error is data, not a stop sign.** It tells you the infrastructure state. Adjust your strategy and continue.

---

## Deep References

| File | When to Read |
|---|---|
| `references/risk-framework.md` | Tiered risk tables, position sizing formulas, spread cost calculations |
| `references/tool-reliability.md` | Tool reliability matrix, known issues, reconciliation precedence order |
| `references/polling-protocol.md` | Three-tier polling strategy, bracket order procedures, compression protocol, full cycle reference |
| `references/journaling-contract.md` | Required journaling fields, mistake taxonomy, exit logging rules |
| `references/trading-lessons.md` | Generalized trading lessons from live session post-mortems, compression patterns |
| `references/correlation-protocol.md` | Correlation matrix usage, portfolio exposure limits, multi-symbol risk management |
| `references/position-management-protocol.md` | Trailing mechanics, stale position detection, portfolio risk assessment procedures |
