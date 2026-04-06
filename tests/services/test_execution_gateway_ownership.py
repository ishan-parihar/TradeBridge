"""Tests for ExecutionGateway ownership/idempotency methods.

Phase 1.1.3 — resolve_magic_number on ExecutionGateway.

Tests:
  - test_resolve_magic_number_from_config: explicit mapping wins
  - test_resolve_magic_number_derived: falls back to hash derivation
  - test_resolve_magic_number_none_strategy: returns 0
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from mt5_mcp.settings.config import Settings, derive_magic_number


class TestResolveMagicNumber:
    """Tests for ExecutionGateway.resolve_magic_number()."""

    def _make_gateway(self, strategy_magic_numbers: dict[str, int] | None = None):
        """Create an ExecutionGateway with mocked settings and adapter."""
        settings = Settings()
        # Override the frozen dataclass field via object.__setattr__
        if strategy_magic_numbers is not None:
            object.__setattr__(
                settings, "strategy_magic_numbers", strategy_magic_numbers
            )

        mock_adapter = MagicMock()

        with patch(
            "mt5_mcp.services.execution_gateway.service.get_settings",
            return_value=settings,
        ):
            from mt5_mcp.services.execution_gateway.service import ExecutionGateway

            return ExecutionGateway(adapter=mock_adapter)

    def test_resolve_magic_number_from_config(self):
        """Explicit mapping in strategy_magic_numbers wins over derivation."""
        gateway = self._make_gateway(
            strategy_magic_numbers={"scalp": 1001, "swing": 2002}
        )
        result = gateway.resolve_magic_number("scalp")
        assert result == 1001

        result = gateway.resolve_magic_number("swing")
        assert result == 2002

    def test_resolve_magic_number_derived(self):
        """Falls back to derive_magic_number when no explicit mapping exists."""
        gateway = self._make_gateway(strategy_magic_numbers={"other": 9999})

        result = gateway.resolve_magic_number("scalp")
        expected = derive_magic_number("scalp")
        assert result == expected
        assert isinstance(result, int)
        assert 1 <= result <= 4294967295

    def test_resolve_magic_number_none_strategy(self):
        """Returns 0 when strategy_id is None."""
        gateway = self._make_gateway(strategy_magic_numbers={"scalp": 1001})
        result = gateway.resolve_magic_number(None)
        assert result == 0
