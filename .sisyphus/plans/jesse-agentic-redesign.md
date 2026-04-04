# Jesse Agent — Architecture Failure Analysis & Objective Definition

## 1. What We Built (The Problem)

We built a **deterministic state machine** and called it an "agentic AI." It is not.

### Current Architecture (Fake Agent)

```
START → wake_up → scan_markets → analyze_setups → decide → execute_trade → manage_positions → exit_reflect → update_memory → END
```

**Every cycle, every time, same path.** The LLM is called exactly once in the `decide` node with pre-fetched data and returns BUY/SELL/HOLD. The graph routes based on that single output.

### Why This Is Not Agentic

| What We Have | What An Agent Should Do |
|---|---|
| Fixed pipeline — same tools called every cycle | Dynamic tool selection — LLM chooses which MCP tools to call |
| LLM called once per cycle with pre-fetched data | LLM observes → thinks → calls tool → observes → repeats |
| No conversation — only `/command` responses | Natural language interaction — ask questions, give instructions |
| Agent can't adapt its information gathering | Agent decides what data it needs, fetches it, acts on it |
| Graph edges are hardcoded | Tool outputs determine next action |
| `agent_tools.py` (14 LangChain tools) — **never used** | Tools are the agent's hands — it picks and uses them |

### Dead Code

`src/mt5_mcp/autonomous/agent_tools.py` — 14 MCP tools wrapped as LangChain `@tool` decorated functions. **Never called by the graph.** Written for a ReAct agent that was never built.

---

## 2. The Objective

Build a **truly agentic AI trading system** where the LLM is the brain that:

### A. Autonomous Trading Mode (Background Loop)
- Wakes up on schedule, **chooses** what to do
- Decides which symbols to scan, which indicators to check, which timeframes to analyze
- Calls MCP tools **dynamically** based on what it finds
- Makes trade decisions with **self-articulated** risk management (SL, TP, position size — all reasoned from data)
- Logs every decision with reasoning
- Goes to sleep when conditions are unfavorable, wakes when they improve

### B. Conversational Mode (User Interaction)
- User can ask in natural language: "What's happening with XAUUSD?"
- Agent thinks → calls `trading_context("XAUUSD")` → calls `market_regime("XAUUSD")` → reads memory → responds
- User can give instructions: "Close half my EURUSD position"
- Agent thinks → calls `positions_open()` → finds position → calls `close_position(partial=0.5)` → confirms
- User can ask: "How am I doing this week?"
- Agent thinks → calls `performance_summary()` → calls `trading_reflect()` → responds with analysis

### C. Self-Management
- Circuit breakers still enforce hard limits (max losses, max daily trades)
- Dynamic sleep intervals based on market conditions
- Persistent memory (ChromaDB + SQLite) informs decisions
- The agent **decides** when to sleep, when to scan, when to trade — not a hardcoded graph

---

## 3. What Needs to Change

### REMOVE
- The entire `build_graph()` state machine pipeline (`src/mt5_mcp/autonomous/graph.py`)
- Individual node functions: `wake_up`, `scan_markets`, `analyze_setups`, `decide`, `execute_trade`, `manage_positions`, `exit_reflect`, `update_memory`, `sleep_node`
- Routing functions: `route_after_wake`, `route_after_decision`, `route_after_manage`
- The deterministic cycle logic

### KEEP
- `mcp_client.py` — 35 MCP tool wrappers with enhanced retry (just built)
- `agent_tools.py` — 14 LangChain `@tool` decorated functions (already written, never used)
- `circuit_breaker.py` — 5 circuit breakers
- `self_manage.py` — interval decision logic
- `semantic_memory.py` — ChromaDB vector store
- `consolidation.py` — pattern extraction
- `decay.py` — Ebbinghaus decay
- `telegram_bot.py` — Telegram interface (needs conversational handler added)
- `scheduler.py` — APScheduler (triggers agent cycles)
- `alerts.py` — Telegram notifications
- `llm_reasoning.py` — LLM reasoning layer (needs ReAct loop instead of single-call)

### BUILD (New Architecture)

**ReAct Agent Loop** — The core pattern:
```
1. Agent receives trigger (schedule tick OR user message)
2. LLM receives: system prompt + current context + available tools
3. LLM decides: call tool(s) OR respond with final answer
4. Tool results fed back to LLM
5. LLM decides again: call more tools OR respond
6. Repeat until LLM has enough information to act/respond
7. Log decision, update memory, schedule next wake
```

**Two Entry Points:**
```
┌─────────────────────┐    ┌──────────────────────┐
│  Schedule Trigger   │    │  Telegram Message    │
│  (autonomous cycle) │    │  (user conversation) │
└─────────┬───────────┘    └──────────┬───────────┘
          │                           │
          ▼                           ▼
┌─────────────────────────────────────────────┐
│           ReAct Agent Core                   │
│  ┌─────────────────────────────────────┐    │
│  │  LLM + 14 MCP Tools (LangChain)     │    │
│  │  Observes → Thinks → Calls Tool     │    │
│  │  Observes → Thinks → Calls Tool     │    │
│  │  Observes → Thinks → Acts/Responds  │    │
│  └─────────────────────────────────────┘    │
│         Circuit Breakers (hard limits)      │
│         Memory (ChromaDB + SQLite)          │
└─────────────────────────────────────────────┘
          │                           │
          ▼                           ▼
┌─────────────────┐        ┌──────────────────┐
│  Back to Sleep  │        │  Reply to User   │
│  (schedule next)│        │  (Telegram)      │
└─────────────────┘        └──────────────────┘
```

