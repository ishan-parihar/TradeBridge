from __future__ import annotations

from typing import Any

from mt5_mcp.schemas.models import Deal, Position
from mt5_mcp.settings.config import Settings


class ReconciliationService:
    """Authoritative combined surface for 'what is mine'."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _owned_strategy_ids(self) -> set[str]:
        return set(self._settings.strategy_magic_numbers.keys())

    def _owned_magic_numbers(self) -> set[int]:
        return set(self._settings.strategy_magic_numbers.values())

    def get_owned_positions(self, all_positions: list[Position]) -> list[Position]:
        if not self._owned_strategy_ids():
            return []
        return [p for p in all_positions if p.strategy_id in self._owned_strategy_ids()]

    def calculate_foreign_pnl(
        self, my_positions: list[Position], all_deals: list[Deal]
    ) -> float:
        if not my_positions:
            return sum(d.profit + d.commission + d.swap + d.fee for d in all_deals)

        active_strategy_ids = {p.strategy_id for p in my_positions if p.strategy_id}
        owned_magics = {
            self._settings.strategy_magic_numbers[sid]
            for sid in active_strategy_ids
            if sid in self._settings.strategy_magic_numbers
        }
        foreign_deals = [d for d in all_deals if d.magic not in owned_magics]
        return sum(d.profit + d.commission + d.swap + d.fee for d in foreign_deals)

    def reconcile(
        self, intent_ids: list[str], actual_positions: list[Position]
    ) -> dict[str, Any]:
        actual_by_id = {p.position_id: p for p in actual_positions}
        intent_set = set(intent_ids)
        actual_set = set(actual_by_id.keys())

        owned = self.get_owned_positions(actual_positions)
        owned_ids = {p.position_id for p in owned}
        foreign = [p for p in actual_positions if p not in owned]

        missing = sorted(intent_set - actual_set)
        unexpected = [actual_by_id[pid] for pid in sorted(actual_set - intent_set)]

        has_discrepancy = bool(missing or unexpected or foreign)

        return {
            "status": "discrepancy" if has_discrepancy else "clean",
            "owned_positions": [p.model_dump() for p in owned],
            "foreign_positions": [p.model_dump() for p in foreign],
            "missing_positions": missing,
            "unexpected_positions": [p.model_dump() for p in unexpected],
            "summary": {
                "expected": len(intent_set),
                "actual": len(actual_set),
                "owned": len(owned),
                "foreign": len(foreign),
                "missing": len(missing),
                "unexpected": len(unexpected),
            },
        }
