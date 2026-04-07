"""Portfolio Exposure & Risk Gate Service.

Aggregates the agent's current risk picture across open positions and pending orders,
provides correlation-adjusted exposure metrics, and implements a pre-trade gate
that warns when proposed trades would push the portfolio past risk thresholds.

All thresholds are ADVISORY only — returns allowed=False with reasoning;
does NOT block or auto-reject.
"""

from __future__ import annotations

from typing import Any, Callable


# Static correlation matrix for major forex pairs (symmetric).
# Positive = same direction (both USD-long or both USD-short), negative = inverse.
_CORRELATION_MATRIX: dict[str, dict[str, float]] = {
    "EURUSD": {
        "EURUSD": 1.00,
        "GBPUSD": 0.80,
        "XAUUSD": -0.40,
        "USDJPY": -0.70,
        "USDCHF": -0.85,
        "AUDUSD": 0.75,
        "NZDUSD": 0.70,
        "USDCAD": -0.75,
    },
    "GBPUSD": {
        "EURUSD": 0.80,
        "GBPUSD": 1.00,
        "XAUUSD": -0.35,
        "USDJPY": -0.60,
        "USDCHF": -0.70,
        "AUDUSD": 0.65,
        "NZDUSD": 0.60,
        "USDCAD": -0.65,
    },
    "XAUUSD": {
        "EURUSD": -0.40,
        "GBPUSD": -0.35,
        "XAUUSD": 1.00,
        "USDJPY": 0.30,
        "USDCHF": 0.35,
        "AUDUSD": -0.30,
        "NZDUSD": -0.25,
        "USDCAD": 0.25,
    },
    "USDJPY": {
        "EURUSD": -0.70,
        "GBPUSD": -0.60,
        "XAUUSD": 0.30,
        "USDJPY": 1.00,
        "USDCHF": 0.65,
        "AUDUSD": -0.55,
        "NZDUSD": -0.50,
        "USDCAD": 0.60,
    },
    "USDCHF": {
        "EURUSD": -0.85,
        "GBPUSD": -0.70,
        "XAUUSD": 0.35,
        "USDJPY": 0.65,
        "USDCHF": 1.00,
        "AUDUSD": -0.70,
        "NZDUSD": -0.65,
        "USDCAD": 0.80,
    },
    "AUDUSD": {
        "EURUSD": 0.75,
        "GBPUSD": 0.65,
        "XAUUSD": -0.30,
        "USDJPY": -0.55,
        "USDCHF": -0.70,
        "AUDUSD": 1.00,
        "NZDUSD": 0.85,
        "USDCAD": -0.60,
    },
    "NZDUSD": {
        "EURUSD": 0.70,
        "GBPUSD": 0.60,
        "XAUUSD": -0.25,
        "USDJPY": -0.50,
        "USDCHF": -0.65,
        "AUDUSD": 0.85,
        "NZDUSD": 1.00,
        "USDCAD": -0.55,
    },
    "USDCAD": {
        "EURUSD": -0.75,
        "GBPUSD": -0.65,
        "XAUUSD": 0.25,
        "USDJPY": 0.60,
        "USDCHF": 0.80,
        "AUDUSD": -0.60,
        "NZDUSD": -0.55,
        "USDCAD": 1.00,
    },
}

# Default contract sizes for common symbols (base currency units per 1.0 lot).
_DEFAULT_CONTRACT_SIZE: dict[str, float] = {
    "EURUSD": 100_000,
    "GBPUSD": 100_000,
    "AUDUSD": 100_000,
    "NZDUSD": 100_000,
    "USDJPY": 100_000,
    "USDCHF": 100_000,
    "USDCAD": 100_000,
    "XAUUSD": 100,  # 100 troy ounces per lot
    "XAUEUR": 100,
    "XAGUSD": 5_000,  # 5000 troy ounces per lot
}

