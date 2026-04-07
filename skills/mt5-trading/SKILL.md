---
name: mt5-trading
description: Use when analyzing markets or operating MetaTrader 5 through mt5-mcp, especially for market scans, order entry, position management, journaling, trade review, or any request to act as a disciplined trading agent on a demo/live/shared MT5 account.
---

# MT5 Trading — Deterministic Agent Playbook

This skill is the operating manual for AI agents trading through MT5-MCP.

Its core lesson from real trading is simple:

**Your biggest risk is not bad chart reading. It is acting in an unreliable, shared, partially observable environment without reconciling state first.**

Future agents must trade like deterministic operators, not optimistic chatbots.

---

## Legacy From Today's Trading

Today's session produced two kinds of truth:

### What worked
- Regime-aware analysis improved entry quality.
- ATR-based stops and targets were directionally correct.
- Trailing profitable trades locked gains.
- BTC breakout capture worked when the thesis and structure matched.
- Logging lessons on exits created reusable intelligence.

### What failed
- Shared-account activity polluted account-wide P&L and attribution.
- Duplicate or repeated order placement happened when existing orders were not reconciled first.
- Bridge and tool outputs were sometimes contradictory:
  - `bridge_status` looked disconnected while some actions still worked
  - `positions_open()` returned empty while margin/profit suggested exposure
  - `orders_pending()` changed before fills/exits were fully obvious
  - `resources/market/wait_for_price` and `resources/positions/monitor` often errored or lagged
- Modifying orders without re-reading state could lead to missing or altered SL/TP assumptions.
- Minimum lot size and spread made some trades mathematically unfit for a small account, but trades were still forced.
- Journaling was incomplete enough that performance learning could have been misleading.

This skill exists to preserve the gains and eliminate those failures.

---

## Core Operating Law

**Before every side effect: observe → reconcile → act → verify → journal.**

If you skip any step, you are no longer trading systematically.

---

## When to Use

Use this skill when you need to:
- scan markets with mt5-mcp tools
- decide whether to buy, sell, wait, or cancel
- submit market, limit, stop, or bracket-style orders
- manage open positions with stops, targets, or trailing logic
- reconcile conflicting MT5 state
- review trading performance or improve future trading behavior

Do **not** use this skill for:
- pure coding tasks unrelated to MT5 trading operations
- narrative market commentary without trading intent
- discretionary gambling without risk sizing or journaling

---

## Phase 0: Session Claim and Ownership

Before analyzing a market, claim your session.

### Create session identity
Maintain these values for the entire session:
- `session_id`
- `strategy_id`
- `intent_id` (per trade intent)
- `idempotency_key` (per submission, prevents duplicates)

### Maintain a local session ledger
Track, in your own notes/logs, every trade you intended to create:

| Field | Why it matters |
|---|---|
| `session_id` | separates this run from prior runs |
| `strategy_id` | groups trades by strategy |
| `intent_id` | required for market-order idempotency |
| `idempotency_key` | prevents duplicate submissions at the broker level |
| `symbol / side / kind` | needed for reconciliation |
| `planned entry / sl / tp / volume` | needed to detect duplicates |
| `decision_id` | links entry and exit journaling |
| `owned order_id / position_id` | authoritative ownership once known |

### Shared-account rule
Assume the account may contain foreign activity.

Treat any order, position, or realized P&L as **foreign** until you can attribute it to your session ledger by one or more of:
- exact `order_id` or `position_id` you created
- matching `intent_id` / `strategy_id`
- exact symbol + side + kind + price + volume + timestamp window
- explicit journal entry created by you

**Never learn from unlabeled P&L as if it were yours.**

---

## Phase 1: Connectivity and State Triage

Do this at the start of every cycle:

```text
1. bridge_status()
2. account_summary()
3. orders_pending()
4. positions_open()
5. deals_history(days=1, limit=20)
```

### Healthy mode
You may trade normally only when all are true:
- account fields are populated
- market data tools return coherent values
- pending orders and positions reconcile with account margin/profit
- post-action verification is behaving normally

