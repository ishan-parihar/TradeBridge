"""Symbol normalization utilities for MT5 symbol suffix handling."""

from __future__ import annotations

from mt5_mcp.settings.config import get_settings


def normalize_symbol(symbol: str) -> str:
    """
    Normalize a symbol by adding the configured suffix if not already present.

    This handles broker-specific symbol naming conventions (e.g., XAUUSD -> XAUUSDm).

    Args:
        symbol: The base symbol name (e.g., 'XAUUSD', 'EURUSD')

    Returns:
        The normalized symbol with suffix (e.g., 'XAUUSDm', 'EURUSDm')
    """
    settings = get_settings()
    suffix = settings.symbol_suffix

    if not suffix:
        return symbol

    if symbol.endswith(suffix):
        return symbol

    return f"{symbol}{suffix}"


def denormalize_symbol(symbol: str) -> str:
    """
    Remove the configured suffix from a symbol if present.

    This is useful for displaying symbols to users without broker-specific suffixes.

    Args:
        symbol: The symbol name possibly containing a suffix (e.g., 'XAUUSDm')

    Returns:
        The base symbol without suffix (e.g., 'XAUUSD')
    """
    settings = get_settings()
    suffix = settings.symbol_suffix

    if not suffix:
        return symbol

    if symbol.endswith(suffix):
        return symbol[: -len(suffix)]

    return symbol
