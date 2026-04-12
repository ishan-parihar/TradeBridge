from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def reset_shared_singletons():
    """Reset shared module singletons between every test."""
    import apps.mcp_server.shared as shared

    shared._http_client = None
    shared._gw = None
    shared._settings = None
    shared._tcp_client = None
    shared._shutdown_state.clear()
    shared._shutdown_state["frozen"] = False
    shared._shutdown_state["frozen_at"] = None
    shared._shutdown_state["frozen_by"] = None

    # Also reset tool-level singletons
    try:
        import apps.mcp_server.tools_ea_native as ea

        ea._trailing_stops.clear()
    except Exception:
        pass
    try:
        import apps.mcp_server.tools_trading as tr

        tr._trailing_stops.clear()
    except Exception:
        pass

    yield
