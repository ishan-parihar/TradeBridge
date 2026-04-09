from __future__ import annotations

import time
from collections import OrderedDict
from typing import Any

from mt5_mcp.adapters.common.ports import ExecutionPort
from mt5_mcp.adapters.pymt5_adapter.adapter import PyMT5Adapter
from mt5_mcp.adapters.ea_bridge_adapter.adapter import EABridgeAdapter
from mt5_mcp.schemas.models import (
    AccountSummary,
    Bars,
    ExecutionResult,
    HealthStatus,
    MarginEstimate,
    MarginEstimateRequest,
    SimulationResult,
    TerminalStatus,
    TradeIntent,
)
from mt5_mcp.settings.config import derive_magic_number, get_settings
from mt5_mcp.observability.logging import logger


class IdempotencyCache:
    """Bounded LRU cache with TTL for idempotency keys."""

    def __init__(self, max_size: int = 10000, ttl: float = 3600):
        self._cache: OrderedDict[str, tuple[Any, float]] = OrderedDict()
        self._max_size = max_size
        self._ttl = ttl

    def get(self, key: str) -> Any | None:
        if key in self._cache:
            result, timestamp = self._cache[key]
            if time.time() - timestamp < self._ttl:
                self._cache.move_to_end(key)
                return result
            else:
                del self._cache[key]
        self._cleanup()
        return None

    def set(self, key: str, result: Any) -> None:
        while len(self._cache) >= self._max_size:
            self._cache.popitem(last=False)
        self._cache[key] = (result, time.time())

    def _cleanup(self) -> None:
        now = time.time()
        expired = [k for k, (_, ts) in self._cache.items() if now - ts >= self._ttl]
        for k in expired:
            del self._cache[k]


class ExecutionGateway:
    """Routes read/write calls to the active adapter.

    Write-paths should be gated by the policy engine (not included in scaffold).
    """

    def __init__(self, adapter: ExecutionPort | None = None) -> None:
        self.settings = get_settings()
        self.adapter: ExecutionPort = adapter or self._load_adapter(
            self.settings.adapter
        )
        self._idempotency_registry = IdempotencyCache()

    def _load_adapter(self, name: str) -> ExecutionPort:
        # Try EA Bridge adapter first (preferred when EA is connected)
        # Fall back to PyMT5 adapter if EA is not available
        try:
            ea_adapter = EABridgeAdapter()
            # Check if EA is connected
            if ea_adapter._check_ea_connected():
                logger.info("EA Bridge adapter initialized (EA connected)")
                return ea_adapter
            else:
                logger.warning("EA not connected, falling back to PyMT5 adapter")
        except Exception as e:
            logger.warning(
                f"EA Bridge adapter initialization failed: {e}, falling back to PyMT5"
            )

        return PyMT5Adapter()

    def resolve_magic_number(self, strategy_id: str | None) -> int:
        if strategy_id is None:
            return 0
        if strategy_id in self.settings.strategy_magic_numbers:
            return self.settings.strategy_magic_numbers[strategy_id]
        return derive_magic_number(strategy_id)

    # Read-path
    def health(self) -> HealthStatus:
        return self.adapter.health()

    def terminal_status(self) -> TerminalStatus:
        return self.adapter.terminal_status()

    def account_summary(self) -> AccountSummary:
        return self.adapter.account_summary()

    def get_bars(self, symbol: str, timeframe: str, count: int) -> Bars:
        return self.adapter.get_bars(symbol, timeframe, count)

    def get_indicator(
        self, symbol: str, timeframe: str, indicator: str, **kwargs: object
    ) -> dict:
        return self.adapter.get_indicator(symbol, timeframe, indicator, **kwargs)

    def get_ticks(self, symbol: str, count: int = 200) -> dict:
        return self.adapter.get_ticks(symbol, count)

    def estimate_margin(self, req: MarginEstimateRequest) -> MarginEstimate:
        return self.adapter.estimate_margin(req)

    def simulate_order(self, req: TradeIntent) -> SimulationResult:
        return self.adapter.simulate_order(req)

    # Write-path (guard elsewhere)
    def submit_order(self, req: TradeIntent) -> ExecutionResult:
        cached = req.idempotency_key and self._idempotency_registry.get(
            req.idempotency_key
        )
        if cached:
            logger.info(f"Idempotent replay: {req.idempotency_key}")
            return cached

        result = self.adapter.submit_order(req)

        if req.idempotency_key:
            self._idempotency_registry.set(req.idempotency_key, result)
        return result
