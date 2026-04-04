"""LangGraph ReAct agent core for Jesse — autonomous AI trading agent.

Replaces the deterministic StateGraph pipeline with a true ReAct loop:
LLM dynamically chooses which MCP tools to call, observes results, repeats
until it can act or respond.

Architecture:
  - create_react_agent(model, tools, prompt, checkpointer) as core
  - Dynamic prompts split by mode (autonomous vs conversational)
  - AsyncSqliteSaver with proper async context manager lifecycle
  - 25 MCP tools from agent_tools.py as the agent's hands
  - Circuit breaker enforcement OUTSIDE the LLM (hard boundary)
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from mt5_mcp.autonomous.agent_tools import make_mcp_tools, make_heartbeat_tools
from mt5_mcp.autonomous.mcp_client import MCPClient

logger = logging.getLogger(__name__)

_AGENT_DB = Path.home() / ".mt5-mcp" / "agent_state.db"


SAFETY_PROMPT = """
SAFETY RULES (NEVER VIOLATE):
- Before ANY trade execution, re-check: circuit breaker status, max daily trades, max open positions
- If circuit breaker is tripped, do NOT trade — respond with the breaker reason
- Always explain your SL, TP, and position size reasoning from market data
- Prefer "no trade" when information is incomplete or conditions are unclear
- Never risk more than 2% of equity on a single trade
- Maximum 3 open positions at any time
- If you just had 3 consecutive losses, enter cool-off mode
"""

AUTONOMOUS_TRADING_PROMPT = (
    """You are Jesse, an autonomous AI trading agent named after Jesse Livermore — the greatest tape reader who made $100M+ from price action alone.

YOUR MODE: Autonomous trading cycle. You are running a background market analysis.

PROACTIVE AWARENESS:
You have a heartbeat system that monitors the market 24/7 between your cycles. Use get_heartbeat_context at the start of each cycle to see:
- Active market sessions (Sydney, Tokyo, London, NY, Crypto)
- Recent events (price alerts hit, volatility spikes, news events)
- Upcoming high-impact news (avoid trading 5min before NFP, CPI, FOMC)
- Volatility states (ATR normal/spike/compress for each symbol)
- Active price alerts you've set

YOUR PROCESS:
1. Check heartbeat context (get_heartbeat_context) — what happened while you were idle?
2. Check account status and open positions (account_summary, positions_open)
3. Scan the market for opportunities (market_scan for multi-symbol overview)
4. For promising symbols, use trading_decision_support(symbol, side) — it returns regime + ATR + RSI + EMA20 + EMA50 + coaching in ONE call (~400ms). Do NOT call market_regime + trading_context + get_indicator + trading_coach separately.
5. For deeper analysis: get_bars (OHLCV), support_resistance (S/R levels), multi_timeframe_indicators (confluence across TFs)
6. Check correlation_matrix before entering — avoid correlated exposure (e.g., long EURUSD + long GBPUSD = double USD risk)
7. Check your past performance (trading_reflect) and news (news_fetch) for context
8. Decide: TRADE (with specific symbol, side, SL, TP, volume) or HOLD
9. If trading, validate with trading_coach FIRST, then execute:
   - submit_market_order for immediate entry at current price
   - submit_pending_order for limit/stop entries when price hasn't reached your level yet (kind: buy_limit, sell_limit, buy_stop, sell_stop)
   - place_bracket_order for breakout capture in compressing markets (paired BUY STOP + SELL STOP)
10. Set price alerts for levels you want to watch (add_price_alert) — they'll wake you up when hit
11. Log your decision (log_decision)
12. Return a summary of what you did

"""
    + SAFETY_PROMPT
    + """
TRADING SCHEDULE:
- Weekdays (Mon-Fri): EURUSD, USDJPY, GBPJPY, AUDUSD, US30, XAUUSD, USOIL, BTCUSD
- Weekends (Sat-Sun): BTCUSD, ETHUSD only

SESSION AWARENESS:
- Sydney (22:00-07:00 UTC): Low vol, AUD/NZD pairs active
- Tokyo (00:00-09:00 UTC): Medium vol, JPY pairs active
- London (08:00-17:00 UTC): High vol, EUR/GBP pairs — best trading hours
- New York (13:00-22:00 UTC): High vol, USD pairs — London overlap (13:00-17:00) = peak
- London-NY overlap: Highest volatility, best breakout opportunities
- Avoid trading during low-vol sessions unless you have a specific edge

RISK MANAGEMENT (YOU MUST ARTICULATE):
- Determine SL from market structure: ATR multiples, support/resistance, swing highs/lows
- Determine TP from risk:reward minimum 1:1.5, target 1:2+
- Determine position size from account equity and SL distance (use calculate_position_size)
- Explain WHY you chose each level
- Never use fixed percentages — reason from the data

ORDER MANAGEMENT:
- Use orders_pending to check existing pending orders before placing new ones
- Use cancel_order or cancel_all_orders to remove stale pending orders
- Use modify_order to adjust pending order price/SL/TP
- Use modify_position_sl_tp to trail stops on open positions
- Use set_trailing_stop for server-side auto-trailing based on ATR

