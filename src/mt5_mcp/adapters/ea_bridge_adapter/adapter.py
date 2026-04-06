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
from mt5_mcp.settings.config import (
    get_settings,
    derive_magic_number,
    compose_comment,
)
from mt5_mcp.observability.logging import logger
from mt5_mcp.adapters.common.symbol_utils import normalize_symbol, denormalize_symbol


class EABridgeAdapter(ExecutionPort):
    """EA Bridge adapter that communicates with MT5 via the bridge gateway.

    This adapter sends commands to the EA through the bridge gateway,
    which are then executed by the BridgeConnectorEA in MT5.

    Performance: Uses a persistent httpx.Client with Keep-Alive connections
    to eliminate TCP handshake overhead on every request.
    """

    def __init__(self, gateway_url: str | None = None) -> None:
        self.settings = get_settings()
        self.gateway_url = (
            gateway_url or self.settings.gateway_url or "http://127.0.0.1:8020"
        )
        self._last_heartbeat: dict[str, Any] = {}
        self._last_heartbeat_time: float = 0.0
        # Persistent HTTP client with Keep-Alive (connection pooling)
        self._client = httpx.Client(
            base_url=self.gateway_url,
            timeout=10.0,
            http2=False,
            limits=httpx.Limits(
                max_keepalive_connections=10,
                max_connections=20,
                keepalive_expiry=30.0,
            ),
        )

    def close(self) -> None:
        """Close the persistent HTTP client."""
        if self._client:
            self._client.close()

    def _check_ea_connected(self) -> bool:
        """Check if EA is connected by checking bridge terminal status."""
        try:
            r = self._client.get("/bridge/terminal/status")
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
            params = {"type": cmd_type, **payload}
            r = self._client.post(
                "/bridge/commands/enqueue",
                params=params,
            )
            r.raise_for_status()
            return r.json()["id"]
        except Exception as e:
            logger.error(f"Failed to enqueue command {cmd_type}: {e}")
            raise

    def _await_result(
        self, request_id: str, timeout_s: float = 10.0, poll_s: float = 0.1
    ) -> dict[str, Any]:
        """Wait for command result.

        Poll interval reduced from 0.5s to 0.1s for 5x faster response detection.
        Uses persistent HTTP client (no TCP handshake per poll).
        """
        import time as _t

        end = _t.time() + timeout_s
        while _t.time() < end:
            try:
                r = self._client.get(f"/bridge/results/{request_id}")
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
            r = self._client.get("/bridge/terminal/status")
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
                margin_level=float(data.get("margin_level", 0.0))
                if data.get("margin_level") is not None
                else None,
                leverage=int(data.get("leverage", 0))
                if data.get("leverage") is not None
                else None,
                profit=float(data.get("profit", 0.0))
                if data.get("profit") is not None
                else None,
                margin_call_level=float(data.get("margin_call_level", 0.0))
                if data.get("margin_call_level") is not None
                else None,
                margin_stop_out_level=float(data.get("margin_stop_out_level", 0.0))
                if data.get("margin_stop_out_level") is not None
                else None,
                currency=data.get("currency"),
                server=data.get("server"),
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
                        magic_number=p.get("magic")
                        if p.get("magic") is not None
                        else None,
                        comment=p.get("comment") or None,
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

    def get_symbol_info(self, symbol: str) -> SymbolInfo | None:
        try:
            symbol_norm = normalize_symbol(symbol)
            result = self._send_command(
                "get_symbol_info", {"symbol": symbol_norm}, timeout_s=10.0
            )
            if result.get("status") != "completed":
                return None

            payload = result.get("result", {}).get("payload")
            if isinstance(payload, str):
                data = json.loads(payload)
            elif isinstance(payload, dict):
                data = payload
            else:
                return None

            if "error" in data:
                return None

            info = SymbolInfo(**data)
            if info.symbol:
                info.symbol = denormalize_symbol(info.symbol)
            return info
        except Exception as e:
            logger.error(f"Get symbol info failed: {e}")
            return None

    def get_deals_history(
        self, limit: int = 100, symbol: str | None = None, days: int = 30
    ) -> list[Deal]:
        try:
            payload: dict[str, Any] = {"limit": limit, "days": days}
            if symbol:
                payload["symbol"] = normalize_symbol(symbol)
            result = self._send_command("get_deals_history", payload, timeout_s=15.0)
            if result.get("status") != "completed":
                return []

            raw_payload = result.get("result", {}).get("payload")
            if isinstance(raw_payload, str):
                data = json.loads(raw_payload)
            elif isinstance(raw_payload, dict):
                data = raw_payload
            else:
                return []

            deals: list[Deal] = []
            for deal_data in data.get("deals", []):
                if "symbol" in deal_data:
                    deal_data["symbol"] = denormalize_symbol(deal_data["symbol"])
                deals.append(Deal(**deal_data))
            return deals
        except Exception as e:
            logger.error(f"Get deals history failed: {e}")
            return []

    def estimate_margin(self, req: MarginEstimateRequest) -> MarginEstimate:
        try:
            symbol_norm = normalize_symbol(req.symbol)
            payload: dict[str, Any] = {
                "symbol": symbol_norm,
                "side": req.side,
                "volume_lots": req.volume_lots,
            }
            if req.price_hint is not None:
                payload["price"] = req.price_hint

            result = self._send_command("estimate_margin", payload, timeout_s=10.0)
            if result.get("status") != "completed":
                return MarginEstimate(
                    required_margin=0.0,
                    comment=result.get("error", "estimate_margin_failed"),
                    raw={},
                )

            raw_payload = result.get("result", {}).get("payload")
            if isinstance(raw_payload, str):
                data = json.loads(raw_payload)
            elif isinstance(raw_payload, dict):
                data = raw_payload
            else:
                data = {}

            if data.get("comment") == "symbol_not_found":
                return MarginEstimate(
                    required_margin=0.0,
                    comment="symbol_not_found",
                    raw=data,
                )

            return MarginEstimate(
                required_margin=float(data.get("required_margin", 0.0) or 0.0),
                leverage=int(data.get("leverage", 0)) if data.get("leverage") else None,
                comment=data.get("comment"),
                raw=data,
            )
        except Exception as e:
            return MarginEstimate(required_margin=0.0, comment=str(e), raw={})

    def simulate_order(self, req: TradeIntent) -> SimulationResult:
        return SimulationResult(intent_id=req.intent_id, status="simulated")

    def submit_order(self, req: TradeIntent) -> ExecutionResult:
        """Submit order via EA bridge command."""
        strategy_id = req.strategy_id or ""
        magic_number: int = 0
        comment: str = ""
        if strategy_id and strategy_id in self.settings.strategy_magic_numbers:
            magic_number = self.settings.strategy_magic_numbers[strategy_id]
        elif strategy_id:
            magic_number = derive_magic_number(strategy_id)

        if strategy_id and req.intent_id and req.session_id:
            comment = compose_comment(strategy_id, req.intent_id, req.session_id)

        try:
            payload: dict[str, Any] = {
                "symbol": req.symbol,
                "side": req.side,
                "volume_lots": req.volume_lots,
                "sl": req.sl or 0,
                "tp": req.tp or 0,
                "deviation": req.deviation_points or 20,
                "magic_number": magic_number,
                "comment": comment,
            }
            if req.strategy_id:
                payload["strategy_id"] = req.strategy_id
            if req.session_id:
                payload["session_id"] = req.session_id
            if req.intent_id:
                payload["intent_id"] = req.intent_id

            result = self._send_command(
                "submit_order",
                payload,
                timeout_s=20.0,
            )

            if result.get("status") != "completed":
                return ExecutionResult(
                    intent_id=req.intent_id,
                    status="error",
                    message=result.get("error", "timeout"),
                    strategy_id=req.strategy_id,
                    session_id=req.session_id,
                    idempotency_key=req.idempotency_key,
                    magic_number=magic_number,
                    comment=comment,
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
                strategy_id=req.strategy_id,
                session_id=req.session_id,
                idempotency_key=req.idempotency_key,
                magic_number=magic_number,
                comment=comment,
            )
        except Exception as e:
            return ExecutionResult(
                intent_id=req.intent_id,
                status="error",
                message=str(e),
                strategy_id=req.strategy_id,
                session_id=req.session_id,
                idempotency_key=req.idempotency_key,
                magic_number=magic_number,
                comment=comment,
            )

    def modify_order(self, req: ModifyOrderRequest) -> ExecutionResult:
        """Modify order via EA bridge command."""
        try:
            payload: dict[str, Any] = {"order_id": req.order_id}
            if req.new_price is not None:
                payload["new_price"] = req.new_price
            if req.new_sl is not None:
                payload["new_sl"] = req.new_sl
            if req.new_tp is not None:
                payload["new_tp"] = req.new_tp
            if req.session_id:
                payload["session_id"] = req.session_id
            if req.strategy_id:
                payload["strategy_id"] = req.strategy_id
            if req.intent_id:
                payload["intent_id"] = req.intent_id
            if req.idempotency_key:
                payload["idempotency_key"] = req.idempotency_key

            result = self._send_command("modify_order", payload, timeout_s=10.0)

            if result.get("status") == "completed":
                return ExecutionResult(
                    intent_id=req.intent_id or "",
                    status="accepted",
                    adapter="EABridgeAdapter",
                    strategy_id=req.strategy_id,
                    session_id=req.session_id,
                    idempotency_key=req.idempotency_key,
                )
            else:
                return ExecutionResult(
                    intent_id=req.intent_id or "",
                    status="error",
                    message=result.get("error", "unknown"),
                    strategy_id=req.strategy_id,
                    session_id=req.session_id,
                    idempotency_key=req.idempotency_key,
                )
        except Exception as e:
            return ExecutionResult(
                intent_id="",
                status="error",
                message=str(e),
                strategy_id=req.strategy_id,
                session_id=req.session_id,
                idempotency_key=req.idempotency_key,
            )

    def get_bars(self, symbol: str, timeframe: str, count: int = 100) -> Bars:
        """Fetch OHLCV bars via EA bridge command.

        The EA already supports get_bars through JsonBars(). This method
        wires the command through the adapter so ExecutionGateway can use it.
        """
        try:
            symbol_norm = normalize_symbol(symbol)
            result = self._send_command(
                "get_bars",
                {"symbol": symbol_norm, "timeframe": timeframe, "count": count},
                timeout_s=15.0,
            )

            if result.get("status") != "completed":
                logger.warning(f"Get bars failed: {result.get('error', 'unknown')}")
                return Bars(symbol=symbol, timeframe=timeframe, data=[])

            raw_payload = result.get("result", {}).get("payload")
            if isinstance(raw_payload, str):
                data = json.loads(raw_payload)
            elif isinstance(raw_payload, dict):
                data = raw_payload
            else:
                return Bars(symbol=symbol, timeframe=timeframe, data=[])

            # EA returns {"symbol": "...", "timeframe": "...", "data": [...]}
            bars_data = data.get("data", [])
            bars = [Bar(**b) for b in bars_data]

            resp_symbol = data.get("symbol", symbol)
            if resp_symbol:
                resp_symbol = denormalize_symbol(resp_symbol)

            return Bars(
                symbol=resp_symbol,
                timeframe=timeframe,
                data=bars,
                source="ea_bridge",
            )
        except Exception as e:
            logger.error(f"Get bars failed: {e}")
            return Bars(symbol=symbol, timeframe=timeframe, data=[])

    def get_indicator(
        self,
        symbol: str,
        timeframe: str,
        indicator: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Fetch indicator values via EA bridge command.

        The EA supports advanced indicator requests through JsonIndicatorAdvanced()
        which parses all parameters from the command string.
        """
        try:
            symbol_norm = normalize_symbol(symbol)
            payload: dict[str, Any] = {
                "symbol": symbol_norm,
                "timeframe": timeframe,
                "indicator": indicator,
            }
            # Forward all optional parameters to the EA
            for key, val in kwargs.items():
                if val is not None:
                    payload[key] = val

            result = self._send_command("get_indicator", payload, timeout_s=15.0)

            if result.get("status") != "completed":
                return {"status": "error", "message": result.get("error", "timeout")}

            raw_payload = result.get("result", {}).get("payload")
            if isinstance(raw_payload, str):
                data = json.loads(raw_payload)
            elif isinstance(raw_payload, dict):
                data = raw_payload
            else:
                data = {}

            # Denormalize symbol in response
            if "symbol" in data:
                data["symbol"] = denormalize_symbol(data["symbol"])

            return data
        except Exception as e:
            logger.error(f"Get indicator failed: {e}")
            return {"status": "error", "message": str(e)}

    def get_ticks(self, symbol: str, count: int = 200) -> dict[str, Any]:
        """Fetch recent tick data via EA bridge command.

        The EA supports this through JsonTicks().
        """
        try:
            symbol_norm = normalize_symbol(symbol)
            result = self._send_command(
                "get_ticks",
                {"symbol": symbol_norm, "count": count},
                timeout_s=15.0,
            )

            if result.get("status") != "completed":
                return {"status": "error", "message": result.get("error", "timeout")}

            raw_payload = result.get("result", {}).get("payload")
            if isinstance(raw_payload, str):
                data = json.loads(raw_payload)
            elif isinstance(raw_payload, dict):
                data = raw_payload
            else:
                data = {}

            # Denormalize symbol in response
            if "symbol" in data:
                data["symbol"] = denormalize_symbol(data["symbol"])

            return data
        except Exception as e:
            logger.error(f"Get ticks failed: {e}")
            return {"status": "error", "message": str(e)}

    def close_position(self, req: ClosePositionRequest) -> ExecutionResult:
        """Close position via EA bridge command."""
        try:
            payload: dict[str, Any] = {"position_id": req.position_id}
            if req.volume is not None:
                payload["volume"] = req.volume
            if req.session_id:
                payload["session_id"] = req.session_id
            if req.strategy_id:
                payload["strategy_id"] = req.strategy_id
            if req.intent_id:
                payload["intent_id"] = req.intent_id
            if req.idempotency_key:
                payload["idempotency_key"] = req.idempotency_key

            result = self._send_command("close_position", payload, timeout_s=20.0)

            if result.get("status") == "completed":
                return ExecutionResult(
                    intent_id=req.intent_id or "",
                    status="accepted",
                    adapter="EABridgeAdapter",
                    strategy_id=req.strategy_id,
                    session_id=req.session_id,
                    idempotency_key=req.idempotency_key,
                )
            else:
                return ExecutionResult(
                    intent_id=req.intent_id or "",
                    status="error",
                    message=result.get("error", "unknown"),
                    strategy_id=req.strategy_id,
                    session_id=req.session_id,
                    idempotency_key=req.idempotency_key,
                )
        except Exception as e:
            return ExecutionResult(
                intent_id="",
                status="error",
                message=str(e),
                strategy_id=req.strategy_id,
                session_id=req.session_id,
                idempotency_key=req.idempotency_key,
            )
