"""Tests for TTL cache service and latency optimizations."""

import time
import pytest
from mt5_mcp.services.cache import (
    TTLCache,
    CacheEntry,
    symbol_cache,
    price_cache,
    indicator_cache,
    bars_cache,
    regime_cache,
    account_cache,
    cache_get,
    cache_set,
    cache_invalidate,
    cache_invalidate_symbol,
    get_all_cache_stats,
)


class TestCacheEntry:
    def test_entry_not_expired_immediately(self):
        entry = CacheEntry("value", ttl=10.0)
        assert not entry.is_expired
        assert entry.value == "value"

    def test_entry_expires_after_ttl(self):
        entry = CacheEntry("value", ttl=0.01)  # 10ms
        time.sleep(0.02)
        assert entry.is_expired

    def test_age_ms_accuracy(self):
        entry = CacheEntry("value", ttl=10.0)
        time.sleep(0.01)
        assert entry.age_ms >= 10


class TestTTLCache:
    def test_set_and_get(self):
        cache = TTLCache(default_ttl=10.0)
        cache.set("key1", "value1")
        assert cache.get("key1") == "value1"

    def test_get_missing_returns_none(self):
        cache = TTLCache(default_ttl=10.0)
        assert cache.get("missing") is None

    def test_expired_entry_returns_none(self):
        cache = TTLCache(default_ttl=0.01)
        cache.set("key", "value")
        time.sleep(0.02)
        assert cache.get("key") is None

    def test_invalidate(self):
        cache = TTLCache(default_ttl=10.0)
        cache.set("key", "value")
        assert cache.invalidate("key") is True
        assert cache.get("key") is None

    def test_invalidate_missing(self):
        cache = TTLCache(default_ttl=10.0)
        assert cache.invalidate("missing") is False

    def test_invalidate_prefix(self):
        cache = TTLCache(default_ttl=10.0)
        cache.set("EURUSD:atr", 1.5)
        cache.set("EURUSD:rsi", 55.0)
        cache.set("GBPUSD:atr", 2.0)
        count = cache.invalidate_prefix("EURUSD:")
        assert count == 2
        assert cache.get("EURUSD:atr") is None
        assert cache.get("EURUSD:rsi") is None
        assert cache.get("GBPUSD:atr") == 2.0

    def test_clear(self):
        cache = TTLCache(default_ttl=10.0)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.clear()
        assert cache.get("a") is None
        assert cache.get("b") is None

    def test_stats(self):
        cache = TTLCache(default_ttl=10.0)
        cache.set("key", "value")
        cache.get("key")  # hit
        cache.get("missing")  # miss
        stats = cache.stats
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["hit_rate"] == 0.5
        assert stats["size"] == 1

    def test_reset_stats(self):
        cache = TTLCache(default_ttl=10.0)
        cache.get("missing")
        cache.reset_stats()
        assert cache.stats["hits"] == 0
        assert cache.stats["misses"] == 0

    def test_max_size_eviction(self):
        cache = TTLCache(default_ttl=10.0, max_size=3)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)
        # Adding 4th should evict oldest
        time.sleep(0.01)  # Ensure different timestamps
        cache.set("d", 4)
        assert cache.stats["size"] <= 3

    def test_custom_ttl(self):
        cache = TTLCache(default_ttl=10.0)
        cache.set("key", "value", ttl=0.01)
        time.sleep(0.02)
        assert cache.get("key") is None


class TestCacheRouting:
    def test_symbol_cache_routing(self):
        cache_set("symbol:EURUSD", {"point": 0.0001})
        result = cache_get("symbol:EURUSD")
        assert result == {"point": 0.0001}

    def test_price_cache_routing(self):
        cache_set("price:EURUSD", {"bid": 1.0850, "ask": 1.0852})
        result = cache_get("price:EURUSD")
        assert result["bid"] == 1.0850

    def test_indicator_cache_routing(self):
        cache_set("indicator:EURUSD:atr", {"value": 15.0})
        result = cache_get("indicator:EURUSD:atr")
        assert result["value"] == 15.0

    def test_bars_cache_routing(self):
        cache_set("bars:EURUSD:H1", {"data": []})
        result = cache_get("bars:EURUSD:H1")
        assert result == {"data": []}

    def test_regime_cache_routing(self):
        cache_set("regime:EURUSD", {"regime": "ranging"})
        result = cache_get("regime:EURUSD")
        assert result["regime"] == "ranging"


class TestCacheInvalidation:
    def test_invalidate_symbol_clears_all(self):
        # Set data across multiple caches
        cache_set("symbol:EURUSD", {"point": 0.0001})
        cache_set("price:EURUSD", {"bid": 1.0850})
        cache_set("indicator:EURUSD:atr", {"value": 15.0})
        cache_set("bars:EURUSD:H1", {"data": []})
        cache_set("regime:EURUSD", {"regime": "ranging"})

        count = cache_invalidate_symbol("EURUSD")
        assert count >= 4  # At least 4 entries invalidated

        # Verify cleared
        assert cache_get("price:EURUSD") is None
        assert cache_get("indicator:EURUSD:atr") is None
        assert cache_get("bars:EURUSD:H1") is None
        assert cache_get("regime:EURUSD") is None


class TestGlobalCacheStats:
    def test_get_all_stats(self):
        stats = get_all_cache_stats()
        assert "symbol" in stats
        assert "account" in stats
        assert "price" in stats
        assert "indicator" in stats
        assert "bars" in stats
        assert "regime" in stats
