from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import apps.mcp_server.main as mcp_main
from mt5_mcp.settings.config import Settings


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(
        mcp_main,
        "get_gateway",
        lambda: SimpleNamespace(
            adapter=SimpleNamespace(
                get_positions=lambda: [],
                get_deals_history=lambda limit=100, symbol=None, days=30: {"deals": []},
            ),
            account_summary=lambda: mcp_main.AccountSummary(environment="demo"),
            health=lambda: mcp_main.HealthStatus(state="healthy"),
        ),
    )
    monkeypatch.setattr(mcp_main, "tool_get_positions", lambda: {"positions": []})
    monkeypatch.setattr(
        mcp_main,
        "tool_get_deals_history",
        lambda limit=100, symbol=None, days=30: {"deals": []},
    )
    monkeypatch.setattr(
        mcp_main,
        "get_settings_cached",
        lambda: Settings(strategy_magic_numbers={"scalp": 12345}),
    )
    return TestClient(mcp_main.app)


class TestReconcileEndpoint:
    def test_reconcile_empty(self, client, monkeypatch):
        monkeypatch.setattr(
            mcp_main,
            "_get_reconcile_context",
            lambda: {
                "positions": [],
                "deals": [],
            },
        )
        resp = client.post("/tools/reconcile", json={"intent_ids": []})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "clean"
        assert data["owned_positions"] == []
        assert data["foreign_positions"] == []

    def test_reconcile_with_owned_position(self, client, monkeypatch):
        monkeypatch.setattr(
            mcp_main,
            "_get_reconcile_context",
            lambda: {
                "positions": [
                    {
                        "position_id": "pos_1",
                        "symbol": "XAUUSD",
                        "side": "buy",
                        "volume": 0.1,
                        "entry_price": 2000.0,
                        "strategy_id": "scalp",
                        "session_id": "sess_1",
                    }
                ],
                "deals": [],
            },
        )
        resp = client.post("/tools/reconcile", json={"intent_ids": ["pos_1"]})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "clean"
        assert len(data["owned_positions"]) == 1
        assert data["owned_positions"][0]["position_id"] == "pos_1"

    def test_reconcile_missing_position(self, client, monkeypatch):
        monkeypatch.setattr(
            mcp_main,
            "_get_reconcile_context",
            lambda: {
                "positions": [],
                "deals": [],
            },
        )
        resp = client.post("/tools/reconcile", json={"intent_ids": ["pos_missing"]})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "discrepancy"
        assert "pos_missing" in data["missing_positions"]

    def test_reconcile_foreign_position(self, client, monkeypatch):
        monkeypatch.setattr(
            mcp_main,
            "_get_reconcile_context",
            lambda: {
                "positions": [
                    {
                        "position_id": "pos_foreign",
                        "symbol": "XAUUSD",
                        "side": "buy",
                        "volume": 0.1,
                        "entry_price": 2000.0,
                        "strategy_id": "other_strategy",
                    }
                ],
                "deals": [
                    {
                        "deal_id": "d1",
                        "symbol": "XAUUSD",
                        "side": "buy",
                        "volume": 0.1,
                        "price": 2000.0,
                        "profit": 50.0,
                        "commission": 0.0,
                        "swap": 0.0,
                        "fee": 0.0,
                        "time": "2025-01-01T00:00:00",
                        "magic": 99999,
                    }
                ],
            },
        )
        resp = client.post("/tools/reconcile", json={"intent_ids": []})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["foreign_positions"]) == 1
        assert data["foreign_positions"][0]["position_id"] == "pos_foreign"
        assert data["foreign_pnl"] == pytest.approx(50.0)

    def test_reconcile_includes_summary(self, client, monkeypatch):
        monkeypatch.setattr(
            mcp_main,
            "_get_reconcile_context",
            lambda: {
                "positions": [
                    {
                        "position_id": "p1",
                        "symbol": "XAUUSD",
                        "side": "buy",
                        "volume": 0.1,
                        "entry_price": 2000.0,
                        "strategy_id": "scalp",
                    }
                ],
                "deals": [],
            },
        )
        resp = client.post("/tools/reconcile", json={"intent_ids": ["p1"]})
        assert resp.status_code == 200
        data = resp.json()
        assert "summary" in data
        assert data["summary"]["expected"] == 1
        assert data["summary"]["actual"] == 1
        assert data["summary"]["owned"] == 1
        assert data["summary"]["foreign"] == 0
