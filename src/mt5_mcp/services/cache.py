"""TTL-based caching service for MT5 market data.

Reduces latency by caching frequently-requested, short-lived data
(symbol_info, account_summary, order_book) with configurable TTLs.

For market data, stale-by-seconds is acceptable — a 2-second-old
order book is still more useful than a 500ms-latency fresh one.
"""

from __future__ import annotations

import time
import threading
from typing import Any, Optional


class CacheEntry:
    __slots__ = ("value", "expires_at", "created_at")

    def __init__(self, value: Any, ttl: float) -> None:
        self.value = value
        self.created_at = time.monotonic()
        self.expires_at = self.created_at + ttl

    @property
    def is_expired(self) -> bool:
        return time.monotonic() > self.expires_at

    @property
    def age_ms(self) -> float:
        return (time.monotonic() - self.created_at) * 1000


class TTLCache:
    """Thread-safe TTL cache with stats tracking."""

    def __init__(self, default_ttl: float = 2.0, max_size: int = 256) -> None:
        self._default_ttl = default_ttl
        self._max_size = max_size
        self._store: dict[str, CacheEntry] = {}
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None
            if entry.is_expired:
                del self._store[key]
                self._misses += 1
                return None
            self._hits += 1
            return entry.value

    def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        with self._lock:
            # Evict oldest if at capacity
            if len(self._store) >= self._max_size and key not in self._store:
                oldest_key = min(self._store, key=lambda k: self._store[k].created_at)
                del self._store[oldest_key]
            self._store[key] = CacheEntry(value, ttl or self._default_ttl)

    def invalidate(self, key: str) -> bool:
        with self._lock:
            if key in self._store:
                del self._store[key]
                return True
            return False

    def invalidate_prefix(self, prefix: str) -> int:
        """Invalidate all keys starting with prefix."""
        with self._lock:
            keys_to_remove = [k for k in self._store if k.startswith(prefix)]
            for k in keys_to_remove:
                del self._store[k]
            return len(keys_to_remove)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    @property
    def stats(self) -> dict:
        with self._lock:
            total = self._hits + self._misses
            return {
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(self._hits / total, 2) if total > 0 else 0.0,
                "size": len(self._store),
                "max_size": self._max_size,
            }

    def reset_stats(self) -> None:
        with self._lock:
            self._hits = 0
            self._misses = 0


# ============================================================================
# Pre-configured cache instances with domain-appropriate TTLs
# ============================================================================

# Symbol info changes rarely — 30s TTL is safe
symbol_cache = TTLCache(default_ttl=30.0, max_size=128)

# Account summary changes with each trade — 5s TTL
account_cache = TTLCache(default_ttl=5.0, max_size=4)

# Order book / prices change constantly — 1.5s TTL
price_cache = TTLCache(default_ttl=1.5, max_size=64)

# Indicator values derived from bars — 2s TTL
indicator_cache = TTLCache(default_ttl=2.0, max_size=256)

# Bars data — 2s TTL (new bar arrives every timeframe interval)
bars_cache = TTLCache(default_ttl=2.0, max_size=128)

# Regime detection — 5s TTL (regime doesn't change every tick)
regime_cache = TTLCache(default_ttl=5.0, max_size=64)

# Master cache for stats aggregation
_market_cache = TTLCache(default_ttl=2.0, max_size=512)


def get_cache_for(key_prefix: str) -> TTLCache:
    """Route cache key to appropriate cache instance."""
    if key_prefix.startswith("symbol:"):
        return symbol_cache
    elif key_prefix.startswith("account:"):
        return account_cache
    elif key_prefix.startswith("price:") or key_prefix.startswith("order_book:"):
        return price_cache
    elif key_prefix.startswith("indicator:"):
        return indicator_cache
    elif key_prefix.startswith("bars:"):
        return bars_cache
    elif key_prefix.startswith("regime:"):
        return regime_cache
    return _market_cache


def cache_get(key: str) -> Any | None:
    """Get from appropriate cache."""
    cache = get_cache_for(key)
    return cache.get(key)


def cache_set(key: str, value: Any, ttl: float | None = None) -> None:
    """Set in appropriate cache."""
    cache = get_cache_for(key)
    cache.set(key, value, ttl)


def cache_invalidate(key: str) -> bool:
    """Invalidate a specific cache entry."""
    cache = get_cache_for(key)
    return cache.invalidate(key)


def cache_invalidate_symbol(symbol: str) -> int:
    """Invalidate all cached data for a symbol (e.g., after a trade).

    Keys follow pattern: <cache_type>:<SYMBOL>:<detail>
    e.g., price:EURUSD, indicator:EURUSD:atr, bars:EURUSD:H1
    """
    sym_upper = symbol.upper()
    total = 0
    for cache in [symbol_cache, price_cache, indicator_cache, bars_cache, regime_cache]:
        # Invalidate keys containing the symbol (case-insensitive)
        with cache._lock:
            keys_to_remove = [
                k
                for k in cache._store
                if f":{sym_upper}:" in k
                or k.endswith(f":{sym_upper}")
                or f":{symbol}:" in k
                or k.endswith(f":{symbol}")
            ]
            for k in keys_to_remove:
                del cache._store[k]
            total += len(keys_to_remove)
    return total


def get_all_cache_stats() -> dict:
    """Aggregate stats from all caches."""
    return {
        "symbol": symbol_cache.stats,
        "account": account_cache.stats,
        "price": price_cache.stats,
        "indicator": indicator_cache.stats,
        "bars": bars_cache.stats,
        "regime": regime_cache.stats,
    }
