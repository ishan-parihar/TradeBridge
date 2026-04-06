"""Tests for EABridgeAdapter ownership/idempotency field wiring.

Phase 1.1.4–1.1.6 — wire identity fields through EABridgeAdapter write methods.

Tests:
  - test_submit_order_includes_magic_number_and_comment
  - test_submit_order_magic_number_from_config
  - test_submit_order_magic_number_derived
  - test_submit_order_comment_truncated
  - test_close_position_includes_ownership_fields
  - test_modify_order_includes_ownership_fields
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from mt5_mcp.schemas.models import ClosePositionRequest, ModifyOrderRequest, TradeIntent
from mt5_mcp.settings.config import Settings, compose_comment, derive_magic_number


def _make_adapter(strategy_magic_numbers: dict[str, int] | None = None):
    settings = Settings()
    if strategy_magic_numbers is not None:
        object.__setattr__(settings, "strategy_magic_numbers", strategy_magic_numbers)

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "id": "req-1",
        "status": "completed",
        "result": {"payload": {"order": 12345, "retcode": 10009}},
    }
    mock_client.post.return_value = mock_response
    mock_client.get.return_value = mock_response

    with (
        patch(
            "mt5_mcp.adapters.ea_bridge_adapter.adapter.get_settings",
            return_value=settings,
        ),
        patch(
            "mt5_mcp.adapters.ea_bridge_adapter.adapter.httpx.Client",
            return_value=mock_client,
        ),
    ):
        from mt5_mcp.adapters.ea_bridge_adapter.adapter import EABridgeAdapter

        adapter = EABridgeAdapter(gateway_url="http://test:8020")
        adapter._client = mock_client
        return adapter


class TestSubmitOrderOwnership:
    def test_submit_order_includes_magic_number_and_comment(self):
        adapter = _make_adapter()
        req = TradeIntent(
            session_id="sess-1",
            strategy_id="scalp",
            intent_id="intent-abc",
            account_id="demo",
            symbol="XAUUSD",
            side="buy",
            volume_lots=0.1,
        )

        captured_payload: dict = {}

        def capture_command(cmd_type, payload, timeout_s=10.0):
            captured_payload.update(payload)
            return {
                "status": "completed",
                "result": {"payload": {"order": 1, "retcode": 10009}},
            }

        with patch.object(adapter, "_send_command", side_effect=capture_command):
            adapter.submit_order(req)

        assert "magic_number" in captured_payload
        assert "comment" in captured_payload
        assert captured_payload["magic_number"] == derive_magic_number("scalp")
        assert captured_payload["comment"] == compose_comment(
            "scalp", "intent-abc", "sess-1"
        )

    def test_submit_order_magic_number_from_config(self):
        adapter = _make_adapter(strategy_magic_numbers={"scalp": 1001})
        req = TradeIntent(
            session_id="sess-1",
            strategy_id="scalp",
            intent_id="intent-abc",
            account_id="demo",
            symbol="XAUUSD",
            side="buy",
            volume_lots=0.1,
        )

        captured_payload: dict = {}

        def capture_command(cmd_type, payload, timeout_s=10.0):
            captured_payload.update(payload)
            return {
                "status": "completed",
                "result": {"payload": {"order": 1, "retcode": 10009}},
            }

        with patch.object(adapter, "_send_command", side_effect=capture_command):
            adapter.submit_order(req)

        assert captured_payload["magic_number"] == 1001

    def test_submit_order_magic_number_derived(self):
        adapter = _make_adapter(strategy_magic_numbers={"other": 9999})
        req = TradeIntent(
            session_id="sess-1",
            strategy_id="scalp",
            intent_id="intent-abc",
            account_id="demo",
            symbol="XAUUSD",
            side="buy",
            volume_lots=0.1,
        )

        captured_payload: dict = {}

        def capture_command(cmd_type, payload, timeout_s=10.0):
            captured_payload.update(payload)
            return {
                "status": "completed",
                "result": {"payload": {"order": 1, "retcode": 10009}},
            }

        with patch.object(adapter, "_send_command", side_effect=capture_command):
            adapter.submit_order(req)

        assert captured_payload["magic_number"] == derive_magic_number("scalp")

    def test_submit_order_comment_truncated(self):
        adapter = _make_adapter()
        long_intent = "a" * 50
        req = TradeIntent(
            session_id="s",
            strategy_id="verylongstrategyname",
            intent_id=long_intent,
            account_id="demo",
            symbol="XAUUSD",
            side="buy",
            volume_lots=0.1,
        )

        captured_payload: dict = {}

        def capture_command(cmd_type, payload, timeout_s=10.0):
            captured_payload.update(payload)
            return {
                "status": "completed",
                "result": {"payload": {"order": 1, "retcode": 10009}},
            }

        with patch.object(adapter, "_send_command", side_effect=capture_command):
            adapter.submit_order(req)

        assert len(captured_payload["comment"]) <= 31
        expected = compose_comment("verylongstrategyname", long_intent, "s")
        assert captured_payload["comment"] == expected


class TestClosePositionOwnership:
    def test_close_position_includes_ownership_fields(self):
        adapter = _make_adapter()
        req = ClosePositionRequest(
            position_id="pos-123",
            session_id="sess-2",
            strategy_id="swing",
            intent_id="intent-close",
            idempotency_key="idem-close-1",
        )

        captured_payload: dict = {}

        def capture_command(cmd_type, payload, timeout_s=10.0):
            captured_payload.update(payload)
            return {"status": "completed", "result": {"payload": {}}}

        with patch.object(adapter, "_send_command", side_effect=capture_command):
            adapter.close_position(req)

        assert captured_payload["session_id"] == "sess-2"
        assert captured_payload["strategy_id"] == "swing"
        assert captured_payload["intent_id"] == "intent-close"
        assert captured_payload["idempotency_key"] == "idem-close-1"


class TestModifyOrderOwnership:
    def test_modify_order_includes_ownership_fields(self):
        adapter = _make_adapter()
        req = ModifyOrderRequest(
            order_id="order-456",
            new_sl=2500.0,
            session_id="sess-3",
            strategy_id="scalp",
            intent_id="intent-mod",
            idempotency_key="idem-mod-1",
        )

        captured_payload: dict = {}

        def capture_command(cmd_type, payload, timeout_s=10.0):
            captured_payload.update(payload)
            return {"status": "completed", "result": {"payload": {}}}

        with patch.object(adapter, "_send_command", side_effect=capture_command):
            adapter.modify_order(req)

        assert captured_payload["session_id"] == "sess-3"
        assert captured_payload["strategy_id"] == "scalp"
        assert captured_payload["intent_id"] == "intent-mod"
        assert captured_payload["idempotency_key"] == "idem-mod-1"


class TestExecutionResultOwnershipEcho:
    """ExecutionResult returned by adapter methods must echo back request ownership."""

    def test_submit_order_result_echoes_ownership(self):
        adapter = _make_adapter()
        req = TradeIntent(
            session_id="sess-echo",
            strategy_id="echo-strategy",
            intent_id="intent-echo",
            account_id="demo",
            symbol="XAUUSD",
            side="buy",
            volume_lots=0.1,
            idempotency_key="idem-echo",
        )

        with patch.object(
            adapter,
            "_send_command",
            return_value={
                "status": "completed",
                "result": {"payload": {"order": 1, "retcode": 10009}},
            },
        ):
            result = adapter.submit_order(req)

        assert result.strategy_id == "echo-strategy"
        assert result.session_id == "sess-echo"
        assert result.idempotency_key == "idem-echo"

    def test_close_position_result_echoes_ownership(self):
        adapter = _make_adapter()
        req = ClosePositionRequest(
            position_id="pos-1",
            session_id="sess-close",
            strategy_id="close-strategy",
            intent_id="intent-close",
            idempotency_key="idem-close",
        )

        with patch.object(
            adapter,
            "_send_command",
            return_value={
                "status": "completed",
                "result": {"payload": {}},
            },
        ):
            result = adapter.close_position(req)

        assert result.strategy_id == "close-strategy"
        assert result.session_id == "sess-close"
        assert result.idempotency_key == "idem-close"


class TestGetPositionsMagicMapping:
    """get_positions must map EA 'magic' field to Position.magic_number."""

    def test_get_positions_maps_magic_to_magic_number(self):
        adapter = _make_adapter()

        with patch.object(
            adapter,
            "_send_command",
            return_value={
                "status": "completed",
                "result": {
                    "payload": {
                        "positions": [
                            {
                                "position_id": "100",
                                "symbol": "XAUUSD",
                                "side": "buy",
                                "volume": 0.1,
                                "entry_price": 2600.0,
                                "mark_price": 2610.0,
                                "sl": 0,
                                "tp": 0,
                                "unrealized_pnl": 1.0,
                                "magic": 12345,
                                "comment": "test comment",
                            }
                        ]
                    }
                },
            },
        ):
            positions = adapter.get_positions()

        assert len(positions) == 1
        assert positions[0].magic_number == 12345
        assert positions[0].comment == "test comment"
