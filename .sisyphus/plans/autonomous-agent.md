# Implementation Plan: Autonomous 24/7 AI Trading Agent

> **Reference**: See `ARCHITECTURE.md` for full architecture design
> **Approach**: Build incrementally — Phase 1 (MVA) → Phase 2 (Memory) → Phase 3 (Production)
> **Constraint**: Reuse all existing MT5-MCP infrastructure; add only what's needed

---

## Phase 1: Minimal Viable Agent (Priority: HIGH)

### 1.1 — MCP HTTP Client

**File**: `src/mt5_mcp/autonomous/mcp_client.py`

**Purpose**: HTTP client that calls MT5-MCP server tools on port 8010.

**Implementation**:
- Async HTTP client using `httpx`
- Wraps all 52 MCP tool calls as Python methods
- Error handling with retries (3 attempts, exponential backoff)
- Timeout configuration (30s default)
- Connection pooling for efficiency

**Key methods to implement**:
```python
class MCPClient:
    # Market data
    async def get_bars(symbol, timeframe, count) → dict
    async def get_indicator(symbol, timeframe, indicator, **kwargs) → dict
    async def get_ticks(symbol, count) → dict
    async def symbol_info(symbol) → dict
    async def get_order_book(symbol) → dict

    # Analysis
    async def trading_decision_support(symbol, side) → dict
    async def trading_coach(symbol, side, **kwargs) → dict
    async def trading_context(symbol) → dict
    async def market_regime(symbol, timeframe) → dict
    async def volatility_profile(symbol, timeframe) → dict

    # Execution
    async def submit_market_order(**kwargs) → dict
    async def submit_pending_order(**kwargs) → dict
    async def close_position(position_id, volume) → dict
    async def modify_position_sl_tp(position_id, sl, tp) → dict

    # State
    async def account_summary() → dict
    async def positions_open() → list[dict]
    async def orders_pending() → list[dict]
    async def bridge_status() → dict

    # Memory
    async def trading_log_decision(**kwargs) → dict
    async def trading_reflect(**kwargs) → dict
    async def trading_insights(lookback_days) → dict

    # Validation
    async def validate_trade_setup(**kwargs) → dict
    async def calculate_position_size(**kwargs) → dict
```

**Acceptance Criteria**:
- [ ] All methods return parsed JSON responses
- [ ] Retry logic handles transient failures
- [ ] Timeout errors are raised clearly
- [ ] Can call MT5-MCP server and get valid responses

---

### 1.2 — LangGraph State Machine

**File**: `src/mt5_mcp/autonomous/graph.py`

**Purpose**: Define the 9-node LangGraph state machine with transitions and checkpointing.

**Implementation**:
- Define `AgentState` TypedDict with all fields
- Create StateGraph with 9 nodes
- Define conditional routing logic
- Configure SqliteSaver checkpointer
- Compile the graph

**File structure**:
```python
# graph.py
from typing import Annotated
from typing_extensions import TypedDict
from operator import add
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver

class AgentState(TypedDict):
    phase: str
    symbols: list[str]
    market_data: dict
    regime_data: dict
    news_context: list[dict]
    open_positions: list[dict]
    account_summary: dict
    confluence_scores: dict
    trade_decision: str
    trade_rationale: str
    emotional_state: str
    confidence: float
    recent_insights: str
    past_patterns: str
    semantic_memories: list[str]
    next_check_interval: int
    sleep_reason: str
    consecutive_losses: int
    daily_pnl: float
    cycle_count: int
    decision_log: Annotated[list, add]

def build_graph() -> CompiledStateGraph:
    """Build and return the compiled LangGraph state machine."""
    builder = StateGraph(AgentState)

    # Register nodes (imported from nodes.py)
    builder.add_node("wake_up", wake_up)
    builder.add_node("scan_markets", scan_markets)
    builder.add_node("analyze_setups", analyze_setups)
    builder.add_node("decide", decide)
    builder.add_node("execute_trade", execute_trade)
    builder.add_node("manage_positions", manage_positions)
    builder.add_node("exit_reflect", exit_reflect)
    builder.add_node("update_memory", update_memory)

    # Define edges
    builder.add_edge(START, "wake_up")
    builder.add_edge("wake_up", "scan_markets")
    builder.add_edge("scan_markets", "analyze_setups")
    builder.add_conditional_edges("analyze_setups", route_after_analysis)
    builder.add_conditional_edges("decide", route_after_decision)
    builder.add_edge("execute_trade", "manage_positions")
    builder.add_conditional_edges("manage_positions", route_after_manage)
    builder.add_edge("exit_reflect", "update_memory")
    builder.add_edge("update_memory", END)

    # Configure checkpointer
    checkpointer = SqliteSaver.from_conn_string(
        str(Path.home() / ".mt5-mcp" / "agent_state.db")
    )

    return builder.compile(checkpointer=checkpointer)
```

