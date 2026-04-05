"""APScheduler-based scheduler + HeartbeatEngine for the autonomous trading agent."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

WEEKDAY_SYMBOLS = [
    "EURUSD",
    "USDJPY",
    "GBPJPY",
    "AUDUSD",
    "US30",
    "XAUUSD",
    "USOIL",
    "BTCUSD",
]
WEEKEND_SYMBOLS = ["BTCUSD", "ETHUSD"]


def get_active_symbols() -> list[str]:
    now = datetime.now(timezone.utc)
    return WEEKDAY_SYMBOLS if now.weekday() < 5 else WEEKEND_SYMBOLS


_TRADE_RE = re.compile(
    r"\b(traded|bought|sold|executed|opened|entered)\b", re.IGNORECASE
)
_HOLD_RE = re.compile(r"\b(hold|no.clear.edge|waiting|monitoring)\b", re.IGNORECASE)
_BREAKER_RE = re.compile(
    r"\b(circuit.breaker|cool.off|stop.trading|daily.limit)\b", re.IGNORECASE
)


class AgentScheduler:
    def __init__(self, jesse_agent, mcp_client, circuit_breaker=None):
        self.scheduler = AsyncIOScheduler()
        self.jesse_agent = jesse_agent
        self.mcp_client = mcp_client
        self.circuit_breaker = circuit_breaker
        self.current_interval = 15
        self._running = False
        self.heartbeat_engine = None
        self._heartbeat_task = None

    async def trigger_cycle(self):
        context = self._build_context()
        try:
            result = await self.jesse_agent.run_autonomous_cycle(context=context)
            if self.heartbeat_engine:
                interval = self.heartbeat_engine.adjust_interval()
                reason = f"HeartbeatEngine adaptive ({interval:.0f}s)"
            else:
                interval_mins, reason = self._compute_interval(result)
                interval = interval_mins * 60
            self.current_interval = interval / 60
            self._reschedule()
            logger.info("Cycle complete — next interval: %s", reason)
        except Exception as exc:
            logger.error("Agent cycle failed: %s", exc, exc_info=True)
            if self.heartbeat_engine:
                self.current_interval = self.heartbeat_engine.config.max_interval / 60
            else:
                self.current_interval = min(self.current_interval * 2, 60)
            self._reschedule()

    def _build_context(self) -> dict:
        context = {}
        if self.circuit_breaker:
            ok, reason = self.circuit_breaker.check_all()
            context["circuit_breaker"] = (ok, reason)
            context["consecutive_losses"] = (
                self.circuit_breaker.state.consecutive_losses
            )
        context["active_symbols"] = get_active_symbols()

        if self.heartbeat_engine:
            hb_context = self.heartbeat_engine.get_context()
            context.update(hb_context)

        return context

    @staticmethod
    def _compute_interval(result: dict) -> tuple[int, str]:
        text = result.get("text", "").lower()

        if _BREAKER_RE.search(text):
            return (120, "Cool-off period")
        if _TRADE_RE.search(text):
            return (5, "Active trade — tight monitoring")
        if _HOLD_RE.search(text):
            return (15, "No clear edge, standard interval")
        return (15, "Default interval")

    def _reschedule(self):
        job = self.scheduler.get_job("heartbeat")
        if job:
            self.scheduler.reschedule_job(
                "heartbeat",
                trigger=IntervalTrigger(minutes=int(self.current_interval)),
            )
            logger.info(
                "Rescheduled heartbeat to %d min interval", int(self.current_interval)
            )

    def init_heartbeat(self, symbols: list[str] | None = None):
        from mt5_mcp.autonomous.heartbeat_engine import HeartbeatEngine

        def _wake():
            asyncio.create_task(self.trigger_immediate_cycle())

        self.heartbeat_engine = HeartbeatEngine(
            self.mcp_client,
            wake_callback=_wake,
        )
        self.heartbeat_engine.initialize(symbols or get_active_symbols())
        logger.info("HeartbeatEngine initialized (event-driven proactivity)")

    async def trigger_immediate_cycle(self):
        logger.info("Event-triggered immediate agent cycle")
        await self.trigger_cycle()

    def set_wake_callback(self, callback: Callable | None) -> None:
        """Set or update the wake callback for event-driven agent cycles."""
        if self.heartbeat_engine:
            self.heartbeat_engine._wake_callback = callback
            logger.info("Wake callback %s", "updated" if callback else "cleared")
        else:
            logger.warning(
                "Cannot set wake callback — heartbeat_engine not initialized"
            )

    async def start_heartbeat(self):
        if not self.heartbeat_engine:
            return
        self._heartbeat_task = self.heartbeat_engine.start()
        logger.info("HeartbeatEngine started — monitors running")

    async def pre_market_scan(self):
        logger.info("Pre-market scan triggered (symbols: %s)", get_active_symbols())
        await self.trigger_cycle()

    async def weekend_crypto_scan(self):
        logger.info("Weekend crypto scan triggered (symbols: %s)", WEEKEND_SYMBOLS)
        await self.trigger_cycle()

    async def eod_review(self):
        logger.info("End-of-day review triggered")
        try:
            insights = await self.mcp_client.trading_insights(lookback_days=1)
            logger.info("Daily insights: %s", insights)
        except Exception as exc:
            logger.error("EOD review failed: %s", exc)

    def add_builtin_jobs(self):
        self.scheduler.add_job(
            self.trigger_cycle,
            IntervalTrigger(minutes=int(self.current_interval)),
            id="heartbeat",
            max_instances=1,
            misfire_grace_time=60,
        )
        self.scheduler.add_job(
            self.pre_market_scan,
            CronTrigger(
                hour=7, minute=45, day_of_week="mon-fri", timezone="Asia/Calcutta"
            ),
            id="pre_market_scan",
        )
        self.scheduler.add_job(
            self.weekend_crypto_scan,
            CronTrigger(
                hour=10, minute=0, day_of_week="sat,sun", timezone="Asia/Calcutta"
            ),
            id="weekend_crypto_scan",
        )
        self.scheduler.add_job(
            self.eod_review,
            CronTrigger(
                hour=22, minute=0, day_of_week="mon-fri", timezone="Asia/Calcutta"
            ),
            id="eod_review",
        )
        self.scheduler.add_job(
            self._memory_consolidation,
            CronTrigger(
                hour=23, minute=0, day_of_week="mon-sun", timezone="Asia/Calcutta"
            ),
            id="memory_consolidation",
        )

    async def _memory_consolidation(self):
        from mt5_mcp.autonomous.consolidation import consolidate
        from mt5_mcp.autonomous.decay import apply_decay
        from mt5_mcp.autonomous.semantic_memory import SemanticMemory
        from apps.autonomous_agent.health import update_health

        logger.info("Memory consolidation triggered")
        try:
            patterns = consolidate()
            logger.info("Extracted %d patterns from trade history", len(patterns))
        except Exception as exc:
            logger.error("Consolidation failed: %s", exc, exc_info=True)

        try:
            memory = SemanticMemory()
            decay_result = apply_decay(memory)
            if decay_result.get("pruned", 0) > 0:
                logger.info("Decay pruned %d stale patterns", decay_result["pruned"])
            update_health(memory_count=memory.count())
        except Exception as exc:
            logger.error("Decay failed: %s", exc, exc_info=True)

    def start(self):
        self.scheduler.start()
        self._running = True
        logger.info(
            "Scheduler started (initial interval: %d min)", self.current_interval
        )

    def shutdown(self):
        if self.heartbeat_engine:
            self.heartbeat_engine.stop()
        if self._running:
            self.scheduler.shutdown(wait=False)
            self._running = False
            logger.info("Scheduler shut down")