# Symbols whose LONG side means the agent is USD-long (i.e. USD is the base currency).
_USD_BASE_SYMBOLS = {
    "USDJPY",
    "USDCHF",
    "USDCAD",
    "USDSEK",
    "USDNOK",
    "USDSGD",
    "USDHKD",
    "USDMXN",
}

# Symbols that behave like USD-quoted pairs (XXX/USD) — long = USD-short.
_USD_QUOTE_SYMBOLS = {
    "EURUSD",
    "GBPUSD",
    "AUDUSD",
    "NZDUSD",
    "XAUUSD",
    "XAGUSD",
    "XAUEUR",
}

# Thresholds for the pre-trade gate
_CORRELATED_EXPOSURE_MULTIPLE = 2.0  # Reject if correlated exposure > 2x equity
_MARGIN_USAGE_LIMIT_PCT = 50.0  # Reject if margin usage would exceed 50%


class PortfolioRiskService:
    """Aggregates portfolio risk picture across open positions and pending orders."""

    CORRELATION_MATRIX = _CORRELATION_MATRIX

    def __init__(
        self,
        get_positions_fn: Callable[[], list[Any]],
        get_orders_fn: Callable[[], list[Any]],
        get_account_fn: Callable[[], Any],
        contract_sizes: dict[str, float] | None = None,
    ):
        """Accept callable functions for fetching positions/orders/account (for decoupling)."""
        self._get_positions = get_positions_fn
        self._get_orders = get_orders_fn
        self._get_account = get_account_fn
        self._contract_sizes: dict[str, float] | None = contract_sizes
        self._correlation_overrides: dict[str, float] = {}

    def _contract_size(self, symbol: str) -> float:
        """Return the contract size (base units per 1.0 lot) for a symbol.

        Checks live contract sizes first, then falls back to defaults.
        """
        sym = symbol.upper().replace("M", "").replace("m", "")
        # Check live contract sizes from symbol_info
        if self._contract_sizes and sym in self._contract_sizes:
            return self._contract_sizes[sym]
        # Fall back to static defaults
        if sym in _DEFAULT_CONTRACT_SIZE:
            return _DEFAULT_CONTRACT_SIZE[sym]
        # Default for unknown forex pairs
        return 100_000.0

    @classmethod
    def update_contract_sizes(cls, symbol: str, contract_size: float) -> None:
        """Update the default contract size for a symbol in the static defaults.

        Note: This modifies the module-level _DEFAULT_CONTRACT_SIZE dict.
        For instance-level overrides, pass contract_sizes to __init__.
        """
        _DEFAULT_CONTRACT_SIZE[symbol.upper()] = contract_size

    @staticmethod
    def compute_rolling_correlation(
        close_prices_a: list[float],
        close_prices_b: list[float],
        window: int = 50,
    ) -> float:
        """Pearson correlation over a rolling window. Returns 0.0 if insufficient data or zero std dev."""
        n = min(len(close_prices_a), len(close_prices_b))
        if n < window:
            return 0.0

        a = close_prices_a[-window:]
        b = close_prices_b[-window:]
        w = len(a)

        mean_a = sum(a) / w
        mean_b = sum(b) / w

        cov_ab = 0.0
        var_a = 0.0
        var_b = 0.0
        for i in range(w):
            diff_a = a[i] - mean_a
            diff_b = b[i] - mean_b
            cov_ab += diff_a * diff_b
            var_a += diff_a * diff_a
            var_b += diff_b * diff_b

        denom_sq = var_a * var_b
        if denom_sq <= 0:
            return 0.0

        return cov_ab / (denom_sq**0.5)

    @staticmethod
    def _usd_direction(symbol: str, side: str) -> str:
        """Return the USD exposure direction: 'usd_long' or 'usd_short'.

        - USD-base pairs (USDJPY, …): long → usd_long, sell → usd_short
        - USD-quote pairs (EURUSD, XAUUSD, …): long → usd_short, sell → usd_long
        """
        sym = symbol.upper().replace("M", "").replace("m", "")
        if sym in _USD_BASE_SYMBOLS:
            return "usd_long" if side.lower() == "buy" else "usd_short"
        # Default: treat as USD-quote (XXX/USD style)
        return "usd_short" if side.lower() == "buy" else "usd_long"

    def _notional_usd(self, symbol: str, volume: float, current_price: float) -> float:
        """Compute notional value in USD."""
        cs = self._contract_size(symbol)
        return volume * cs * current_price

    def _correlation(self, sym_a: str, sym_b: str) -> float:
        """Return correlation between two symbols. Checks overrides first, then static matrix."""
        a = sym_a.upper().replace("M", "").replace("m", "")
        b = sym_b.upper().replace("M", "").replace("m", "")
        key = f"{a}_{b}" if a <= b else f"{b}_{a}"
        if key in self._correlation_overrides:
            return self._correlation_overrides[key]
        if a in _CORRELATION_MATRIX and b in _CORRELATION_MATRIX[a]:
            return _CORRELATION_MATRIX[a][b]
        return 0.0

    def set_correlation_override(
        self, symbol_a: str, symbol_b: str, correlation: float
    ) -> None:
        """Override the static correlation value for a symbol pair."""
        a = symbol_a.upper().replace("M", "").replace("m", "")
        b = symbol_b.upper().replace("M", "").replace("m", "")
        key = f"{a}_{b}" if a <= b else f"{b}_{a}"
        self._correlation_overrides[key] = correlation

    def get_correlation_matrix_snapshot(self) -> dict[str, dict[str, float]]:
        """Return effective correlation matrix (static merged with overrides)."""
        symbols = set(_CORRELATION_MATRIX.keys())
        for key in self._correlation_overrides:
            for s in key.split("_"):
                symbols.add(s)
        result: dict[str, dict[str, float]] = {}
        for s_a in symbols:
            result[s_a] = {}
            for s_b in symbols:
                result[s_a][s_b] = self._correlation(s_a, s_b)
        return result

    def update_from_symbol_info(self, symbol: str, contract_size: float) -> None:
        """Update contract size for a symbol from live symbol_info data."""
        if self._contract_sizes is None:
            self._contract_sizes = {}
        self._contract_sizes[symbol.upper()] = contract_size

    def _compute_exposure(
        self,
        positions: list[Any],
        account: Any | None = None,
    ) -> dict:
        """Core exposure computation for a list of position-like objects."""
        equity = 0.0
        margin_used = 0.0
        free_margin = 1.0

        if account is not None:
            equity = float(getattr(account, "equity", 0) or 0)
            margin_used = float(getattr(account, "margin", 0) or 0)
            free_margin = float(getattr(account, "free_margin", 0) or 0)
            if free_margin <= 0:
                free_margin = 1.0

        agg_exposure: dict[str, dict] = {}
        for pos in positions:
            sym = getattr(pos, "symbol", "").upper()
            side = getattr(pos, "side", "buy").lower()
            volume = float(getattr(pos, "volume", 0) or 0)
            price = float(
                getattr(pos, "mark_price", 0) or getattr(pos, "entry_price", 0) or 0
            )
            if volume <= 0 or price <= 0:
                continue

            notional = self._notional_usd(sym, volume, price)
            usd_dir = self._usd_direction(sym, side)

            if sym not in agg_exposure:
                agg_exposure[sym] = {
                    "side": side,
                    "notional_usd": 0.0,
                    "usd_direction": usd_dir,
                    "raw_notional_short": 0.0,
                    "raw_notional_long": 0.0,
                }

            if usd_dir == "usd_short":
                agg_exposure[sym]["raw_notional_short"] += notional
            else:
                agg_exposure[sym]["raw_notional_long"] += notional

        for sym, data in agg_exposure.items():
            net_short = data["raw_notional_short"]
            net_long = data["raw_notional_long"]
            if net_short >= net_long:
                data["notional_usd"] = net_short - net_long
                data["usd_direction"] = "usd_short"
                data["side"] = "buy"
            else:
                data["notional_usd"] = net_long - net_short
                data["usd_direction"] = "usd_long"
                data["side"] = "sell"

        agg_exposure = {
            k: v for k, v in agg_exposure.items() if v["notional_usd"] > 0.001
        }

        if not agg_exposure:
            margin_usage_pct = (
                (margin_used / free_margin * 100) if free_margin > 0 else 0.0
            )
            return {
                "total_exposure_usd": 0,
                "exposure_by_symbol": {},
                "correlation_groups": [],
                "margin_usage_pct": round(margin_usage_pct, 2),
                "risk_score": 0,
            }

        sym_keys = list(agg_exposure.keys())
        for sym_i in sym_keys:
            corr_exp = 0.0
            dir_i = agg_exposure[sym_i]["usd_direction"]
            not_i = agg_exposure[sym_i]["notional_usd"]
            for sym_j in sym_keys:
                corr = self._correlation(sym_i, sym_j)
                dir_j = agg_exposure[sym_j]["usd_direction"]
                not_j = agg_exposure[sym_j]["notional_usd"]
                if dir_i == dir_j:
                    corr_exp += not_j * corr
                else:
                    corr_exp -= not_j * corr
            agg_exposure[sym_i]["notional_usd"] = round(
                agg_exposure[sym_i]["notional_usd"], 2
            )
            if sym_i not in _CORRELATION_MATRIX:
                corr_exp = not_i
            agg_exposure[sym_i]["correlated_exposure_usd"] = round(corr_exp, 2)

        groups: dict[str, dict] = {}
        for sym, data in agg_exposure.items():
            group_name = data["usd_direction"]
            if group_name not in groups:
                groups[group_name] = {
                    "group": group_name,
                    "symbols": [],
                    "total_notional": 0.0,
                    "effective_notional": 0.0,
                }
            groups[group_name]["symbols"].append(sym)
            groups[group_name]["total_notional"] += data["notional_usd"]
            groups[group_name]["effective_notional"] += data["correlated_exposure_usd"]

        for g in groups.values():
            g["total_notional"] = round(g["total_notional"], 2)
            g["effective_notional"] = round(g["effective_notional"], 2)

        correlation_groups = sorted(
            groups.values(), key=lambda g: g["effective_notional"], reverse=True
        )

        total_exposure = sum(
            abs(d["correlated_exposure_usd"]) for d in agg_exposure.values()
        )

        margin_usage_pct = (margin_used / free_margin * 100) if free_margin > 0 else 0.0

        risk_score = self._compute_risk_score(
            total_exposure=total_exposure,
            equity=equity,
            margin_usage_pct=margin_usage_pct,
            n_positions=len(agg_exposure),
        )

        return {
            "total_exposure_usd": round(total_exposure, 2),
            "exposure_by_symbol": agg_exposure,
            "correlation_groups": correlation_groups,
            "margin_usage_pct": round(margin_usage_pct, 2),
            "risk_score": risk_score,
        }

    @staticmethod
    def _compute_risk_score(
        total_exposure: float,
        equity: float,
        margin_usage_pct: float,
        n_positions: int,
    ) -> int:
        """Compute risk score 0-100 based on exposure concentration and margin usage."""
        if equity <= 0:
            if total_exposure > 0:
                return 100
            return 0

        exposure_ratio = total_exposure / equity
        # Exposure component: 0 at 0x, 60 at 2x, scales linearly
        exposure_score = min(60, exposure_ratio / 2.0 * 60)

        # Margin component: 0 at 0%, 40 at 50%
        margin_score = min(40, margin_usage_pct / 50.0 * 40)

        # Concentration penalty: more correlated positions → higher score
        concentration_penalty = min(10, max(0, (n_positions - 1) * 2))

        raw = exposure_score + margin_score + concentration_penalty
        return int(min(100, max(0, raw)))

    def get_exposure(self) -> dict:
        """Compute notional exposure by currency direction across open positions."""
        positions = self._get_positions()
        account = self._get_account()
        return self._compute_exposure(positions, account)

    def get_projection_with_pending(self) -> dict:
        """Same as get_exposure but includes pending orders as-if-filled."""
        positions = list(self._get_positions())
        orders = list(self._get_orders())
        account = self._get_account()

        # Treat pending orders as positions (using their price as mark_price)
        for order in orders:

            class _PendingAsPosition:
                symbol: str
                side: str
                volume: float
                mark_price: float
                entry_price: float

                def __init__(self, o: Any):
                    self.symbol = getattr(o, "symbol", "").upper()
                    self.side = getattr(o, "side", "buy").lower()
                    self.volume = float(getattr(o, "volume", 0) or 0)
                    self.mark_price = float(getattr(o, "price", 0) or 0)
                    self.entry_price = self.mark_price

            positions.append(_PendingAsPosition(order))

        return self._compute_exposure(positions, account)

    def pre_trade_gate(
        self,
        symbol: str,
        side: str,
        volume: float,
        sl_distance: float,
    ) -> dict:
        """Pre-trade risk gate — check if proposed trade is safe.

        Args:
            symbol: e.g., "XAUUSD"
            side: "buy" or "sell"
            volume: lot size
            sl_distance: stop loss distance in price units

        Returns:
            Dict with allowed flag, reason, current/projected exposures, risk score.
        """
        current = self.get_exposure()
        projected = self.get_projection_with_pending()

        # Add the proposed trade to the projection
        # We need to add it to the projected exposure dict
        positions = list(self._get_positions())
        orders = list(self._get_orders())
        account = self._get_account()

        equity = float(getattr(account, "equity", 0) or 0) if account else 0

        # Get current price from mark_price of existing position or use entry as proxy
        current_price = 0.0
        for pos in positions:
            if getattr(pos, "symbol", "").upper() == symbol.upper():
                current_price = float(
                    getattr(pos, "mark_price", 0) or getattr(pos, "entry_price", 0) or 0
                )
                break
        if current_price <= 0:
            # Use entry price from first matching pending order
            for order in orders:
                if getattr(order, "symbol", "").upper() == symbol.upper():
                    current_price = float(getattr(order, "price", 0) or 0)
                    break
        if current_price <= 0:
            current_price = 1.0  # fallback

        class _ProposedPosition:
            def __init__(self, sym: str, s: str, v: float, p: float):
                self.symbol = sym
                self.side = s
                self.volume = v
                self.mark_price = p
                self.entry_price = p

        all_positions = positions + [
            _ProposedPosition(symbol.upper(), side, volume, current_price)
        ]

        # Also include pending orders as positions
        for order in orders:
            all_positions.append(
                _ProposedPosition(
                    getattr(order, "symbol", "").upper(),
                    getattr(order, "side", "buy").lower(),
                    float(getattr(order, "volume", 0) or 0),
                    float(getattr(order, "price", 0) or 0),
                )
            )

        projected_full = self._compute_exposure(all_positions, account)

        # Gate checks
        allowed = True
        reason = "ok"

        # Check 1: correlated exposure exceeds threshold
        projected_correlated = projected_full.get("total_exposure_usd", 0)
        if equity > 0 and projected_correlated > equity * _CORRELATED_EXPOSURE_MULTIPLE:
            allowed = False
            reason = "correlated_exposure_exceeds_threshold"
        elif equity <= 0 and projected_correlated > 0:
            # Zero equity with any exposure → reject
            allowed = False
            reason = "correlated_exposure_exceeds_threshold"

        # Check 2: margin would exceed limit
        projected_margin_pct = projected_full.get("margin_usage_pct", 0)
        if projected_margin_pct > _MARGIN_USAGE_LIMIT_PCT:
            allowed = False
            reason = "margin_would_exceed_limit"

        return {
            "allowed": allowed,
            "reason": reason,
            "current_exposure": current,
            "projected_exposure": projected_full,
            "risk_score": projected_full.get("risk_score", 0),
        }