### Degraded mode
You are in degraded mode when any of these happen:
- `bridge_status().connected` is false but some reads still work
- `positions_open()` is empty while `account_summary().margin > 0` or floating `profit != 0`
- a tool returns empty arrays or nulls unexpectedly
- `wait_for_price` or `positions_monitor` errors repeatedly
- order or position state changes are delayed or contradictory

In degraded mode:
- keep reconciling
- reduce action frequency
- prefer read-only analysis or low-frequency management
- do **not** stack new orders on uncertainty
- verify every write with multiple state reads

### Blind mode
You are blind when you cannot trust current state.

Examples:
- account summary null/empty
- market data stale or contradictory
- you cannot determine whether you have exposure

In blind mode:
- do **not** enter new trades
- do **not** assume you are flat
- use account, deals, and pending orders to reconstruct what happened
- log `decision_to_wait` with the reason

### Important correction to stale guidance
Old rule: “If the bridge is disconnected, stop. Nothing works without it.”

New rule:
- `bridge_status()` is a **health hint**, not sole truth.
- Treat actual account/order/deal reconciliation as the authoritative signal.

---

## Phase 2: Authoritative State Reconciliation

Use this precedence order when tool outputs disagree:

| Source | Best use | Do not assume |
|---|---|---|
| `account_summary()` | whether exposure exists at all | it identifies which position is yours |
| `orders_pending()` | pending-order truth | disappearance always means cancellation succeeded |
| `positions_open()` | open-position details when populated | empty list means flat |
| `deals_history()` | authoritative fills and exits | missing newest deal means no fill yet |
| `bridge_status()` | health and trade-allowed context | false means execution is impossible |
| `resources/*` wait tools | convenience alerts | they are reliable enough to be your only watcher |

### Reconciliation loop
Before and after every write:

1. Snapshot `account_summary()`
2. Snapshot `orders_pending()`
3. Snapshot `positions_open()`
4. If anything is unclear, check `deals_history()`
5. Compare the observed state to your ledger
6. Only then decide whether an action is still needed

### Contradiction rules

If `positions_open()` is empty **and** account margin is nonzero:
- assume an open position exists or just closed
- check `deals_history()` immediately
- do not open a new trade on the same symbol until reconciled

If an order disappears from `orders_pending()`:
- do not assume success or cancellation
- check `deals_history()` and `account_summary()` before acting

If a modify/cancel action says success:
- re-read state before trusting it

---

## Phase 3: Market Discovery

Only look for trades after ownership and state are reconciled.

### First-pass scan

```text
market_scan(symbols=[...], timeframe="H1")
```

Use it to shortlist symbols with:
- clear regime
- acceptable ATR
- acceptable spread
- active movement near meaningful levels

### Deep dive
Prefer batched decision tools over assembling many fragile indicator calls.

Primary tools:
- `trading_decision_support(...)`
- `market_regime(...)`
- `volatility_profile(...)`
- `trading_context(...)`

Secondary tools:
- `get_indicator(...)`
- `support_resistance(...)`

### Tool reliability note
Observed and documented behavior shows:
- `trading_decision_support(...)` is often more robust than many sequential indicator calls
- some indicator requests may return empty/zero or fail on some combinations
- treat an empty indicator result as “unverified,” not as market truth

---

## Phase 4: Trade Viability Gate

Do **not** trade because a chart looks interesting. Trade only if the setup is viable.

All of these must pass:

1. **State is attributable** — you know what is yours and what is not.
2. **Regime matches the tactic**
   - trend → pullback or continuation
   - range → extremes only
   - compression → breakout preparation
3. **Risk:reward is at least 2:1**
4. **No duplicate intent exists**
5. **Single-trade risk is within budget**
6. **Minimum lot size still fits the risk budget**
7. **Spread cost is acceptable relative to ATR and planned risk**
8. **No major scheduled event invalidates the setup**
9. **Confidence is explicit, not implied**
10. **You can explain why this trade is better than waiting**

### Minimum-lot viability rule
If `calculate_position_size(...)` implies the broker minimum lot would exceed your allowed risk,
**skip the trade**.

Do not “just trade the minimum” when it violates risk.

### Spread viability rule
If spread cost meaningfully destroys the trade thesis, skip.

Practical red flags:
- spread consumes a large share of the planned stop
- spread/ATR is abnormally high
- spread alone removes the 2:1 structure