---

## 4. Technical Requirements

### LangGraph ReAct Pattern (Recommended)
- Use `create_react_agent` from LangGraph — NOT custom StateGraph
- Pass the 14 MCP tools from `agent_tools.py` as the tool set
- System prompt defines agent behavior, not hardcoded nodes
- Checkpointer for state persistence (SQLite)

### LLM Requirements
- Must support **function/tool calling** (not just chat completion)
- OpenAI-compatible format works
- Current setup: `qwen-proxy/coder-model` at `http://127.0.0.1:3000/v1`
- Need to verify: does qwen-proxy support tool calling?

### Telegram Integration
- Commands (`/scan`, `/status`, etc.) still work
- **Non-command messages** route to the ReAct agent as conversation
- Agent can include chart screenshots in responses
- Agent can execute trades based on natural language instructions

### What to Research
1. **LangGraph `create_react_agent`** — correct usage with custom tools
2. **Tool calling through qwen-proxy** — does the proxy support it, or need direct OpenAI/Ollama?
3. **LangGraph ReAct + MCP** — how other projects wire MCP tools into ReAct agents
4. **Headless OpenCode sessions** — could use OpenCode's own agent as the LLM brain via fixed session ID
5. **Alternative: raw OpenAI tool-calling API** — skip LangGraph, use `tools=[]` parameter directly

---

## 5. Success Criteria

The agent passes when:

1. **Autonomous Mode**: Agent wakes up, scans markets, calls multiple MCP tools per symbol (regime, context, bars, indicators), reasons over the data, and either trades or holds — all through LLM tool selection, not hardcoded pipeline.

2. **Conversational Mode**: User sends "What's the outlook on gold?" → agent calls trading tools → responds with analysis grounded in live data.

3. **Instruction Mode**: User sends "Close my EURUSD" → agent finds position → closes it → confirms.

4. **Risk Management**: Agent articulates SL, TP, position size from market data — no hardcoded values.

5. **Self-Management**: Agent decides when to sleep, when to scan, based on conditions — not a fixed graph.

---

## 6. Files Inventory

### To Delete
- `src/mt5_mcp/autonomous/graph.py` (entire file — state machine)
- `src/mt5_mcp/autonomous/nodes.py` (entire file — node implementations)

### To Modify Significantly
- `src/mt5_mcp/autonomous/llm_reasoning.py` → ReAct loop instead of single `llm_decide()`
- `src/mt5_mcp/autonomous/telegram_bot.py` → Add conversational message handler
- `apps/autonomous_agent/main.py` → Bootstrap ReAct agent instead of StateGraph

### To Keep As-Is
- `src/mt5_mcp/autonomous/mcp_client.py` (enhanced retry — keep)
- `src/mt5_mcp/autonomous/agent_tools.py` (14 tools — keep, these become the agent's hands)
- `src/mt5_mcp/autonomous/circuit_breaker.py`
- `src/mt5_mcp/autonomous/self_manage.py`
- `src/mt5_mcp/autonomous/semantic_memory.py`
- `src/mt5_mcp/autonomous/consolidation.py`
- `src/mt5_mcp/autonomous/decay.py`
- `src/mt5_mcp/autonomous/scheduler.py`
- `src/mt5_mcp/autonomous/alerts.py`
- `src/mt5_mcp/autonomous/telegram_bot.py` (extend, don't replace)

### To Create
- `src/mt5_mcp/autonomous/react_agent.py` — new ReAct agent core
- `src/mt5_mcp/autonomous/conversation.py` — conversation state management

---

## 7. Key Question for Research

**Should we use:**

| Option | Pros | Cons |
|---|---|---|
| **LangGraph ReAct** (`create_react_agent`) | Battle-tested, built-in tool loop, persistence | Depends on LangChain ecosystem, may be heavy |
| **Raw OpenAI tool-calling** | Simple, direct control, fewer deps | Must build loop + error handling manually |
| **Headless OpenCode session** | Already have it, uses our model, has skills | Experimental, needs fixed session ID, auth issues |
| **Custom ReAct loop** | Full control, minimal deps | Reinventing the wheel |

The answer depends on: (a) whether qwen-proxy supports tool calling, (b) whether we want to add more dependencies, (c) whether OpenCode headless sessions are viable.

---

## 8. Current Dependencies

```
langgraph==1.1.6          # Keep (for ReAct)
langchain-core==1.2.26    # Keep (for tool definitions)
langchain-openai==1.2.26  # Keep (for LLM client)
chromadb==1.5.5           # Keep (memory)
apscheduler==3.11.2       # Keep (scheduling)
httpx                     # Keep (MCP client)
python-telegram-bot       # Keep (already installed)
```

---

*Document created for handoff to research agent. The research agent should investigate the options in Section 7 and return a concrete implementation plan.*
