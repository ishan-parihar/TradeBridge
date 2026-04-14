"""Tests for Vibe-Trading gateway routes."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import apps.vibe_bridge.gateway_routes as gw
from apps.vibe_bridge.gateway_routes import (
    VibeBacktestRequest,
    VibeMarketDataRequest,
    VibeSwarmRunRequest,
    VibeToolRequest,
    router,
)


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset the client singleton before each test."""
    gw._client = None
    yield
    gw._client = None


@pytest.fixture
def app():
    """Create test app with vibe router."""
    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")
    return test_app


@pytest.fixture
def mock_client():
    """Create a mocked VibeBridgeClient."""
    m = MagicMock()
    m.get_status.return_value = {"running": True, "started": True, "pid": 12345, "port": 8900}
    m.base_url = "http://127.0.0.1:8900"
    m.ensure_ready = AsyncMock(return_value=True)
    m.call_tool = AsyncMock(return_value='{"skills": []}')
    m.list_skills = AsyncMock(return_value='["skill1", "skill2"]')
    m.get_market_data = AsyncMock(return_value='{"data": []}')
    m.run_swarm = AsyncMock(return_value='{"run_id": "abc123", "final_report": "BUY XAUUSD at 2350"}')
    m.backtest = AsyncMock(return_value='{"sharpe_ratio": 1.5, "total_return": 0.25}')
    m._lifecycle = MagicMock()
    m._lifecycle.stop = AsyncMock()
    return m


def _patched_client(mock_client, app):
    """Helper to create TestClient with a mocked singleton client."""
    gw._client = mock_client
    return TestClient(app)


class TestVibeStatus:
    def test_status_returns_running(self, app, mock_client):
        client = _patched_client(mock_client, app)
        response = client.get("/api/vibe/status")
        assert response.status_code == 200
        data = response.json()
        assert data["running"] is True
        assert data["pid"] == 12345


class TestVibeStart:
    def test_start_returns_running(self, app, mock_client):
        client = _patched_client(mock_client, app)
        response = client.post("/api/vibe/start")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "running"
        assert data["url"] == "http://127.0.0.1:8900"

    def test_start_fails_raises_503(self, app, mock_client):
        mock_client.ensure_ready = AsyncMock(return_value=False)
        client = _patched_client(mock_client, app)
        response = client.post("/api/vibe/start")
        assert response.status_code == 503


class TestVibeStop:
    def test_stop_returns_stopped(self, app, mock_client):
        client = _patched_client(mock_client, app)
        response = client.post("/api/vibe/stop")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "stopped"
        mock_client._lifecycle.stop.assert_awaited_once()


class TestVibeCallTool:
    def test_call_tool_success(self, app, mock_client):
        client = _patched_client(mock_client, app)
        response = client.post(
            "/api/vibe/tool",
            json={"tool": "web_search", "arguments": {"query": "gold price"}},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["tool"] == "web_search"

    def test_call_tool_invalid_tool_raises(self, app, mock_client):
        mock_client.call_tool = AsyncMock(side_effect=ValueError("Unknown Vibe-Trading tool"))
        gw._client = mock_client
        tc = TestClient(app, raise_server_exceptions=True)
        with pytest.raises(ValueError, match="Unknown Vibe-Trading tool"):
            tc.post("/api/vibe/tool", json={"tool": "invalid_tool", "arguments": {}})


class TestVibeListSkills:
    def test_list_skills(self, app, mock_client):
        client = _patched_client(mock_client, app)
        response = client.get("/api/vibe/skills")
        assert response.status_code == 200
        data = response.json()
        assert "skills" in data


class TestVibeMarketData:
    def test_market_data_request(self, app, mock_client):
        client = _patched_client(mock_client, app)
        response = client.post(
            "/api/vibe/market-data",
            json={
                "codes": ["BTC-USDT"],
                "start_date": "2024-01-01",
                "end_date": "2024-12-31",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "data" in data

    def test_market_data_missing_fields(self, app, mock_client):
        client = _patched_client(mock_client, app)
        response = client.post(
            "/api/vibe/market-data",
            json={"codes": ["BTC-USDT"]},
        )
        assert response.status_code == 422


class TestVibeSwarm:
    def test_swarm_run(self, app, mock_client):
        client = _patched_client(mock_client, app)
        response = client.post(
            "/api/vibe/swarm/run",
            json={
                "preset": "investment_committee",
                "variables": {"symbol": "XAUUSD"},
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "result" in data

    def test_swarm_presets(self, app, mock_client):
        client = _patched_client(mock_client, app)
        response = client.get("/api/vibe/swarm/presets")
        assert response.status_code == 200
        data = response.json()
        assert "presets" in data


class TestVibeBacktest:
    def test_backtest_without_auto_execute(self, app, mock_client):
        client = _patched_client(mock_client, app)
        response = client.post("/api/vibe/backtest", json={"run_dir": "/tmp/test_run"})
        assert response.status_code == 200
        data = response.json()
        assert "backtest_result" in data
        assert "signal" not in data

    def test_backtest_with_auto_execute_good(self, app, mock_client):
        client = _patched_client(mock_client, app)
        response = client.post(
            "/api/vibe/backtest",
            json={
                "run_dir": "/tmp/test_run",
                "auto_execute": True,
                "symbol": "XAUUSD",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "signal" in data
        assert data["signal"]["action"] in ("BUY", "SELL")


class TestVibeSwarmAnalyzeAndTrade:
    def test_swarm_analyze_and_trade_with_signal(self, app, mock_client):
        mock_client.run_swarm = AsyncMock(
            return_value='{"run_id": "abc123", "final_report": "BUY XAUUSD at 2350.00 with confidence 80%"}'
        )
        client = _patched_client(mock_client, app)
        response = client.post(
            "/api/vibe/swarm/analyze-and-trade",
            json={
                "preset": "investment_committee",
                "variables": {"symbol": "XAUUSD"},
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "signal" in data
        assert data["signal"] is not None
        assert data["signal"]["action"] == "BUY"

    def test_swarm_analyze_and_trade_no_signal(self, app, mock_client):
        mock_client.run_swarm = AsyncMock(
            return_value='{"run_id": "def456", "final_report": "Market conditions are neutral, no clear direction"}'
        )
        client = _patched_client(mock_client, app)
        response = client.post(
            "/api/vibe/swarm/analyze-and-trade",
            json={
                "preset": "market_research",
                "variables": {"symbol": "EURUSD"},
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["signal"] is None
        assert "message" in data


class TestPydanticModels:
    def test_vibe_tool_request_defaults(self):
        req = VibeToolRequest(tool="web_search")
        assert req.tool == "web_search"
        assert req.arguments == {}

    def test_vibe_market_data_request_defaults(self):
        req = VibeMarketDataRequest(codes=["BTC-USDT"], start_date="2024-01-01", end_date="2024-12-31")
        assert req.source == "auto"
        assert req.interval == "1D"

    def test_vibe_backtest_request_defaults(self):
        req = VibeBacktestRequest(run_dir="/tmp/test")
        assert req.auto_execute is False
        assert req.symbol == ""

    def test_vibe_swarm_run_request(self):
        req = VibeSwarmRunRequest(preset="investment_committee", variables={"symbol": "XAUUSD"})
        assert req.preset == "investment_committee"
        assert req.variables == {"symbol": "XAUUSD"}