FINAL OUTPUT FORMAT (after all tool calls):
Summarize your actions concisely:
- If traded: "TRADED: SYMBOL SIDE @ PRICE, SL=level, TP=level, vol=lots. Rationale: ..."
- If held: "HOLD: No clear edge detected. Market conditions: ..."
- If pending order placed: "PENDING: SYMBOL kind @ PRICE, SL=level, TP=level. Rationale: ..."
- Include any alerts or concerns
"""
)

CONVERSATIONAL_PROMPT = (
    """You are Jesse, an autonomous AI trading agent named after Jesse Livermore.

YOUR MODE: Conversational. The user is asking you a question or giving instructions.

You can use your tools to:
- Check account status (account_summary, positions_open)
- Analyze markets:
  - trading_decision_support(symbol, side) — regime + ATR + RSI + EMAs + coaching in ONE call (preferred)
  - market_scan for multi-symbol overview
  - support_resistance for S/R level detection
  - correlation_matrix to check correlated exposure before entering
  - get_bars, get_indicator, multi_timeframe_indicators for deeper analysis
- Review past performance (trading_reflect, trading_insights)
- Check news (news_fetch, get_upcoming_news)
- Check heartbeat context (get_heartbeat_context) — recent events, active sessions, volatility
- Execute trades:
  - submit_market_order for immediate market entry
  - submit_pending_order for limit/stop entries (kind: buy_limit, sell_limit, buy_stop, sell_stop)
  - place_bracket_order for breakout setups (paired BUY STOP + SELL STOP)
  - orders_pending to list pending orders
  - cancel_order / cancel_all_orders to remove pending orders
  - modify_order to adjust pending order price/SL/TP
- Manage positions:
  - close_position to close a single position
  - close_all_positions to close everything (optional symbol/side filter)
  - modify_position_sl_tp to adjust SL/TP on open positions
  - set_trailing_stop for server-side auto-trailing
- Set price alerts (add_price_alert, remove_price_alert, list_price_alerts)
- Get coaching feedback (trading_coach)
- Validate setups (validate_trade_setup) and calculate position size (calculate_position_size)
- Check correlation_matrix to avoid correlated exposure
- Get S/R levels (support_resistance) for precise SL/TP placement

"""
    + SAFETY_PROMPT
    + """
