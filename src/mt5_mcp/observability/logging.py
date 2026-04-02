from __future__ import annotations

import logging
import os


def setup_logging(level: str | int | None = None) -> None:
    lvl = level or os.getenv("LOG_LEVEL", "INFO")
    logging.basicConfig(
        level=getattr(logging, str(lvl).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


logger = logging.getLogger("mt5_mcp")
