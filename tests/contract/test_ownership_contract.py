"""Ownership & idempotency contract tests for write-path request models.

Phase 0.1 — TDD first batch.

Canonical ownership fields (all Optional[str], additive, non-breaking):
  - session_id:     identifies the agent session that originated the request
  - strategy_id:    groups requests by strategy / playbook
  - intent_id:      unique label for the trade intent (market orders) or action
  - idempotency_key: client-supplied key to prevent duplicate submissions

Read-only response models and pure market-data requests are out of scope.
"""

from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path

import pytest
from pydantic import BaseModel


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))


def _resolve_model(module_name: str, symbol_name: str) -> type[BaseModel]:
    module = import_module(module_name)
    return getattr(module, symbol_name)


# ---------------------------------------------------------------------------
# Helper: assert a model class declares specific optional str fields
# ---------------------------------------------------------------------------

OWNERSHIP_FIELDS: tuple[str, ...] = (
    "session_id",
    "strategy_id",
    "intent_id",
    "idempotency_key",
)


def _assert_ownership_fields(
    model_cls: type[BaseModel], *, partial_fields: tuple[str, ...] | None = None
) -> None:
    """Assert that *model_cls* has the given ownership fields as optional str."""
    fields_to_check = partial_fields or OWNERSHIP_FIELDS
    model_fields = model_cls.model_fields

    for field_name in fields_to_check:
        assert field_name in model_fields, (
            f"{model_cls.__name__} is missing canonical ownership field '{field_name}'"
        )
        annotation = model_fields[field_name].annotation
        # Accept Optional[str] (str | None) patterns
        # We just verify the field exists and has a default (optional)
        assert model_fields[field_name].is_required() is False, (
            f"{model_cls.__name__}.{field_name} must be Optional (have a default), not required"
        )


def _assert_field_type_str_or_none(model_cls: type[BaseModel], field_name: str) -> None:
    """Lightweight check: field annotation should include str."""
    field = model_cls.model_fields[field_name]
    annotation = field.annotation
    # str | None should have str in its args
    annotation_str = str(annotation)
    assert "str" in annotation_str, (
        f"{model_cls.__name__}.{field_name} should be str | None, got {annotation}"
    )


# ===========================================================================
# 1. TradeIntent — the gold standard (models.py)
# ===========================================================================


class TestTradeIntentOwnership:
    """TradeIntent MUST have all 4 canonical ownership fields."""

    def test_has_session_id(self):
        TradeIntent = _resolve_model("mt5_mcp.schemas.models", "TradeIntent")
        _assert_ownership_fields(TradeIntent)

    def test_session_id_is_optional_str(self):
        TradeIntent = _resolve_model("mt5_mcp.schemas.models", "TradeIntent")
        _assert_field_type_str_or_none(TradeIntent, "session_id")

    def test_idempotency_key_is_optional_str(self):
        TradeIntent = _resolve_model("mt5_mcp.schemas.models", "TradeIntent")
        _assert_field_type_str_or_none(TradeIntent, "idempotency_key")

    def test_strategy_id_is_optional_str(self):
        TradeIntent = _resolve_model("mt5_mcp.schemas.models", "TradeIntent")
        _assert_field_type_str_or_none(TradeIntent, "strategy_id")

    def test_intent_id_is_optional_str(self):
        TradeIntent = _resolve_model("mt5_mcp.schemas.models", "TradeIntent")
        _assert_field_type_str_or_none(TradeIntent, "intent_id")

    def test_instantiation_with_all_ownership_fields(self):
        TradeIntent = _resolve_model("mt5_mcp.schemas.models", "TradeIntent")
        intent = TradeIntent(
            intent_id="test-001",
            strategy_id="scalp",
            account_id="demo",
            symbol="XAUUSD",
            side="buy",
            volume_lots=0.01,
            session_id="sess-abc",
            idempotency_key="idem-xyz",
        )
        assert getattr(intent, "session_id") == "sess-abc"
        assert getattr(intent, "idempotency_key") == "idem-xyz"
        assert getattr(intent, "strategy_id") == "scalp"
        assert getattr(intent, "intent_id") == "test-001"


