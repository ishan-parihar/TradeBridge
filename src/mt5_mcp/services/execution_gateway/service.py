from __future__ import annotations

from typing import Literal

from mt5_mcp.adapters.common.ports import ExecutionPort
from mt5_mcp.adapters.pymt5_adapter.adapter import PyMT5Adapter
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
from mt5_mcp.settings.config import get_settings


class ExecutionGateway:
    """Routes read/write calls to the active adapter.

    Write-paths should be gated by the policy engine (not included in scaffold).
    """

    def __init__(self, adapter: ExecutionPort | None = None) -> None:
        self.settings = get_settings()
        self.adapter: ExecutionPort = adapter or self._load_adapter(self.settings.adapter)

    def _load_adapter(self, name: str) -> ExecutionPort:
        # Future: support ea_socket adapter via registry
        return PyMT5Adapter()

    # Read-path
    def health(self) -> HealthStatus:
        return self.adapter.health()

    def terminal_status(self) -> TerminalStatus:
        return self.adapter.terminal_status()

    def account_summary(self) -> AccountSummary:
        return self.adapter.account_summary()

    def get_bars(self, symbol: str, timeframe: str, count: int) -> Bars:
        return self.adapter.get_bars(symbol, timeframe, count)

    def estimate_margin(self, req: MarginEstimateRequest) -> MarginEstimate:
        return self.adapter.estimate_margin(req)

    def simulate_order(self, req: TradeIntent) -> SimulationResult:
        return self.adapter.simulate_order(req)

    # Write-path (guard elsewhere)
    def submit_order(self, req: TradeIntent) -> ExecutionResult:
        return self.adapter.submit_order(req)
