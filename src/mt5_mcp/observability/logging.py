from __future__ import annotations

import logging
import os
from contextvars import ContextVar

# Thread-safe context variables for correlation tracking
correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)
intent_id: ContextVar[str | None] = ContextVar("intent_id", default=None)
request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


class CorrelationFilter(logging.Filter):
    """Inject correlation_id, intent_id, request_id into log records from contextvars."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = correlation_id.get() or "-"
        record.intent_id = intent_id.get() or "-"
        record.request_id = request_id.get() or "-"
        return True


def setup_logging(level: str | int | None = None) -> None:
    lvl = level or os.getenv("LOG_LEVEL", "INFO")
    logging.basicConfig(
        level=getattr(logging, str(lvl).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s [corr:%(correlation_id)s] [intent:%(intent_id)s] [req:%(request_id)s] %(message)s",
    )
    # Add correlation filter to root logger
    root = logging.getLogger()
    for handler in root.handlers:
        handler.addFilter(CorrelationFilter())


# Convenience functions for setting correlation context
def set_correlation_id(cid: str) -> None:
    """Set the correlation ID for the current request context."""
    correlation_id.set(cid)


def set_intent_id(iid: str) -> None:
    """Set the intent ID for the current trading operation."""
    intent_id.set(iid)


def set_request_id(rid: str) -> None:
    """Set the request ID for the current API request."""
    request_id.set(rid)


logger = logging.getLogger("mt5_mcp")