# ===========================================================================
# 2. models.py write-path scaffolding requests
# ===========================================================================


class TestModelsWritePathOwnership:
    """Write-path request models in models.py must carry ownership metadata."""

    def test_modify_order_request_has_ownership(self):
        ModelModifyOrderRequest = _resolve_model(
            "mt5_mcp.schemas.models", "ModifyOrderRequest"
        )
        _assert_ownership_fields(ModelModifyOrderRequest)

    def test_close_position_request_has_ownership(self):
        ModelClosePositionRequest = _resolve_model(
            "mt5_mcp.schemas.models", "ClosePositionRequest"
        )
        _assert_ownership_fields(ModelClosePositionRequest)

    def test_modify_order_accepts_session_id(self):
        ModelModifyOrderRequest = _resolve_model(
            "mt5_mcp.schemas.models", "ModifyOrderRequest"
        )
        req = ModelModifyOrderRequest(order_id="ord-1", session_id="sess-x")
        assert getattr(req, "session_id") == "sess-x"

    def test_close_position_accepts_idempotency_key(self):
        ModelClosePositionRequest = _resolve_model(
            "mt5_mcp.schemas.models", "ClosePositionRequest"
        )
        req = ModelClosePositionRequest(position_id="pos-1", idempotency_key="idem-1")
        assert getattr(req, "idempotency_key") == "idem-1"


# ===========================================================================
# 3. tools.py write-path request models
# ===========================================================================


class TestToolsWritePathOwnership:
    """Write-path tool request models in tools.py must carry ownership metadata."""

    # --- Must have full 4-field ownership ---
    def test_modify_position_sl_tp_request(self):
        ModifyPositionSLTPRequest = _resolve_model(
            "mt5_mcp.schemas.tools", "ModifyPositionSLTPRequest"
        )
        _assert_ownership_fields(ModifyPositionSLTPRequest)

    def test_close_position_request(self):
        ToolClosePositionRequest = _resolve_model(
            "mt5_mcp.schemas.tools", "ClosePositionRequest"
        )
        _assert_ownership_fields(ToolClosePositionRequest)

    def test_submit_pending_order_request(self):
        SubmitPendingOrderRequest = _resolve_model(
            "mt5_mcp.schemas.tools", "SubmitPendingOrderRequest"
        )
        _assert_ownership_fields(SubmitPendingOrderRequest)

    def test_cancel_order_request(self):
        CancelOrderRequest = _resolve_model(
            "mt5_mcp.schemas.tools", "CancelOrderRequest"
        )
        _assert_ownership_fields(CancelOrderRequest)

    def test_modify_order_request(self):
        ToolModifyOrderRequest = _resolve_model(
            "mt5_mcp.schemas.tools", "ModifyOrderRequest"
        )
        _assert_ownership_fields(ToolModifyOrderRequest)

    def test_close_all_positions_request(self):
        CloseAllPositionsRequest = _resolve_model(
            "mt5_mcp.schemas.tools", "CloseAllPositionsRequest"
        )
        _assert_ownership_fields(CloseAllPositionsRequest)

    def test_cancel_all_orders_request(self):
        CancelAllOrdersRequest = _resolve_model(
            "mt5_mcp.schemas.tools", "CancelAllOrdersRequest"
        )
        _assert_ownership_fields(CancelAllOrdersRequest)

    def test_bracket_order_request(self):
        BracketOrderRequest = _resolve_model(
            "mt5_mcp.schemas.tools", "BracketOrderRequest"
        )
        _assert_ownership_fields(BracketOrderRequest)

    def test_set_trailing_stop_request(self):
        SetTrailingStopRequest = _resolve_model(
            "mt5_mcp.schemas.tools", "SetTrailingStopRequest"
        )
        _assert_ownership_fields(SetTrailingStopRequest)

    # --- Acceptance tests: verify fields are actually usable ---
    def test_submit_pending_order_with_session(self):
        SubmitPendingOrderRequest = _resolve_model(
            "mt5_mcp.schemas.tools", "SubmitPendingOrderRequest"
        )
        req = SubmitPendingOrderRequest(
            symbol="XAUUSD",
            side="buy",
            kind="limit",
            price=2650.0,
            volume_lots=0.01,
            session_id="sess-123",
            strategy_id="breakout",
            intent_id="pending-001",
            idempotency_key="idem-pending-001",
        )
        assert getattr(req, "session_id") == "sess-123"
        assert getattr(req, "strategy_id") == "breakout"

    def test_trailing_stop_with_intent(self):
        SetTrailingStopRequest = _resolve_model(
            "mt5_mcp.schemas.tools", "SetTrailingStopRequest"
        )
        req = SetTrailingStopRequest(
            position_id="pos-1",
            distance_atr_multiplier=1.0,
            intent_id="trail-001",
        )
        assert getattr(req, "intent_id") == "trail-001"

    def test_bracket_order_with_full_ownership(self):
        BracketOrderRequest = _resolve_model(
            "mt5_mcp.schemas.tools", "BracketOrderRequest"
        )
        req = BracketOrderRequest(
            symbol="BTCUSD",
            buy_trigger=70000.0,
            sell_trigger=65000.0,
            volume_lots=0.01,
            session_id="sess-btc",
            strategy_id="bracket",
            intent_id="bracket-001",
            idempotency_key="idem-bracket-001",
        )
        assert getattr(req, "session_id") == "sess-btc"
        assert getattr(req, "idempotency_key") == "idem-bracket-001"


