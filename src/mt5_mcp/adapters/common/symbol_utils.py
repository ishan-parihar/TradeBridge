"""Symbol normalization utilities for MT5 symbol suffix handling."""

from __future__ import annotations

from mt5_mcp.settings.config import get_settings


def normalize_symbol(symbol: str) -> str:
    """Normalize a symbol to always have exactly one broker suffix.

    Strips ALL trailing suffix occurrences (handles double/triple suffix
    bugs like ETHUSDmm), then appends exactly one suffix.
    Guarantees idempotent output:
      ETHUSD    → ETHUSDm
      ETHUSDm   → ETHUSDm
      ETHUSDmm  → ETHUSDm
      ETHUSDmmm → ETHUSDm
    """
    settings = get_settings()
    suffix = settings.symbol_suffix

    if not suffix:
        return symbol

    # Strip ALL trailing occurrences of the suffix (handles double/triple suffix)
    base = symbol
    while base.endswith(suffix):
        base = base[: -len(suffix)]

    # Re-add exactly one suffix
    return f"{base}{suffix}"


def denormalize_symbol(symbol: str) -> str:
    """Remove broker suffix from a symbol, handling repeated suffixes."""
    settings = get_settings()
    suffix = settings.symbol_suffix

    if not suffix:
        return symbol

    # Strip ALL trailing occurrences of the suffix
    base = symbol
    while base.endswith(suffix):
        base = base[: -len(suffix)]

    return base


def canonical_symbol(symbol: str) -> str:
    """Return a case-stable canonical form of the symbol.

    Strips suffix, uppercases the base, then re-applies normalization.
    This ensures 'btcusd', 'BTCUSD', 'BTCUSDm' all resolve to the same
    broker-correct symbol.
    """
    base = denormalize_symbol(symbol).upper()
    return normalize_symbol(base)