### Duplication gate
Before placing any order, check whether a materially identical order already exists.

For pending orders, compare:
- symbol
- side
- order kind
- price (or tight tolerance)
- volume
- SL / TP

If an equivalent order already exists, do **not** place another one.

### Bracket limit
Never keep more than **one active bracket pair per symbol**.

---

## Phase 5: Position Sizing

Use:

```text
calculate_position_size(...)
validate_trade_setup(...)
```

### Risk budget rules
- Normal: 1-2% of attributable session equity
- After 2 consecutive owned losses: 0.5-1%
- After 3 consecutive owned losses: reduce size by 50% or stop
- After 4 consecutive owned losses: stop opening new trades

### Hard rule
If the computed size or minimum tradable size breaks your risk budget, there is no trade.

### What to log before entry
At minimum:
- intended risk in % and account currency
- stop distance
- TP distance
- spread context
- why this size is acceptable

---

## Pre-Trade Checklist (MANDATORY)

Before submitting **ANY** order (market, pending, or bracket), you MUST complete all three steps below. Skipping any step is a process failure.

### 1. Validate Trade Setup
Call `validate_trade_setup` with your planned trade parameters:
- symbol, side (buy/sell), order_kind (market/limit/stop)
- volume_lots, entry_price (for limit/stop, null for market), sl, tp

This checks against broker constraints (stopsLevel, min_volume, max_volume, margin requirements).
If `valid=false`, **DO NOT submit the order**. Review the `errors` array and fix violations.

### 2. Calculate Position Size
Call `calculate_position_size` with:
- symbol, entry_price, stop_loss_price, risk_percent (1-3% of equity per trade)

This computes the optimal lot size using a fixed-fractional risk model.
**DO NOT use fixed lot sizes** (e.g., always 0.01). Always size based on SL distance and risk %.

### 3. Coach Review (Strongly Recommended)
Call `trading/coach` with your planned trade to get advisory feedback:
- symbol, side, sl_distance_points, tp_distance_points
- Include regime, atr_value, rsi if available

Review warnings and recommendations. If `confluence_score < 3`, reconsider the trade.

### Mandatory Workflow

```
analyze_market() → regime + ATR + support/resistance
validate_trade_setup(...) → check broker constraints (MUST pass valid=true)
calculate_position_size(...) → compute optimal lot size from risk % and SL distance
trading/coach(...) → get advisory feedback (review warnings)
submit_order(...) → execute with validated, sized parameters
```

⚠️ **NEVER submit an order without first calling `validate_trade_setup` and `calculate_position_size`. Fixed lot sizing (e.g., 0.01) is deprecated and risks over/under-exposure.**

---

## Phase 6: Execution

### Market orders
Use `submit_market_order_via_bridge(...)` when timing matters.

Mandatory rules:
- supply a unique `intent_id`
- verify the returned response
- reconcile immediately afterward using state reads

### Pending orders
Use `submit_pending_order(...)` for pullbacks or breakout triggers.

⚠️ **NEVER submit without first calling `validate_trade_setup(order_kind="limit"/"stop")` and `calculate_position_size(...)`.**

Mandatory rules:
- verify the order exists in `orders_pending()` after submission
- store the `order_id` in your ledger

### Bracket-style execution
For compression or breakout setups, you may use paired stop orders or `place_bracket_order(...)`.

**Critical correction:** MT5 does **not** give you automatic OCO behavior here.

When one leg fills:
- manually identify the orphan leg in `orders_pending()`
- cancel it
- verify cancellation actually happened

### After every submission
Run this exact discipline:

```text
1. account_summary()
2. orders_pending()
3. positions_open()
4. deals_history(days=1, limit=10)
```

Ask:
- Did my order appear where expected?
- Did a position open?
- Did the broker reject or alter anything?
- Does the observed state match my intent?

If not, do not keep trading as if execution succeeded.

---

## Phase 7: Modification and Management

### Tool split
- `modify_order(...)` is for **pending** orders only
- `modify_position_sl_tp(...)` is for **open** positions only

Do not mix them.

### Re-read after every modify
Observed failure mode: modifying pending orders can lead to unexpected state assumptions.

