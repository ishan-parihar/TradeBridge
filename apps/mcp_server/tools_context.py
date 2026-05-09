import json
import asyncio
import time
from typing import Any, Optional, Literal
from pydantic import BaseModel, ConfigDict, Field
from mcp.types import ToolAnnotations
from . import mcp
from .shared import (
    get_gateway,
    get_http_client,
    get_settings_cached,
    _tcp_send_and_await,
    _batch_enqueue_and_await,
    _await_result,
    _parse_payload,
    _parse_payload_dict,
    _parse_indicator_value,
    _first_bid_ask,
)
from mt5_mcp.adapters.common.symbol_utils import normalize_symbol, denormalize_symbol

_CONTEXT_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)