from __future__ import annotations

from typing import Literal

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
from mt5_mcp.settings.config import get_settings
from mt5_mcp.observability.logging import logger


class ExecutionGateway:
    """Routes read/write calls to the active adapter.

    Write-paths should be gated by the policy engine (not included in scaffold).
    """

    def __init__(self, adapter: ExecutionPort | None = None) -> None:
        self.settings = get_settings()
        self.adapter: ExecutionPort = adapter or self._load_adapter(
            self.settings.adapter
        )

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
        return self.adapter.submit_order(req)
