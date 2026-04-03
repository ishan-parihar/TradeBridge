from __future__ import annotations

import json
from typing import Any, Optional
import httpx

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


class EABridgeAdapter(ExecutionPort):
    """EA Bridge adapter that communicates with MT5 via the bridge gateway.

    This adapter sends commands to the EA through the bridge gateway,
    which are then executed by the BridgeConnectorEA in MT5.
    """

    def __init__(self, gateway_url: str | None = None) -> None:
        self.settings = get_settings()
        self.gateway_url = (
            gateway_url or self.settings.gateway_url or "http://127.0.0.1:8020"
        )
        self._last_heartbeat: dict[str, Any] = {}
        self._last_heartbeat_time: float = 0.0

    def _check_ea_connected(self) -> bool:
        """Check if EA is connected by checking bridge terminal status."""
        try:
            with httpx.Client(timeout=5.0) as client:
                r = client.get(f"{self.gateway_url}/bridge/terminal/status")
                if r.status_code == 200:
                    data = r.json()
                    connected = data.get("connected", False)
                    return bool(connected)
        except Exception as e:
            logger.warning(f"EA connection check failed: {e}")
        return False

    def _enqueue_command(self, cmd_type: str, payload: dict[str, Any]) -> str:
        """Enqueue a command and return the request ID."""
        try:
            with httpx.Client(timeout=10.0) as client:
                params = {"type": cmd_type, **payload}
                r = client.post(
                    f"{self.gateway_url}/bridge/commands/enqueue",
                    params=params,
                )
                r.raise_for_status()
                return r.json()["id"]
        except Exception as e:
            logger.error(f"Failed to enqueue command {cmd_type}: {e}")
            raise

    def _await_result(
        self, request_id: str, timeout_s: float = 10.0, poll_s: float = 0.5
    ) -> dict[str, Any]:
        """Wait for command result."""
        import time as _t

        end = _t.time() + timeout_s
        while _t.time() < end:
            try:
                with httpx.Client(timeout=5.0) as client:
                    r = client.get(f"{self.gateway_url}/bridge/results/{request_id}")
                    if r.status_code == 200:
                        data = r.json()
                        status = data.get("status")
                        if status in ("completed", "error"):
                            return data
            except Exception as e:
                logger.warning(f"Polling error: {e}")
            _t.sleep(poll_s)

        return {"status": "timeout", "error": "timeout"}

    def _send_command(
        self, cmd_type: str, payload: dict[str, Any], timeout_s: float = 10.0
    ) -> dict[str, Any]:
        """Send command and wait for result."""
        req_id = self._enqueue_command(cmd_type, payload)
        result = self._await_result(req_id, timeout_s=timeout_s)
        return result

    def health(self) -> HealthStatus:
        ea_connected = self._check_ea_connected()
        state = "healthy" if ea_connected else "disconnected"
        return HealthStatus(
            state=state, details="EA Bridge" if ea_connected else "EA not connected"
        )

    def terminal_status(self) -> TerminalStatus:
        try:
            with httpx.Client(timeout=5.0) as client:
                r = client.get(f"{self.gateway_url}/bridge/terminal/status")
                if r.status_code == 200:
                    data = r.json()
                    return TerminalStatus(**data)
        except Exception as e:
            logger.warning(f"Terminal status failed: {e}")
        return TerminalStatus(connected=False, message="Bridge status unavailable")

    def account_summary(self) -> AccountSummary:
        """Fetch account summary via EA bridge command."""
        try:
            result = self._send_command("get_account", {}, timeout_s=10.0)

            if result.get("status") != "completed":
                logger.warning(
                    f"Account summary command failed: {result.get('error', 'unknown')}"
                )
                return AccountSummary(environment=self.settings.environment or "demo")

            payload = result.get("result", {}).get("payload")
            if not payload:
                logger.warning("Account summary returned empty payload")
                return AccountSummary(environment=self.settings.environment or "demo")

            # Parse payload (may be string or dict)
            if isinstance(payload, str):
                data = json.loads(payload)
            elif isinstance(payload, dict):
                data = payload
            else:
                logger.warning(f"Unexpected payload type: {type(payload)}")
                return AccountSummary(environment=self.settings.environment or "demo")

            # Map EA response to AccountSummary model
            # EA returns: account_id, name, currency, balance, equity, margin, free_margin, server
            return AccountSummary(
                account_id=str(data.get("account_id", "")) or None,
                name=data.get("name"),
                balance=float(data.get("balance", 0.0))
                if data.get("balance") is not None
                else None,
                equity=float(data.get("equity", 0.0))
                if data.get("equity") is not None
                else None,
                margin=float(data.get("margin", 0.0))
                if data.get("margin") is not None
                else None,
                free_margin=float(data.get("free_margin", 0.0))
                if data.get("free_margin") is not None
                else None,
                currency=data.get("currency"),
                environment=self.settings.environment or "demo",
            )
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse account summary JSON: {e}")
            return AccountSummary(environment=self.settings.environment or "demo")
        except Exception as e:
            logger.error(f"Account summary failed: {e}")
            return AccountSummary(environment=self.settings.environment or "demo")

    def get_positions(self) -> list[Position]:
        """Fetch positions via EA bridge command."""
        try:
            result = self._send_command("get_positions", {}, timeout_s=10.0)

            if result.get("status") != "completed":
                return []

            payload = result.get("result", {}).get("payload")
            if not payload:
                return []

            if isinstance(payload, str):
                data = json.loads(payload)
            elif isinstance(payload, dict):
                data = payload
            else:
                return []

            positions_data = data.get("positions", [])
            results: list[Position] = []

            for p in positions_data:
                results.append(
                    Position(
                        position_id=str(p.get("position_id", "")),
                        symbol=p.get("symbol", ""),
                        side=p.get("side", "buy"),
                        volume=float(p.get("volume", 0.0)),
                        entry_price=float(p.get("entry_price", 0.0)),
                        mark_price=float(p.get("mark_price", 0.0)),
                        sl=float(p.get("sl", 0.0)) or None,
                        tp=float(p.get("tp", 0.0)) or None,
                        unrealized_pnl=float(p.get("unrealized_pnl", 0.0)),
                        strategy_id=None,
                        opened_at=str(p.get("opened_at", ""))
                        if p.get("opened_at")
                        else None,
                        source="ea_bridge",
                    )
                )
            return results
        except Exception as e:
            logger.error(f"Get positions failed: {e}")
            return []

    def get_orders(self) -> list[Order]:
        """Fetch orders via EA bridge command."""
        try:
            result = self._send_command("get_orders", {}, timeout_s=10.0)

            if result.get("status") != "completed":
                return []

            payload = result.get("result", {}).get("payload")
            if not payload:
                return []

            if isinstance(payload, str):
                data = json.loads(payload)
            elif isinstance(payload, dict):
                data = payload
            else:
                return []

            orders_data = data.get("orders", [])
            results: list[Order] = []

            for o in orders_data:
                results.append(
                    Order(
                        order_id=str(o.get("order_id", "")),
                        symbol=o.get("symbol", ""),
                        side=o.get("side", "buy"),
                        kind=o.get("kind", "market"),
                        volume=float(o.get("volume", 0.0)),
                        price=float(o.get("price", 0.0)) or None,
                        sl=float(o.get("sl", 0.0)) or None,
                        tp=float(o.get("tp", 0.0)) or None,
                        status=o.get("status"),
                    )
                )
            return results
        except Exception as e:
            logger.error(f"Get orders failed: {e}")
            return []

    def get_bars(self, symbol: str, timeframe: str, count: int) -> Bars:
        """Fetch bars via EA bridge command."""
        try:
            result = self._send_command(
                "get_bars",
                {"symbol": symbol, "timeframe": timeframe, "count": count},
                timeout_s=15.0,
            )

            if result.get("status") != "completed":
                return Bars(
                    symbol=symbol, timeframe=timeframe, data=[], source="ea_bridge"
                )

            payload = result.get("result", {}).get("payload")
            if not payload:
                return Bars(
                    symbol=symbol, timeframe=timeframe, data=[], source="ea_bridge"
                )

            if isinstance(payload, str):
                data = json.loads(payload)
            elif isinstance(payload, dict):
                data = payload
            else:
                return Bars(
                    symbol=symbol, timeframe=timeframe, data=[], source="ea_bridge"
                )

            bars_data = data.get("data", [])
            bars: list[Bar] = []

            for b in bars_data:
                bars.append(
                    Bar(
                        time=int(b.get("time", 0)),
                        open=float(b.get("open", 0.0)),
                        high=float(b.get("high", 0.0)),
                        low=float(b.get("low", 0.0)),
                        close=float(b.get("close", 0.0)),
                        tick_volume=int(b.get("tick_volume", 0))
                        if b.get("tick_volume")
                        else None,
                    )
                )

            return Bars(
                symbol=data.get("symbol", symbol),
                timeframe=data.get("timeframe", timeframe),
                data=bars,
                source="ea_bridge",
            )
        except Exception as e:
            logger.error(f"Get bars failed: {e}")
            return Bars(symbol=symbol, timeframe=timeframe, data=[], source="ea_bridge")

    def estimate_margin(self, req: MarginEstimateRequest) -> MarginEstimate:
        return MarginEstimate(
            required_margin=0.0, comment="Not implemented in EA bridge", raw={}
        )

    def simulate_order(self, req: TradeIntent) -> SimulationResult:
        return SimulationResult(intent_id=req.intent_id, status="simulated")

    def submit_order(self, req: TradeIntent) -> ExecutionResult:
        """Submit order via EA bridge command."""
        try:
            result = self._send_command(
                "submit_order",
                {
                    "symbol": req.symbol,
                    "side": req.side,
                    "volume_lots": req.volume_lots,
                    "sl": req.sl or 0,
                    "tp": req.tp or 0,
                    "deviation": req.deviation_points or 20,
                },
                timeout_s=20.0,
            )

            if result.get("status") != "completed":
                return ExecutionResult(
                    intent_id=req.intent_id,
                    status="error",
                    message=result.get("error", "timeout"),
                )

            payload = result.get("result", {}).get("payload")
            if isinstance(payload, str):
                data = json.loads(payload)
            elif isinstance(payload, dict):
                data = payload
            else:
                data = {}

            return ExecutionResult(
                intent_id=req.intent_id,
                status="submitted",
                adapter="EABridgeAdapter",
                broker_order_id=str(data.get("order", "")) if data else None,
                retcode=str(data.get("retcode")) if data else None,
                raw=data,
            )
        except Exception as e:
            return ExecutionResult(
                intent_id=req.intent_id,
                status="error",
                message=str(e),
            )

    def modify_order(self, req: ModifyOrderRequest) -> ExecutionResult:
        """Modify order via EA bridge command."""
        try:
            payload = {"order_id": req.order_id}
            if req.new_price is not None:
                payload["new_price"] = req.new_price
            if req.new_sl is not None:
                payload["new_sl"] = req.new_sl
            if req.new_tp is not None:
                payload["new_tp"] = req.new_tp

            result = self._send_command("modify_order", payload, timeout_s=10.0)

            if result.get("status") == "completed":
                return ExecutionResult(
                    intent_id="",
                    status="accepted",
                    adapter="EABridgeAdapter",
                )
            else:
                return ExecutionResult(
                    intent_id="",
                    status="error",
                    message=result.get("error", "unknown"),
                )
        except Exception as e:
            return ExecutionResult(
                intent_id="",
                status="error",
                message=str(e),
            )

    def close_position(self, req: ClosePositionRequest) -> ExecutionResult:
        """Close position via EA bridge command."""
        try:
            payload = {"position_id": req.position_id}
            if req.volume is not None:
                payload["volume"] = req.volume

            result = self._send_command("close_position", payload, timeout_s=20.0)

            if result.get("status") == "completed":
                return ExecutionResult(
                    intent_id="",
                    status="accepted",
                    adapter="EABridgeAdapter",
                )
            else:
                return ExecutionResult(
                    intent_id="",
                    status="error",
                    message=result.get("error", "unknown"),
                )
        except Exception as e:
            return ExecutionResult(
                intent_id="",
                status="error",
                message=str(e),
            )
