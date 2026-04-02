from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    # Path to MT5 terminal directory (on Arch via Bottles/Wine by default)
    mt5_terminal_path: str = os.getenv(
        "MT5_TERMINAL_PATH",
        "/home/ishanp/.local/share/bottles/bottles/Apps/drive_c/Program Files/MetaTrader 5",
    )
    environment: str = os.getenv("MT5_ENV", "demo")
    execution_mode: str = os.getenv("MT5_EXECUTION_MODE", "human_approval_required")
    adapter: str = os.getenv("MT5_ADAPTER", "pymt5")  # or "ea_socket"
    gateway_url: str = os.getenv("MT5_GATEWAY_URL", "http://127.0.0.1:8020")
    redis_url: str = os.getenv("MT5_REDIS_URL", "redis://localhost:6379/0")
    symbol_suffix: str = os.getenv(
        "MT5_SYMBOL_SUFFIX", "m"
    )  # Suffix for symbols (e.g., 'm' for XAUUSDm)


def get_settings() -> Settings:
    return Settings()