# ===========================================================================
# 4. Read-only models should NOT be forced to have ownership fields
# ===========================================================================


class TestReadOnlyModelsExempt:
    """Read-only / market-data models are exempt from the ownership contract."""

    def test_bars_request_no_ownership_required(self):
        """BarsRequest is read-only — ownership fields should NOT be required."""
        BarsRequest = _resolve_model("mt5_mcp.schemas.tools", "BarsRequest")
        for field in OWNERSHIP_FIELDS:
            assert field not in BarsRequest.model_fields, (
                f"BarsRequest should NOT require '{field}' (read-only model)"
            )

    def test_health_status_no_ownership(self):
        HealthStatus = _resolve_model("mt5_mcp.schemas.models", "HealthStatus")
        for field in OWNERSHIP_FIELDS:
            assert field not in HealthStatus.model_fields

    def test_symbol_info_no_ownership(self):
        SymbolInfo = _resolve_model("mt5_mcp.schemas.models", "SymbolInfo")
        for field in OWNERSHIP_FIELDS:
            assert field not in SymbolInfo.model_fields


# ===========================================================================
# 5. Position model — already has strategy_id, should get session_id too
# ===========================================================================


class TestPositionOwnership:
    """Position (result model) should carry extended ownership fields."""

    def test_position_has_strategy_id(self):
        Position = _resolve_model("mt5_mcp.schemas.models", "Position")
        assert "strategy_id" in Position.model_fields

    def test_position_has_session_id(self):
        Position = _resolve_model("mt5_mcp.schemas.models", "Position")
        assert "session_id" in Position.model_fields, (
            "Position should have session_id for attribution"
        )

    def test_position_has_magic_number(self):
        Position = _resolve_model("mt5_mcp.schemas.models", "Position")
        assert "magic_number" in Position.model_fields, (
            "Position should have magic_number for EA attribution"
        )
        assert Position.model_fields["magic_number"].is_required() is False

    def test_position_has_comment(self):
        Position = _resolve_model("mt5_mcp.schemas.models", "Position")
        assert "comment" in Position.model_fields, (
            "Position should have comment for EA attribution"
        )
        assert Position.model_fields["comment"].is_required() is False


