from __future__ import annotations

from fastapi.testclient import TestClient

import apps.bridge_gateway.main as gateway_main


class DummyQueue:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, dict[str, object]]] = []
        self.failed: list[tuple[str, str]] = []

    def enqueue(self, command_type: str, payload: dict[str, object]) -> str:
        self.enqueued.append((command_type, payload))
        return "req-1"

    def fail(self, request_id: str, error: str) -> bool:
        self.failed.append((request_id, error))
        return True


def test_bridge_commands_enqueue_preserves_advanced_indicator_args(monkeypatch) -> None:
    queue = DummyQueue()
    monkeypatch.setattr(gateway_main, "get_queue_cached", lambda: queue)

    client = TestClient(gateway_main.app)
    response = client.post(
        "/bridge/commands/enqueue",
        params={
            "type": "get_indicator",
            "symbol": "XAUUSDm",
            "timeframe": "H1",
            "indicator": "macd",
            "fast": 12,
            "slow": 26,
            "signal": 9,
            "k_period": 14,
            "d_period": 3,
            "slowing": 3,
            "tenkan": 9,
            "kijun": 26,
            "senkou": 52,
            "window": 50,
        },
    )

    assert response.status_code == 200
    assert queue.enqueued == [
        (
            "get_indicator",
            {
                "symbol": "XAUUSDm",
                "timeframe": "H1",
                "indicator": "macd",
                "fast": 12,
                "slow": 26,
                "signal": 9,
                "k_period": 14,
                "d_period": 3,
                "slowing": 3,
                "tenkan": 9,
                "kijun": 26,
                "senkou": 52,
                "window": 50,
            },
        )
    ]


def test_bridge_results_accepts_malformed_error_json(monkeypatch) -> None:
    queue = DummyQueue()
    monkeypatch.setattr(gateway_main, "get_queue_cached", lambda: queue)

    client = TestClient(gateway_main.app)
    raw_body = (
        '{"request_id":"req-1","status":"error","error":"{"retcode":10021,'
        '"order":0,"deal":0}"}'
    )
    response = client.post(
        "/bridge/results",
        content=raw_body,
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 200
    assert queue.failed == [("req-1", '{"retcode":10021,"order":0,"deal":0}')]


def test_bridge_commands_enqueue_preserves_agent_research_args(monkeypatch) -> None:
    queue = DummyQueue()
    monkeypatch.setattr(gateway_main, "get_queue_cached", lambda: queue)

    client = TestClient(gateway_main.app)
    response = client.post(
        "/bridge/commands/enqueue",
        params={
            "type": "estimate_margin",
            "symbol": "BTCUSDm",
            "side": "buy",
            "volume_lots": 0.02,
            "price": 66500.0,
            "limit": 25,
            "days": 7,
        },
    )

    assert response.status_code == 200
    assert queue.enqueued == [
        (
            "estimate_margin",
            {
                "symbol": "BTCUSDm",
                "side": "buy",
                "volume_lots": 0.02,
                "price": 66500.0,
                "limit": 25,
                "days": 7,
            },
        )
    ]
