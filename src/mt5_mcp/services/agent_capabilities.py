from __future__ import annotations

from math import floor, sqrt
from typing import Iterable


def summarize_deals(deals: list[dict]) -> dict[str, float | int | None]:
    closed = []
    for deal in deals:
        entry = str(deal.get("entry", "")).lower()
        if entry not in {"out", "out_by", "inout"}:
            continue

        profit = float(deal.get("profit", 0.0) or 0.0)
        commission = float(deal.get("commission", 0.0) or 0.0)
        swap = float(deal.get("swap", 0.0) or 0.0)
        fee = float(deal.get("fee", 0.0) or 0.0)
        realized = profit + commission + swap + fee
        closed.append(realized)

    closed_trades = len(closed)
    winning = [value for value in closed if value > 0]
    losing = [abs(value) for value in closed if value < 0]

    gross_profit = sum(winning)
    gross_loss = sum(losing)
    net_profit = sum(closed)
    winning_trades = len(winning)
    losing_trades = len(losing)

    return {
        "closed_trades": closed_trades,
        "winning_trades": winning_trades,
        "losing_trades": losing_trades,
        "win_rate": (winning_trades / closed_trades) if closed_trades else 0.0,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "net_profit": net_profit,
        "average_win": (gross_profit / winning_trades) if winning_trades else 0.0,
        "average_loss": (gross_loss / losing_trades) if losing_trades else 0.0,
        "profit_factor": (gross_profit / gross_loss) if gross_loss else None,
        "expectancy": (net_profit / closed_trades) if closed_trades else 0.0,
    }


def calculate_position_size(
    *,
    equity: float,
    risk_percent: float,
    entry_price: float,
    stop_loss_price: float,
    tick_size: float,
    tick_value: float,
    volume_min: float,
    volume_max: float,
    volume_step: float,
) -> dict[str, float | bool]:
    risk_amount = max(equity, 0.0) * max(risk_percent, 0.0) / 100.0
    stop_distance_ticks = (
        abs(entry_price - stop_loss_price) / tick_size if tick_size else 0.0
    )
    loss_per_lot = stop_distance_ticks * tick_value
    raw_volume = (risk_amount / loss_per_lot) if loss_per_lot > 0 else 0.0

    if volume_step > 0:
        stepped_volume = floor((raw_volume / volume_step) + 1e-9) * volume_step
        step_digits = _decimal_places(volume_step)
        stepped_volume = round(stepped_volume, step_digits)
    else:
        stepped_volume = raw_volume

    within_risk_budget = stepped_volume >= volume_min or raw_volume >= volume_min
    volume_lots = (
        min(max(stepped_volume, volume_min), volume_max) if stepped_volume > 0 else 0.0
    )
    if raw_volume and raw_volume < volume_min:
        within_risk_budget = False
        volume_lots = round(volume_min, _decimal_places(volume_step or volume_min))

    estimated_loss = volume_lots * loss_per_lot

    return {
        "risk_amount": risk_amount,
        "stop_distance_ticks": stop_distance_ticks,
        "raw_volume_lots": raw_volume,
        "volume_lots": volume_lots,
        "estimated_loss_at_stop": estimated_loss,
        "within_risk_budget": within_risk_budget,
    }


