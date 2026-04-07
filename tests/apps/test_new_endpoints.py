from __future__ import annotations

import json
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

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
                get_orders=lambda: [],
                get_deals_history=lambda limit=100, symbol=None, days=30: {"deals": []},
            ),
            account_summary=lambda: mcp_main.AccountSummary(
                environment="demo",
                balance=10000.0,
                equity=10000.0,
                margin=0.0,
                free_margin=10000.0,
            ),
            health=lambda: mcp_main.HealthStatus(state="healthy"),
        ),
    )
    monkeypatch.setattr(mcp_main, "tool_get_positions", lambda: {"positions": []})
    monkeypatch.setattr(mcp_main, "tool_get_orders", lambda: {"orders": []})
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


# ============================================================
# Task 1: Portfolio Exposure Endpoints
# ============================================================


class TestPortfolioExposureEndpoint:
    def test_empty_portfolio(self, client):
        resp = client.post("/tools/portfolio/exposure", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert "total_exposure_usd" in data or "exposure" in data
        assert data.get("risk_score", 0) == 0

    def test_with_positions(self, client, monkeypatch):
        monkeypatch.setattr(
            mcp_main,
            "resource_positions_open",
            lambda: [
                SimpleNamespace(
                    position_id="1",
                    symbol="EURUSD",
                    side="buy",
                    volume=0.10,
                    entry_price=1.0850,
                    mark_price=1.0860,
                    sl=1.0800,
                    tp=1.0950,
                    profit=10.0,
                    magic=0,
                ),
            ],
        )
        monkeypatch.setattr(
            mcp_main,
            "resource_account_summary",
            lambda: SimpleNamespace(
                balance=10000.0, equity=10010.0, margin=50.0, free_margin=9960.0
            ),
        )
        resp = client.post("/tools/portfolio/exposure", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert "error" not in data

    def test_with_pending_orders_in_projection(self, client, monkeypatch):
        monkeypatch.setattr(
            mcp_main,
            "resource_orders_pending",
            lambda: [
                SimpleNamespace(
                    order_id="100",
                    symbol="GBPUSD",
                    side="buy",
                    volume=0.10,
                    price=1.2700,
                    type="buy_limit",
                ),
            ],
        )
        monkeypatch.setattr(
            mcp_main,
            "resource_account_summary",
            lambda: SimpleNamespace(
                balance=10000.0, equity=10000.0, margin=0.0, free_margin=10000.0
            ),
        )
        resp = client.post("/tools/portfolio/exposure", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert "error" not in data


class TestPreTradeGateEndpoint:
    def test_gate_allows_low_risk_trade(self, client):
        resp = client.post(
            "/tools/portfolio/pre_trade_gate",
            json={
                "symbol": "EURUSD",
                "side": "buy",
                "volume_lots": 0.01,
                "sl_distance": 50.0,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "allowed" in data

    def test_gate_rejects_excessive_volume(self, client, monkeypatch):
        monkeypatch.setattr(
            mcp_main,
            "resource_positions_open",
            lambda: [
                SimpleNamespace(
                    position_id="1",
                    symbol="EURUSD",
                    side="buy",
                    volume=5.0,
                    entry_price=1.0850,
                    mark_price=1.0860,
                    sl=1.0800,
                    tp=1.0950,
                    profit=50.0,
                    magic=0,
                ),
                SimpleNamespace(
                    position_id="2",
                    symbol="GBPUSD",
                    side="buy",
                    volume=5.0,
                    entry_price=1.2700,
                    mark_price=1.2710,
                    sl=1.2650,
                    tp=1.2800,
                    profit=50.0,
                    magic=0,
                ),
            ],
        )
        monkeypatch.setattr(
            mcp_main,
            "resource_account_summary",
            lambda: SimpleNamespace(
                balance=10000.0, equity=10100.0, margin=9000.0, free_margin=1100.0
            ),
        )
        resp = client.post(
            "/tools/portfolio/pre_trade_gate",
            json={
                "symbol": "XAUUSD",
                "side": "buy",
                "volume_lots": 1.0,
                "sl_distance": 10.0,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "allowed" in data
        assert "reason" in data

    def test_gate_validates_required_fields(self, client):
        resp = client.post(
            "/tools/portfolio/pre_trade_gate",
            json={
                "symbol": "EURUSD",
            },
        )
        assert resp.status_code == 422


# ============================================================
# Task 2: Custom Indicator Endpoint
# ============================================================


class TestCustomIndicatorEndpoint:
    def test_schema_validation(self, client):
        resp = client.post(
            "/tools/market/custom_indicator",
            json={
                "symbol": "EURUSD",
                "timeframe": "H1",
            },
        )
        assert resp.status_code == 422

    def test_tcp_success(self, client, monkeypatch):
        monkeypatch.setattr(
            mcp_main,
            "_tcp_send_and_await",
            lambda *a, **kw: {
                "status": "completed",
                "result": {
                    "payload": json.dumps(
                        {
                            "indicator": "Examples\\MACD",
                            "buffer_index": 0,
                            "count": 5,
                            "values": [0.0012, -0.0008, 0.0005, 0.0001, -0.0003],
                            "error": None,
                        }
                    )
                },
            },
        )
        resp = client.post(
            "/tools/market/custom_indicator",
            json={
                "symbol": "EURUSD",
                "timeframe": "H1",
                "indicator_name": "Examples\\MACD",
                "params": "fast=12,slow=26,signal=9",
                "buffer_index": 0,
                "count": 5,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "values" in data
        assert len(data["values"]) == 5
        assert data["values"][0] == 0.0012

    def test_http_fallback(self, client, monkeypatch):
        monkeypatch.setattr(mcp_main, "_tcp_send_and_await", lambda *a, **kw: None)
        monkeypatch.setattr(
            mcp_main,
            "_await_result",
            lambda *a, **kw: {
                "status": "completed",
                "result": {
                    "payload": json.dumps(
                        {
                            "indicator": "Custom\\MyOscillator",
                            "buffer_index": 0,
                            "count": 3,
                            "values": [45.2, 52.1, 48.7],
                            "error": None,
                        }
                    )
                },
            },
        )
        mock_response = MagicMock()
        mock_response.json.return_value = {"id": "test-req-1"}
        mock_response.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.post.return_value = mock_response
        monkeypatch.setattr(mcp_main, "get_http_client", lambda: mock_client)

        resp = client.post(
            "/tools/market/custom_indicator",
            json={
                "symbol": "XAUUSD",
                "timeframe": "M15",
                "indicator_name": "Custom\\MyOscillator",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "values" in data

    def test_ea_error_response(self, client, monkeypatch):
        monkeypatch.setattr(
            mcp_main,
            "_tcp_send_and_await",
            lambda *a, **kw: {
                "status": "completed",
                "result": {
                    "payload": json.dumps(
                        {
                            "indicator": "NonExistent\\Indicator",
                            "error": "indicator_handle_failed",
                        }
                    )
                },
            },
        )
        resp = client.post(
            "/tools/market/custom_indicator",
            json={
                "symbol": "EURUSD",
                "timeframe": "H1",
                "indicator_name": "NonExistent\\Indicator",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data
        assert "indicator_handle_failed" in data["error"]

    def test_timeout_handling(self, client, monkeypatch):
        monkeypatch.setattr(mcp_main, "_tcp_send_and_await", lambda *a, **kw: None)
        monkeypatch.setattr(
            mcp_main,
            "_await_result",
            lambda *a, **kw: {"status": "timeout", "error": "timeout"},
        )
        mock_response = MagicMock()
        mock_response.json.return_value = {"id": "test-timeout"}
        mock_response.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.post.return_value = mock_response
        monkeypatch.setattr(mcp_main, "get_http_client", lambda: mock_client)

        resp = client.post(
            "/tools/market/custom_indicator",
            json={
                "symbol": "EURUSD",
                "timeframe": "H1",
                "indicator_name": "Examples\\RSI",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data


# ============================================================
# Task 3: ONNX Inference Endpoints
# ============================================================


class TestMLPredictEndpoint:
    def test_predict_without_onnx_runtime(self, client, monkeypatch):
        monkeypatch.setattr(mcp_main, "_get_onnx_service", lambda: None)
        resp = client.post(
            "/tools/ml/predict",
            json={
                "model_name": "test_model",
                "features": [0.1, 0.2, 0.3],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data
        assert "onnxruntime" in data["error"].lower() or "ONNX" in data["error"]

    def test_predict_success_classification(self, client, monkeypatch):
        mock_svc = MagicMock()
        mock_svc.predict.return_value = {
            "model_name": "test_classifier",
            "prediction": "up",
            "confidence": 0.73,
            "raw_output": [0.73, 0.27],
            "inference_time_ms": 2.1,
            "model_info": {
                "input_shape": [1, 3],
                "output_shape": [1, 2],
                "input_names": ["features"],
                "output_names": ["probability"],
            },
        }
        monkeypatch.setattr(mcp_main, "_get_onnx_service", lambda: mock_svc)
        resp = client.post(
            "/tools/ml/predict",
            json={
                "model_name": "test_classifier",
                "features": [0.1, 0.2, 0.3],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["prediction"] == "up"
        assert data["confidence"] == 0.73

    def test_predict_shape_mismatch(self, client, monkeypatch):
        mock_svc = MagicMock()
        mock_svc.predict.side_effect = ValueError(
            "Input shape mismatch: expected [1, 5], got [1, 3]"
        )
        monkeypatch.setattr(mcp_main, "_get_onnx_service", lambda: mock_svc)
        resp = client.post(
            "/tools/ml/predict",
            json={
                "model_name": "test_model",
                "features": [0.1, 0.2, 0.3],
            },
        )
        assert resp.status_code == 400
        data = resp.json()
        assert "detail" in data


class TestMLModelsEndpoint:
    def test_list_models_without_runtime(self, client, monkeypatch):
        monkeypatch.setattr(mcp_main, "_get_onnx_service", lambda: None)
        resp = client.get("/tools/ml/models")
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data

    def test_list_models_success(self, client, monkeypatch):
        mock_svc = MagicMock()
        mock_svc.list_models.return_value = {
            "models": {
                "test_model": {
                    "file": "/path/to/test_model.onnx",
                    "input_shape": [1, 5],
                    "output_shape": [1, 2],
                    "loaded": True,
                }
            }
        }
        monkeypatch.setattr(mcp_main, "_get_onnx_service", lambda: mock_svc)
        resp = client.get("/tools/ml/models")
        assert resp.status_code == 200
        data = resp.json()
        assert "models" in data
        assert "test_model" in data["models"]


class TestMLReloadEndpoint:
    def test_reload_without_runtime(self, client, monkeypatch):
        monkeypatch.setattr(mcp_main, "_get_onnx_service", lambda: None)
        resp = client.post("/tools/ml/models/reload", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data

    def test_reload_success(self, client, monkeypatch):
        mock_svc = MagicMock()
        mock_svc.reload.return_value = {"loaded": 2, "failed": []}
        monkeypatch.setattr(mcp_main, "_get_onnx_service", lambda: mock_svc)
        resp = client.post("/tools/ml/models/reload", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["loaded"] == 2


# ============================================================
# Task 4: Historical Data Cache Endpoints
# ============================================================


class TestDataImportEndpoint:
    def test_import_bars_csv(self, client, monkeypatch, tmp_path):
        db_path = tmp_path / "test.db"
        from mt5_mcp.services.data_store import DataStore

        store = DataStore(db_path=str(db_path))
        monkeypatch.setattr(mcp_main, "_get_data_store", lambda: store)

        csv_content = """<DATE>,<TIME>,<OPEN>,<HIGH>,<LOW>,<CLOSE>,<TICKVOL>,<VOL>,<SPREAD>
2024.01.01,00:00,1.08500,1.08600,1.08450,1.08550,1000,0,10
2024.01.01,01:00,1.08550,1.08700,1.08500,1.08650,1200,0,12"""

        resp = client.post(
            "/tools/data/import",
            json={
                "data_type": "bars",
                "format": "csv",
                "content": csv_content,
                "symbol": "EURUSD",
                "timeframe": "H1",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["imported"] >= 2
        assert data["errors"] == []

    def test_import_bars_json(self, client, monkeypatch, tmp_path):
        db_path = tmp_path / "test2.db"
        from mt5_mcp.services.data_store import DataStore

        store = DataStore(db_path=str(db_path))
        monkeypatch.setattr(mcp_main, "_get_data_store", lambda: store)

        json_content = json.dumps(
            [
                {
                    "symbol": "EURUSD",
                    "timeframe": "H1",
                    "time": "2024-01-01T00:00:00",
                    "open": 1.08500,
                    "high": 1.08600,
                    "low": 1.08450,
                    "close": 1.08550,
                    "tick_volume": 1000,
                },
            ]
        )
        resp = client.post(
            "/tools/data/import",
            json={
                "data_type": "bars",
                "format": "json",
                "content": json_content,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["imported"] >= 1

    def test_import_invalid_data_type(self, client, monkeypatch):
        monkeypatch.setattr(mcp_main, "_get_data_store", lambda: None)
        resp = client.post(
            "/tools/data/import",
            json={
                "data_type": "invalid",
                "format": "csv",
                "content": "test",
            },
        )
        assert resp.status_code == 422


class TestDataBarsEndpoint:
    def test_query_bars_empty(self, client, monkeypatch, tmp_path):
        db_path = tmp_path / "test3.db"
        from mt5_mcp.services.data_store import DataStore

        store = DataStore(db_path=str(db_path))
        monkeypatch.setattr(mcp_main, "_get_data_store", lambda: store)

        resp = client.post("/tools/data/bars", json={"symbol": "EURUSD"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"] == []
        assert data["count"] == 0

    def test_query_bars_with_data(self, client, monkeypatch, tmp_path):
        db_path = tmp_path / "test4.db"
        from mt5_mcp.services.data_store import DataStore

        store = DataStore(db_path=str(db_path))

        csv_content = """<DATE>,<TIME>,<OPEN>,<HIGH>,<LOW>,<CLOSE>,<TICKVOL>,<VOL>,<SPREAD>
2024.01.01,00:00,1.08500,1.08600,1.08450,1.08550,1000,0,10
2024.01.01,01:00,1.08550,1.08700,1.08500,1.08650,1200,0,12"""
        store.import_bars_csv(csv_content, symbol="EURUSD", timeframe="H1")
        monkeypatch.setattr(mcp_main, "_get_data_store", lambda: store)

        resp = client.post("/tools/data/bars", json={"symbol": "EURUSD"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]) == 2
        assert data["data"][0]["open"] == 1.085
        assert data["source"] == "cache"

    def test_query_bars_date_range(self, client, monkeypatch, tmp_path):
        db_path = tmp_path / "test5.db"
        from mt5_mcp.services.data_store import DataStore

        store = DataStore(db_path=str(db_path))

        csv_content = """<DATE>,<TIME>,<OPEN>,<HIGH>,<LOW>,<CLOSE>,<TICKVOL>,<VOL>,<SPREAD>
2024.01.01,00:00,1.08500,1.08600,1.08450,1.08550,1000,0,10
2024.01.02,00:00,1.08600,1.08700,1.08550,1.08650,1100,0,11
2024.01.03,00:00,1.08700,1.08800,1.08650,1.08750,1200,0,12"""
        store.import_bars_csv(csv_content, symbol="EURUSD", timeframe="H1")
        monkeypatch.setattr(mcp_main, "_get_data_store", lambda: store)

        resp = client.post(
            "/tools/data/bars",
            json={
                "symbol": "EURUSD",
                "start_time": "2024-01-02T00:00:00",
                "end_time": "2024-01-02T23:59:59",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]) == 1
        assert data["data"][0]["close"] == 1.0865


class TestDataStatsEndpoint:
    def test_stats_empty(self, client, monkeypatch, tmp_path):
        db_path = tmp_path / "test6.db"
        from mt5_mcp.services.data_store import DataStore

        store = DataStore(db_path=str(db_path))
        monkeypatch.setattr(mcp_main, "_get_data_store", lambda: store)

        resp = client.get("/tools/data/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "bars" in data
        assert data["bars"]["total_rows"] == 0

    def test_stats_with_data(self, client, monkeypatch, tmp_path):
        db_path = tmp_path / "test7.db"
        from mt5_mcp.services.data_store import DataStore

        store = DataStore(db_path=str(db_path))

        csv_content = """<DATE>,<TIME>,<OPEN>,<HIGH>,<LOW>,<CLOSE>,<TICKVOL>,<VOL>,<SPREAD>
2024.01.01,00:00,1.08500,1.08600,1.08450,1.08550,1000,0,10"""
        store.import_bars_csv(csv_content, symbol="EURUSD", timeframe="H1")
        monkeypatch.setattr(mcp_main, "_get_data_store", lambda: store)

        resp = client.get("/tools/data/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["bars"]["total_rows"] >= 1
        assert "EURUSD" in data["bars"]["symbols"]


class TestDataStoreUnavailable:
    def test_import_unavailable(self, client, monkeypatch):
        monkeypatch.setattr(mcp_main, "_get_data_store", lambda: None)
        resp = client.post(
            "/tools/data/import",
            json={
                "data_type": "bars",
                "format": "csv",
                "content": "test",
                "symbol": "EURUSD",
                "timeframe": "H1",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data

    def test_bars_unavailable(self, client, monkeypatch):
        monkeypatch.setattr(mcp_main, "_get_data_store", lambda: None)
        resp = client.post("/tools/data/bars", json={"symbol": "EURUSD"})
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data

    def test_stats_unavailable(self, client, monkeypatch):
        monkeypatch.setattr(mcp_main, "_get_data_store", lambda: None)
        resp = client.get("/tools/data/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data
