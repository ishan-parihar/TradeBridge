"""Trading Policy Engine — behavioral guardrails for automated trading.

Prevents overtrading, revenge trading, and excessive daily losses.
All limits are configurable per-environment.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, date, timezone
from typing import Optional


@dataclass
class PolicyDecision:
    """Result of a policy evaluation."""

    allowed: bool
    reason: Optional[str] = None
    details: Optional[dict] = None


@dataclass
class TradingLimits:
    """Configurable trading limits (per-session / per-day).

    All limits default to None meaning "no limit / no requirement" —
    gating is opt-in, not opt-out.
    """

    # Trade frequency limits
    max_trades_per_day: int | None = None
    """None = no limit. Set to int to cap daily trades."""

    min_rest_between_trades_sec: int | None = None
    """None = no cooldown. Set to int for minimum seconds between trades."""

    # Loss limits
    max_loss_per_day_pct: float | None = None
    """None = no circuit breaker. Set to float for max daily loss % of equity."""

    max_loss_per_trade_pct: float | None = None
    """None = no per-trade cap. Set to float for max risk % per trade."""

    # Consecutive loss handling
    cooldown_after_consecutive_losses: int | None = None
    """None = no cooldown. Set to int to trigger cool-off after N consecutive losses."""

    cooldown_duration_after_losses_sec: int = 1800
    """30-minute mandatory cool-off after consecutive losses (when cooldown is enabled)."""

    # Confluence requirements
    require_indicator_confluence: bool = False
    """False = advisory only (warnings in details). True = hard block on low confluence."""

    min_confluence_count: int = 2
    """Minimum number of indicators that must agree (when confluence is required)."""

    # Environment safety
    allow_live_trading: bool = False
    """Block live trading unless explicitly enabled."""

    # Breakeven rules
    min_profit_before_breakeven_atr: float | None = None
    """None = no restriction. Set to float to require N x ATR profit before BE move."""

    # Session management
    session_start_time: Optional[str] = None
    """Optional: only allow trading during specific hours (HH:MM UTC)."""

    session_end_time: Optional[str] = None


@dataclass
class TradeRecord:
    """Minimal record for policy tracking (in-memory session journal)."""

    timestamp: float
    symbol: str
    side: str
    entry_price: float
    exit_price: Optional[float] = None
    pnl: Optional[float] = None
    exit_reason: Optional[str] = None
    intent_id: Optional[str] = None


class TradingPolicy:
    """Stateful policy engine that enforces trading guardrails.

    Tracks trade history, consecutive losses, and daily P&L to
    prevent behavioral failures identified in the audit session.
    """

    def __init__(self, limits: Optional[TradingLimits] = None) -> None:
        self.limits = limits or TradingLimits()
        self._trade_log: list[TradeRecord] = []
        self._last_trade_time: Optional[float] = None
        self._consecutive_losses: int = 0
        self._cooldown_until: Optional[float] = None
        self._daily_pnl: float = 0.0
        self._session_date: Optional[date] = None

    def _reset_if_new_day(self) -> None:
        """Reset daily counters when the date changes."""
        today = datetime.now(timezone.utc).date()
        if self._session_date is None:
            # First call — initialize session date but don't reset data
            self._session_date = today
            return
        if self._session_date != today:
            self._session_date = today
            self._daily_pnl = 0.0
            self._consecutive_losses = 0
            self._trade_log = []
            self._last_trade_time = None
            self._cooldown_until = None

    def _today_trades(self) -> list[TradeRecord]:
        """Count of trades placed today."""
        self._reset_if_new_day()
        return self._trade_log

    def record_trade(self, record: TradeRecord) -> None:
        """Record a trade that was executed (for future policy decisions)."""
        self._trade_log.append(record)
        self._last_trade_time = record.timestamp

        if record.pnl is not None:
            self._daily_pnl += record.pnl
            if record.pnl < 0:
                self._consecutive_losses += 1
            else:
                self._consecutive_losses = 0

    def validate_submit_order(
        self,
        environment: str,
        equity: Optional[float] = None,
        proposed_risk_pct: Optional[float] = None,
        indicator_agreements: Optional[int] = None,
        approval_token: Optional[str] = None,
    ) -> PolicyDecision:
        """Full policy check before allowing an order submission.

        Args:
            environment: "demo", "live", or "paper"
            equity: Current account equity
            proposed_risk_pct: Risk % of equity for this trade
            indicator_agreements: Number of indicators agreeing with the trade
            approval_token: Override token for emergency bypass
        """
        self._reset_if_new_day()

        # 1. Environment gate
        if environment != "demo" and not self.limits.allow_live_trading:
            if environment == "live":
                return PolicyDecision(
                    allowed=False,
                    reason="live_trading_blocked",
                    details={
                        "message": "Live trading is disabled. Enable in policy config."
                    },
                )
            return PolicyDecision(
                allowed=False,
                reason="non-demo environment blocked in R&D",
            )

        # 2. Daily trade limit (opt-in)
        if self.limits.max_trades_per_day is not None:
            trade_count = len(self._trade_log)
            if trade_count >= self.limits.max_trades_per_day:
                return PolicyDecision(
                    allowed=False,
                    reason="daily_trade_limit_reached",
                    details={
                        "message": f"Daily trade limit reached ({trade_count}/{self.limits.max_trades_per_day}). Cool off until tomorrow.",
                        "trades_today": trade_count,
                        "max_trades": self.limits.max_trades_per_day,
                    },
                )

        # 3. Minimum rest between trades (opt-in)
        if self.limits.min_rest_between_trades_sec is not None:
            if self._last_trade_time is not None:
                elapsed = time.time() - self._last_trade_time
                if elapsed < self.limits.min_rest_between_trades_sec:
                    remaining = int(self.limits.min_rest_between_trades_sec - elapsed)
                    return PolicyDecision(
                        allowed=False,
                        reason="rest_period_active",
                        details={
                            "message": f"Must wait {remaining}s before next trade (min {self.limits.min_rest_between_trades_sec}s between trades).",
                            "seconds_remaining": remaining,
                        },
                    )

        # 4. Daily loss circuit breaker (opt-in)
        if self.limits.max_loss_per_day_pct is not None:
            if equity is not None and equity > 0:
                daily_loss_pct = abs(min(self._daily_pnl, 0.0) / equity * 100)
                if daily_loss_pct >= self.limits.max_loss_per_day_pct:
                    return PolicyDecision(
                        allowed=False,
                        reason="daily_loss_circuit_breaker",
                        details={
                            "message": f"Daily loss limit reached ({daily_loss_pct:.1f}% of equity). Trading halted for today.",
                            "daily_pnl": self._daily_pnl,
                            "loss_pct": daily_loss_pct,
                            "max_loss_pct": self.limits.max_loss_per_day_pct,
                        },
                    )

        # 5. Per-trade risk limit (opt-in)
        if (
            self.limits.max_loss_per_trade_pct is not None
            and proposed_risk_pct is not None
        ):
            if proposed_risk_pct > self.limits.max_loss_per_trade_pct:
                return PolicyDecision(
                    allowed=False,
                    reason="trade_risk_exceeds_limit",
                    details={
                        "message": f"Proposed risk ({proposed_risk_pct:.1f}%) exceeds max per-trade risk ({self.limits.max_loss_per_trade_pct}%).",
                        "proposed_risk_pct": proposed_risk_pct,
                        "max_risk_pct": self.limits.max_loss_per_trade_pct,
                    },
                )

        # 6. Consecutive loss cooldown (opt-in)
        if self.limits.cooldown_after_consecutive_losses is not None:
            if (
                self._consecutive_losses
                >= self.limits.cooldown_after_consecutive_losses
            ):
                if self._cooldown_until is None:
                    self._cooldown_until = (
                        time.time() + self.limits.cooldown_duration_after_losses_sec
                    )

                if time.time() < self._cooldown_until:
                    remaining = int(self._cooldown_until - time.time())
                    return PolicyDecision(
                        allowed=False,
                        reason="consecutive_loss_cooldown",
                        details={
                            "message": f"Cooldown active after {self._consecutive_losses} consecutive losses. Wait {remaining}s.",
                            "consecutive_losses": self._consecutive_losses,
                            "seconds_remaining": remaining,
                        },
                    )
                else:
                    # Cooldown expired, reset
                    self._cooldown_until = None
                    self._consecutive_losses = 0

        # 7. Indicator confluence requirement
        if (
            self.limits.require_indicator_confluence
            and indicator_agreements is not None
        ):
            if indicator_agreements < self.limits.min_confluence_count:
                return PolicyDecision(
                    allowed=False,
                    reason="insufficient_indicator_confluence",
                    details={
                        "message": f"Only {indicator_agreements} indicator(s) agree. Need {self.limits.min_confluence_count}+ for confluence.",
                        "agreements": indicator_agreements,
                        "required": self.limits.min_confluence_count,
                    },
                )

        # All checks passed — build response
        trade_count = len(self._trade_log)
        result_details: dict = {
            "trades_today": trade_count,
            "daily_pnl": self._daily_pnl,
            "consecutive_losses": self._consecutive_losses,
        }

        # Advisory warnings when confluence is low but not hard-gated
        if (
            not self.limits.require_indicator_confluence
            and indicator_agreements is not None
            and indicator_agreements < self.limits.min_confluence_count
        ):
            result_details["advisory"] = {
                "low_confluence": f"Only {indicator_agreements} indicator(s) agree. Consider waiting for {self.limits.min_confluence_count}+ confirmations.",
                "agreements": indicator_agreements,
                "recommended": self.limits.min_confluence_count,
            }

        return PolicyDecision(
            allowed=True,
            details=result_details,
        )

    def validate_breakeven_move(
        self,
        profit_points: float,
        atr_points: float,
    ) -> PolicyDecision:
        """Check if it's safe to move SL to breakeven."""
        if self.limits.min_profit_before_breakeven_atr is None:
            return PolicyDecision(allowed=True)

        if atr_points <= 0:
            return PolicyDecision(
                allowed=False,
                reason="atr_unavailable",
                details={"message": "Cannot validate BE move without ATR data."},
            )

        required = self.limits.min_profit_before_breakeven_atr * atr_points
        if profit_points < required:
            return PolicyDecision(
                allowed=False,
                reason="premature_breakeven",
                details={
                    "message": f"Price has moved {profit_points:.0f} points. Need {required:.0f} points ({self.limits.min_profit_before_breakeven_atr}x ATR of {atr_points:.0f}) before moving to BE.",
                    "profit_points": profit_points,
                    "required_points": required,
                    "atr_points": atr_points,
                },
            )

        return PolicyDecision(allowed=True)

    def get_status(self, equity: Optional[float] = None) -> dict:
        """Return current policy state for dashboard/debugging."""
        self._reset_if_new_day()
        status: dict = {
            "trades_today": len(self._trade_log),
            "max_trades_per_day": self.limits.max_trades_per_day,
            "daily_pnl": self._daily_pnl,
            "consecutive_losses": self._consecutive_losses,
            "cooldown_active": self._cooldown_until is not None
            and time.time() < (self._cooldown_until or 0),
        }
        if equity:
            status["daily_loss_pct"] = (
                abs(min(self._daily_pnl, 0.0) / equity * 100) if equity > 0 else 0.0
            )
        if self._cooldown_until:
            remaining = max(0, int(self._cooldown_until - time.time()))
            status["cooldown_seconds_remaining"] = remaining
        return status

    def update_limits(self, **kwargs) -> dict:
        """Update trading limits at runtime. Only updates fields that are provided.

        Returns the full current limits configuration after update.
        """
        valid_fields = {f.name for f in TradingLimits.__dataclass_fields__.values()}
        applied: dict = {}
        skipped: list[str] = []

        for key, value in kwargs.items():
            if key in valid_fields:
                setattr(self.limits, key, value)
                applied[key] = value
            else:
                skipped.append(key)

        result: dict = {"applied": applied, "limits": self.get_limits()}
        if skipped:
            result["skipped_unknown_fields"] = skipped
        return result

    def get_limits(self) -> dict:
        """Return current limits configuration as a dict."""
        return {
            f.name: getattr(self.limits, f.name)
            for f in TradingLimits.__dataclass_fields__.values()
        }


# Global singleton for use across the application
_policy_instance: Optional[TradingPolicy] = None


def get_policy(limits: Optional[TradingLimits] = None) -> TradingPolicy:
    """Get or create the global trading policy instance."""
    global _policy_instance
    if _policy_instance is None:
        _policy_instance = TradingPolicy(limits=limits)
    return _policy_instance


def reset_policy(limits: Optional[TradingLimits] = None) -> TradingPolicy:
    """Reset the global policy instance (for testing or reconfiguration)."""
    global _policy_instance
    _policy_instance = TradingPolicy(limits=limits)
    return _policy_instance


# Legacy compatibility — still used by MCP server
def validate_submit_order(
    environment: str, approval_token: Optional[str] = None
) -> PolicyDecision:
    """Legacy wrapper for backwards compatibility."""
    policy = get_policy()
    return policy.validate_submit_order(
        environment=environment, approval_token=approval_token
    )