**Acceptance Criteria**:
- [ ] Graph compiles without errors
- [ ] SqliteSaver persists state to `~/.mt5-mcp/agent_state.db`
- [ ] Conditional routing works correctly
- [ ] Graph can be resumed after interruption

---

### 1.3 — Node Implementations

**File**: `src/mt5_mcp/autonomous/nodes.py`

**Purpose**: Implement all 9 LangGraph nodes with MCP client calls and business logic.

**Node specifications**:

#### `wake_up(state) → state`
- Call `account_summary` → update `account_summary` in state
- Call `bridge_status` → verify connection
- Call `positions_open` → update `open_positions` in state
- If bridge disconnected → set phase to SLEEP, set `next_check_interval=5`
- Increment `cycle_count`
- Return updated state

#### `scan_markets(state) → state`
- For each symbol in `symbols`:
  - Call `trading_decision_support(symbol, side="both")`
  - Call `market_regime(symbol, H1)`
  - Compute confluence score (0-100)
- Store results in `market_data`, `regime_data`, `confluence_scores`
- If no symbol has score > 60 → will route to HOLD decision
- Return updated state

#### `analyze_setups(state) → state`
- For top scoring symbol:
  - Call `get_bars(symbol, H1, 100)` and `get_bars(symbol, M15, 50)`
  - Call `get_indicator` for RSI(14), EMA(20), EMA(50), ATR(14)
  - Call `news_fetch(pools=["FINANCIAL_MARKETS"], limit=10)`
- Call `trading_insights(lookback_days=7)` → store in `recent_insights`
- Return updated state

#### `decide(state) → state`
- Build prompt from: market data + insights + SKILL.md rules
- Call LLM (GPT-4o) with: "Analyze setup and decide: BUY, SELL, or HOLD"
- If BUY/SELL:
  - Set `trade_decision`, `trade_rationale`, `confidence`
  - Call `trading_log_decision` with full reasoning
- If HOLD:
  - Set `trade_decision="HOLD"`
  - Determine `next_check_interval` based on conditions
- Return updated state

#### `execute_trade(state) → state`
- Call `validate_trade_setup` → pre-flight check
- Call `calculate_position_size` → risk-based sizing
- Call `submit_market_order_via_bridge` → execute
- Log trade in decision journal
- Return updated state

#### `manage_positions(state) → state`
- Call `positions_open` → check status
- For each position:
  - Check P&L vs alert thresholds
  - Check if trailing stop needed
- If position closed → set phase to EXIT_REFLECT
- If still open → set phase to SLEEP with appropriate interval
- Return updated state

#### `exit_reflect(state) → state`
- Call `performance_summary(days=1)` → today's P&L
- Call `trading_reflect` → analyze recent trades
- Update `consecutive_losses`, `daily_pnl`
- Return updated state

#### `update_memory(state) → state`
- If trade count % 10 == 0 → trigger consolidation (Phase 2)
- Update semantic memories (Phase 2)
- Apply memory decay (Phase 2)
- For Phase 1: just log that memory would be updated
- Return updated state

#### Routing functions:
```python
def route_after_analysis(state) -> str:
    max_score = max(state["confluence_scores"].values()) if state["confluence_scores"] else 0
    if max_score >= 60:
        return "decide"
    return "decide"  # Always decide; HOLD is a valid decision

def route_after_decision(state) -> str:
    if state["trade_decision"] in ("BUY", "SELL"):
        return "execute_trade"
    return "sleep"  # HOLD → sleep

def route_after_manage(state) -> str:
    if state.get("position_closed"):
        return "exit_reflect"
    return "sleep"  # Still managing → sleep
```