class TestOrderOwnership:
    """Order (result model) must carry the full ownership vocabulary."""

    def test_order_has_strategy_id(self):
        Order = _resolve_model("mt5_mcp.schemas.models", "Order")
        assert "strategy_id" in Order.model_fields, (
            "Order should have strategy_id for attribution"
        )

    def test_order_has_session_id(self):
        Order = _resolve_model("mt5_mcp.schemas.models", "Order")
        assert "session_id" in Order.model_fields, (
            "Order should have session_id for attribution"
        )

    def test_order_has_intent_id(self):
        Order = _resolve_model("mt5_mcp.schemas.models", "Order")
        assert "intent_id" in Order.model_fields, (
            "Order should have intent_id for attribution"
        )

    def test_order_has_magic_number(self):
        Order = _resolve_model("mt5_mcp.schemas.models", "Order")
        assert "magic_number" in Order.model_fields, (
            "Order should have magic_number for EA attribution"
        )

    def test_order_has_comment(self):
        Order = _resolve_model("mt5_mcp.schemas.models", "Order")
        assert "comment" in Order.model_fields, (
            "Order should have comment for EA attribution"
        )


class TestDealOwnership:
    """Deal (result model) must carry the ownership vocabulary."""

    def test_deal_has_strategy_id(self):
        Deal = _resolve_model("mt5_mcp.schemas.models", "Deal")
        assert "strategy_id" in Deal.model_fields, (
            "Deal should have strategy_id for attribution"
        )

    def test_deal_has_session_id(self):
        Deal = _resolve_model("mt5_mcp.schemas.models", "Deal")
        assert "session_id" in Deal.model_fields, (
            "Deal should have session_id for attribution"
        )

    def test_deal_has_intent_id(self):
        Deal = _resolve_model("mt5_mcp.schemas.models", "Deal")
        assert "intent_id" in Deal.model_fields, (
            "Deal should have intent_id for attribution"
        )


class TestExecutionResultOwnership:
    """ExecutionResult must echo back ownership fields from the request."""

    def test_execution_result_has_strategy_id(self):
        ExecutionResult = _resolve_model("mt5_mcp.schemas.models", "ExecutionResult")
        assert "strategy_id" in ExecutionResult.model_fields, (
            "ExecutionResult should have strategy_id for echo-back"
        )

    def test_execution_result_has_session_id(self):
        ExecutionResult = _resolve_model("mt5_mcp.schemas.models", "ExecutionResult")
        assert "session_id" in ExecutionResult.model_fields, (
            "ExecutionResult should have session_id for echo-back"
        )

    def test_execution_result_has_idempotency_key(self):
        ExecutionResult = _resolve_model("mt5_mcp.schemas.models", "ExecutionResult")
        assert "idempotency_key" in ExecutionResult.model_fields, (
            "ExecutionResult should have idempotency_key for echo-back"
        )

    def test_execution_result_has_magic_number(self):
        ExecutionResult = _resolve_model("mt5_mcp.schemas.models", "ExecutionResult")
        assert "magic_number" in ExecutionResult.model_fields, (
            "ExecutionResult should have magic_number for echo-back"
        )

    def test_execution_result_has_comment(self):
        ExecutionResult = _resolve_model("mt5_mcp.schemas.models", "ExecutionResult")
        assert "comment" in ExecutionResult.model_fields, (
            "ExecutionResult should have comment for echo-back"
        )


# ===========================================================================
# 6. Terminology consistency — field names must match canonical vocabulary
# ===========================================================================


class TestTerminologyConsistency:
    """All write-path models must use canonical field names, not synonyms."""

    @pytest.mark.parametrize(
        "model_cls",
        [
            pytest.param(("mt5_mcp.schemas.models", "TradeIntent")),
            pytest.param(("mt5_mcp.schemas.models", "ModifyOrderRequest")),
            pytest.param(("mt5_mcp.schemas.models", "ClosePositionRequest")),
        ],
    )
    def test_models_use_canonical_field_names(self, model_cls):
        """No old synonyms like 'session', 'idempotency', 'trade_id' should exist."""
        resolved_model = _resolve_model(*model_cls)
        forbidden_names = {"session", "idempotency", "trade_id", "client_token"}
        for field_name in resolved_model.model_fields:
            assert field_name.lower() not in forbidden_names, (
                f"{resolved_model.__name__} uses non-canonical field name '{field_name}'; "
                f"use 'session_id', 'idempotency_key', 'intent_id' instead"
            )
