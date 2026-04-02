from __future__ import annotations

from pydantic import BaseModel
from typing import Literal, Optional


class BarsRequest(BaseModel):
    symbol: str
    timeframe: str
    count: int = 100


class IndicatorRequest(BaseModel):
    symbol: str
    timeframe: str
    indicator: Literal[
        "sma",
        "ema",
        "wma",
        "smma",
        "rsi",
        "macd",
        "bbands",
        "stoch",
        "atr",
        "adx",
        "dmi",
        "ichimoku",
        "obv",
        "cci",
    ]
    # Common period
    period: int | None = None
    # MACD
    fast: int | None = None
    slow: int | None = None
    signal: int | None = None
    # BBands
    deviation: float | None = None
    shift: int | None = None
    # Stochastic
    k_period: int | None = None
    d_period: int | None = None
    slowing: int | None = None
    # Ichimoku
    tenkan: int | None = None
    kijun: int | None = None
    senkou: int | None = None
    # Series window
    window: int | None = None


class ChartScreenshotRequest(BaseModel):
    symbol: str
    timeframe: str
    width: int = 1280
    height: int = 720


class ChartScreenshotResult(BaseModel):
    image_base64: str
    content_type: str = "image/png"


class AccountSummaryResult(BaseModel):
    account_id: str | None = None
    name: str | None = None
    balance: float | None = None
    equity: float | None = None
    margin: float | None = None
    free_margin: float | None = None
    currency: str | None = None


class ModifyPositionSLTPRequest(BaseModel):
    position_id: str
    sl: float | None = None
    tp: float | None = None


class ClosePositionRequest(BaseModel):
    position_id: str
    volume: float | None = None


class SubmitPendingOrderRequest(BaseModel):
    symbol: str
    side: Literal["buy", "sell"]
    kind: Literal["limit", "stop"]
    price: float
    volume_lots: float
    sl: float | None = None
    tp: float | None = None
    deviation: int = 20


class CancelOrderRequest(BaseModel):
    order_id: str


class ModifyOrderRequest(BaseModel):
    order_id: str
    new_price: float | None = None
    new_sl: float | None = None
    new_tp: float | None = None


class CloseAllPositionsRequest(BaseModel):
    symbol: str | None = None
    side: Literal["buy", "sell", "both"] = "both"


class CancelAllOrdersRequest(BaseModel):
    symbol: str | None = None
    side: Literal["buy", "sell", "both"] = "both"


class TicksRequest(BaseModel):
    symbol: str
    count: int = 200


class OrderBookRequest(BaseModel):
    symbol: str