**Acceptance Criteria**:
- [ ] Each node calls correct MCP tools
- [ ] Each node returns properly updated state
- [ ] Error handling in each node (graceful degradation)
- [ ] Nodes are idempotent (safe to retry)

---

### 1.4 — Scheduler

**File**: `src/mt5_mcp/autonomous/scheduler.py`

**Purpose**: APScheduler configuration with dynamic interval control.

**Implementation**:
```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

class AgentScheduler:
    def __init__(self, graph, mcp_client):
        self.scheduler = AsyncIOScheduler()
        self.graph = graph
        self.mcp_client = mcp_client
        self.current_interval = 15  # default: 15 minutes

    async def trigger_cycle(self):
        """Execute one agent cycle."""
        config = {"configurable": {"thread_id": "trading_agent_main"}}
        initial_state = {
            "phase": "WAKE",
            "cycle_count": 0,
            "symbols": ["BTCUSD", "ETHUSD", "XAUUSD"],
            "next_check_interval": self.current_interval,
            # ... other defaults
        }
        result = await self.graph.ainvoke(initial_state, config=config)
        # Update interval based on agent's decision
        self.current_interval = result.get("next_check_interval", 15)
        self._reschedule()

    def _reschedule(self):
        """Reschedule with new interval."""
        job = self.scheduler.get_job("heartbeat")
        if job:
            self.scheduler.reschedule_job(
                "heartbeat",
                trigger=IntervalTrigger(minutes=self.current_interval)
            )

    def add_builtin_jobs(self):
        # Heartbeat (dynamic interval)
        self.scheduler.add_job(
            self.trigger_cycle,
            IntervalTrigger(minutes=self.current_interval),
            id="heartbeat",
            max_instances=1,
            misfire_grace_time=60
        )
        # Pre-market scan
        self.scheduler.add_job(
            self.pre_market_scan,
            CronTrigger(hour=7, minute=45, day_of_week="mon-fri", timezone="Asia/Calcutta"),
            id="pre_market_scan"
        )
        # End-of-day review
        self.scheduler.add_job(
            self.eod_review,
            CronTrigger(hour=22, minute=0, timezone="Asia/Calcutta"),
            id="eod_review"
        )

    def start(self):
        self.scheduler.start()

    def shutdown(self):
        self.scheduler.shutdown(wait=False)
```

**Acceptance Criteria**:
- [ ] Scheduler starts and triggers cycles at correct intervals
- [ ] Dynamic interval rescheduling works
- [ ] Cron jobs fire at correct times
- [ ] Graceful shutdown on SIGINT/SIGTERM

---

### 1.5 — Self-Management Logic

**File**: `src/mt5_mcp/autonomous/self_manage.py`

**Purpose**: Agent decides its own sleep/wake intervals based on market conditions.

**Implementation**:
```python
INTERVAL_DECISION_TABLE = {
    "no_positions_no_setups":        (60,  "Quiet market, no edge"),
    "no_positions_good_setups":      (15,  "Setups forming, watch closely"),
    "position_open":                  (5,  "Active trade — tight monitoring"),
    "position_near_tp_sl":            (1,  "Critical — near exit levels"),
    "consecutive_losses_2":           (30,  "Cool-off period"),
    "consecutive_losses_3_plus":      (120, "Extended cool-off"),
    "daily_loss_exceeded":            (1440,"Stop trading for 24h"),
    "weekend":                        (120, "Low volume — minimal checks"),
    "asian_session":                  (30,  "Lower volatility"),
    "london_ny_overlap":              (10,  "Highest volume — active"),
    "spread_atr_ratio_high":          (60,  "Poor conditions, wait"),
    "bridge_disconnected":            (5,   "Reconnect check"),
}

def compute_next_interval(state: dict) -> tuple[int, str]:
    """Determine next check interval and reason."""
    # Check circuit breakers first
    if state.get("daily_loss_exceeded"):
        return INTERVAL_DECISION_TABLE["daily_loss_exceeded"]

    if state.get("consecutive_losses", 0) >= 3:
        return INTERVAL_DECISION_TABLE["consecutive_losses_3_plus"]

    if state.get("consecutive_losses", 0) >= 2:
        return INTERVAL_DECISION_TABLE["consecutive_losses_2"]

    # Active trade management
    if state.get("open_positions"):
        # Check if any position is near TP/SL
        if any(p.get("near_exit") for p in state["open_positions"]):
            return INTERVAL_DECISION_TABLE["position_near_tp_sl"]
        return INTERVAL_DECISION_TABLE["position_open"]

    # No positions — check market conditions
    if state.get("trade_decision") == "HOLD":
        if state.get("confluence_scores") and max(state["confluence_scores"].values()) > 50:
            return INTERVAL_DECISION_TABLE["no_positions_good_setups"]
        return INTERVAL_DECISION_TABLE["no_positions_no_setups"]

    # Default
    return (15, "Default interval")

def save_wake_plan(state: dict, interval: int, reason: str):
    """Save wake plan to JSON file for crash recovery."""
    plan = {
        "next_wake": (datetime.utcnow() + timedelta(minutes=interval)).isoformat(),
        "check_interval_minutes": interval,
        "reason": reason,
        "active_positions": len(state.get("open_positions", [])),
        "consecutive_losses": state.get("consecutive_losses", 0),
        "daily_pnl": state.get("daily_pnl", 0.0),
        "last_action": state.get("trade_decision", "SLEEP"),
        "timestamp": datetime.utcnow().isoformat(),
    }
    path = Path.home() / ".mt5-mcp" / "agent_wake_plan.json"
    path.write_text(json.dumps(plan, indent=2))
```

