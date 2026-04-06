"""Tests for config ownership fields and magic number derivation.

Phase 1.1.1 — strategy_id -> magic_number mapping and idempotency TTL.

Tests:
  - test_strategy_magic_numbers_from_env: env var JSON parsing loads dict
  - test_derive_magic_number_deterministic: same strategy_id -> same magic
  - test_derive_magic_number_never_zero: clamped to 1..4294967295
  - test_idempotency_ttl_default: default 86400 seconds
"""

from __future__ import annotations

import os
import sys
from importlib import reload
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))


class TestStrategyMagicNumbersFromEnv:
    def test_strategy_magic_numbers_from_env(self, monkeypatch):
        monkeypatch.setenv(
            "MT5_STRATEGY_MAGIC_NUMBERS", '{"scalp": 1001, "swing": 2002}'
        )
        from mt5_mcp.settings import config

        reload(config)
        settings = config.get_settings()
        assert settings.strategy_magic_numbers == {"scalp": 1001, "swing": 2002}

    def test_strategy_magic_numbers_invalid_json(self, monkeypatch):
        monkeypatch.setenv("MT5_STRATEGY_MAGIC_NUMBERS", "{invalid json")
        from mt5_mcp.settings import config

        reload(config)
        settings = config.get_settings()
        assert settings.strategy_magic_numbers == {}


class TestDeriveMagicNumber:
    def test_derive_magic_number_deterministic(self):
        from mt5_mcp.settings.config import derive_magic_number

        result1 = derive_magic_number("scalp")
        result2 = derive_magic_number("scalp")
        assert result1 == result2
        assert isinstance(result1, int)
        assert 1 <= result1 <= 4294967295

    def test_derive_magic_number_never_zero(self):
        from mt5_mcp.settings.config import derive_magic_number

        for strategy_id in ["scalp", "swing", "0", "test-strategy"]:
            magic = derive_magic_number(strategy_id)
            assert magic != 0, f"derive_magic_number('{strategy_id}') returned 0"
            assert 1 <= magic <= 4294967295

    def test_derive_magic_number_empty_raises(self):
        from mt5_mcp.settings.config import derive_magic_number

        with pytest.raises(ValueError, match="strategy_id cannot be empty"):
            derive_magic_number("")

    def test_derive_magic_number_whitespace_raises(self):
        from mt5_mcp.settings.config import derive_magic_number

        with pytest.raises(ValueError, match="strategy_id cannot be empty"):
            derive_magic_number("   ")


class TestIdempotencyTTL:
    def test_idempotency_ttl_default(self, monkeypatch):
        monkeypatch.delenv("MT5_IDEMPOTENCY_TTL_SECONDS", raising=False)
        from mt5_mcp.settings import config

        reload(config)
        settings = config.get_settings()
        assert settings.idempotency_ttl_seconds == 86400

    def test_idempotency_ttl_invalid_fallback(self, monkeypatch):
        monkeypatch.setenv("MT5_IDEMPOTENCY_TTL_SECONDS", "not-a-number")
        from mt5_mcp.settings import config

        reload(config)
        settings = config.get_settings()
        assert settings.idempotency_ttl_seconds == 86400


class TestComposeComment:
    def test_compose_comment_format(self):
        from mt5_mcp.settings.config import compose_comment

        result = compose_comment("scalp", "intent-42", "sess-abc")
        assert result == "scalp:intent-42:sess-abc"

    def test_compose_comment_truncation(self):
        from mt5_mcp.settings.config import compose_comment

        result = compose_comment(
            "very-long-strategy-id",
            "very-long-intent-identifier",
            "very-long-session-id",
        )
        assert len(result) == 31

    def test_compose_comment_deterministic(self):
        from mt5_mcp.settings.config import compose_comment

        result1 = compose_comment("scalp", "intent-42", "sess-abc")
        result2 = compose_comment("scalp", "intent-42", "sess-abc")
        assert result1 == result2