def validate_trade_setup(
    *,
    symbol_info: dict,
    account_summary: dict,
    side: str,
    order_kind: str,
    volume_lots: float,
    current_bid: float,
    current_ask: float,
    entry_price: float | None,
    sl: float | None,
    tp: float | None,
    required_margin: float | None,
) -> dict[str, object]:
    errors: list[str] = []
    warnings: list[str] = []

    volume_min = float(symbol_info.get("volume_min", 0.0) or 0.0)
    volume_max = float(symbol_info.get("volume_max", 0.0) or 0.0)
    volume_step = float(symbol_info.get("volume_step", 0.0) or 0.0)
    point = float(symbol_info.get("point", 0.0) or 0.0)
    stops_level_points = float(symbol_info.get("stops_level_points", 0.0) or 0.0)
    digits = int(symbol_info.get("digits", 5) or 5)
    side = side.lower()
    order_kind = order_kind.lower()

    if volume_min and volume_lots < volume_min:
        errors.append("volume below broker minimum")
    if volume_max and volume_lots > volume_max:
        errors.append("volume above broker maximum")
    if volume_step and not _is_step_aligned(
        volume_lots, volume_min or volume_step, volume_step
    ):
        errors.append("volume does not align with broker step")

    if order_kind == "limit" and entry_price is not None:
        if side == "buy" and entry_price >= current_ask:
            errors.append("buy limit entry must be below current ask")
        if side == "sell" and entry_price <= current_bid:
            errors.append("sell limit entry must be above current bid")
    if order_kind == "stop" and entry_price is not None:
        if side == "buy" and entry_price <= current_ask:
            errors.append("buy stop entry must be above current ask")
        if side == "sell" and entry_price >= current_bid:
            errors.append("sell stop entry must be below current bid")

    min_distance = stops_level_points * point
    market_price = current_bid if side == "buy" else current_ask

    if sl is not None and sl > 0 and min_distance > 0:
        sl_distance = abs(market_price - sl)
        if sl_distance < min_distance:
            errors.append("stop loss too close to market for broker minimum")
    if tp is not None and tp > 0 and min_distance > 0:
        tp_distance = abs(tp - market_price)
        if tp_distance < min_distance:
            errors.append("take profit too close to market for broker minimum")

    free_margin = float(account_summary.get("free_margin", 0.0) or 0.0)
    if required_margin is not None and required_margin > free_margin:
        errors.append("insufficient free margin for proposed trade")
    elif (
        required_margin is not None
        and free_margin > 0
        and required_margin / free_margin > 0.7
    ):
        warnings.append("trade consumes more than 70% of free margin")

    normalized_entry = round(entry_price, digits) if entry_price is not None else None
    normalized_sl = round(sl, digits) if sl is not None else None
    normalized_tp = round(tp, digits) if tp is not None else None

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "normalized_entry_price": normalized_entry,
        "normalized_sl": normalized_sl,
        "normalized_tp": normalized_tp,
    }


def build_volatility_profile(
    *,
    symbol: str,
    timeframe: str,
    bars: list[dict],
    atr_value: float,
) -> dict[str, float | int | str | None]:
    ranges = [
        float(bar["high"]) - float(bar["low"])
        for bar in bars
        if "high" in bar and "low" in bar
    ]
    closes = [float(bar["close"]) for bar in bars if "close" in bar]
    last_close = closes[-1] if closes else 0.0
    average_range = sum(ranges) / len(ranges) if ranges else 0.0

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "bars_used": len(bars),
        "last_close": last_close,
        "atr_value": atr_value,
        "average_range": average_range,
        "average_range_percent": (average_range / last_close * 100.0)
        if last_close
        else 0.0,
        "atr_percent_of_price": (atr_value / last_close * 100.0) if last_close else 0.0,
    }


def compute_correlation_matrix(
    close_series: dict[str, list[float]],
) -> dict[str, dict[str, float | None]]:
    returns_map = {symbol: _returns(values) for symbol, values in close_series.items()}
    matrix: dict[str, dict[str, float | None]] = {}

    for left_symbol, left_returns in returns_map.items():
        row: dict[str, float | None] = {}
        for right_symbol, right_returns in returns_map.items():
            row[right_symbol] = _pearson(left_returns, right_returns)
        matrix[left_symbol] = row

    return matrix


def _returns(values: Iterable[float]) -> list[float]:
    points = list(values)
    return [
        current / previous - 1.0
        for previous, current in zip(points, points[1:])
        if previous
    ]


def _pearson(left: list[float], right: list[float]) -> float | None:
    size = min(len(left), len(right))
    if size < 2:
        return None

    x = left[-size:]
    y = right[-size:]
    mean_x = sum(x) / size
    mean_y = sum(y) / size
    numerator = sum((a - mean_x) * (b - mean_y) for a, b in zip(x, y))
    denom_left = sqrt(sum((a - mean_x) ** 2 for a in x))
    denom_right = sqrt(sum((b - mean_y) ** 2 for b in y))
    denominator = denom_left * denom_right
    if denominator == 0:
        return None
    return numerator / denominator


def _decimal_places(step: float) -> int:
    text = f"{step:.10f}".rstrip("0")
    if "." not in text:
        return 0
    return len(text.split(".", 1)[1])


def _is_step_aligned(value: float, minimum: float, step: float) -> bool:
    if step <= 0:
        return True
    remainder = (value - minimum) / step
    return abs(remainder - round(remainder)) < 1e-9