After any modify:
- re-read `orders_pending()` or `positions_open()`
- confirm entry, SL, TP, and volume persisted exactly as intended

### Trailing profits
When a trade moves in your favor:
- move to breakeven only after the move is real, not just noise
- trail only in the favorable direction
- never widen a stop loss

### If management tools disagree with account state
- trust the reconciliation loop over any single endpoint
- if you cannot confirm ownership or current exposure, stop adding new risk

---

## Phase 8: Exit and Closeout

Use TP/SL, trailing exits, or manual closes.

On every exit, you must determine:
- whether this was your trade
- whether the exit was manual, stop, target, or foreign interference
- realized P&L for the owned trade

### Mandatory post-exit update
Update the original decision with:
- `outcome`
- `pnl`
- `exit_price`
- `lesson_learned`
- `quality_rating`
- `mistake_category` when applicable

If you fail to do this, the trade cannot teach future agents anything.

---

## Phase 9: Journaling Contract

Entry logging is mandatory.
Exit logging is mandatory.
Waiting decisions are often worth logging.

### Required fields for every owned entry
- `session_id`
- `symbol`
- `side`
- `action`
- `entry_price`
- `sl`
- `tp`
- `volume_lots`
- `regime`
- `atr_value`
- `rsi_value` when relevant
- `indicators_considered`
- `confidence_level`
- `model_justification`
- `emotional_self_report`
- `alternatives_considered`
- `risk_assessment`

### Required fields for every owned exit
- `decision_id`
- `exit_price`
- `pnl`
- `outcome`
- `lesson_learned`
- `quality_rating`
- `mistake_category` when applicable

### Journaling quality rules
- `indicators_considered` must never be null if you used indicators
- `mistake_category` should be populated on preventable losses or process failures
- `emotional_self_report` should be honest; “calm” is not mandatory
- if a trade belongs to another system, do **not** log it as yours

---

## What To Pay Attention To

Always pay attention to:
- nonzero account margin with empty `positions_open()`
- disappearing orders
- fills or exits in `deals_history()` that were not yet reflected elsewhere
- minimum lot size versus risk budget
- spread cost versus planned stop and ATR
- duplicate pending orders on the same symbol
- whether SL/TP survived a modify action
- whether realized P&L belongs to your session or another actor
- repeated `Error:` responses from long-poll tools
- consecutive owned losses and risk escalation

---

## What Not To Pay Attention To

Do **not** let these distract or mislead you:
- account-wide realized P&L from foreign/manual/other-agent trades
- a single tool response when other state sources contradict it
- stale static heuristics when live spread/ATR says the setup is invalid
- short-term floating noise if your reconciliation and risk are sound
- your prior narrative if current market data disproves it

---

## What You Must Do

1. Reconcile state before every side effect.
2. Track ownership explicitly.
3. Use risk sizing every time.
4. Skip trades that fail minimum-lot or spread viability.
5. Prevent duplicate orders.
6. Re-read state after every submit, modify, cancel, or close.
7. Journal entries and exits completely.
8. Treat bridge/tool contradictions as a process problem, not a minor nuisance.

## What You Must Not Do

1. Do not assume an empty positions list means you are flat.
2. Do not attribute foreign P&L to your own process.
3. Do not place the same bracket or pending order repeatedly.
4. Do not widen stops.
5. Do not keep trading through blind-state conditions.
6. Do not force minimum lot sizes when they violate risk.
7. Do not trust a successful response without post-action verification.
8. Do not let journaling become optional.

---

## Mistake Taxonomy

Use these categories when logging preventable failures:

| Category | Definition | Prevention |
|---|---|---|
| `duplicate_intent` | Same trade idea submitted more than once | reconcile pending orders before new submission |
| `foreign_pnl_confusion` | Learned from account-wide P&L not owned by session | maintain ownership ledger |
| `bridge_blindness` | Kept trading while state was contradictory | enter degraded/blind mode |
| `journal_incomplete` | Entry or exit missing required fields | enforce journaling contract |
| `min_lot_violation` | Took trade even though minimum size broke risk budget | skip trade |
| `spread_ignored` | Spread cost invalidated setup | run viability gate |
| `sl_too_tight` | Stop inside normal noise | anchor to ATR and structure |
| `counter_regime` | Trade fought the detected regime | align tactic with regime |
| `lost_sl_tp_on_modify` | Modification changed assumptions without verification | re-read state after every modify |
| `premature_exit` | Exited before structure invalidated | predefine exit logic |
| `overtrading` | Kept entering because market was active, not because edge existed | log `decision_to_wait` |

