from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
    Deal,
    ExecutionResult,
    HealthStatus,
    MarginEstimate,
    MarginEstimateRequest,
    Order,
    Position,
    SimulationResult,
    SymbolInfo,
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
                margin_level=float(getattr(ai, "margin_level", 0.0))
                if getattr(ai, "margin_level", None) is not None
                else None,
                leverage=int(getattr(ai, "leverage", 0))
                if getattr(ai, "leverage", None) is not None
                else None,
                profit=float(getattr(ai, "profit", 0.0))
                if getattr(ai, "profit", None) is not None
                else None,
                margin_call_level=float(getattr(ai, "margin_so_call", 0.0))
                if getattr(ai, "margin_so_call", None) is not None
                else None,
                margin_stop_out_level=float(getattr(ai, "margin_so_so", 0.0))
                if getattr(ai, "margin_so_so", None) is not None
                else None,
                currency=getattr(ai, "currency", None),
                server=getattr(ai, "server", None),
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

    def get_symbol_info(self, symbol: str) -> SymbolInfo | None:
        if self._mt5 is None or not self._ensure_initialized():
            return None
        try:
            info = self._mt5.symbol_info(symbol)
            if info is None:
                return None
            return SymbolInfo(
                symbol=symbol,
                description=getattr(info, "description", None),
                digits=int(getattr(info, "digits", 0))
                if getattr(info, "digits", None) is not None
                else None,
                point=float(getattr(info, "point", 0.0))
                if getattr(info, "point", None) is not None
                else None,
                tick_size=float(getattr(info, "trade_tick_size", 0.0))
                if getattr(info, "trade_tick_size", None) is not None
                else None,
                tick_value=float(getattr(info, "trade_tick_value", 0.0))
                if getattr(info, "trade_tick_value", None) is not None
                else None,
                contract_size=float(getattr(info, "trade_contract_size", 0.0))
                if getattr(info, "trade_contract_size", None) is not None
                else None,
                volume_min=float(getattr(info, "volume_min", 0.0))
                if getattr(info, "volume_min", None) is not None
                else None,
                volume_max=float(getattr(info, "volume_max", 0.0))
                if getattr(info, "volume_max", None) is not None
                else None,
                volume_step=float(getattr(info, "volume_step", 0.0))
                if getattr(info, "volume_step", None) is not None
                else None,
                stops_level_points=int(getattr(info, "trade_stops_level", 0))
                if getattr(info, "trade_stops_level", None) is not None
                else None,
                freeze_level_points=int(getattr(info, "trade_freeze_level", 0))
                if getattr(info, "trade_freeze_level", None) is not None
                else None,
                spread_points=int(getattr(info, "spread", 0))
                if getattr(info, "spread", None) is not None
                else None,
                spread_float=bool(getattr(info, "spread_float", False)),
                trade_mode=str(getattr(info, "trade_mode", "")) or None,
                calc_mode=str(getattr(info, "trade_calc_mode", "")) or None,
                currency_base=getattr(info, "currency_base", None),
                currency_profit=getattr(info, "currency_profit", None),
                currency_margin=getattr(info, "currency_margin", None),
                swap_long=float(getattr(info, "swap_long", 0.0))
                if getattr(info, "swap_long", None) is not None
                else None,
                swap_short=float(getattr(info, "swap_short", 0.0))
                if getattr(info, "swap_short", None) is not None
                else None,
            )
        except Exception:
            return None

    def get_deals_history(
        self, limit: int = 100, symbol: str | None = None, days: int = 30
    ) -> list[Deal]:
        if self._mt5 is None or not self._ensure_initialized():
            return []
        try:
            end = datetime.now(timezone.utc)
            start = end - timedelta(days=max(days, 1))
            if symbol:
                rows = self._mt5.history_deals_get(start, end, group=symbol)
            else:
                rows = self._mt5.history_deals_get(start, end)
            if not rows:
                return []

            deals: list[Deal] = []
            for row in list(rows)[-max(limit, 1) :]:
                deal_type = getattr(row, "type", None)
                if deal_type not in (0, 1):
                    continue
                entry_map = {0: "in", 1: "out", 2: "inout", 3: "out_by"}
                deals.append(
                    Deal(
                        deal_id=str(getattr(row, "ticket", "")),
                        order_id=str(getattr(row, "order", "")) or None,
                        position_id=str(getattr(row, "position_id", "")) or None,
                        symbol=str(getattr(row, "symbol", "")),
                        side="buy" if deal_type == 0 else "sell",
                        entry=entry_map.get(getattr(row, "entry", None)),
                        volume=float(getattr(row, "volume", 0.0)),
                        price=float(getattr(row, "price", 0.0)),
                        profit=float(getattr(row, "profit", 0.0)),
                        commission=float(getattr(row, "commission", 0.0)),
                        swap=float(getattr(row, "swap", 0.0)),
                        fee=float(getattr(row, "fee", 0.0)),
                        time=str(
                            getattr(row, "time_msc", "") or getattr(row, "time", "")
                        ),
                        comment=str(getattr(row, "comment", "")) or None,
                        reason=str(getattr(row, "reason", "")) or None,
                        magic=int(getattr(row, "magic", 0))
                        if getattr(row, "magic", None) is not None
                        else None,
                    )
                )
            return deals
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
        if self._mt5 is None or not self._ensure_initialized():
            return MarginEstimate(required_margin=0.0, comment="scaffold", raw={})
        try:
            order_type = (
                self._mt5.ORDER_TYPE_BUY
                if req.side == "buy"
                else self._mt5.ORDER_TYPE_SELL
            )
            price = req.price_hint
            if price is None:
                tick = self._mt5.symbol_info_tick(req.symbol)
                price = (
                    getattr(tick, "ask", None)
                    if req.side == "buy"
                    else getattr(tick, "bid", None)
                )
            if price is None:
                return MarginEstimate(
                    required_margin=0.0, comment="price_unavailable", raw={}
                )

            required_margin = self._mt5.order_calc_margin(
                order_type, req.symbol, req.volume_lots, price
            )
            account = self._mt5.account_info()
            return MarginEstimate(
                required_margin=float(required_margin or 0.0),
                leverage=int(getattr(account, "leverage", 0))
                if account and getattr(account, "leverage", None) is not None
                else None,
                comment="ok",
                raw={"price": price},
            )
        except Exception as e:
            return MarginEstimate(required_margin=0.0, comment=str(e), raw={})

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