**Acceptance Criteria**:
- [ ] Correct interval selected for each condition
- [ ] Wake plan saved to JSON after each cycle
- [ ] Weekend/session awareness works
- [ ] Circuit breaker intervals override all others

---

### 1.6 — Circuit Breakers

**File**: `src/mt5_mcp/autonomous/circuit_breaker.py`

**Purpose**: Safety mechanisms that prevent catastrophic losses.

**Implementation**:
```python
@dataclass
class CircuitBreakerState:
    consecutive_losses: int = 0
    daily_loss: float = 0.0
    daily_trades: int = 0
    open_positions: int = 0
    bridge_failures: int = 0
    crash_count: int = 0
    crash_window_start: datetime | None = None

    MAX_CONSECUTIVE_LOSSES = 3
    MAX_DAILY_LOSS_PERCENT = 0.05  # 5% of equity
    MAX_DAILY_TRADES = 10
    MAX_OPEN_POSITIONS = 3
    MAX_BRIDGE_FAILURES = 3
    MAX_CRASHES_PER_HOUR = 5

class CircuitBreaker:
    def __init__(self, equity: float):
        self.state = CircuitBreakerState()
        self.equity = equity

    def check_all(self) -> tuple[bool, str | None]:
        """Check all circuit breakers. Returns (ok, reason_if_blocked)."""
        if self.state.consecutive_losses >= self.state.MAX_CONSECUTIVE_LOSSES:
            return False, f"Cool-off: {self.state.consecutive_losses} consecutive losses"

        if self.state.daily_loss >= self.equity * self.state.MAX_DAILY_LOSS_PERCENT:
            return False, f"Daily loss limit: ${self.state.daily_loss:.2f}"

        if self.state.daily_trades >= self.state.MAX_DAILY_TRADES:
            return False, f"Max daily trades: {self.state.MAX_DAILY_TRADES}"

        if self.state.open_positions >= self.state.MAX_OPEN_POSITIONS:
            return False, f"Max open positions: {self.state.MAX_OPEN_POSITIONS}"

        if self.state.bridge_failures >= self.state.MAX_BRIDGE_FAILURES:
            return False, "Bridge disconnected — stop trading"

        return True, None

    def record_trade(self, pnl: float):
        """Record a completed trade."""
        self.state.daily_trades += 1
        self.state.daily_loss = max(0, self.state.daily_loss - pnl)  # losses add up
        if pnl < 0:
            self.state.consecutive_losses += 1
        else:
            self.state.consecutive_losses = 0

    def record_bridge_failure(self):
        self.state.bridge_failures += 1

    def reset_daily(self):
        """Reset daily counters (call at start of new day)."""
        self.state.daily_loss = 0.0
        self.state.daily_trades = 0
        self.state.bridge_failures = 0
```

