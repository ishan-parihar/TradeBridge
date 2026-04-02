from __future__ import annotations

from typing import Protocol

from mt5_mcp.schemas.models import (
    AccountSummary,
    Bars,
    ClosePositionRequest,
    ExecutionResult,
    HealthStatus,
    MarginEstimate,
    MarginEstimateRequest,
    ModifyOrderRequest,
    Order,
    Position,
    SimulationResult,
    TerminalStatus,
    TradeIntent,
)


class ExecutionPort(Protocol):
    def health(self) -> HealthStatus: ...

    def terminal_status(self) -> TerminalStatus: ...

    def account_summary(self) -> AccountSummary: ...

    def get_positions(self) -> list[Position]: ...

    def get_orders(self) -> list[Order]: ...

    def get_bars(self, symbol: str, timeframe: str, count: int) -> Bars: ...

    def estimate_margin(self, req: MarginEstimateRequest) -> MarginEstimate: ...

    def simulate_order(self, req: TradeIntent) -> SimulationResult: ...

    def submit_order(self, req: TradeIntent) -> ExecutionResult: ...

    def modify_order(self, req: ModifyOrderRequest) -> ExecutionResult: ...

    def close_position(self, req: ClosePositionRequest) -> ExecutionResult: ...
