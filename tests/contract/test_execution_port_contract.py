from __future__ import annotations

from mt5_mcp.adapters.pymt5_adapter.adapter import PyMT5Adapter
from mt5_mcp.services.execution_gateway.service import ExecutionGateway


def test_adapter_health_contract():
    gw = ExecutionGateway(PyMT5Adapter())
    h = gw.health()
    assert h.state in {
        "healthy",
        "degraded_read_only",
        "degraded_write_blocked",
        "disconnected",
        "incident",
    }


def test_resources_contract():
    gw = ExecutionGateway(PyMT5Adapter())
    ts = gw.terminal_status()
    assert ts.connected in {True, False}
    acc = gw.account_summary()
    assert acc.environment in {"paper", "demo", "live"}
