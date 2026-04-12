"""Symbol normalization utilities for MT5 symbol suffix handling."""

from __future__ import annotations

from mt5_mcp.settings.config import get_settings


def normalize_symbol(symbol: str) -> str:
    settings = get_settings()
    suffix = settings.symbol_suffix

    if not suffix:
        return symbol

    if symbol.endswith(suffix):
        return symbol

    return f"{symbol}{suffix}"


def denormalize_symbol(symbol: str) -> str:
    settings = get_settings()
    suffix = settings.symbol_suffix

    if not suffix:
        return symbol

    if symbol.endswith(suffix):
        return symbol[: -len(suffix)]

    return symbol


def canonical_symbol(symbol: str) -> str:
    """Return a case-stable canonical form of the symbol.

    Strips suffix, uppercases the base, then re-applies normalization.
    This ensures 'btcusd', 'BTCUSD', 'BTCUSDm' all resolve to the same
    broker-correct symbol.
    """
    base = denormalize_symbol(symbol).upper()
    return normalize_symbol(base)