**Acceptance Criteria**:
- [ ] All 6 circuit breakers trigger correctly
- [ ] Trade recording updates counters
- [ ] Daily reset works
- [ ] Clear error messages for each trigger

---

### 1.7 — Main Entrypoint

**File**: `apps/autonomous_agent/main.py`

**Purpose**: Application entry point — initializes all components and runs the agent.

**Implementation**:
```python
#!/usr/bin/env python3
"""Autonomous 24/7 AI Trading Agent — Main Entry Point.

Usage:
    poetry run python -m apps.autonomous_agent.main
"""

import asyncio
import signal
import logging
from pathlib import Path

from mt5_mcp.autonomous.mcp_client import MCPClient
from mt5_mcp.autonomous.graph import build_graph
from mt5_mcp.autonomous.scheduler import AgentScheduler
from mt5_mcp.autonomous.circuit_breaker import CircuitBreaker

logger = logging.getLogger("autonomous_agent")

async def main():
    # Initialize components
    mcp_client = MCPClient(base_url="http://127.0.0.1:8010")
    graph = build_graph()
    circuit_breaker = CircuitBreaker(equity=200.0)  # Will load from account_summary

    scheduler = AgentScheduler(graph=graph, mcp_client=mcp_client)
    scheduler.add_builtin_jobs()

    # Graceful shutdown
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: scheduler.shutdown())

    logger.info("Autonomous Trading Agent starting...")
    logger.info(f"MT5-MCP Server: http://127.0.0.1:8010")
    logger.info(f"Symbols: BTCUSD, ETHUSD, XAUUSD")

    scheduler.start()

    # Keep running
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        scheduler.shutdown()

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    asyncio.run(main())
```

**Acceptance Criteria**:
- [ ] Agent starts and connects to MT5-MCP
- [ ] Scheduler triggers first cycle
- [ ] Graceful shutdown on Ctrl+C
- [ ] All components initialized correctly

---

## Phase 2: Memory Enhancement (Priority: MEDIUM)

### 2.1 — Semantic Memory (ChromaDB)

**File**: `src/mt5_mcp/autonomous/semantic_memory.py`

**Purpose**: Vector store for learned trading patterns.

**Implementation**:
- Initialize ChromaDB persistent client at `~/.mt5-mcp/chroma/`
- Collection: `trading_patterns`
- Methods: `add_pattern()`, `search_patterns()`, `get_active_rules()`
- Metadata filtering by symbol, regime, date range

### 2.2 — Consolidation Engine

**File**: `src/mt5_mcp/autonomous/consolidation.py`

**Purpose**: Extract semantic patterns from episodic trade data every 10 trades.

**Implementation**:
- Fetch last 10 trades from SQLite journal
- Compute statistics (win rate by regime, symbol, emotion)
- LLM analysis to extract actionable rules
- Store new patterns in ChromaDB
- Update confidence scores based on sample size

### 2.3 — Memory Decay

**File**: `src/mt5_mcp/autonomous/decay.py`

**Purpose**: Importance-weighted forgetting for stale patterns.

**Implementation**:
- Ebbinghaus decay formula
- Pruning threshold at 0.1
- Importance multipliers for large wins/losses
- Configurable retention periods by memory type

---

## Phase 3: Production Hardening (Priority: MEDIUM)

### 3.1 — Docker Deployment

**File**: `Dockerfile.autonomous`

**Purpose**: Containerized deployment for 24/7 operation.

### 3.2 — Health Endpoint

**File**: `apps/autonomous_agent/health.py`

**Purpose**: FastAPI health check endpoint for monitoring.

### 3.3 — Telegram Alerts

**File**: `src/mt5_mcp/autonomous/alerts.py`

**Purpose**: Push notifications for trades, errors, and circuit breaker events.

---

## Dependencies to Add

```bash
poetry add langgraph langchain-openai apscheduler chromadb httpx
```

---

## Testing Strategy

1. **Unit tests**: Each node function in isolation (mock MCP client)
2. **Integration tests**: Full graph execution with test MCP server
3. **End-to-end**: Run agent in demo mode for 24 hours, verify:
   - No crashes
   - Proper checkpointing
   - Correct interval adjustments
   - Circuit breaker triggers

---

*Plan version: 1.0 | Last updated: April 4, 2026*
