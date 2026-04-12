from __future__ import annotations

import json
from typing import Any

from mt5_mcp.schemas.models import Deal, Position
from mt5_mcp.settings.config import Settings


def _pnl(d: dict) -> float:
    return (
        float(d.get("profit", 0) or 0)
        + float(d.get("commission", 0) or 0)
        + float(d.get("swap", 0) or 0)
        + float(d.get("fee", 0) or 0)
    )


def _to_dict(p: Position | dict) -> dict:
    if hasattr(p, "model_dump"):
        return p.model_dump()  # type: ignore[union-attr]
    return dict(p)


class ReconciliationService:
    """Authoritative combined surface for 'what is mine'."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _owned_strategy_ids(self) -> set[str]:
        return set(self._settings.strategy_magic_numbers.keys())

    def _owned_magic_numbers(self) -> set[int]:
        return set(self._settings.strategy_magic_numbers.values())

    def get_owned_positions(self, all_positions: list[dict]) -> list[dict]:
        if not self._owned_strategy_ids():
            return []
        return [
            p
            for p in all_positions
            if p.get("strategy_id") in self._owned_strategy_ids()
        ]

    def calculate_foreign_pnl(
        self, my_positions: list[dict], all_deals: list[dict]
    ) -> float:
        if not my_positions:
            return sum(_pnl(d) for d in all_deals)

        active_strategy_ids = {
            p.get("strategy_id") for p in my_positions if p.get("strategy_id")
        }
        owned_magics = {
            self._settings.strategy_magic_numbers[sid]
            for sid in active_strategy_ids
            if sid in self._settings.strategy_magic_numbers
        }
        foreign_deals = [d for d in all_deals if d.get("magic") not in owned_magics]
        return sum(_pnl(d) for d in foreign_deals)

    def reconcile(
        self, intent_ids: list[str] | str, actual_positions: list[dict]
    ) -> dict[str, Any]:
        if isinstance(intent_ids, str):
            try:
                intent_ids = json.loads(intent_ids)
            except (json.JSONDecodeError, TypeError):
                intent_ids = [intent_ids] if intent_ids else []

        if not isinstance(intent_ids, list):
            intent_ids = []

        actual_by_id = {
            p["position_id"]: p for p in actual_positions if "position_id" in p
        }
        intent_set = set(intent_ids)
        actual_set = set(actual_by_id.keys())

        owned = self.get_owned_positions(actual_positions)
        foreign = [p for p in actual_positions if p not in owned]

        missing = sorted(intent_set - actual_set)
        unexpected = [actual_by_id[pid] for pid in sorted(actual_set - intent_set)]

        has_discrepancy = bool(missing or unexpected or foreign)

        return {
            "status": "discrepancy" if has_discrepancy else "clean",
            "owned_positions": [_to_dict(p) for p in owned],
            "foreign_positions": [_to_dict(p) for p in foreign],
            "missing_positions": missing,
            "unexpected_positions": [_to_dict(p) for p in unexpected],
            "summary": {
                "expected": len(intent_set),
                "actual": len(actual_set),
                "owned": len(owned),
                "foreign": len(foreign),
                "missing": len(missing),
                "unexpected": len(unexpected),
            },
        }
