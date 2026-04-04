from __future__ import annotations

from typing import Protocol

from mt5_mcp.schemas.models import (
    AccountSummary,
    Bars,
    ClosePositionRequest,
    Deal,
    ExecutionResult,
    HealthStatus,
    MarginEstimate,
    MarginEstimateRequest,
    ModifyOrderRequest,
    Order,
    Position,
    SymbolInfo,
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

    def get_symbol_info(self, symbol: str) -> SymbolInfo | None: ...

    def get_deals_history(
        self, limit: int = 100, symbol: str | None = None, days: int = 30
    ) -> list[Deal]: ...

    def get_bars(self, symbol: str, timeframe: str, count: int) -> Bars: ...

    def get_indicator(
        self, symbol: str, timeframe: str, indicator: str, **kwargs: object
    ) -> dict[str, object]: ...

    def get_ticks(self, symbol: str, count: int = 200) -> dict[str, object]: ...

    def estimate_margin(self, req: MarginEstimateRequest) -> MarginEstimate: ...

    def simulate_order(self, req: TradeIntent) -> SimulationResult: ...

    def submit_order(self, req: TradeIntent) -> ExecutionResult: ...

    def modify_order(self, req: ModifyOrderRequest) -> ExecutionResult: ...

    def close_position(self, req: ClosePositionRequest) -> ExecutionResult: ...