---

## Reliability Notes for Specific Tools

### Prefer first
- `trading_decision_support(...)` for batched analysis
- `market_scan(...)` for shortlist discovery
- `deals_history(...)` for authoritative fills/exits
- `account_summary(...)` for exposure detection

### Use carefully
- `positions_open()` — useful when populated, not authoritative when empty
- `wait_for_price` / `positions_monitor` — helpful when they work, but they may error or lag
- `modify_order(...)` / `modify_position_sl_tp(...)` — verify after every call

### Critical execution notes
- `validate_trade_setup(...)` checks broker mechanics, not whether the setup is actually good
- `place_bracket_order(...)` does not give automatic OCO behavior
- `submit_market_order_via_bridge(...)` should be treated as submitted only after reconciliation confirms the result

---

## Quick Reference: Daily Cycle

```text
START OF CYCLE
1. bridge_status()
2. account_summary()
3. orders_pending()
4. positions_open()
5. deals_history()
6. Reconcile ownership and contradictions

ONLY IF STATE IS TRUSTWORTHY
7. market_scan()
8. trading_decision_support() / market_regime() / volatility_profile()
9. Run viability gate
10. calculate_position_size()
11. validate_trade_setup()
12. submit order
13. Re-read state immediately
14. trading_log_decision(entry)

WHILE IN TRADE
15. Reconcile exposure repeatedly
16. Manage stops/targets carefully
17. Never widen stop

AT EXIT
18. Confirm exit via deals_history/account state
19. trading_log_decision(exit update)
20. Extract lesson and mistake category
```

---

## Example: Reconcile-Then-Act Session

```text
# 1. Reconcile current state
bridge_status()
account_summary()
orders_pending()
positions_open()
deals_history(days=1, limit=20)

# 2. Detect contradiction
positions_open() -> []
account_summary().margin -> nonzero

# 3. Enter degraded mode
# Do not place new trades yet.
# Check deals_history and pending orders to determine if a trade just filled or closed.

# 4. Once state is attributable, scan for opportunity
market_scan(symbols=["XAUUSD", "EURUSD", "BTCUSD"], timeframe="H1")
trading_decision_support(symbol="BTCUSD", side="buy", sl_distance_points=500, tp_distance_points=1000)

# 5. Run viability gate
calculate_position_size(...)
validate_trade_setup(...)

# 6. Submit only if no duplicate order already exists
submit_market_order_via_bridge(... intent_id="btc-breakout-2026-04-06-01" ...)

# 7. Verify immediately
account_summary()
orders_pending()
positions_open()
deals_history(days=1, limit=10)

# 8. Log entry
trading_log_decision(...)

# 9. On exit, update the same decision
trading_log_decision(decision_id="...", action="exit", pnl=..., outcome="win", lesson_learned="...", quality_rating=4)
```

---

## Non-Negotiable Rules

1. **Reconcile before every action.**
2. **Never trade unattributable account state as if it were yours.**
3. **Never duplicate an existing intent.**
4. **Never force a trade below viability thresholds.**
5. **Never widen a stop loss.**
6. **Never skip exit journaling.**
7. **Never trust a single endpoint when multiple endpoints disagree.**
8. **When blind, stop entering.**

---

## Key Lessons Preserved for Future Generations

1. Good analysis is not enough. Operational discipline determines survival.
2. Shared accounts distort learning unless ownership is explicit.
3. In MT5-MCP, empty arrays and contradictory states must be treated as a first-class failure mode.
4. The best trade is often the trade you skip because minimum size, spread, or uncertainty makes it invalid.
5. Trailing winners is useful; forcing losers or duplicating orders is destructive.
6. A future agent should inherit this mindset immediately: **be precise, attributable, idempotent, and skeptical of unverified state.**
