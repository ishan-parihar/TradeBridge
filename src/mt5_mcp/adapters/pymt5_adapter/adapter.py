from __future__ import annotations

from typing import Any, Optional

from mt5_mcp.adapters.common.ports import (
    ClosePositionRequest,
    ExecutionPort,
    ModifyOrderRequest,
)
from mt5_mcp.schemas.models import (
    AccountSummary,
    Bars,
    Bar,
    ExecutionResult,
    HealthStatus,
    MarginEstimate,
    MarginEstimateRequest,
    Order,
    Position,
    SimulationResult,
    TerminalStatus,
    TradeIntent,
)
from mt5_mcp.settings.config import get_settings
from mt5_mcp.observability.logging import logger
from .timeframes import map_timeframe


class PyMT5Adapter(ExecutionPort):
    """Safe scaffold adapter.

    Attempts to import MetaTrader5 lazily. If unavailable or terminal isn't reachable,
    methods return conservative placeholders and mark writes as not implemented.
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self._mt5: Any | None = None
        self._initialized: bool = False
        try:
            import MetaTrader5 as MT5  # type: ignore

            self._mt5 = MT5
        except Exception as e:  # pragma: no cover - env dependent
            logger.warning("MetaTrader5 module not available: %s", e)
            self._mt5 = None

    def _terminal_exe(self) -> str:
        base = self.settings.mt5_terminal_path.rstrip("/\\")
        # Prefer Terminal64.exe
        return f"{base}/terminal64.exe"

    def _ensure_initialized(self) -> bool:
        if self._mt5 is None:
            return False
        if self._initialized:
            return True
        try:
            path = self._terminal_exe()

            # Use threading with timeout to prevent indefinite blocking
            import threading

            result = [None]

            def init_thread():
                try:
                    result[0] = self._mt5.initialize(path=path)
                except Exception as e:
                    logger.warning("MT5.initialize exception: %s", e)
                    result[0] = False

            thread = threading.Thread(target=init_thread)
            thread.daemon = True
            thread.start()
            thread.join(timeout=5.0)  # 5 second timeout

            if thread.is_alive():
                logger.warning("MT5.initialize timed out after 5s")
                return False

            self._initialized = bool(result[0])
            if not self._initialized:
                logger.warning("MT5.initialize failed for path=%s", path)
            return self._initialized
        except Exception as e:  # pragma: no cover - env dependent
            logger.warning("MT5.initialize exception: %s", e)
            return False

    def _connected(self) -> bool:
        try:
            if self._mt5 is None:
                return False
            # On non-Windows this may fail; keep conservative
            self._ensure_initialized()
            info = self._mt5.terminal_info()  # type: ignore[attr-defined]
            return bool(info and getattr(info, "build", None))
        except Exception:
            return False

    def health(self) -> HealthStatus:
        state = "healthy" if self._connected() else "disconnected"
        return HealthStatus(state=state)

    def terminal_status(self) -> TerminalStatus:
        if self._mt5 is None:
            return TerminalStatus(
                connected=False,
                path=self.settings.mt5_terminal_path,
                message="MetaTrader5 Python module not loaded",
            )
        try:
            ti = self._mt5.terminal_info()
            return TerminalStatus(
                connected=True,
                login=None,
                server=getattr(ti, "server", None),
                build=getattr(ti, "build", None),
                path=getattr(ti, "path", None) or self.settings.mt5_terminal_path,
            )
        except Exception as e:
            return TerminalStatus(
                connected=False, path=self.settings.mt5_terminal_path, message=str(e)
            )

    def account_summary(self) -> AccountSummary:
        if self._mt5 is None or not self._ensure_initialized():
            return AccountSummary(environment=self.settings.environment or "demo")
        try:
            ai = self._mt5.account_info()
            return AccountSummary(
                account_id=str(getattr(ai, "login", "")) or None,
                name=getattr(ai, "name", None),
                balance=float(getattr(ai, "balance", 0.0)),
                equity=float(getattr(ai, "equity", 0.0)),
                margin=float(getattr(ai, "margin", 0.0)),
                free_margin=float(getattr(ai, "margin_free", 0.0)),
                currency=getattr(ai, "currency", None),
                environment=self.settings.environment or "demo",
            )
        except Exception:
            return AccountSummary(environment=self.settings.environment or "demo")

    def get_positions(self) -> list[Position]:
        if self._mt5 is None or not self._ensure_initialized():
            return []
        try:
            rows = self._mt5.positions_get()
            results: list[Position] = []
            if not rows:
                return results
            for p in rows:
                # Map MT5 position to canonical model
                results.append(
                    Position(
                        position_id=str(getattr(p, "ticket", "")),
                        symbol=str(getattr(p, "symbol", "")),
                        side="buy" if getattr(p, "type", 0) == 0 else "sell",
                        volume=float(getattr(p, "volume", 0.0)),
                        entry_price=float(getattr(p, "price_open", 0.0)),
                        mark_price=float(getattr(p, "price_current", 0.0)),
                        sl=float(getattr(p, "sl", 0.0)) or None,
                        tp=float(getattr(p, "tp", 0.0)) or None,
                        unrealized_pnl=float(getattr(p, "profit", 0.0)),
                        strategy_id=None,
                        opened_at=str(getattr(p, "time_msc", ""))
                        if getattr(p, "time_msc", None)
                        else None,
                        source="pymt5",
                    )
                )
            return results
        except Exception:
            return []

    def get_orders(self) -> list[Order]:
        if self._mt5 is None or not self._ensure_initialized():
            return []
        try:
            rows = self._mt5.orders_get()
            results: list[Order] = []
            if not rows:
                return results
            for o in rows:
                kind_map = {0: "buy", 1: "sell"}
                # Map order type to kind; simplify to market/limit/stop
                t = getattr(o, "type", 2)
                side = "buy" if t in (0, 2, 4) else "sell"
                kind: str = (
                    "limit" if t in (2, 3) else ("stop" if t in (4, 5) else "market")
                )
                results.append(
                    Order(
                        order_id=str(getattr(o, "ticket", "")),
                        symbol=str(getattr(o, "symbol", "")),
                        side=side,
                        kind=kind,  # simplified
                        volume=float(
                            getattr(o, "volume_current", 0.0)
                            or getattr(o, "volume_initial", 0.0)
                        ),
                        price=float(getattr(o, "price_open", 0.0)) or None,
                        sl=float(getattr(o, "sl", 0.0)) or None,
                        tp=float(getattr(o, "tp", 0.0)) or None,
                        status=str(getattr(o, "state", "")) or None,
                    )
                )
            return results
        except Exception:
            return []

    def get_bars(self, symbol: str, timeframe: str, count: int) -> Bars:
        if self._mt5 is None or not self._ensure_initialized():
            return Bars(symbol=symbol, timeframe=timeframe, data=[], source="pymt5")
        try:
            tf = map_timeframe(self._mt5, timeframe)
            rates = self._mt5.copy_rates_from_pos(symbol, tf, 0, max(1, int(count)))
            bars = []
            if rates is not None:
                for r in rates:
                    # r is a numpy structured row; access by field name
                    bars.append(
                        Bar(
                            time=int(r["time"]),
                            open=float(r["open"]),
                            high=float(r["high"]),
                            low=float(r["low"]),
                            close=float(r["close"]),
                            tick_volume=int(r["tick_volume"])
                            if "tick_volume" in r.dtype.names
                            else None,
                        )
                    )
            return Bars(symbol=symbol, timeframe=timeframe, data=bars, source="pymt5")
        except Exception:
            return Bars(symbol=symbol, timeframe=timeframe, data=[], source="pymt5")

    def estimate_margin(self, req: MarginEstimateRequest) -> MarginEstimate:
        return MarginEstimate(required_margin=0.0, comment="scaffold", raw={})

    def simulate_order(self, req: TradeIntent) -> SimulationResult:
        return SimulationResult(intent_id=req.intent_id, status="simulated")

    # Sensitive path: return 501-like error for now
    def submit_order(self, req: TradeIntent) -> ExecutionResult:
        return ExecutionResult(
            intent_id=req.intent_id,
            status="error",
            adapter=self.__class__.__name__,
            message="submit_order not enabled in scaffold",
        )

    def modify_order(self, req: ModifyOrderRequest) -> ExecutionResult:
        return ExecutionResult(
            intent_id="", status="error", message="modify_order not implemented"
        )

    def close_position(self, req: ClosePositionRequest) -> ExecutionResult:
        return ExecutionResult(
            intent_id="", status="error", message="close_position not implemented"
        )