RESPONSE STYLE:
- Be concise and professional
- Use data from your tools to support your answers
- If the user asks to trade, confirm the details before executing
- If asked about performance, provide specific numbers
- If you don't know something, say so — don't hallucinate
"""
)


def _build_system_prompt(
    mode: str, context: dict | None = None, memory_rules: list[str] | None = None
) -> str:
    if mode == "autonomous":
        base = AUTONOMOUS_TRADING_PROMPT
    else:
        base = CONVERSATIONAL_PROMPT

    context_parts = []

    if context:
        if "circuit_breaker" in context:
            ok, reason = context["circuit_breaker"]
            if not ok:
                context_parts.append(f"CIRCUIT BREAKER ACTIVE: {reason}")

        if "active_symbols" in context:
            syms = ", ".join(context["active_symbols"])
            context_parts.append(f"Active symbols for this session: {syms}")

        if "account_brief" in context:
            context_parts.append(f"Account: {context['account_brief']}")

        if "active_sessions" in context:
            context_parts.append(
                f"Active sessions: {', '.join(context['active_sessions'])}"
            )

        if "session_volatility_hint" in context:
            context_parts.append(
                f"Market volatility: {context['session_volatility_hint']}"
            )

        if "recent_events" in context and context["recent_events"]:
            context_parts.append("Recent market events:")
            for evt in context["recent_events"][:5]:
                context_parts.append(f"  • {evt}")

    if memory_rules:
        context_parts.append(
            "LEARNED PATTERNS FROM YOUR TRADING HISTORY:\n"
            + "\n".join(f"  - {r}" for r in memory_rules)
        )

    if context_parts:
        return base + "\n\nRUNTIME CONTEXT:\n" + "\n".join(context_parts)

    return base


def _build_runtime_context(context: dict | None = None) -> str:
    if not context:
        return ""

    parts = []

    if "circuit_breaker" in context:
        ok, reason = context["circuit_breaker"]
        if not ok:
            parts.append(f"CIRCUIT BREAKER ACTIVE: {reason}")

    if "active_symbols" in context:
        syms = ", ".join(context["active_symbols"])
        parts.append(f"Active symbols for this session: {syms}")

    if "account_brief" in context:
        parts.append(f"Account: {context['account_brief']}")

    if "active_sessions" in context:
        parts.append(f"Sessions: {', '.join(context['active_sessions'])}")

    if "session_volatility_hint" in context:
        parts.append(f"Volatility: {context['session_volatility_hint']}")

    return "\n".join(parts)


def _text_from_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                parts.append(block.get("text", ""))
            elif hasattr(block, "text"):
                parts.append(block.text)
        return "\n".join(p for p in parts if p)
    return str(content)


def _extract_final_response(result: dict) -> dict:
    messages = result.get("messages", [])
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            return {
                "text": _text_from_content(msg.content),
                "messages": messages,
                "full_result": result,
            }
    return {
        "text": "No response generated",
        "messages": messages,
        "full_result": result,
    }


class JesseAgent:
    """LangGraph ReAct agent wrapper for Jesse.

    Prompt is injected per-invocation (not at init) so autonomous and
    conversational modes use different system prompts.
    """

    def __init__(self, llm, tools, checkpointer):
        from langgraph.prebuilt import create_react_agent

        self._agent = create_react_agent(
            model=llm,
            tools=tools,
            checkpointer=checkpointer,
        )
        self._llm = llm
        self._tools = tools
        self._checkpointer = checkpointer

    async def run_autonomous_cycle(self, context: dict | None = None) -> dict:
        memory_rules = self._fetch_memory_rules(context)
        system = _build_system_prompt("autonomous", context, memory_rules)
        runtime_context = _build_runtime_context(context)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        user_content = (
            f"[{today}]"
            + (f" {runtime_context}" if runtime_context else "")
            + " Run your autonomous trading cycle. Analyze the market and act accordingly."
        )

        config = {"configurable": {"thread_id": "auto:portfolio"}}

        logger.info("Starting autonomous trading cycle")
        result = await self._agent.ainvoke(
            {
                "messages": [
                    SystemMessage(content=system),
                    HumanMessage(content=user_content),
                ]
            },
            config=config,
        )
        return _extract_final_response(result)

    def _fetch_memory_rules(self, context: dict | None) -> list[str]:
        try:
            from mt5_mcp.autonomous.semantic_memory import SemanticMemory

            memory = SemanticMemory()
            rules = memory.get_active_rules()
            return [r["text"] for r in rules[:5]]
        except Exception as exc:
            logger.debug("No learned rules available: %s", exc)
            return []

    async def run_conversation(
        self,
        user_message: str,
        chat_id: str,
        context: dict | None = None,
    ) -> dict:
        system = _build_system_prompt("conversational", context)
        thread_id = f"telegram:{chat_id}"
        config = {"configurable": {"thread_id": thread_id}}

        prefix = (
            f"{_build_runtime_context(context)}\n\n"
            if _build_runtime_context(context)
            else ""
        )
        logger.info("Conversation with %s: %s", chat_id, user_message[:80])
        result = await self._agent.ainvoke(
            {
                "messages": [
                    SystemMessage(content=system),
                    HumanMessage(content=prefix + user_message),
                ]
            },
            config=config,
        )
        return _extract_final_response(result)


class AgentResources:
    """Holds all agent dependencies with proper lifecycle management."""

    def __init__(
        self,
        mcp_client: MCPClient,
        checkpointer,
        agent: JesseAgent,
        db_conn=None,
    ):
        self.mcp_client = mcp_client
        self.checkpointer = checkpointer
        self.agent = agent
        self._db_conn = db_conn

    async def close(self):
        if self._db_conn:
            try:
                await self._db_conn.close()
            except Exception:
                logger.warning("DB connection close failed", exc_info=True)
        try:
            await self.mcp_client.close()
        except Exception:
            logger.warning("MCP client close failed", exc_info=True)


def _resolve_llm_params() -> tuple[str, str, str]:
    return (
        os.environ.get("JESSE_MODEL", "coder-model"),
        os.environ.get("JESSE_BASE_URL", "http://127.0.0.1:3000/v1"),
        os.environ.get("JESSE_API_KEY", "not-needed"),
    )


async def create_agent_resources(
    db_path: Path | str | None = None,
    llm=None,
    heartbeat_engine=None,
) -> AgentResources:
    import aiosqlite
    from langchain_openai import ChatOpenAI
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    if llm is None:
        model_name, base_url, api_key = _resolve_llm_params()
        llm = ChatOpenAI(
            model=model_name,
            openai_api_base=base_url,
            openai_api_key=api_key,
            temperature=0.3,
            max_tokens=4000,
        )

    db = db_path or _AGENT_DB
    Path(db).parent.mkdir(parents=True, exist_ok=True)

    conn = await aiosqlite.connect(str(db))
    await conn.execute("PRAGMA journal_mode=WAL;")
    await conn.commit()

    checkpointer = AsyncSqliteSaver(conn)

    mcp_url = os.environ.get("MT5_MCP_URL", "http://127.0.0.1:8010")
    mcp_client = MCPClient(base_url=mcp_url)
    tools = make_mcp_tools(mcp_client)
    tools.extend(make_heartbeat_tools(heartbeat_engine))

    agent = JesseAgent(
        llm=llm,
        tools=tools,
        checkpointer=checkpointer,
    )

    return AgentResources(
        mcp_client=mcp_client,
        checkpointer=checkpointer,
        agent=agent,
        db_conn=conn,
    )
